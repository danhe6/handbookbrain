"""Graph read-outs for the UI: the story behind one answer, the company graph, the data model.

answer_story(aid) walks out from an Answer over the graph and returns
  graph:    nodes + edges (Answer, Question, cited Section versions and their predecessors, Page, Source, owners,
            Commit / MergeRequest that changed them, Decision + Conversation + who decided, conflicting copies,
            contradicting chat claims) with the reasoning path flagged `hl`
  timeline: the same facts as dated events, oldest first: decided in a meeting -> implemented by a change ->
            section version valid from -> answer given -> answer invalidated / revalidated -> conflicts found
Everything is a single read of the graph; nothing here calls a model.
"""
import hashlib

from .graph import rows
from .parse import cite_key


def _h(s: str) -> str:
    return hashlib.sha1(str(s).encode()).hexdigest()[:10]


class G:
    def __init__(self):
        self.nodes, self.edges, self._e = {}, [], set()

    def node(self, nid, type_, label, hl=False, **data):
        n = self.nodes.get(nid)
        if n is None:
            n = self.nodes[nid] = {"id": nid, "type": type_, "label": label, "hl": hl, **data}
        else:
            n["hl"] = n["hl"] or hl
            n.update({k: v for k, v in data.items() if v is not None})
        return nid

    def edge(self, a, b, type_, hl=False, **data):
        if a not in self.nodes or b not in self.nodes:
            return
        k = (a, b, type_)
        if k in self._e:
            if hl:
                for e in self.edges:
                    if (e["source"], e["target"], e["type"]) == k:
                        e["hl"] = True
            return
        self._e.add(k)
        self.edges.append({"source": a, "target": b, "type": type_, "hl": hl, **data})

    def out(self):
        return {"nodes": list(self.nodes.values()), "edges": self.edges}


def _owner_nodes(g: G, pid: str, owners: list, hl=False):
    for o in owners or []:
        if not o or not o.get("h"):
            continue
        oid = "O:" + o["h"]
        g.node(oid, "Team" if o.get("team") else "Person", o.get("n") or o["h"], hl=hl, handle=o["h"])
        g.edge(pid, oid, "OWNED_BY", hl=hl, source_kind=o.get("src"))


def _decisions_for(sids: list[str]) -> list[str]:
    a = rows("MATCH (d:Decision)-[:IMPLEMENTED_BY]->(:Commit)-[:CHANGED]->(s:Section) WHERE s.sid IN $sids "
             "RETURN DISTINCT d.id AS id", {"sids": sids})
    b = rows("MATCH (d:Decision)-[:ABOUT]->(s:Section) WHERE s.sid IN $sids RETURN DISTINCT d.id AS id", {"sids": sids})
    return sorted({r["id"] for r in a + b})


