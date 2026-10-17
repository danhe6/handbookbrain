"""HTTP API + web UI.   python -m hb --company larkspur serve   ->  http://localhost:8000

Everything here is a thin read/act layer over the same graph and functions the CLI uses; no logic of its own.
  GET  /api/overview            company, sources (live/static, last sync), graph counts, open items
  POST /api/ask                 {question} -> answer, citations (exact section versions), agent steps
  GET  /api/updates             recent changes, newest first (what changed, where it came from)
  GET  /api/review              needs review: stale answers, decided-not-documented, chat/meeting vs docs,
                                source conflicts (live vs static), knowledge gaps
  POST /api/refresh             re-answer stale questions
  GET  /api/brain               pages by source with owners + provenance, decisions and their status
  GET  /api/page?path=          one page: owners, sections, history
  GET  /api/trust               latest benchmark (runs/<company>/bench.json)
  POST /api/upload              add files to the company's uploads source (static)
  POST /api/sync, GET /api/sync daily sync (pull live sources, apply changes, re-check), run in background
  GET  /api/companies, POST /api/switch {name}   company switcher (restarts this process on the other config)
  GET  /api/learn/areas|area|brief  onboarding start page per area (documents, people, decisions, systems, brief)
  GET  /api/glossary, /api/term     jargon found in the docs, defined from them on first use (Term nodes)
  POST /api/explain {node, question, history}   "ask about this": one node's graph context -> plain answer
"""
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .graph import get_meta, rows

app = FastAPI(title="HandbookBrain")
WEB = config.ROOT / "web"
SYNC = {"running": False, "log": [], "started": None, "finished": None, "error": None}


def _cite(sid: str) -> str:
    from .parse import cite_key
    return cite_key(sid)


# ---------------------------------------------------------------- overview / companies

@app.get("/api/companies")
def companies():
    out = []
    for f in sorted((config.ROOT / "companies").glob("*.yaml")):
        import yaml
        cfg = yaml.safe_load(f.read_text()) or {}
        out.append({"id": f.stem, "name": cfg.get("name") or ("GitLab" if f.stem == "gitlab" else f.stem.title()),
                    "sources": [s.get("name") for s in cfg.get("sources") or [cfg.get("docs") or {}]]})
    if not any(c["id"] == "gitlab" for c in out):
        out.insert(0, {"id": "gitlab", "name": "GitLab", "sources": ["handbook"]})
    return {"current": config.COMPANY, "companies": out}


class Switch(BaseModel):
    name: str


@app.post("/api/switch")
def switch(s: Switch):
    if not (config.ROOT / "companies" / f"{s.name}.yaml").exists() and s.name != "gitlab":
        raise HTTPException(404, "unknown company")
    os.environ["HB_COMPANY"] = s.name

    def restart():
        time.sleep(0.4)
        os.execv(sys.executable, [sys.executable, "-m", "hb", "--company", s.name, "serve",
                                  "--port", os.getenv("HB_PORT", "8000")])
    threading.Thread(target=restart, daemon=True).start()
    return {"ok": True, "restarting_as": s.name}


@app.get("/api/overview")
def overview():
    from .crosscheck import open_conflicts
    meta = get_meta()
    srcs = rows("""MATCH (x:Source) OPTIONAL MATCH (x)-[:PROVIDES]->(p:Page) WHERE p.deleted = false
                   RETURN x.name AS name, x.label AS label, x.kind AS kind, x.type AS type, x.authority AS authority,
                          x.last_synced AS last_synced, count(p) AS pages ORDER BY authority DESC""")
    c = rows("""OPTIONAL MATCH (p:Page) WHERE p.deleted = false WITH count(p) AS pages
                OPTIONAL MATCH (s:Section) WHERE s.valid_to IS NULL WITH pages, count(s) AS sections
                OPTIONAL MATCH (v:Section) WHERE v.valid_to IS NOT NULL WITH pages, sections, count(v) AS versions
                OPTIONAL MATCH (cv:Conversation) WITH pages, sections, versions, count(cv) AS conversations
                OPTIONAL MATCH (d:Decision) WITH pages, sections, versions, conversations, count(d) AS decisions
                OPTIONAL MATCH (a:Answer) WITH pages, sections, versions, conversations, decisions,
                     sum(CASE WHEN a.status='fresh' THEN 1 ELSE 0 END) AS fresh,
                     sum(CASE WHEN a.status='stale' THEN 1 ELSE 0 END) AS stale
                RETURN pages, sections, versions, conversations, decisions, fresh, stale""")[0]
    review = review_counts()
    return {"company": config.COMPANY, "name": config.COMPANY_NAME, "graph": config.GRAPH,
            "as_of": meta.get("head_ts"), "t0": meta.get("t0_ts"), "sources": srcs, "counts": c,
            "review": review, "open_source_conflicts": len(open_conflicts()), "sync": SYNC_STATUS()}


