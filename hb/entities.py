"""Business + technical entities mentioned in documents: systems, dashboards, fields, metrics.

  (Section)-[:MENTIONS]->(System {name})       Salesforce, Marketo, Zuora, Snowflake, Tableau ...
  (Section)-[:MENTIONS]->(Dashboard {url})     links to BI tools (Tableau, Looker, Sisense, Power BI, ...)
  (Section)-[:MENTIONS]->(Field {name})        CRM / warehouse fields (`Something__c`, `snake_case_field`)
  (Section)-[:MENTIONS]->(Metric {name})       business metrics (MQL, SAO, ARR, win rate, lead score ...)

Extracted deterministically (no model calls) from every section VERSION when it is written, so the
graph also knows when a doc started or stopped referring to a field or dashboard. This is what lets a
decision reach past documents: decision -> change -> section -> field/dashboard -> every other doc
that relies on the same field/dashboard -> their owners.

The dictionaries are generic business vocabulary, not company-specific; a company can add its own in
its yaml (`systems:`, `metrics:`).
"""
import re

from . import config
from .graph import q, rows

SYSTEMS = {  # canonical -> aliases (word-boundary, case-insensitive unless uppercase acronym)
    "Salesforce": ["Salesforce", "SFDC"], "Marketo": ["Marketo"], "Gainsight": ["Gainsight"], "Zuora": ["Zuora"],
    "Outreach": ["Outreach.io", "Outreach"], "6sense": ["6sense"], "ZoomInfo": ["ZoomInfo"], "Clari": ["Clari"],
    "Tableau": ["Tableau"], "Looker": ["Looker"], "Sisense": ["Sisense", "Periscope"], "Power BI": ["Power BI"],
    "Snowflake": ["Snowflake"], "dbt": ["dbt"], "Workday": ["Workday"], "NetSuite": ["NetSuite"], "Coupa": ["Coupa"],
    "HubSpot": ["HubSpot"], "Greenhouse": ["Greenhouse"], "Navan": ["Navan"], "Workato": ["Workato"],
    "Zapier": ["Zapier"], "Cognism": ["Cognism"], "Goldcast": ["Goldcast"], "DocuSign": ["DocuSign"],
    "Google Analytics": ["GA4", "Google Analytics"], "LeanData": ["LeanData"], "Bizible": ["Bizible", "Marketo Measure"],
    "Avalara": ["Avalara"], "Stripe": ["Stripe"],
}
METRICS = {
    "ARR": [r"\bARR\b", r"annual recurring revenue"], "NRR": [r"\bNRR\b", r"net retention", r"net revenue retention"],
    "GRR": [r"\bGRR\b", r"gross retention"], "ACV": [r"\bACV\b"], "TCV": [r"\bTCV\b"], "CAC": [r"\bCAC\b"],
    "MQL": [r"\bMQLs?\b", r"marketing qualified lead"], "SQL": [r"\bSQLs?\b(?! ?(query|server|style))"],
    "SAO": [r"\bSAOs?\b", r"sales accepted opportunit"], "PQL": [r"\bPQLs?\b", r"product qualified lead"],
    "Pipeline": [r"\bpipeline (coverage|generation|created)\b"], "Win rate": [r"\bwin rate\b"],
    "Lead score": [r"\blead scor(e|ing)\b", r"\bbehavior(al)? scor(e|ing)\b", r"\btrial (threshold|scor(e|ing))\b"],
    "Conversion rate": [r"\bconversion rate\b"], "Churn": [r"\bchurn\b"], "Bookings": [r"\bbookings\b"],
    "Quota attainment": [r"\bquota attainment\b"], "Health score": [r"\bhealth scor(e|ing)\b"],
    "NPS": [r"\bNPS\b"], "CSAT": [r"\bCSAT\b"], "Renewal rate": [r"\brenewal rate\b"],
    "Deal registration": [r"\bdeal reg(istration)?s?\b"], "Discount": [r"\bdiscount (approval|threshold|level)s?\b"],
}
BI = re.compile(r"(tableau|looker|lookerstudio|datastudio|sisense|periscope|powerbi|app\.mode\.com|metabase)", re.I)
LINK = re.compile(r"\[([^\]]{2,120})\]\((https?://[^)\s]+)\)")
BARE = re.compile(r"(?<!\()https?://[^\s)>\]]+")
FIELD = re.compile(r"`([A-Za-z][A-Za-z0-9_]{2,60}__c)`|\b([A-Z][A-Za-z0-9_]{2,60}__c)\b|`([a-z][a-z0-9]*(?:_[a-z0-9]+){1,6})`")