def answer_story(aid: str) -> dict:
    head = rows(
        """
        MATCH (a:Answer {id:$id})-[:ANSWERS]->(qn:Question)
        RETURN a.id AS id, a.text AS text, a.status AS status, a.created AS created, a.asked_at_ts AS asked_at,
               a.stale_reason AS stale_reason, a.stale_since AS stale_since, qn.text AS question
        """, {"id": aid})
    if not head:
        return {"error": "unknown answer"}
    a = head[0]
    g, tl = G(), []
    g.node("A:" + aid, "Answer", "Answer", hl=True, status=a["status"], text=(a["text"] or "")[:600])
    g.node("Q:" + _h(a["question"]), "Question", a["question"], hl=True)
    g.edge("A:" + aid, "Q:" + _h(a["question"]), "ANSWERS", hl=True)

    cited = rows(
        """
        MATCH (:Answer {id:$id})-[:CITES]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        RETURN DISTINCT s.vid AS vid, s.sid AS sid, s.heading AS heading, s.valid_from_ts AS since, s.valid_to_ts AS until,
               left(s.text, 700) AS text, p.path AS path, p.title AS page, p.source AS source, p.source_kind AS kind,
               p.as_of AS uploaded_on, p.url AS url, p.owner_source AS owner_source,
               [(p)-[r:OWNED_BY]->(o) | {h:o.handle, n:o.name, team:'Team' IN labels(o), src:r.source}] AS owners
        """, {"id": aid})
    sids = sorted({c["sid"] for c in cited})
    for c in cited:
        sid_node = "S:" + c["vid"]
        pid = "P:" + c["path"]
        g.node(pid, "Page", c["page"], hl=True, path=c["path"], url=c["url"], source=c["source"], kind=c["kind"],
               owner_source=c["owner_source"])
        g.node(sid_node, "Section", c["heading"], hl=True, cite=cite_key(c["sid"]), since=c["since"], until=c["until"],
               text=c["text"], current=c["until"] is None)
        g.edge("A:" + aid, sid_node, "CITES", hl=True)
        g.edge(pid, sid_node, "HAS_SECTION", hl=True)
        if c["source"]:
            xid = "X:" + c["source"]
            g.node(xid, "Source", c["source"], kind=c["kind"])
            g.edge(xid, pid, "PROVIDES")
        _owner_nodes(g, pid, c["owners"], hl=True)

    # every version of the cited sections, with the change that created it
    vers = rows(
        """
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.sid IN $sids
        OPTIONAL MATCH (c:Commit)-[ch:CHANGED]->(s)
        OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:Section)
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (au:Person)-[:AUTHORED]->(c)
        OPTIONAL MATCH (mg:Person)-[:MERGED]->(c)
        RETURN s.vid AS vid, s.sid AS sid, s.heading AS heading, s.valid_from_ts AS since, s.valid_to_ts AS until,
               left(s.text, 700) AS text, prev.vid AS prev, prev.text AS prev_text, s.text AS full_text, p.path AS path,
               c.sha AS sha, c.ts AS ts, c.title AS title, c.url AS commit_url, ch.kind AS kind, m.iid AS mr, m.url AS mr_url,
               au.handle AS author, mg.name AS merged_by
        ORDER BY since
        """, {"sids": sids})
    from .queries import _diff
    t0 = rows("MATCH (m:Meta {id:'meta'}) RETURN m.t0 AS t0")
    t0 = t0[0]["t0"] if t0 else None
    for v in vers:
        sn = "S:" + v["vid"]
        g.node(sn, "Section", v["heading"], cite=cite_key(v["sid"]), since=v["since"], until=v["until"], text=v["text"],
               current=v["until"] is None)
        g.edge("P:" + v["path"], sn, "HAS_SECTION")
        if v["prev"]:
            g.node("S:" + v["prev"], "Section", v["heading"], cite=cite_key(v["sid"]), current=False)
            g.edge(sn, "S:" + v["prev"], "SUPERSEDES")
        if v["sha"] and v["sha"] != t0:
            cn = "C:" + v["sha"]
            g.node(cn, "Change", v["title"] or "change", ts=v["ts"], url=v["commit_url"], mr=v["mr"])
            g.edge(cn, sn, "CHANGED", kind=v["kind"])
            if v["mr"]:
                g.node("M:" + str(v["mr"]), "MergeRequest", f"MR !{v['mr']}", url=v["mr_url"])
                g.edge("M:" + str(v["mr"]), cn, "MERGED_AS")
            if v["merged_by"] and v["merged_by"] not in ("snapshot", "upload", "multi"):
                g.node("U:" + v["merged_by"], "Person", v["merged_by"])
                g.edge("U:" + v["merged_by"], cn, "MERGED")
            if v["author"]:
                g.node("U:" + v["author"], "Person", v["author"])
                g.edge("U:" + v["author"], cn, "AUTHORED")
            diff = _diff(v["prev_text"], v["full_text"], 1200) if v["prev_text"] else ""
            tl.append({"ts": v["ts"], "kind": "change", "sha": v["sha"], "title": v["title"] or "Document changed",
                       "detail": f"{v['heading']} - {v['kind'] or 'changed'}", "diff": diff, "who": v["merged_by"],
                       "author": v["author"], "mr": v["mr"], "url": v["mr_url"] or v["commit_url"],
                       "node": cn, "cite": cite_key(v["sid"])})

    # decisions behind those changes (or about those sections), with who / where / the quote
    # business + technical layer: systems, dashboards, fields, metrics the cited sections rely on, and how many
    # other documents rely on the same thing (that is the blast radius of a change here)
    for e in rows(
        """
        MATCH (s:Section)-[:MENTIONS]->(e) WHERE s.vid IN $vids
        OPTIONAL MATCH (e)<-[:MENTIONS]-(o:Section) WHERE o.valid_to IS NULL AND NOT o.sid IN $sids
        RETURN s.vid AS vid, labels(e)[0] AS type, coalesce(e.name, e.url) AS key, coalesce(e.name, e.url) AS name,
               count(DISTINCT o) AS others
        """, {"vids": [c["vid"] for c in cited], "sids": sids}):
        en = "E:" + e["type"] + ":" + e["key"]
        g.node(en, e["type"], e["name"] if e["type"] != "Dashboard" else (e["name"] or "dashboard"), others=e["others"])
        g.edge("S:" + e["vid"], en, "MENTIONS")

    did = _decisions_for(sids)
    decs = rows(
        """
        MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation) WHERE d.id IN $ids
        OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
        OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(c:Commit)
        RETURN d.id AS id, d.statement AS statement, d.quote AS quote, d.decided_at AS at, d.status AS status,
               d.lag_days AS lag, cv.cid AS cid, cv.title AS where, cv.source AS src, cv.kind AS conv_kind,
               by.name AS by, collect(DISTINCT c.sha) AS shas,
               [(d)-[:EVIDENCED_BY]->(seg:Segment) | {speaker:seg.speaker, at:seg.at, text:left(seg.text, 300)}][..3] AS evidence
        """, {"ids": did}) if did else []
    for d in decs:
        dn, vn = "D:" + d["id"], "V:" + d["cid"]
        g.node(dn, "Decision", d["statement"], hl=True, status=d["status"], quote=d["quote"], at=d["at"])
        g.node(vn, "Conversation", d["where"], hl=True, src=d["src"], at=d["at"])
        g.edge(dn, vn, "DECIDED_IN", hl=True)
        if d["by"] and d["by"] != "unknown":
            g.node("U:" + d["by"], "Person", d["by"], hl=True)
            g.edge(dn, "U:" + d["by"], "DECIDED_BY", hl=True)
        for sha in d["shas"]:
            g.edge(dn, "C:" + sha, "IMPLEMENTED_BY", hl=True)
            if "C:" + sha in g.nodes:
                g.nodes["C:" + sha]["hl"] = True
        tl.append({"ts": d["at"], "kind": "decision", "title": d["statement"], "who": d["by"], "where": d["where"],
                   "src": d["src"], "quote": d["quote"], "evidence": d["evidence"], "status": d["status"], "lag": d["lag"],
                   "node": dn})
    for e in g.edges:
        if e["type"] == "CHANGED" and g.nodes[e["source"]]["hl"]:
            e["hl"] = True
            g.nodes[e["target"]]["hl"] = True

    # sources that disagree about these sections, and chat claims that contradict them
    conf = rows(
        """
        MATCH (x:Section)-[c:CONFLICTS_WITH]->(y:Section) WHERE x.sid IN $sids OR y.sid IN $sids
        MATCH (px:Page)-[:HAS_SECTION]->(x) MATCH (py:Page)-[:HAS_SECTION]->(y)
        RETURN x.vid AS lv, x.sid AS lsid, x.heading AS lh, px.path AS lpath, px.title AS lpage, px.source AS lsrc,
               px.source_kind AS lkind, px.as_of AS lup, y.vid AS wv, y.sid AS wsid, c.reason AS reason, c.topic AS topic,
               c.found_ts AS found, c.status AS status
        """, {"sids": sids})
    for c in conf:
        g.node("P:" + c["lpath"], "Page", c["lpage"], path=c["lpath"], source=c["lsrc"], kind=c["lkind"])
        g.node("S:" + c["lv"], "Section", c["lh"], cite=cite_key(c["lsid"]), current=True, outdated_copy=True)
        g.edge("P:" + c["lpath"], "S:" + c["lv"], "HAS_SECTION")
        g.node("S:" + c["wv"], "Section", "", cite=cite_key(c["wsid"]))
        g.edge("S:" + c["lv"], "S:" + c["wv"], "CONFLICTS_WITH", hl=c["status"] == "open", reason=c["reason"])
        tl.append({"ts": c["found"], "kind": "conflict", "title": c["topic"] or "Sources disagree", "detail": c["reason"],
                   "status": c["status"], "cite": cite_key(c["lsid"]), "uploaded": c["lup"], "node": "S:" + c["lv"]})
    claims = rows(
        """
        MATCH (k:Claim)-[r:CONTRADICTS]->(s:Section) WHERE s.sid IN $sids
        MATCH (k)-[:STATED_IN]->(cv:Conversation)
        RETURN k.id AS id, k.statement AS st, k.speaker AS who, k.at AS at, r.reason AS reason, s.vid AS vid,
               cv.cid AS cid, cv.title AS where, cv.source AS src
        """, {"sids": sids})
    for k in claims:
        g.node("K:" + k["id"], "Claim", k["st"], who=k["who"], at=k["at"])
        g.node("V:" + k["cid"], "Conversation", k["where"], src=k["src"])
        g.edge("K:" + k["id"], "V:" + k["cid"], "STATED_IN")
        g.edge("K:" + k["id"], "S:" + k["vid"], "CONTRADICTS", reason=k["reason"])
        tl.append({"ts": k["at"], "kind": "claim", "title": k["st"], "who": k["who"], "where": k["where"], "src": k["src"],
                   "detail": k["reason"], "node": "K:" + k["id"]})

    # the answer's own life: asked, revalidated / invalidated by later changes, replaced by a refresh
    tl.append({"ts": a["asked_at"] or a["created"], "kind": "answer", "title": "Answered", "detail": a["question"],
               "status": a["status"], "node": "A:" + aid})
    for r in rows("""MATCH (:Answer {id:$id})-[r:INVALIDATED_BY|REVALIDATED_BY]->(c:Commit)
                     RETURN type(r) AS t, r.reason AS reason, r.kind AS kind, c.ts AS ts, c.title AS title""", {"id": aid}):
        tl.append({"ts": r["ts"], "kind": "stale" if r["t"] == "INVALIDATED_BY" else "revalidated", "title": r["title"],
                   "detail": r["reason"], "status": r["kind"]})
    old = rows("""MATCH (:Answer {id:$id})-[:SUPERSEDES*1..5]->(o:Answer)
                  RETURN o.id AS id, o.created AS created, left(o.text, 300) AS text, o.stale_reason AS why""", {"id": aid})
    for o in old:
        g.node("A:" + o["id"], "Answer", "Earlier answer", status="replaced", text=o["text"])
        g.edge("A:" + aid, "A:" + o["id"], "SUPERSEDES")
        tl.append({"ts": o["created"], "kind": "earlier_answer", "title": "Earlier answer (replaced)", "detail": o["why"],
                   "text": o["text"], "node": "A:" + o["id"]})

    def key(e):
        return str(e.get("ts") or "")[:19].replace(" ", "T")
    tl = [e for e in tl if e.get("ts")]
    tl.sort(key=key)
    return {"answer": a, "graph": g.out(), "timeline": tl}