# ---------------------------------------------------------------- ask

class Ask(BaseModel):
    question: str


@app.post("/api/ask")
def ask_q(a: Ask):
    from .answer import ask
    if not a.question.strip():
        raise HTTPException(400, "empty question")
    r = ask(a.question.strip())
    cites = rows(
        """
        UNWIND $c AS cite
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section {cite:cite}) WHERE s.vid IN $vids
        OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
        RETURN cite, s.heading AS heading, p.title AS page, p.path AS path, s.url AS url, left(s.text, 600) AS text,
               s.valid_from_ts AS since, p.source AS source, p.source_kind AS kind, p.as_of AS uploaded_on,
               p.origin AS origin, p.owner_source AS owner_source, collect(DISTINCT o.handle) AS owners
        """,
        {"c": r["citations"], "vids": [v for v in _answer_vids(r["id"])]},
    )
    steps = [{"tool": t.get("tool"), "args": t.get("args"), "n": t.get("n_sources"), "ms": t.get("ms"),
              "error": t.get("error")} for t in r["trace"] if t.get("step") == "tool"]
    return {**{k: r[k] for k in ("id", "question", "answer", "answerable", "secs", "cost_usd")},
            "citations": cites, "steps": steps, "revisions": r["revisions"], "as_of": r.get("graph_head")}


def _answer_vids(aid: str) -> list[str]:
    return [x["v"] for x in rows("MATCH (:Answer {id:$id})-[:CITES]->(s:Section) RETURN s.vid AS v", {"id": aid})]


# ---------------------------------------------------------------- graph views

DEMO_Q = {
    "gitlab": ["Why did the Ireland pension employer contribution change, and who decided it?",
               "How many weeks of parental leave do employees in Australia get now?",
               "Who owns the paid time off page, and when did it last change?"],
    "larkspur": ["Who do I contact for pre-approval of an expense that's not listed as eligible?",
                 "Why did the OpsAlert response time for on-call change, and who decided it?",
                 "What's the maximum relocation reimbursement per move?"],
}


@app.get("/api/story/{aid}")
def story(aid: str):
    from .story import answer_story
    from .story import tag_domains
    r = answer_story(aid)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    r["graph"] = tag_domains(r["graph"])
    return r


@app.get("/api/answers")
def answers(limit: int = 30):
    return {"answers": rows(
        """
        MATCH (a:Answer)-[:ANSWERS]->(qn:Question) WHERE a.status IN ['fresh', 'stale']
        OPTIONAL MATCH (a)-[:CITES]->(s:Section)
        WITH a, qn, count(s) AS cites
        OPTIONAL MATCH (a)-[r:INVALIDATED_BY|REVALIDATED_BY]->()
        RETURN a.id AS id, qn.text AS question, a.status AS status, a.created AS created, a.answerable AS answerable,
               cites, count(r) AS checks
        ORDER BY checks DESC, created DESC LIMIT $n
        """, {"n": limit}),
        "suggested": config.CFG.get("demo_questions") or DEMO_Q.get(config.COMPANY, [])}


@app.get("/api/graph/explore")
def graph_explore(limit: int = 220):
    from .story import explore, tag_domains
    return tag_domains(explore(limit))