ACRONYMS = {"ARR", "NRR", "GRR", "ACV", "TCV", "CAC", "MQL", "SAO", "PQL", "NPS", "CSAT"}


def _compile():
    """Systems are proper nouns -> case-sensitive (so 'outreach' the word is not Outreach the tool).
    Metric acronyms are case-sensitive, their spelled-out forms are not."""
    sysd = dict(SYSTEMS)
    for s in config.CFG.get("systems") or []:
        sysd.setdefault(s, [s])
    mets = dict(METRICS)
    mets.pop("SQL", None)  # too ambiguous next to the query language in data docs
    for m in config.CFG.get("metrics") or []:
        mets.setdefault(m, [r"\b" + re.escape(m) + r"\b"])
    sys_re = [(k, re.compile(r"(?<![\w/.-])(" + "|".join(re.escape(a) for a in v) + r")(?![\w-])")) for k, v in sysd.items()]
    met_re = [(k, [re.compile(p, 0 if k in ACRONYMS and p.startswith(r"\b" + k[:2]) else re.I) for p in v])
              for k, v in mets.items()]
    return sys_re, met_re


_SYS, _MET = None, None


def extract(text: str) -> dict:
    global _SYS, _MET
    if _SYS is None:
        _SYS, _MET = _compile()
    text = text or ""
    systems = sorted({k for k, r in _SYS if r.search(text)})
    metrics = sorted({k for k, rs in _MET if any(r.search(text) for r in rs)})
    dashboards = {}
    for name, url in LINK.findall(text):
        if BI.search(url):
            dashboards[url.rstrip(".,")] = name.strip()
    for url in BARE.findall(text):
        if BI.search(url) and url not in dashboards:
            dashboards[url.rstrip(".,")] = url.split("/")[-1][:60] or "dashboard"
    fields = sorted({a or b or c for a, b, c in FIELD.findall(text)} - {"snake_case"})
    if dashboards:
        systems = sorted(set(systems) | {BI.search(u).group(1).title().replace("Powerbi", "Power BI") for u in dashboards})
    return {"systems": systems, "metrics": metrics, "fields": fields[:20],
            "dashboards": [{"url": u, "name": n} for u, n in list(dashboards.items())[:20]]}


def ensure_schema():
    for s in ["CREATE INDEX FOR (x:System) ON (x.name)", "CREATE INDEX FOR (x:Metric) ON (x.name)",
              "CREATE INDEX FOR (x:Field) ON (x.name)", "CREATE INDEX FOR (x:Dashboard) ON (x.url)"]:
        try:
            q(s)
        except Exception as e:
            if "already" not in str(e).lower():
                raise


def link_page(path: str) -> int:
    """Extract entities for the CURRENT section versions of a page that were not processed yet."""
    todo = rows("MATCH (:Page {path:$p})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND s.ents IS NULL "
                "RETURN s.vid AS vid, s.text AS text", {"p": path})
    n = 0
    for s in todo:
        e = extract(s["text"])
        q(
            """
            MATCH (s:Section {vid:$vid}) SET s.ents = true
            FOREACH (x IN $systems | MERGE (y:System {name:x}) MERGE (s)-[:MENTIONS]->(y))
            FOREACH (x IN $metrics | MERGE (y:Metric {name:x}) MERGE (s)-[:MENTIONS]->(y))
            FOREACH (x IN $fields | MERGE (y:Field {name:x}) MERGE (s)-[:MENTIONS]->(y))
            FOREACH (d IN $dash | MERGE (y:Dashboard {url:d.url}) SET y.name = coalesce(y.name, d.name)
                                  MERGE (s)-[:MENTIONS]->(y))
            """,
            {"vid": s["vid"], "systems": e["systems"], "metrics": e["metrics"], "fields": e["fields"], "dash": e["dashboards"]},
        )
        n += sum(len(v) for v in e.values())
    return n