def explore(limit_pages: int = 220) -> dict:
    """Company-level graph: sources, pages, owners, decisions and the meetings/chats they came from,
    cross-source conflicts and page links. Sections are left out (open a page to see them)."""
    g = G()
    pages = rows(
        """
        MATCH (p:Page) WHERE p.deleted = false
        OPTIONAL MATCH (p)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL
        WITH p, count(s) AS n, max(s.valid_from_ts) AS changed
        RETURN p.path AS path, p.title AS title, p.dept AS dept, p.source AS source, p.source_kind AS kind,
               p.owner_source AS owner_source, n, changed,
               [(p)-[r:OWNED_BY]->(o) | {h:o.handle, n:o.name, team:'Team' IN labels(o), src:r.source}] AS owners
        ORDER BY changed DESC LIMIT $n
        """, {"n": limit_pages})
    keep = set()
    for p in pages:
        pid = "P:" + p["path"]
        keep.add(p["path"])
        g.node(pid, "Page", p["title"], path=p["path"], dept=p["dept"], source=p["source"], kind=p["kind"],
               owner_source=p["owner_source"], sections=p["n"], changed=p["changed"])
        if p["source"]:
            g.node("X:" + p["source"], "Source", p["source"], kind=p["kind"])
            g.edge("X:" + p["source"], pid, "PROVIDES")
        _owner_nodes(g, pid, p["owners"])
    for d in rows(
        """
        MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (d)-[:ABOUT]->(:Section)<-[:HAS_SECTION]-(p:Page)
        OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(:Commit)-[:CHANGED]->(:Section)<-[:HAS_SECTION]-(p2:Page)
        OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
        RETURN d.id AS id, d.statement AS st, d.status AS status, d.decided_at AS at, cv.cid AS cid, cv.title AS where,
               cv.source AS src, by.name AS by, collect(DISTINCT p.path) + collect(DISTINCT p2.path) AS paths
        """):
        dn = "D:" + d["id"]
        g.node(dn, "Decision", d["st"], status=d["status"], at=d["at"])
        g.node("V:" + d["cid"], "Conversation", d["where"], src=d["src"])
        g.edge(dn, "V:" + d["cid"], "DECIDED_IN")
        if d["by"] and d["by"] != "unknown":
            g.node("U:" + d["by"], "Person", d["by"])
            g.edge(dn, "U:" + d["by"], "DECIDED_BY")
        for p in set(d["paths"]):
            if p in keep:
                g.edge(dn, "P:" + p, "IMPLEMENTED_BY" if d["status"] == "implemented" else "ABOUT")
    for c in rows("""MATCH (px:Page)-[:HAS_SECTION]->(:Section)-[c:CONFLICTS_WITH {status:'open'}]->(:Section)<-[:HAS_SECTION]-(py:Page)
                     RETURN DISTINCT px.path AS a, py.path AS b, c.topic AS topic"""):
        g.edge("P:" + c["a"], "P:" + c["b"], "CONFLICTS_WITH", hl=True, reason=c["topic"])
    # shared business/technical entities: metrics, dashboards and fields that two or more documents rely on
    for e in rows(
        """
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section)-[:MENTIONS]->(e) WHERE s.valid_to IS NULL AND p.deleted = false
              AND (e:Metric OR e:Dashboard OR e:Field)
        WITH e, collect(DISTINCT p.path) AS paths WHERE size(paths) >= 2
        RETURN labels(e)[0] AS type, coalesce(e.name, e.url) AS name, paths ORDER BY size(paths) DESC LIMIT 40
        """):
        en = "E:" + e["type"] + ":" + e["name"]
        g.node(en, e["type"], e["name"], docs=len(e["paths"]))
        for pth in e["paths"]:
            if pth in keep:
                g.edge("P:" + pth, en, "MENTIONS")
    for l in rows("MATCH (a:Page)-[:LINKS_TO]->(b:Page) RETURN a.path AS a, b.path AS b LIMIT 400"):
        g.edge("P:" + l["a"], "P:" + l["b"], "LINKS_TO")
    return g.out()


