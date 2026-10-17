"""Onboarding: one start page per area for someone new to it, built from the graph.

  areas()            the company's areas (yaml scope folders, or document departments)
  area(a)            for one area: documents to read first, who to ask, how it evolved (decisions -> changes),
                     the systems / metrics / dashboards / fields it runs on, and its subgraph
  brief(a)           a one-page orientation written by a model from that data only (cached per graph head)

Everything comes from the same versioned graph the agent answers from, so the onboarding page is
never staler than the last sync: when a decision changes a rule, the area page shows the new rule and
the decision behind it the next morning.
"""
import json
import re

from . import config
from .config import COMPANY_NAME, MODEL
from .graph import get_meta, rows
from .llm import complete
from .parse import cite_key
from .story import G, _owner_nodes

AREA_FILTER = "(p.path STARTS WITH $a OR p.dept = $a)"


def _name(a: str) -> str:
    seg = [x for x in a.strip("/").split("/") if x][-1:] or [a]
    return re.sub(r"[-_]+", " ", seg[0]).strip().title().replace("Ops", "Ops")


def areas() -> list[dict]:
    if config.SCOPE:
        out = []
        for s in config.SCOPE:
            n = rows(f"MATCH (p:Page) WHERE p.deleted = false AND p.path STARTS WITH $a RETURN count(p) AS n", {"a": s})[0]["n"]
            if n:
                out.append({"id": s, "name": _name(s), "pages": n})
        if out:
            return out
    return [{"id": r["d"], "name": _name(r["d"]), "pages": r["n"]} for r in rows(
        "MATCH (p:Page) WHERE p.deleted = false AND p.dept IS NOT NULL AND p.dept <> '' "
        "RETURN p.dept AS d, count(p) AS n ORDER BY n DESC LIMIT 12")]


def area(a: str) -> dict:
    p = {"a": a}
    pages = rows(f"""
        MATCH (p:Page) WHERE p.deleted = false AND {AREA_FILTER}
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL
        OPTIONAL MATCH (s)-[:MENTIONS]->(e)
        WITH p, count(DISTINCT s) AS sections, count(DISTINCT e) AS ents, max(s.valid_from_ts) AS changed
        OPTIONAL MATCH (inn:Page)-[:LINKS_TO]->(p)
        WITH p, sections, ents, changed, count(DISTINCT inn) AS inbound
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(:Section)<-[ch:CHANGED]-(c:Commit) WHERE c.sha <> $t0
        WITH p, sections, ents, changed, inbound, count(DISTINCT c) AS changes
        RETURN p.path AS path, p.title AS title, p.url AS url, sections, ents, changed, inbound, changes,
               [(p)-[r:OWNED_BY]->(o) | {{h:o.handle, n:o.name, team:'Team' IN labels(o), src:r.source}}] AS owners
        """, {**p, "t0": get_meta().get("t0")})
    for x in pages:
        x["score"] = 2 * x["inbound"] + 3 * x["changes"] + min(x["ents"], 10) + min(x["sections"], 10) / 2
    pages.sort(key=lambda x: -x["score"])
    paths = [x["path"] for x in pages]

    decisions = rows("""
        MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (d)-[:ABOUT]->(s1:Section)<-[:HAS_SECTION]-(p1:Page)
        OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(c:Commit)-[:CHANGED]->(s2:Section)<-[:HAS_SECTION]-(p2:Page)
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
        WITH d, cv, by, c, m, collect(DISTINCT p1.path) + collect(DISTINCT p2.path) AS paths,
             collect(DISTINCT p2.title)[0] AS page, collect(DISTINCT s2.sid)[0] AS sid,
             collect(DISTINCT s1.sid)[0] AS about_sid
        WHERE any(x IN paths WHERE x IN $paths)
        RETURN d.id AS id, d.statement AS statement, d.quote AS quote, d.decided_at AS at, d.status AS status,
               cv.title AS where, cv.source AS src, by.name AS by, c.sha AS sha, c.ts AS implemented_at,
               m.iid AS mr, m.url AS mr_url, page, coalesce(sid, about_sid) AS sid, paths
        ORDER BY at DESC
        """, {"paths": paths}) if paths else []
    for d in decisions:
        d["cite"] = cite_key(d.pop("sid")) if d.get("sid") else None
        d["path"] = next((x for x in d.pop("paths") if x in paths), None)

    ents = rows(f"""
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section)-[:MENTIONS]->(e) WHERE s.valid_to IS NULL AND p.deleted = false
              AND {AREA_FILTER}
        RETURN labels(e)[0] AS type, coalesce(e.name, e.url) AS name, e.name AS label, count(DISTINCT p) AS docs,
               collect(DISTINCT p.path)[..6] AS paths
        ORDER BY docs DESC LIMIT 60
        """, p)

    people = {}
    for x in pages:
        for o in x["owners"] or []:
            if o and o.get("h"):
                k = o["h"]
                people.setdefault(k, {"name": o.get("n") or k, "handle": k, "team": bool(o.get("team")),
                                      "owns": [], "decided": []})["owns"].append(x["title"])
    for d in decisions:
        if d.get("by") and d["by"] != "unknown":
            people.setdefault("U:" + d["by"], {"name": d["by"], "handle": None, "team": False, "owns": [],
                                               "decided": []})["decided"].append(d["statement"])
    ppl = sorted(people.values(), key=lambda x: -(2 * len(x["decided"]) + len(x["owns"])))[:12]

    # subgraph: top documents, what they run on, who owns them, decisions that changed them
    g = G()
    top = pages[:14]
    keep = {x["path"] for x in top}
    for x in top:
        pid = "P:" + x["path"]
        g.node(pid, "Page", x["title"], path=x["path"])
        _owner_nodes(g, pid, x["owners"])
    for e in ents:
        if e["type"] == "Field" and e["docs"] < 2:
            continue
        hit = [pp for pp in e["paths"] if pp in keep]
        if len(hit) >= 1 and (e["docs"] >= 2 or e["type"] in ("System", "Metric", "Dashboard")):
            en = "E:" + e["type"] + ":" + e["name"]
            g.node(en, e["type"], e["label"] or e["name"], docs=e["docs"])
            for pp in hit:
                g.edge("P:" + pp, en, "MENTIONS")
    for d in decisions[:10]:
        dn = "D:" + d["id"]
        g.node(dn, "Decision", d["statement"], hl=True, at=d["at"], quote=d["quote"], status=d["status"])
        if d.get("by") and d["by"] != "unknown":
            g.node("U:" + d["by"], "Person", d["by"])
            g.edge(dn, "U:" + d["by"], "DECIDED_BY")
        if d.get("path") in keep:
            g.edge(dn, "P:" + d["path"], "IMPLEMENTED_BY" if d["status"] == "implemented" else "ABOUT", hl=True)
    return {"area": a, "name": _name(a), "pages": pages[:12], "page_count": len(pages), "decisions": decisions,
            "entities": ents, "people": ppl, "graph": g.out(), "as_of": get_meta().get("head_ts")}


