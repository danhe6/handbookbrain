"""Glossary: the jargon a new hire runs into, defined from the company's own documents.

  (Term {name, expansion, definition, general})-[:DEFINED_IN]->(Section version it was read from)

Candidates are found deterministically (no model calls): the systems and metrics already in the graph
(entities.py) plus acronyms that recur across documents. A definition is written once, on first use,
by a model that sees only the 3 sections that best explain the term; it says when the documents do not
define it (then it gives the general industry meaning and labels it as such). Cached in the graph, so
every later hover is a lookup. `python -m hb glossary` pre-writes all of them.
"""
import json
import re
import time
from collections import Counter

from .config import COMPANY_NAME, MODEL
from .graph import q, rows
from .llm import complete
from .parse import cite_key

ACR = re.compile(r"(?<![\w/#.-])([A-Z][A-Z0-9&]{1,5})s?(?![\w-])")
STOP = {"THE", "AND", "FOR", "TBD", "FAQ", "NOT", "ALL", "NEW", "USA", "PST", "UTC", "EST", "PT", "ET", "AM", "PM",
        "ID", "OR", "IF", "IS", "IT", "OK", "US", "UK", "EU", "CC", "TO", "DO", "IN", "ON", "AT", "BY", "NO", "AN",
        "AS", "BE", "WE", "MR", "MRS", "PR", "QA", "UI", "URL", "PDF", "CSV", "HTML", "HTTP", "HTTPS", "JSON", "YAML",
        "README", "TODO", "NOTE", "CEO", "CTO", "CFO", "VP", "HR", "IT", "ASAP", "FYI", "EOD", "ETA", "TL", "DR",
        "GB", "MB", "KB", "API", "SQL", "AI", "ML", "N/A", "NA", "PTO", "DM", "Q1", "Q2", "Q3", "Q4", "H1", "H2",
        "FY", "YES", "OKR", "OKRS", "GIF", "PNG", "SVG", "XL", "XS", "OOO", "SLA", "SLAS"}
_TERMS = {"at": 0, "list": []}


def candidates(min_docs: int = 3) -> list[dict]:
    """Terms worth linking: systems, metrics, recurring acronyms. Cached for 10 minutes per process."""
    if _TERMS["list"] and time.time() - _TERMS["at"] < 600:
        return _TERMS["list"]
    out = {}
    for r in rows("""MATCH (e)<-[:MENTIONS]-(s:Section) WHERE (e:System OR e:Metric) AND s.valid_to IS NULL
                     RETURN labels(e)[0] AS type, e.name AS name, count(DISTINCT s.path) AS docs"""):
        if r["name"]:
            out[r["name"]] = {"name": r["name"], "type": r["type"], "docs": r["docs"]}
    docs = Counter()
    for r in rows("MATCH (s:Section) WHERE s.valid_to IS NULL RETURN s.path AS p, s.text AS t"):
        for a in set(ACR.findall(r["t"] or "")):
            docs[a] += 1
    for a, n in docs.items():
        if n >= min_docs and a not in STOP and not a.isdigit() and a not in out and len(a) >= 2:
            out[a] = {"name": a, "type": "Acronym", "docs": n}
    for r in rows("MATCH (t:Term) RETURN t.name AS name, t.expansion AS x"):
        if r["name"] in out:
            out[r["name"]]["defined"] = True
            out[r["name"]]["expansion"] = r["x"]
    _TERMS.update(at=time.time(), list=sorted(out.values(), key=lambda x: -x["docs"])[:400])
    return _TERMS["list"]