@app.get("/api/graph/page")
def graph_page(path: str):
    from .story import page_graph, tag_domains
    return tag_domains(page_graph(path))


@app.get("/api/graph/schema")
def graph_schema():
    from .story import schema
    return schema()


@app.get("/api/config")
def cfg():
    from .llm import CACHE, NET
    raw = (config.ROOT / "companies" / f"{config.COMPANY}.yaml")
    return {"company": config.COMPANY, "name": config.COMPANY_NAME, "yaml": raw.read_text() if raw.exists() else None,
            "sources": config.SOURCES or [config.DOCS], "t0": config.T0, "graph": config.GRAPH,
            "models": {"agent": config.MODEL, "critic": config.CRITIC_MODEL, "embeddings": config.EMBED_MODEL,
                       "conflict_judge": os.getenv("HB_CONFLICT_MODEL", config.MODEL)},
            "cost_controls": {"llm_cache": CACHE is not None, "retries": NET["num_retries"],
                              "tool_result_chars": int(os.getenv("HB_TOOL_CHARS", "9000")),
                              "answer_sample": os.getenv("HB_ANSWER_SAMPLE", "1")},
            "conversations_dir": str(config.CONV_DIR.relative_to(config.ROOT)) if config.CONV_DIR.exists() else None}


# ---------------------------------------------------------------- updates / review

@app.get("/api/updates")
def updates(limit: int = 40):
    res = rows(
        """
        MATCH (c:Commit)-[ch:CHANGED]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        WHERE c.sha <> $t0
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (d:Decision)-[:IMPLEMENTED_BY]->(c)
        OPTIONAL MATCH (d)-[:DECIDED_IN]->(cv:Conversation)
        RETURN c.sha AS sha, c.ts AS ts, c.title AS title, c.url AS url, m.iid AS mr, m.url AS mr_url,
               p.source AS source, p.source_kind AS kind, p.dept AS dept,
               collect(DISTINCT {page:p.title, path:p.path, heading:s.heading, kind:ch.kind, cite:s.cite})[..8] AS sections,
               collect(DISTINCT {statement:d.statement, where:cv.title, src:cv.source, at:d.decided_at})[..3] AS decisions
        ORDER BY ts DESC LIMIT $n
        """,
        {"t0": get_meta().get("t0"), "n": limit},
    )
    for r in res:
        r["decisions"] = [d for d in r["decisions"] if d.get("statement")]
    return {"changes": res}


def review_counts() -> dict:
    r = review()
    return {k: len(v) for k, v in r.items() if isinstance(v, list)}


@app.get("/api/review")
def review():
    from .conversations import unimplemented
    from .crosscheck import open_conflicts
    meta = get_meta()
    now = meta.get("head_epoch") or time.time()
    stale = rows(
        """
        MATCH (a:Answer {status:'stale'})-[:ANSWERS]->(qn:Question)
        OPTIONAL MATCH (a)-[inv:INVALIDATED_BY]->(c:Commit)
        OPTIONAL MATCH (n:Notification)-[:ABOUT]->(a)
        RETURN a.id AS id, qn.text AS question, left(a.text, 400) AS answer, a.stale_reason AS reason,
               a.stale_since AS since, collect(DISTINCT inv.kind)[0] AS kind, c.title AS change,
               collect(DISTINCT n.to) AS notified ORDER BY since DESC
        """
    )
    chat = rows(
        """
        MATCH (k:Claim)-[r:CONTRADICTS]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        MATCH (k)-[:STATED_IN]->(cv:Conversation)
        WHERE s.valid_to IS NULL
        OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
        RETURN k.statement AS statement, k.speaker AS speaker, cv.title AS where, cv.source AS source, k.at AS at,
               r.reason AS reason, s.cite AS cite, p.title AS page, collect(DISTINCT o.handle) AS owners
        ORDER BY at DESC
        """
    )
    gaps = rows(
        """
        MATCH (qn:Question {gap:true})
        OPTIONAL MATCH (qn)-[ai:ASKED_IN]->(cv:Conversation)
        RETURN qn.text AS question, count(cv) AS asked, coalesce(qn.tribal, false) AS tribal,
               collect(ai.chat_answer)[0] AS chat_answer, collect(cv.title)[0] AS where
        ORDER BY tribal DESC, asked DESC
        """
    )
    undocumented = unimplemented(now, 21)
    return {"stale_answers": stale, "decided_not_documented": undocumented, "chat_vs_docs": chat,
            "source_conflicts": open_conflicts(), "gaps": gaps}