def page_graph(path: str) -> dict:
    """One page and everything around it: sections (current + superseded), changes, decisions, conflicts."""
    g = G()
    p = rows("""MATCH (p:Page {path:$p}) RETURN p.title AS title, p.source AS source, p.source_kind AS kind,
                [(p)-[r:OWNED_BY]->(o) | {h:o.handle, n:o.name, team:'Team' IN labels(o), src:r.source}] AS owners""",
             {"p": path})
    if not p:
        return g.out()
    pid = "P:" + path
    g.node(pid, "Page", p[0]["title"], hl=True, path=path, source=p[0]["source"], kind=p[0]["kind"])
    _owner_nodes(g, pid, p[0]["owners"])
    for s in rows("""MATCH (:Page {path:$p})-[:HAS_SECTION]->(s:Section)
                     OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:Section)
                     OPTIONAL MATCH (c:Commit)-[:CHANGED]->(s)
                     OPTIONAL MATCH (m:Meta {id:'meta'})
                     RETURN s.vid AS vid, s.sid AS sid, s.heading AS h, s.valid_to IS NULL AS cur, prev.vid AS prev,
                            c.sha AS sha, c.title AS title, c.ts AS ts, m.t0 AS t0""", {"p": path}):
        sn = "S:" + s["vid"]
        g.node(sn, "Section", s["h"], cite=cite_key(s["sid"]), current=s["cur"])
        if s["cur"]:
            g.edge(pid, sn, "HAS_SECTION")
        if s["prev"]:
            g.node("S:" + s["prev"], "Section", s["h"], current=False)
            g.edge(sn, "S:" + s["prev"], "SUPERSEDES")
        if s["sha"] and s["sha"] != s["t0"]:
            g.node("C:" + s["sha"], "Change", s["title"] or "change", ts=s["ts"])
            g.edge("C:" + s["sha"], sn, "CHANGED")
    sids = [r["sid"] for r in rows("MATCH (:Page {path:$p})-[:HAS_SECTION]->(s:Section) RETURN DISTINCT s.sid AS sid", {"p": path})]
    ids = _decisions_for(sids)
    for d in rows("""MATCH (d:Decision) WHERE d.id IN $ids
                     OPTIONAL MATCH (d)-[:DECIDED_IN]->(cv:Conversation)
                     RETURN d.id AS id, d.statement AS st, cv.cid AS cid, cv.title AS where, cv.source AS src""",
                  {"ids": ids}) if ids else []:
        g.node("D:" + d["id"], "Decision", d["st"])
        g.edge("D:" + d["id"], pid, "ABOUT")
        if d["cid"]:
            g.node("V:" + d["cid"], "Conversation", d["where"], src=d["src"])
            g.edge("D:" + d["id"], "V:" + d["cid"], "DECIDED_IN")
    return g.out()


def schema() -> dict:
    """The data model as it exists in this graph right now (label -[TYPE]-> label, with counts)."""
    g = G()
    counts = {r["l"]: r["n"] for r in rows("MATCH (n) RETURN labels(n)[0] AS l, count(*) AS n")}
    for r in rows("MATCH (a)-[r]->(b) RETURN labels(a)[0] AS a, type(r) AS t, labels(b)[0] AS b, count(*) AS n"):
        if r["a"] in ("Meta",) or r["b"] in ("Meta",):
            continue
        for l in (r["a"], r["b"]):
            g.node("L:" + l, l, f"{l} · {counts.get(l, 0):,}", count=counts.get(l, 0))
        g.edge("L:" + r["a"], "L:" + r["b"], r["t"], count=r["n"])
    return g.out()
