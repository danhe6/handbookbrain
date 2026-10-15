"""FalkorDB access + every write the product does. All Cypher lives here or in
queries.py so the README can point judges at two files."""
import difflib
import re

from falkordb import FalkorDB

from .config import COMMIT_URL, EMBED_DIM, FALKOR_HOST, FALKOR_PORT, GRAPH, MR_URL
from .parse import OwnerRule, Page, owners_for

_db = None


def _epoch(ts: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def g(name: str = GRAPH):
    global _db
    if _db is None:
        _db = FalkorDB(host=FALKOR_HOST, port=FALKOR_PORT)
    return _db.select_graph(name)


def q(cypher: str, params: dict | None = None):
    return g().query(cypher, params or {})


def rows(cypher: str, params: dict | None = None) -> list[dict]:
    res = q(cypher, params)
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


def reset():
    try:
        g().delete()
    except Exception:
        pass


def ensure_schema():
    stmts = [
        "CREATE INDEX FOR (p:Page) ON (p.path)",
        "CREATE INDEX FOR (p:Page) ON (p.url)",
        "CREATE INDEX FOR (s:Section) ON (s.sid)",
        "CREATE INDEX FOR (s:Section) ON (s.vid)",
        "CREATE INDEX FOR (s:Section) ON (s.cite)",
        "CREATE INDEX FOR (c:Commit) ON (c.sha)",
        "CREATE INDEX FOR (m:MergeRequest) ON (m.iid)",
        "CREATE INDEX FOR (p:Person) ON (p.handle)",
        "CREATE INDEX FOR (p:Person) ON (p.name)",
        "CREATE INDEX FOR (t:Team) ON (t.handle)",
        "CREATE INDEX FOR (a:Answer) ON (a.id)",
        "CREATE INDEX FOR (qn:Question) ON (qn.text)",
        "CREATE INDEX FOR (cv:Conversation) ON (cv.cid)",
        "CREATE INDEX FOR (g:Segment) ON (g.gid)",
        "CREATE INDEX FOR (d:Decision) ON (d.id)",
        "CREATE INDEX FOR (d:Decision) ON (d.status)",
        "CREATE INDEX FOR (a:ActionItem) ON (a.id)",
        "CREATE INDEX FOR (k:Claim) ON (k.id)",
        "CREATE INDEX FOR (s:AISummary) ON (s.id)",
        "CALL db.idx.fulltext.createNodeIndex('Segment', 'text')",
        "CALL db.idx.fulltext.createNodeIndex('Decision', 'statement')",
        "CALL db.idx.fulltext.createNodeIndex('Section', 'heading', 'text')",
        f"CREATE VECTOR INDEX FOR (s:Section) ON (s.embedding) OPTIONS {{dimension:{EMBED_DIM}, similarityFunction:'cosine'}}",
    ]
    for s in stmts:
        try:
            q(s)
        except Exception as e:  # index already exists
            if "already" not in str(e).lower():
                raise


# ---------- commits / decisions ----------

def upsert_commit(c) -> None:
    """Commit + (if it is a merged MR) the decision: who proposed it (branch author handle, when the
    branch name reveals it) and who merged/approved it (merge committer name)."""
    q(
        """
        MERGE (c:Commit {sha:$sha})
        SET c.ts=$ts, c.epoch=$epoch, c.title=$title, c.url=$url, c.branch=$branch
        MERGE (mg:Person {name:$merger})
        MERGE (mg)-[:MERGED]->(c)
        """,
        dict(sha=c.sha, ts=c.ts, epoch=_epoch(c.ts), title=c.title, url=(COMMIT_URL + c.sha) if COMMIT_URL else None, branch=c.branch,
             merger=c.author),
    )
    if c.mr_iid:
        body = "\n".join(c.body.splitlines()[1:])
        body = body.split("See merge request")[0].strip()[:4000]
        q(
            """
            MATCH (c:Commit {sha:$sha})
            MERGE (m:MergeRequest {iid:$iid})
            SET m.title=$title, m.description=$body, m.url=$url, m.merged_at=$ts
            MERGE (m)-[:MERGED_AS]->(c)
            """,
            dict(sha=c.sha, iid=c.mr_iid, title=c.title, body=body, url=f"{MR_URL}{c.mr_iid}", ts=c.ts),
        )
    if c.mr_author:
        q(
            """
            MATCH (c:Commit {sha:$sha})
            MERGE (a:Person {handle:$h})
            MERGE (a)-[:AUTHORED]->(c)
            """,
            dict(sha=c.sha, h=c.mr_author),
        )


# ---------- pages / sections (versioned) ----------

def upsert_page(page: Page, sha: str, ts: str) -> dict:
    """Write a page as of commit `sha`. Unchanged sections are untouched; changed ones get a
    new version node that SUPERSEDES the old one. Returns counts of added/modified/removed."""
    q(
        """
        MERGE (p:Page {path:$path})
        SET p.url=$url, p.title=$title, p.dept=$dept, p.links=$links, p.deleted=false
        """,
        dict(path=page.path, url=page.url, title=page.title, dept=page.dept, links=page.links),
    )
    current = {
        r["sid"]: r
        for r in rows(
            "MATCH (:Page {path:$path})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL "
            "RETURN s.sid AS sid, s.hash AS hash, s.vid AS vid, s.text AS text",
            {"path": page.path},
        )
    }
    new_secs, mods, keep = [], [], []
    for s in page.sections:
        cur = current.get(s.sid)
        rec = dict(sid=s.sid, vid=f"{s.sid}@{s.hash}", cite=s.cite, anchor=s.anchor, heading=s.heading,
                   level=s.level, ord=s.order, text=s.text, hash=s.hash)
        if cur is None:
            new_secs.append(rec)
        elif cur["hash"] != s.hash:
            rec["old_vid"] = cur["vid"]
            rec["kind"] = "modified"
            mods.append(rec)
        else:
            keep.append(s.sid)
    removed = [sid for sid in current if sid not in {s.sid for s in page.sections}]
    # Heading renamed / section re-anchored: a removed section and an added one with near-identical
    # text are the same knowledge -> link as a new version ('moved') instead of a deletion.
    for sid in list(removed):
        old = _norm(current[sid]["text"])
        best, score = None, 0.0
        for rec in new_secs:
            sm = difflib.SequenceMatcher(None, old, _norm(rec["text"]), autojunk=False)
            if sm.real_quick_ratio() >= 0.75 and sm.quick_ratio() >= 0.75 and (r := sm.ratio()) > score:
                best, score = rec, r
        if best and score >= 0.75:
            new_secs.remove(best)
            removed.remove(sid)
            mods.append({**best, "old_vid": current[sid]["vid"], "kind": "moved"})

    base = dict(path=page.path, sha=sha, ts=ts, url=page.url)
    if new_secs:
        q(
            """
            MATCH (p:Page {path:$path}) MATCH (c:Commit {sha:$sha})
            UNWIND $secs AS s
            CREATE (n:Section {sid:s.sid, vid:s.vid, cite:s.cite, anchor:s.anchor, heading:s.heading, level:s.level,
                               ord:s.ord, text:s.text, hash:s.hash, path:$path,
                               url:$url + '#' + s.anchor, valid_from:$sha, valid_from_ts:$ts,
                               origin:'human_authored', review_status:'approved'})
            CREATE (p)-[:HAS_SECTION]->(n)
            CREATE (c)-[:CHANGED {kind:'added'}]->(n)
            """,
            {**base, "secs": new_secs},
        )
    if mods:
        q(
            """
            MATCH (p:Page {path:$path}) MATCH (c:Commit {sha:$sha})
            UNWIND $secs AS s
            MATCH (old:Section {vid:s.old_vid}) WHERE old.valid_to IS NULL
            SET old.valid_to=$sha, old.valid_to_ts=$ts
            CREATE (n:Section {sid:s.sid, vid:s.vid, cite:s.cite, anchor:s.anchor, heading:s.heading, level:s.level,
                               ord:s.ord, text:s.text, hash:s.hash, path:$path,
                               url:$url + '#' + s.anchor, valid_from:$sha, valid_from_ts:$ts,
                               origin:'human_authored', review_status:'approved'})
            CREATE (p)-[:HAS_SECTION]->(n)
            CREATE (n)-[:SUPERSEDES]->(old)
            CREATE (c)-[:CHANGED {kind:s.kind}]->(n)
            """,
            {**base, "secs": mods},
        )
    if removed:
        remove_sections(page.path, removed, sha, ts)
    if keep:  # section order can shift without content change
        q(
            """
            UNWIND $ords AS o
            MATCH (s:Section {sid:o.sid}) WHERE s.valid_to IS NULL SET s.ord=o.ord
            """,
            {"ords": [{"sid": s.sid, "ord": s.order} for s in page.sections if s.sid in keep]},
        )
    return {"added": len(new_secs), "modified": len(mods), "removed": len(removed),
            "moved": sum(1 for m in mods if m["kind"] == "moved")}


def remove_sections(path: str, sids: list[str], sha: str, ts: str):
    q(
        """
        MATCH (c:Commit {sha:$sha})
        UNWIND $sids AS sid
        MATCH (:Page {path:$path})-[:HAS_SECTION]->(s:Section {sid:sid}) WHERE s.valid_to IS NULL
        SET s.valid_to=$sha, s.valid_to_ts=$ts
        CREATE (c)-[:CHANGED {kind:'removed'}]->(s)
        """,
        dict(path=path, sids=sids, sha=sha, ts=ts),
    )


def delete_page(path: str, sha: str, ts: str):
    sids = [r["sid"] for r in rows(
        "MATCH (:Page {path:$p})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL RETURN s.sid AS sid", {"p": path})]
    if sids:
        remove_sections(path, sids, sha, ts)
    q("MATCH (p:Page {path:$p}) SET p.deleted=true", {"p": path})


def relink_pages():
    """Resolve markdown links into LINKS_TO edges between pages we hold."""
    q("MATCH (:Page)-[r:LINKS_TO]->(:Page) DELETE r")
    q(
        """
        MATCH (a:Page) WHERE a.deleted = false
        UNWIND a.links AS u
        MATCH (b:Page {url:u}) WHERE b <> a
        MERGE (a)-[:LINKS_TO]->(b)
        """
    )


def apply_owners(rules: list[OwnerRule]):
    """CODEOWNERS -> (Page)-[:OWNED_BY]->(Team|Person). '@a/b' handles are groups (Team)."""
    q("MATCH (:Page)-[r:OWNED_BY]->() DELETE r")
    q("MATCH (p:Page) SET p.owner_source = 'none', p.origin = 'human_authored'")
    pages = [r["path"] for r in rows("MATCH (p:Page) WHERE p.deleted = false RETURN p.path AS path")]
    batch = []
    for path in pages:
        rule = owners_for(path, rules)
        if not rule:
            continue
        for o in rule.owners:
            handle, _, name = o.partition("|")
            batch.append({"path": path, "handle": handle.lower(), "name": name or None, "team": "/" in handle,
                          "rule": rule.pattern, "section": rule.section, "src": getattr(rule, "source", "explicit")})
    q(
        """
        UNWIND $b AS x
        MATCH (p:Page {path:x.path})
        SET p.owner_source = x.src
        FOREACH (_ IN CASE WHEN x.team THEN [1] ELSE [] END |
            MERGE (t:Team {handle:x.handle}) SET t.name = coalesce(x.name, t.name)
            MERGE (p)-[:OWNED_BY {rule:x.rule, section:x.section, source:x.src}]->(t))
        FOREACH (_ IN CASE WHEN x.team THEN [] ELSE [1] END |
            MERGE (u:Person {handle:x.handle}) SET u.name = coalesce(x.name, u.name)
            MERGE (p)-[:OWNED_BY {rule:x.rule, section:x.section, source:x.src}]->(u))
        """,
        {"b": batch},
    )
    return len(batch)


def embed_missing(batch: int = 200) -> int:
    """Embed current section versions that don't have a vector yet (new + changed ones after replay)."""
    from .llm import embed
    done = 0
    while True:
        todo = rows(
            """
            MATCH (p:Page)-[:HAS_SECTION]->(s:Section)
            WHERE s.valid_to IS NULL AND s.embedding IS NULL
            RETURN s.vid AS vid, p.title + ' > ' + s.heading + '\n' + left(s.text, 3000) AS t LIMIT $n
            """,
            {"n": batch},
        )
        if not todo:
            return done
        vecs = embed([r["t"] for r in todo])
        q(
            "UNWIND $b AS x MATCH (s:Section {vid:x.vid}) SET s.embedding = vecf32(x.v)",
            {"b": [{"vid": r["vid"], "v": v} for r, v in zip(todo, vecs)]},
        )
        done += len(todo)


def set_meta(**kv):
    q("MERGE (m:Meta {id:'meta'}) SET m += $kv", {"kv": kv})


def get_meta() -> dict:
    r = rows("MATCH (m:Meta {id:'meta'}) RETURN properties(m) AS m")
    return r[0]["m"] if r else {}


# ---------- sources (live wiki / static uploads / git ...) ----------

def tag_sources(sources: list[dict], multi: bool) -> None:
    """(:Source)-[:PROVIDES]->(:Page). Page carries source, source_kind (live|static) and authority so
    retrieval, answers and conflict resolution can tell a daily-synced wiki from a PDF uploaded once."""
    for s in sources:
        q(
            """
            MERGE (x:Source {name:$name})
            SET x.kind=$kind, x.type=$type, x.authority=$authority, x.label=$label
            WITH x
            MATCH (p:Page) WHERE p.source IS NULL AND ($all OR p.path STARTS WITH $prefix)
            SET p.source=x.name, p.source_kind=x.kind, p.authority=x.authority
            MERGE (x)-[:PROVIDES]->(p)
            """,
            {**s, "all": not multi, "prefix": s["name"] + "/"},
        )


def mark_synced(name: str | None = None) -> None:
    import time
    q("MATCH (x:Source) WHERE $n IS NULL OR x.name = $n SET x.last_synced = $now",
      {"n": name, "now": time.strftime("%Y-%m-%dT%H:%M:%S")})