class Draft(BaseModel):
    decision_id: str


class Decide(BaseModel):
    status: str
    by: str = ""


@app.get("/api/proposals")
def proposals():
    from .proposals import listing
    return {"proposals": listing()}


@app.post("/api/proposals/draft")
def proposal_draft(d: Draft):
    from .proposals import draft
    r = draft(d.decision_id)
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.post("/api/proposals/{pid}")
def proposal_decide(pid: str, d: Decide):
    from .proposals import decide
    r = decide(pid, d.status, d.by)
    if isinstance(r, dict) and r.get("error"):
        raise HTTPException(400, r["error"])
    return r


@app.get("/api/impact/{sha}")
def impact(sha: str):
    from .entities import impact_of_change
    return impact_of_change(sha)


@app.post("/api/refresh")
def refresh():
    from . import stale
    return {"refreshed": stale.refresh()}


# ---------------------------------------------------------------- onboarding: learn / glossary / ask-about-this

@app.get("/api/decisions/triage")
def decisions_triage(refresh: bool = False):
    from .triage import triage
    return {"triage": triage(refresh)}


@app.get("/api/learn/areas")
def learn_areas():
    from .learn import areas
    return {"areas": areas()}


@app.get("/api/learn/area")
def learn_area(a: str):
    from .learn import area
    return area(a)


@app.get("/api/learn/brief")
def learn_brief(a: str, refresh: bool = False):
    from .learn import brief
    return brief(a, refresh)


@app.get("/api/glossary")
def glossary_terms(area: str = ""):
    from .glossary import candidates, for_area
    return {"terms": for_area(area) if area else candidates()}


@app.get("/api/term")
def term(name: str, refresh: bool = False):
    from .glossary import define
    return define(name, refresh)


class Explain(BaseModel):
    node: str
    question: str = ""
    history: list[dict] = []


@app.post("/api/explain")
def explain_node(e: Explain):
    from .explain import explain
    return explain(e.node, e.question, e.history)


@app.get("/api/explain/suggest")
def explain_suggest(node: str):
    from .explain import context, suggestions
    c = context(node)
    return {"label": c["label"], "title": c["title"], "suggestions": suggestions(c["label"]), "found": bool(c["text"])}


# ---------------------------------------------------------------- brain / page / trust

@app.get("/api/brain")
def brain():
    pages = rows(
        """
        MATCH (p:Page) WHERE p.deleted = false
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL
        WITH p, count(s) AS sections, max(s.valid_from_ts) AS last_changed
        OPTIONAL MATCH (p)-[ow:OWNED_BY]->(o)
        RETURN p.path AS path, p.title AS title, p.dept AS dept, p.url AS url, p.source AS source,
               p.source_kind AS kind, p.as_of AS uploaded_on, p.origin AS origin, p.owner_source AS owner_source,
               sections, last_changed, collect(DISTINCT coalesce(o.name, o.handle)) AS owners
        ORDER BY source, path
        """
    )
    open_c = {r["p"]: r["n"] for r in rows(
        "MATCH (p:Page)-[:HAS_SECTION]->(:Section)-[c:CONFLICTS_WITH {status:'open'}]-(:Section) "
        "RETURN p.path AS p, count(c) AS n")}
    for p in pages:
        p["open_conflicts"] = open_c.get(p["path"], 0)
    decisions = rows(
        """
        MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(c:Commit)
        RETURN d.statement AS statement, d.decided_at AS at, d.status AS status, cv.title AS where, cv.source AS source,
               d.about_sid AS about, c.ts AS implemented_at, d.lag_days AS lag_days,
               coalesce(d.changes_policy, true) AS changes_policy
        ORDER BY at DESC
        """
    )
    people = rows(
        """
        MATCH (p:Page)-[:OWNED_BY]->(o) WHERE p.deleted = false
        RETURN coalesce(o.name, o.handle) AS who, o.handle AS handle, labels(o)[0] AS kind, count(p) AS pages
        ORDER BY pages DESC LIMIT 30
        """
    )
    return {"pages": pages, "decisions": decisions, "owners": people}