def impact_of_change(sha: str) -> dict:
    """Multi-hop impact of one change: entities the changed sections mention (or stopped mentioning),
    every OTHER current document that relies on the same entities, the answers that cited the old
    versions, and who owns the affected pages."""
    touched = rows(
        """
        MATCH (:Commit {sha:$sha})-[ch:CHANGED]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        OPTIONAL MATCH (s)-[:SUPERSEDES]->(old:Section)
        WITH s, p, old, ch
        OPTIONAL MATCH (s)-[:MENTIONS]->(e)
        WITH s, p, old, ch, collect(DISTINCT {label:labels(e)[0], name:coalesce(e.name, e.url)}) AS now
        OPTIONAL MATCH (old)-[:MENTIONS]->(e2)
        RETURN s.sid AS sid, s.heading AS heading, p.path AS path, p.title AS page, ch.kind AS kind, old.vid AS old,
               now, collect(DISTINCT {label:labels(e2)[0], name:coalesce(e2.name, e2.url)}) AS before
        """, {"sha": sha})
    ents = {}
    for t in touched:
        now = {(x["label"], x["name"]) for x in t["now"] if x.get("name")}
        before = {(x["label"], x["name"]) for x in t["before"] if x.get("name")}
        t["added"] = sorted(now - before)
        t["removed"] = sorted(before - now)
        for k in now | before:
            ents.setdefault(k, set()).add(t["path"])
    names = [k[1] for k in ents]
    also = rows(
        """
        MATCH (e)<-[:MENTIONS]-(s:Section)<-[:HAS_SECTION]-(p:Page)
        WHERE coalesce(e.name, e.url) IN $names AND s.valid_to IS NULL AND NOT p.path IN $paths
        OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
        RETURN labels(e)[0] AS label, coalesce(e.name, e.url) AS name, p.path AS path, p.title AS page,
               collect(DISTINCT s.heading)[..3] AS sections, collect(DISTINCT o.handle) AS owners
        """, {"names": names, "paths": sorted({t["path"] for t in touched})}) if names else []
    answers = rows(
        """
        MATCH (:Commit {sha:$sha})-[:CHANGED]->(s:Section)-[:SUPERSEDES]->(old:Section)<-[:CITES]-(a:Answer)-[:ANSWERS]->(qn:Question)
        RETURN DISTINCT a.id AS id, qn.text AS question, a.status AS status
        """, {"sha": sha})
    owners = rows(
        """
        MATCH (:Commit {sha:$sha})-[:CHANGED]->(:Section)<-[:HAS_SECTION]-(p:Page)-[:OWNED_BY]->(o)
        RETURN DISTINCT o.handle AS handle, coalesce(o.name, o.handle) AS name
        """, {"sha": sha})
    # entities that matter most: mentioned by the change AND relied on elsewhere
    counts = {}
    for a in also:
        counts[(a["label"], a["name"])] = counts.get((a["label"], a["name"]), 0) + 1
    return {"sections": touched, "entities": [{"label": l, "name": n, "other_docs": counts.get((l, n), 0)} for l, n in
                                              sorted(ents, key=lambda k: -counts.get(k, 0))],
            "other_docs": also, "answers": answers, "owners": owners}


def related(topic: str, k: int = 6) -> dict:
    """Agent tool body: systems, dashboards, fields and metrics connected to a topic, and the documents
    (with owners) that reference them."""
    from .queries import cite_key, search_handbook
    hits = search_handbook(topic, k=k)
    vids = list(hits["sources"].values())
    res = rows(
        """
        MATCH (s:Section)-[:MENTIONS]->(e) WHERE s.vid IN $vids
        OPTIONAL MATCH (e)<-[:MENTIONS]-(o:Section)<-[:HAS_SECTION]-(p:Page) WHERE o.valid_to IS NULL
        OPTIONAL MATCH (p)-[:OWNED_BY]->(w)
        RETURN labels(e)[0] AS type, coalesce(e.name, e.url) AS name, e.name AS label,
               count(DISTINCT p) AS docs_using_it, collect(DISTINCT p.title)[..5] AS docs,
               collect(DISTINCT w.handle)[..4] AS owners, collect(DISTINCT o.sid)[..3] AS sids
        ORDER BY docs_using_it DESC LIMIT 25
        """, {"vids": vids})
    src = dict(hits["sources"])
    sids = [x for r in res for x in r["sids"]]
    for r in res:
        r["cites"] = [cite_key(x) for x in r.pop("sids")]
    for r in rows("MATCH (s:Section) WHERE s.sid IN $sids AND s.valid_to IS NULL RETURN s.sid AS sid, s.vid AS vid",
                  {"sids": sids}) if sids else []:
        src[cite_key(r["sid"])] = r["vid"]
    return {"entities": res, "matched_sections": [r["cite"] for r in hits["rows"]], "sources": src}