BRIEF = """You are a senior colleague at {company} writing the first page a new hire in {area} reads.
Use ONLY the DATA below (documents, decisions, systems and metrics from the company graph). Plain words; when you
first use a piece of jargon, explain it in a few words. Write markdown with exactly these parts:

**What this area does** - 2-3 sentences.
**How the work flows** - 3-6 bullets in the order work happens, naming the systems involved.
**Recent decisions you should know** - up to 4 bullets, newest first: what changed, who decided, when.
**Your first week** - 3 bullets: which documents to read first and who to ask about what.

Mention documents by title and decisions by date. Never invent numbers, names or systems not in the DATA."""


def brief(a: str, refresh: bool = False) -> dict:
    d = area(a)
    head = get_meta().get("head") or ""
    f = config.RUNS / "learn" / (re.sub(r"[^a-z0-9]+", "-", a.lower()).strip("-") + ".json")
    if f.exists() and not refresh:
        c = json.loads(f.read_text())
        if c.get("head") == head:
            return c
    from .queries import best_window
    secs = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.path IN $paths
                   RETURN p.title AS page, s.heading AS heading, s.text AS text, s.ord AS ord ORDER BY p.path, ord""",
                {"paths": [x["path"] for x in d["pages"][:8]]})
    by_page = {}
    for s in secs:
        if len(by_page.setdefault(s["page"], [])) < 3:
            by_page[s["page"]].append(f"  {s['heading']}: {best_window(s['text'] or '', 'process', 500)}")
    data = {
        "documents (most central first)": [f"{x['title']} (owners: {', '.join(o.get('n') or o.get('h') for o in x['owners'] if o and o.get('h')) or 'none'}; "
                                           f"{x['changes']} changes recently)" for x in d["pages"][:10]],
        "excerpts": [f"{pg}\n" + "\n".join(v) for pg, v in by_page.items()],
        "decisions": [f"{(x['at'] or '')[:10]} {x['statement']} (decided by {x['by'] or 'unknown'} in {x['where']}; "
                      f"{'implemented in MR !' + str(x['mr']) if x.get('mr') else x['status']})" for x in d["decisions"][:8]],
        "systems/metrics/dashboards": [f"{e['type']}: {e['label'] or e['name']} (in {e['docs']} docs)"
                                       for e in d["entities"] if e["type"] != "Field"][:25],
        "people": [f"{p['name']}: owns {', '.join(p['owns'][:3]) or '-'}; decided {len(p['decided'])}" for p in d["people"]],
    }
    txt = json.dumps(data, indent=1)[:22000]
    r = complete(MODEL, [{"role": "system", "content": BRIEF.format(company=COMPANY_NAME, area=d["name"])},
                         {"role": "user", "content": "DATA:\n" + txt}])
    out = {"area": a, "name": d["name"], "text": r.choices[0].message.content or "", "head": head,
           "as_of": d["as_of"], "from": {"documents": len(d["pages"]), "decisions": len(d["decisions"])}}
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, indent=1))
    return out