@app.get("/api/page")
def page(path: str):
    from .queries import page_details, section_history
    d = page_details(path)
    secs = rows(
        """
        MATCH (p:Page {path:$p})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL
        OPTIONAL MATCH (s)-[:SUPERSEDES*1..20]->(old:Section)
        OPTIONAL MATCH (s)-[c1:CONFLICTS_WITH {status:'open'}]->(w:Section)
        OPTIONAL MATCH (l:Section)-[c2:CONFLICTS_WITH {status:'open'}]->(s)
        RETURN s.cite AS cite, s.heading AS heading, s.ord AS ord, s.text AS text, s.valid_from_ts AS since,
               count(DISTINCT old) AS versions, collect(DISTINCT w.cite) AS outdated_by,
               collect(DISTINCT l.cite) AS older_copies
        ORDER BY ord
        """,
        {"p": path},
    )
    for s in secs:
        if s["versions"]:
            s["history"] = section_history(s["cite"]).get("versions", [])[:5]
    return {"details": d, "sections": secs}


@app.get("/api/trust")
def trust():
    f = config.RUNS / "bench.json"
    if not f.exists():
        return {"available": False}
    b = json.loads(f.read_text())
    b.pop("answers", None)
    b["staleness"].pop("detail", None)
    b["available"] = True
    b["ran_at"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(f.stat().st_mtime))
    return b


# ---------------------------------------------------------------- upload / sync

@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), folder: str = ""):
    ups = [s for s in config.SOURCES if s.get("type") == "uploads"]
    if not ups:
        raise HTTPException(400, "This company has no uploads source. Add one under `sources:` in its yaml.")
    day = time.strftime("%Y-%m-%d")
    dest = config.ROOT / ups[0]["path"] / day / folder.strip("/.")
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in files:
        name = Path(f.filename or "file").name
        if not name.lower().endswith(config.DOC_EXTS):
            raise HTTPException(400, f"{name}: unsupported type (use {', '.join(config.DOC_EXTS)})")
        (dest / name).write_bytes(await f.read())
        saved.append(f"{ups[0]['name']}/{day}/{name}")
    return {"saved": saved, "next": "Run a sync to add them to the brain."}


def SYNC_STATUS():
    return {k: SYNC[k] for k in ("running", "started", "finished", "error")} | {"log": SYNC["log"][-40:]}


@app.get("/api/sync")
def sync_status():
    return SYNC_STATUS()


@app.post("/api/sync")
def sync_start():
    if SYNC["running"]:
        return SYNC_STATUS()
    SYNC.update(running=True, log=[], started=time.strftime("%H:%M:%S"), finished=None, error=None)

    def run():
        import contextlib
        import io

        class Tee(io.TextIOBase):
            def write(self, s):
                for line in s.splitlines():
                    if line.strip():
                        SYNC["log"].append(line[:300])
                return len(s)
        try:
            from argparse import Namespace
            from .__main__ import cmd_sync
            with contextlib.redirect_stdout(Tee()):
                cmd_sync(Namespace(no_pull=False, no_refresh=False, no_conversations=False))
        except Exception as e:
            SYNC["error"] = f"{type(e).__name__}: {e}"
            SYNC["log"].append(traceback.format_exc()[-600:])
        finally:
            SYNC.update(running=False, finished=time.strftime("%H:%M:%S"))
    threading.Thread(target=run, daemon=True).start()
    return SYNC_STATUS()


# ---------------------------------------------------------------- web

if WEB.exists():
    app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


def serve(port: int = 8000):
    import uvicorn
    os.environ["HB_PORT"] = str(port)
    print(f"HandbookBrain · {config.COMPANY_NAME} · http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
