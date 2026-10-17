"""Decision triage: what KIND of change each decision is, and how much it IMPACTS, read from the graph.

Impact is not guessed by a model. For each decision we walk the graph from the sections it is about (or the
change that implemented it):
    decision -> section -> metrics / fields / dashboards / systems it relies on
             -> every other current document relying on the same metrics / fields / dashboards
             -> answers people were given from that section
             -> owners and domains involved
and score what we find. The reasons shown to people are the graph facts behind the score.

Kind (business rule / process / system & data) is classified by a model that sees the decision AND those graph
neighbours (a decision that changes a Salesforce field is a system change even if it is worded as a rule).
Stored on the Decision node, so it is classified once.
"""
import json
import time

from .config import MODEL
from .entities import extract
from .graph import q, rows
from .llm import complete

KINDS = ["Business rule", "Process", "System & data"]
_CACHE = {"at": 0, "data": None}


def _decisions() -> list[dict]:
    return rows("""
        MATCH (d:Decision)
        OPTIONAL MATCH (d)-[:ABOUT]->(s1:Section)
        OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(:Commit)-[:CHANGED]->(s2:Section)
        RETURN d.id AS id, d.statement AS statement, d.about_sid AS about, d.kind AS kind, d.kind_why AS kind_why,
               collect(DISTINCT s1.sid) + collect(DISTINCT s2.sid) AS sids""")


def _impact(d: dict) -> dict:
    from .queries import sid_for
    sids = [s for s in d["sids"] if s]
    if not sids and d.get("about"):
        sids = [sid_for(d["about"])]
    secs = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.sid IN $sids AND s.valid_to IS NULL
                   OPTIONAL MATCH (s)-[:MENTIONS]->(e)
                   OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
                   RETURN p.path AS path, p.title AS page, s.heading AS heading,
                          collect(DISTINCT {t:labels(e)[0], n:coalesce(e.name, e.url)}) AS ents,
                          collect(DISTINCT coalesce(o.name, o.handle)) AS owners""", {"sids": sids}) if sids else []
    ents = {(x["t"], x["n"]) for s in secs for x in s["ents"] if x.get("n")}
    said = extract(d["statement"] or "")   # what the decision itself names, even before it touches a document
    ents |= {("System", x) for x in said["systems"]} | {("Metric", x) for x in said["metrics"]} | {("Field", x) for x in said["fields"]}
    own_paths = sorted({s["path"] for s in secs})
    specific = [n for t, n in ents if t in ("Metric", "Field", "Dashboard")]
    reach = rows("""MATCH (e)<-[:MENTIONS]-(s:Section)<-[:HAS_SECTION]-(p:Page)
                    WHERE coalesce(e.name, e.url) IN $names AND s.valid_to IS NULL AND p.deleted = false AND NOT p.path IN $own
                    RETURN coalesce(e.name, e.url) AS name, count(DISTINCT p) AS docs""",
                 {"names": specific, "own": own_paths}) if specific else []
    reach = {r["name"]: r["docs"] for r in reach}
    answers = rows("""MATCH (a:Answer)-[:CITES]->(s:Section) WHERE s.sid IN $sids AND a.status IN ['fresh', 'stale']
                      RETURN count(DISTINCT a) AS n""", {"sids": sids})[0]["n"] if sids else 0
    owners = sorted({o for s in secs for o in s["owners"] if o})
    from .story import _domains_of_path, domains
    doms = domains()
    dset = {x for p in own_paths for x in _domains_of_path(p, doms)}
    other_docs = sum(reach.values())
    systems = sorted(n for t, n in ents if t == "System")
    fields = sorted(n for t, n in ents if t in ("Field", "Dashboard"))
    metrics = sorted(n for t, n in ents if t == "Metric")
    score = (3 * answers + min(other_docs, 30) * 0.4 + 2 * len(fields) + 1.5 * len(metrics) + min(len(systems), 3)
             + 2 * max(len(dset) - 1, 0) + 0.5 * len(owners) + (1 if secs else 0))
    why = []
    for name in sorted(reach, key=lambda k: -reach[k])[:2]:
        why.append(f"{name} is used in {reach[name]} other document{'s' if reach[name] != 1 else ''}")
    if answers:
        why.append(f"{answers} answer{'s' if answers != 1 else ''} relied on this section")
    if fields:
        why.append("touches " + ", ".join(fields[:2]))
    if systems and len(why) < 3:
        why.append("runs in " + ", ".join(systems[:3]))
    if len(dset) > 1:
        why.append(f"crosses {len(dset)} domains")
    if not secs:
        why.append("not linked to a document yet")
    return {"score": round(score, 1), "why": why[:3], "systems": systems, "metrics": metrics, "fields": fields,
            "other_docs": other_docs, "answers": answers, "owners": owners, "docs": [s["page"] for s in secs][:3],
            "headings": [s["heading"] for s in secs][:3]}


KIND_PROMPT = """Classify each company decision by what it changes. Kinds:
- "Business rule": a rule the business runs on (thresholds, scoring, eligibility, pricing, discounts, qualification, deal policy).
- "Process": who does what, when, how often; ownership, cadence, meetings, approvals, communication, event logistics.
- "System & data": a tool, integration, CRM/marketing-automation field, data pipeline, report or dashboard changes.
Use the GRAPH CONTEXT (systems, fields, metrics the decision touches) as evidence, not only the wording.
Return JSON only: {"items": [{"id": "...", "kind": "<one of the kinds>", "why": "<= 12 words"}]}"""


def _classify(todo: list[dict]):
    for i in range(0, len(todo), 25):
        chunk = todo[i:i + 25]
        payload = [{"id": d["id"], "decision": d["statement"],
                    "graph_context": {k: d["impact"][k] for k in ("systems", "metrics", "fields", "headings")}} for d in chunk]
        r = complete(MODEL, [{"role": "system", "content": KIND_PROMPT},
                             {"role": "user", "content": json.dumps(payload)}], response_format={"type": "json_object"})
        try:
            items = json.loads(r.choices[0].message.content).get("items", [])
        except Exception:
            items = []
        for it in items:
            k = it.get("kind") if it.get("kind") in KINDS else "Process"
            q("MATCH (d:Decision {id:$id}) SET d.kind=$k, d.kind_why=$w", {"id": it.get("id"), "k": k, "w": it.get("why", "")})
            for d in chunk:
                if d["id"] == it.get("id"):
                    d["kind"], d["kind_why"] = k, it.get("why", "")


def triage(refresh: bool = False) -> dict:
    """{decision id: {kind, kind_why, impact: high|medium|low, score, why[], ...}} (cached for 5 minutes)."""
    if _CACHE["data"] is not None and not refresh and time.time() - _CACHE["at"] < 300:
        return _CACHE["data"]
    ds = _decisions()
    for d in ds:
        d["impact"] = _impact(d)
    todo = [d for d in ds if not d.get("kind") or refresh]
    if todo:
        _classify(todo)
    scores = sorted(d["impact"]["score"] for d in ds)
    hi = scores[int(len(scores) * 0.7)] if scores else 0
    lo = scores[int(len(scores) * 0.35)] if scores else 0
    out = {}
    for d in ds:
        sc = d["impact"]["score"]
        level = "high" if sc >= max(hi, 8) else "medium" if sc >= max(lo, 3) else "low"
        out[d["id"]] = {"kind": d.get("kind") or "Process", "kind_why": d.get("kind_why") or "", "impact": level, **d["impact"]}
    _CACHE.update(at=time.time(), data=out)
    return out