def _sections_for(term: str, k: int = 3) -> list[dict]:
    """Sections that best explain a term: ones that spell it out ('MQL (Marketing Qualified Lead)',
    'MQL is ...', a heading named after it) first, then the ones that use it most."""
    low = term.lower()
    hits = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.deleted = false
                     AND (s.text CONTAINS $t OR toLower(s.heading) CONTAINS $l)
                   RETURN s.vid AS vid, s.sid AS sid, s.heading AS heading, s.text AS text, p.title AS page, p.path AS path
                   LIMIT 300""", {"t": term, "l": low})
    pat = re.compile(r"\b" + re.escape(term) + r"s?\b\s*(\(|is |are |stands for|means|refers to|=|:|-)", re.I)

    def score(h):
        t = h["text"] or ""
        return (3 * (low in (h["heading"] or "").lower()) + 2 * bool(pat.search(t))
                + min(t.count(term), 6) / 6 - len(t) / 40000)
    hits.sort(key=score, reverse=True)
    return hits[:k]


def _window(text: str, term: str, size: int = 900) -> str:
    i = max((text or "").find(term), 0)
    a = max(0, i - size // 3)
    return ("…" if a else "") + text[a:a + size] + ("…" if a + size < len(text) else "")


DEFINE = """You write the glossary a new hire at {company} reads. Define the TERM as it is used at {company}.
Use ONLY the EXCERPTS. Plain words, at most 2 short sentences, no marketing. If it is an acronym, give the expansion.
If the excerpts use the term but do not explain it, give the standard industry meaning and set "general": true.
Return JSON only: {{"expansion": "<spelled out, or empty>", "definition": "...", "general": true|false,
"best_excerpt": <1-based index of the excerpt that explains it best, or 0>}}"""


def define(term: str, refresh: bool = False) -> dict:
    term = term.strip()
    if not term:
        return {"error": "empty term"}
    have = rows("""MATCH (t:Term {name:$n}) OPTIONAL MATCH (t)-[:DEFINED_IN]->(s:Section)<-[:HAS_SECTION]-(p:Page)
                   RETURN t.name AS name, t.expansion AS expansion, t.definition AS definition, t.general AS general,
                          s.sid AS sid, p.title AS page, p.path AS path LIMIT 1""", {"n": term})
    if have and not refresh:
        return _out(have[0], term)
    secs = _sections_for(term)
    ex = "\n\n".join(f"[{i + 1}] {s['page']} › {s['heading']}\n{_window(s['text'] or '', term)}" for i, s in enumerate(secs))
    r = complete(MODEL, [{"role": "system", "content": DEFINE.format(company=COMPANY_NAME)},
                         {"role": "user", "content": f"TERM: {term}\n\nEXCERPTS:\n{ex or '(none)'}"}],
                 response_format={"type": "json_object"})
    try:
        j = json.loads(r.choices[0].message.content)
    except Exception:
        j = {"definition": (r.choices[0].message.content or "")[:400], "general": True}
    best = secs[int(j.get("best_excerpt") or 1) - 1] if secs and 0 < int(j.get("best_excerpt") or 1) <= len(secs) else None
    q("""MERGE (t:Term {name:$n}) SET t.expansion=$x, t.definition=$d, t.general=$g, t.at=timestamp()
         WITH t OPTIONAL MATCH (t)-[old:DEFINED_IN]->() DELETE old""",
      {"n": term, "x": j.get("expansion") or "", "d": j.get("definition") or "", "g": bool(j.get("general"))})
    if best:
        q("MATCH (t:Term {name:$n}), (s:Section {vid:$v}) MERGE (t)-[:DEFINED_IN]->(s)", {"n": term, "v": best["vid"]})
    _TERMS["at"] = 0
    return _out({"name": term, "expansion": j.get("expansion"), "definition": j.get("definition"),
                 "general": bool(j.get("general")), "sid": best and best["sid"], "page": best and best["page"],
                 "path": best and best["path"]}, term)


def _out(t: dict, term: str) -> dict:
    used = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.deleted = false
                     AND s.text CONTAINS $t
                   RETURN p.title AS page, p.path AS path, count(s) AS n ORDER BY n DESC LIMIT 6""", {"t": term})
    total = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.deleted = false
                      AND s.text CONTAINS $t RETURN count(DISTINCT p) AS n""", {"t": term})
    return {"name": t["name"], "expansion": t.get("expansion") or "", "definition": t.get("definition") or "",
            "general": bool(t.get("general")), "cite": cite_key(t["sid"]) if t.get("sid") else None,
            "page": t.get("page"), "path": t.get("path"), "used_in": used, "docs": total[0]["n"] if total else 0}


def for_area(area: str) -> list[dict]:
    """Terms that show up in one area's documents, with definitions where already written."""
    texts = rows("""MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.deleted = false
                      AND (p.path STARTS WITH $a OR p.dept = $a) RETURN s.text AS t""", {"a": area})
    blob = "\n".join(r["t"] or "" for r in texts)
    defs = {r["name"]: r for r in rows("MATCH (t:Term) RETURN t.name AS name, t.expansion AS expansion, "
                                       "t.definition AS definition, t.general AS general")}
    out = []
    for c in candidates():
        n = blob.count(c["name"]) if c["type"] != "Metric" else blob.lower().count(c["name"].lower())
        if n >= 2:
            d = defs.get(c["name"]) or {}
            out.append({**c, "uses": n, "expansion": d.get("expansion") or "", "definition": d.get("definition") or "",
                        "general": d.get("general")})
    return sorted(out, key=lambda x: -x["uses"])[:40]


def build(limit: int = 60, area: str = "") -> int:
    todo = for_area(area) if area else candidates()
    n = 0
    for c in todo[:limit]:
        if not c.get("definition") and not c.get("defined"):
            define(c["name"])
            n += 1
            print(f"  {c['name']}")
    return n
