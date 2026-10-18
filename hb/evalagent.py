"""Agent evaluation: HandbookBrain (graph agent) vs a plain RAG baseline on the same corpus, same model.

    python -m hb --company gitlab-gtm evalagent                 # 29 questions x 3 trials x 2 systems (~25 min, ~$3)
    python -m hb --company gitlab-gtm evalagent --trials 1      # quick pass
    python -m hb --company gitlab-gtm evalagent --only why,version --no-baseline

Questions: eval/<company>/questions.jsonl, written from sources the agent never reads directly
(labels.json for meetings/MRs, `git show` diffs for versions, real ticket wording for lookups):
  why             why / who decided:   MR number, meeting date and person must all appear      (code-graded)
  version         current vs older rule, when it changed                                      (code-graded)
  not_documented  decided in a meeting, never written down: must say so                       (facts by code + model judge)
  lookup          an employee ticket answered by one handbook section                         (model judge vs that section's text;
                                                                                               "cites the right section" reported too)
  unanswerable    not in the documents: must abstain, never invent                            (code: abstains)
  tribal          only said in chat: must answer from the conversation and say so             (facts by code + model judge)

Baseline ("plain RAG"): the same hybrid vector + keyword retrieval over the same current sections, plus keyword search
over meeting/chat transcripts, given to the same model in one call. No graph traversal: no versions, no decision ->
change links, no merge requests, no owners. This is the "is the graph doing real work?" check.

Nothing is written to the graph (the agent loop runs without storing Answer nodes). The LLM response cache is turned
off, so the 3 trials are independent and pass^3 (all trials pass) measures consistency.
Output: runs/<company>/evalagent.md + evalagent.json (every answer kept, for reading failures).
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, llm, queries
from .answer import _cites, abstains, default_preload, run_agent
from .config import MODEL
from .graph import rows
from .llm import USAGE, complete
from .parse import cite_key

QFILE = lambda: config.ROOT / "eval" / config.COMPANY / "questions.jsonl"
CATS = ["why", "version", "not_documented", "lookup", "unanswerable", "tribal"]
CAT_NAME = {"why": "Why / who decided", "version": "Versions over time", "not_documented": "Decided, never documented",
            "lookup": "Employee questions (lookup)", "unanswerable": "Not in the docs (must abstain)", "tribal": "Only said in chat"}

# ---------------------------------------------------------------- systems

def graph_agent(question: str) -> dict:
    """The production agent (same tools, critic, citation check, why-preload) without storing an Answer node."""
    preload = default_preload(question)
    out = run_agent(question, preload=preload)
    cited = [c for c in dict.fromkeys(_cites(out["answer"])) if c in out["allowed"]]
    tools = [f"{t['tool']}({json.dumps(t.get('args'), default=str)[:120]})" for t in out["trace"] if t.get("step") == "tool"]
    return {"answer": out["answer"], "cited": cited, "tools": tools, "revisions": len(out["revisions"])}


BASE_SYSTEM = f"""You answer questions about how {config.COMPANY_NAME} works using ONLY the CONTEXT below: handbook sections
(each with a cite key) and excerpts from meetings, chat and tickets. Answer exactly what was asked, concisely.
Cite handbook claims with their cite key in square brackets, e.g. [marketing/marketo#scoring]. If you use a meeting or
chat excerpt, say it comes from that conversation and name it. If the context does not directly answer the question,
begin with exactly "The handbook doesn't say." Never guess or use outside knowledge."""


def plain_rag(question: str) -> dict:
    hb = queries.search_handbook(question, k=8)
    cv = queries.search_conversations(question, k=6)
    ctx = [f"[{r['cite']}] {r['page']} > {r['heading']}\n{r['text']}" for r in hb["rows"]]   # text only: no owners, dates, links
    ctx += [f"(conversation: {r['conversation']}, {r['source']}, {r['at']}, {r['speaker']}) {r['text']}" for r in cv["rows"]]
    r = complete(MODEL, [{"role": "system", "content": BASE_SYSTEM},
                         {"role": "user", "content": "CONTEXT:\n\n" + "\n\n---\n".join(ctx) + f"\n\nQUESTION: {question}"}])
    ans = (r.choices[0].message.content or "").strip()
    allowed = {row["cite"] for row in hb["rows"]}
    return {"answer": ans, "cited": [c for c in dict.fromkeys(_cites(ans)) if c in allowed],
            "tools": ["search_handbook", "search_conversations"], "revisions": 0}


_GITLOG = []


def _gitlog() -> list:
    """The repository's merge log as plain text records (what `git log --stat` would give a RAG system):
    MR number, date, title, description and the files it touched. No links to meetings or decisions."""
    if not _GITLOG:
        for r in rows("""MATCH (m:MergeRequest)-[:MERGED_AS]->(c:Commit)
                         OPTIONAL MATCH (c)-[:CHANGED]->(:Section)<-[:HAS_SECTION]-(p:Page)
                         RETURN m.iid AS mr, m.merged_at AS at, m.title AS title, left(coalesce(m.description, ''), 400) AS body,
                                collect(DISTINCT p.path)[..5] AS files"""):
            _GITLOG.append({**r, "text": f"{r['title']} {r['body']} {' '.join(r['files'] or [])}".lower()})
    return _GITLOG


def plain_rag_git(question: str) -> dict:
    """Stronger baseline: plain RAG plus the merge log as extra text documents, retrieved by word overlap."""
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in queries.STOP and len(w) > 2}
    log = sorted(_gitlog(), key=lambda r: -len({w for w in words if w in r["text"]}))[:6]
    hb = queries.search_handbook(question, k=8)
    cv = queries.search_conversations(question, k=6)
    ctx = [f"[{r['cite']}] {r['page']} > {r['heading']}\n{r['text']}" for r in hb["rows"]]
    ctx += [f"(conversation: {r['conversation']}, {r['source']}, {r['at']}, {r['speaker']}) {r['text']}" for r in cv["rows"]]
    ctx += [f"(merge request !{r['mr']}, merged {str(r['at'])[:10]}: {r['title']}; files: {', '.join(r['files'] or [])}) {r['body']}" for r in log]
    r = complete(MODEL, [{"role": "system", "content": BASE_SYSTEM + " Merge requests in the context are the repository's change log."},
                         {"role": "user", "content": "CONTEXT:\n\n" + "\n\n---\n".join(ctx) + f"\n\nQUESTION: {question}"}])
    ans = (r.choices[0].message.content or "").strip()
    allowed = {row["cite"] for row in hb["rows"]}
    return {"answer": ans, "cited": [c for c in dict.fromkeys(_cites(ans)) if c in allowed],
            "tools": ["search_handbook", "search_conversations", "merge_log"], "revisions": 0}


SYSTEMS = {"handbookbrain": graph_agent, "plain_rag": plain_rag, "rag_plus_gitlog": plain_rag_git}

# ---------------------------------------------------------------- graders

JUDGE = """You grade an answer to a question about a company's internal documentation.
REFERENCE is the ground truth (for lookups: the text of the handbook section that answers it).
- "correct": answers the question and agrees with the REFERENCE on every key fact; extra correct detail is fine.
- "partial": answers part of it, or is vague on a key fact, but contradicts nothing.
- "wrong": contradicts the REFERENCE, misses the main point, invents facts, or wrongly says the information is not available.
Expectations in the REFERENCE (e.g. "must say it comes from chat", "must say it is not yet in the handbook") are key facts.
Details in the ANSWER that the REFERENCE does not mention are NOT errors (they may come from other cited sources);
only contradictions of the REFERENCE, or missing key facts, lower the grade.
If you cannot tell, use "unknown". Return JSON: {"grade": "correct"|"partial"|"wrong"|"unknown", "why": "<= 20 words"}"""


def judge(question: str, reference: str, answer: str) -> dict:
    r = complete(MODEL, [{"role": "system", "content": JUDGE},
                         {"role": "user", "content": f"QUESTION: {question}\n\nREFERENCE:\n{reference}\n\nANSWER:\n{answer}"}],
                 response_format={"type": "json_object"})
    try:
        d = json.loads(r.choices[0].message.content)
        return {"grade": d.get("grade", "unknown"), "why": d.get("why", "")}
    except Exception:
        return {"grade": "unknown", "why": "unparseable judge output"}


def _norm_cite(c: str) -> str:
    return re.sub(r"/_?index(?=#|$)", "", c.strip().lower())


def facts_ok(must: list, text: str) -> tuple[bool, list]:
    t = text.lower().replace("’", "'")
    missing = [g for g in must if not any(alt.lower() in t for alt in g)]
    return not missing, [g[0] for g in missing]


def section_text(sid: str) -> str:
    r = rows("MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.sid = $sid AND s.valid_to IS NULL "
             "RETURN p.title AS page, s.heading AS heading, s.text AS text LIMIT 1", {"sid": sid})
    if not r:   # sid formats differ by source; fall back to path tail + anchor
        tail = "/".join(sid.split("/")[-2:])
        r = rows("MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.sid ENDS WITH $t AND s.valid_to IS NULL "
                 "RETURN p.title AS page, s.heading AS heading, s.text AS text LIMIT 1", {"t": tail})
    return f"{r[0]['page']} > {r[0]['heading']}\n{(r[0]['text'] or '')[:6000]}" if r else ""


def grade(item: dict, res: dict, ref_text: str) -> dict:
    a, cat = res["answer"], item["cat"]
    absn = abstains(a)
    ok_facts, missing = facts_ok(item.get("must", []), a)
    g = {"abstained": absn, "facts_ok": ok_facts, "missing": missing, "grounded": bool(res["cited"])}
    if cat in ("why", "version"):
        g["pass"] = ok_facts and not absn
    elif cat == "unanswerable":
        g["pass"] = absn
    elif cat == "lookup":
        j = judge(item["q"], ref_text or item.get("gold", ""), a)
        g["judge"] = j
        want = _norm_cite(cite_key(item["gold_sid"]))
        g["right_section"] = any(_norm_cite(c) == want for c in res["cited"])
        g["pass"] = j["grade"] == "correct" and not absn
        g["partial"] = j["grade"] == "partial"
    else:   # not_documented, tribal: code facts AND judge
        j = judge(item["q"], item.get("gold", ""), a)
        g["judge"] = j
        g["pass"] = ok_facts and j["grade"] == "correct"
        g["partial"] = j["grade"] == "partial"
    return g

# ---------------------------------------------------------------- run


def _one(system: str, item: dict, trial: int, ref: str) -> dict:
    t = time.time()
    try:
        res = SYSTEMS[system](item["q"])
    except Exception as e:
        res = {"answer": f"ERROR {type(e).__name__}: {e}", "cited": [], "tools": [], "revisions": 0}
    secs = round(time.time() - t, 1)
    return {"id": item["id"], "cat": item["cat"], "system": system, "trial": trial, "secs": secs, **res,
            "grade": grade(item, res, ref)}


def run(trials: int = 3, only: str = "", baseline: bool = True, workers: int = 4, cache: bool = False, systems: str = ""):
    if not cache:
        llm.CACHE = None              # independent trials; the cost reported is the real cost
    items = [json.loads(x) for x in QFILE().read_text().splitlines() if x.strip()]
    if only:
        keep = set(only.split(","))
        items = [x for x in items if x["cat"] in keep or x["id"] in keep]
    refs = {x["id"]: section_text(x["gold_sid"]) for x in items if x.get("gold_sid")}
    missing_ref = [k for k, v in refs.items() if not v]
    if missing_ref:
        print("WARNING: gold section not found in graph for", missing_ref)
    systems = systems.split(",") if systems else ["handbookbrain"] + (["plain_rag", "rag_plus_gitlog"] if baseline else [])
    results, cost = [], {}
    for s in systems:
        c0, t0 = USAGE["cost_usd"], time.time()
        jobs = [(s, it, k) for k in range(trials) for it in items]
        print(f"{s}: {len(jobs)} runs ({len(items)} questions x {trials} trials), {workers} in parallel")
        with ThreadPoolExecutor(workers) as ex:
            for i, r in enumerate(ex.map(lambda j: _one(j[0], j[1], j[2], refs.get(j[1]["id"], "")), jobs), 1):
                results.append(r)
                mark = "PASS" if r["grade"]["pass"] else "fail"
                print(f"  [{i:>3}/{len(jobs)}] {mark} {r['id']:<10} t{r['trial']} {r['secs']:>5}s  {r['answer'][:90]!r}")
        cost[s] = {"usd": round(USAGE["cost_usd"] - c0, 3), "minutes": round((time.time() - t0) / 60, 1)}
    report(items, results, systems, trials, cost)
    return results


def _summ(items, results, system, trials):
    out = {}
    for cat in CATS + ["all"]:
        ids = [x["id"] for x in items if cat == "all" or x["cat"] == cat]
        if not ids:
            continue
        rs = [r for r in results if r["system"] == system and r["id"] in ids]
        per_q = {i: [r["grade"]["pass"] for r in rs if r["id"] == i] for i in ids}
        n = len(rs)
        out[cat] = {
            "questions": len(ids),
            "pass_rate": round(sum(r["grade"]["pass"] for r in rs) / n, 3) if n else 0,
            "partial_rate": round(sum(bool(r["grade"].get("partial")) for r in rs) / n, 3) if n else 0,
            "pass_all_trials": round(sum(all(v) and len(v) == trials for v in per_q.values()) / len(ids), 3),
            "right_section": (round(sum(r["grade"].get("right_section", False) for r in rs) / n, 3)
                              if cat == "lookup" and n else None),
            "abstained_wrongly": sum(r["grade"]["abstained"] for r in rs if r["cat"] != "unanswerable" and r["cat"] != "tribal"),
            "mean_secs": round(sum(r["secs"] for r in rs) / n, 1) if n else 0,
        }
    return out


def report(items, results, systems, trials, cost):
    summ = {s: _summ(items, results, s, trials) for s in systems}
    pct = lambda x: f"{100 * x:.0f}%"
    L = [f"# Agent evaluation · {config.COMPANY_NAME}", "",
         f"{len(items)} questions × {trials} trial(s) × {len(systems)} system(s), model `{MODEL}` (judge `{MODEL}`), "
         f"LLM cache off. Run {time.strftime('%Y-%m-%d %H:%M')}. Questions: `eval/{config.COMPANY}/questions.jsonl`.", "",
         "Pass rate = share of all trials that pass; pass^k = share of questions that pass in every trial (consistency).", ""]
    hdr = "| Category | Q | " + " | ".join(f"{s} pass | {s} pass^{trials}" for s in systems) + " |"
    L += [hdr, "|" + "---|" * (2 + 2 * len(systems))]
    for cat in CATS + ["all"]:
        if cat not in summ[systems[0]]:
            continue
        row = f"| {'**All**' if cat == 'all' else CAT_NAME[cat]} | {summ[systems[0]][cat]['questions']} | "
        row += " | ".join(f"{pct(summ[s][cat]['pass_rate'])} | {pct(summ[s][cat]['pass_all_trials'])}" for s in systems)
        L.append(row + " |")
    L += [""]
    for s in systems:
        a = summ[s]["all"]
        lk = summ[s].get("lookup", {})
        L.append(f"- **{s}**: {cost[s]['usd']:.2f} USD, {cost[s]['minutes']} min wall clock, {a['mean_secs']} s per answer"
                 + (f"; lookups citing the exact gold section {pct(lk['right_section'])}, judged partial {pct(lk['partial_rate'])}"
                    if lk.get("right_section") is not None else "")
                 + f"; wrongly said 'not documented' {a['abstained_wrongly']}×")
    # which facts each system got, per fact (so a category score is not read as more than it is)
    qd = {x["id"]: x for x in items}
    L += ["", "Facts found, all trials (why: MR / meeting date / person or meeting; version: per required fact):", ""]
    for s in systems:
        tot = {}
        for r in results:
            if r["system"] != s or r["cat"] not in ("why", "version"):
                continue
            t = r["answer"].lower()
            for gi, g in enumerate(qd[r["id"]].get("must", [])):
                k = (r["cat"] + ":" + (["MR", "date", "who/where"][gi] if r["cat"] == "why" else f"fact{gi + 1}"))
                tot.setdefault(k, [0, 0])
                tot[k][1] += 1
                tot[k][0] += any(a.lower() in t for a in g)
        L.append(f"- {s}: " + ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in sorted(tot.items())))
    L += ["", "## Failures (first trial), to read", ""]
    for s in systems:
        L.append(f"### {s}")
        for r in [r for r in results if r["system"] == s and r["trial"] == 0 and not r["grade"]["pass"]]:
            g = r["grade"]
            why = (f"missing {g['missing']}" if g["missing"] else "") + (f" judge: {g['judge']['grade']} ({g['judge']['why']})" if g.get("judge") else "") \
                + (" abstained" if g["abstained"] else "")
            L.append(f"- `{r['id']}` {why.strip()}\n  > {r['answer'][:400].replace(chr(10), ' ')}")
        L.append("")
    (config.RUNS / "evalagent.md").write_text("\n".join(L))
    (config.RUNS / "evalagent.json").write_text(json.dumps({"summary": summ, "cost": cost, "trials": trials, "model": MODEL,
                                                             "results": results}, indent=1, default=str))
    print("\n".join(L[:20 + len(CATS)]))
    print(f"\nsaved {config.RUNS / 'evalagent.md'} and evalagent.json")
