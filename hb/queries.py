"""Agent tools = parameterised Cypher over FalkorDB.

Design rule: the agent picks a tool and fills arguments; it never writes Cypher for anything a tool
covers. Every tool returns {"rows": ..., "sources": {cite_key: section_vid}} so the answer can cite
exact section VERSIONS, which is what lets us detect later that an answer went stale."""
import difflib
import re

from .graph import g, rows

STOP = set("a an the of to in on for and or is are do does can i my we our you what how when who which "
           "why where with at by be it this that as from if any much many there have has get".split())
WRITE = re.compile(r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD|FOREACH)\b|db\.idx\.\w+\.(create|drop)", re.I)


from .parse import cite_key  # noqa: E402  (re-exported)


def sid_for(cite: str) -> str:
    """Cite key -> section id, via the graph (works for any company / file format)."""
    cite = cite.strip().strip("[]")
    r = rows("MATCH (s:Section {cite:$c}) RETURN s.sid AS sid LIMIT 1", {"c": cite})
    if r:
        return r[0]["sid"]
    path, _, anchor = cite.partition("#")   # graphs built before cite keys existed, or slightly-off keys
    r = rows("MATCH (s:Section {anchor:$a}) WHERE s.path CONTAINS $p RETURN s.sid AS sid ORDER BY s.valid_to IS NULL DESC LIMIT 1",
             {"a": anchor, "p": path})
    return r[0]["sid"] if r else cite


def _src(rs, vid="vid", sid="sid") -> dict:
    return {cite_key(r[sid]): r[vid] for r in rs if r.get(vid)}


def _ft(text: str) -> str:
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", text.lower()) if w not in STOP and len(w) > 2]
    return "|".join(dict.fromkeys(words)) or text


SEARCH_WINDOW = int(__import__("os").getenv("HB_SEARCH_WINDOW", "800"))  # chars per hit: 8 hits must fit the tool budget


def best_window(text: str, query: str, size: int = 1800) -> str:
    """Long sections: return the contiguous run of paragraphs that best matches the query instead of
    blindly truncating (key facts often sit deep inside a long policy section)."""
    if len(text) <= size:
        return text
    words = {w for w in re.findall(r"[a-z0-9%]+", query.lower()) if w not in STOP and len(w) > 2}
    paras = [x for x in re.split(r"\n\s*\n|\n(?=[-*] |\d+\. )", text) if x.strip()]
    scores = [sum(w in x.lower() for w in words) for x in paras]
    best, best_s = (0, 0), -1
    for i in range(len(paras)):
        length, sc, j = 0, 0, i
        while j < len(paras) and length + len(paras[j]) <= size:
            length += len(paras[j]) + 2
            sc += scores[j]
            j += 1
        if sc > best_s:
            best, best_s = (i, j), sc
    i, j = best
    out = "\n\n".join(paras[i:max(j, i + 1)])[:size]
    return ("[...]\n" if i > 0 else "") + out + ("\n[...]" if j < len(paras) else "")


def _diff(old: str, new: str, limit: int = 1500) -> str:
    """Explicit REMOVED/ADDED lines (models misread bare -/+ prefixes, especially on bullet lists)."""
    import difflib
    out = []
    for l in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if l.startswith(("+++", "---", "@@")):
            continue
        if l.startswith("-") and l[1:].strip():
            out.append("REMOVED (old text): " + l[1:].strip())
        elif l.startswith("+") and l[1:].strip():
            out.append("ADDED (new text):   " + l[1:].strip())
    return "\n".join(out)[:limit]


def _resolve_page(name: str) -> str | None:
    """Fuzzy page lookup by title, url or path fragment (entity resolution at query time)."""
    pages = rows("MATCH (p:Page) WHERE p.deleted = false RETURN p.path AS path, p.title AS title, p.url AS url")
    key = name.lower().strip(" /")
    for p in pages:
        if key in (p["title"].lower(), p["url"].strip("/").lower(), p["path"].lower()) or p["url"].strip("/").lower().endswith(key):
            return p["path"]
    by_title = {p["title"].lower(): p["path"] for p in pages}
    hit = difflib.get_close_matches(key, list(by_title), n=1, cutoff=0.6)
    return by_title[hit[0]] if hit else None


# ------------------------------------------------------------------ retrieval

def search_handbook(query: str, k: int = 8) -> dict:
    """Hybrid search over CURRENT handbook sections: semantic (vector) + keyword (full-text),
    fused with reciprocal-rank fusion. Each hit includes the page owners. Use for any policy / process question."""
    from .llm import embed
    ranked: dict[str, dict] = {}
    try:
        v = embed([query])[0]
        vec = rows(
            """
            CALL db.idx.vector.queryNodes('Section', 'embedding', $n, vecf32($v)) YIELD node
            WHERE node.valid_to IS NULL
            RETURN node.vid AS vid
            """,
            {"n": k * 4, "v": v},
        )
    except Exception:
        vec = []
    kw = rows(
        """
        CALL db.idx.fulltext.queryNodes('Section', $q) YIELD node, score
        WHERE node.valid_to IS NULL
        RETURN node.vid AS vid ORDER BY score DESC LIMIT $n
        """,
        {"q": _ft(query), "n": k * 2},
    )
    for lst in (vec, kw):
        for rank, r in enumerate(lst):
            ranked.setdefault(r["vid"], {"s": 0.0})["s"] += 1.0 / (60 + rank)
    top = [vid for vid, _ in sorted(ranked.items(), key=lambda x: -x[1]["s"])[:k * 2]]  # over-fetch, dedupe below
    hits = rows(
        """
        UNWIND range(0, size($vids)-1) AS i
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section {vid:$vids[i]})
        RETURN i, s.vid AS vid, s.sid AS sid, p.path AS path, p.title AS page, s.heading AS heading, s.url AS url,
               s.text AS text, s.valid_from_ts AS last_changed, [(p)-[:OWNED_BY]->(o) | o.handle] AS owners,
               p.source AS source, p.source_kind AS source_kind, p.as_of AS uploaded_on, coalesce(p.authority, 1) AS authority,
               [(s)-[c1:CONFLICTS_WITH {status:'open'}]->(w:Section) | [w.sid, c1.reason]] AS outdated_by,
               [(l:Section)-[c2:CONFLICTS_WITH {status:'open'}]->(s) | l.sid] AS older_copies_disagree
        ORDER BY i
        """,
        {"vids": top},
    )
    hits = _dedupe_copies(hits)[:k]
    out = []
    for h in hits:
        row = {"cite": cite_key(h["sid"]), **{k2: h[k2] for k2 in ("page", "path", "heading", "url", "last_changed", "owners")}}
        if h.get("source"):
            row["source"] = f"{h['source']} ({h['source_kind']}" + (f", uploaded {h['uploaded_on']})" if h.get("uploaded_on") else ")")
        if h["outdated_by"]:  # this copy disagrees with a more authoritative / newer source
            why = next((r for _, r in h["outdated_by"] if r), "")
            row["WARNING"] = (f"OUTDATED COPY - conflicts with {', '.join(cite_key(x) for x, _ in h['outdated_by'])}"
                              + (f": {why}" if why else "") + ". Answer from the authoritative section, mention this copy is outdated.")
        if h["older_copies_disagree"]:
            row["note"] = "authoritative; older copies that disagree: " + ", ".join(cite_key(x) for x in h["older_copies_disagree"])
        if h.get("copies"):
            row["same_text_also_in"] = h["copies"]
        row["text"] = best_window(h["text"] or "", query, SEARCH_WINDOW)
        out.append(row)
    return {"rows": out, "sources": _src(hits)}


def _dedupe_copies(hits: list[dict]) -> list[dict]:
    """The same section often exists twice (wiki page + an exported PDF on the drive). Showing both wastes the
    agent's context and pushes other relevant sections out. Near-identical copies from different sources collapse
    into the most authoritative one, which lists the others. Copies that DISAGREE are not near-identical and stay."""
    def norm(t):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9$%.,: ]+", " ", (t or "").lower())).strip()
    kept: list[dict] = []
    for h in hits:
        nh = norm(h["text"])
        dup = None
        for i, kp in enumerate(kept):
            if kp.get("source") == h.get("source"):
                continue
            sm = difflib.SequenceMatcher(None, kp["_n"], nh, autojunk=False)
            if sm.real_quick_ratio() >= 0.9 and sm.quick_ratio() >= 0.9 and sm.ratio() >= 0.9:
                dup = i
                break
        if dup is None:
            kept.append({**h, "_n": nh, "copies": []})
            continue
        kp = kept[dup]
        if (h.get("authority") or 1) > (kp.get("authority") or 1):  # better copy takes the slot
            kept[dup] = {**h, "_n": nh, "copies": kp["copies"] + [cite_key(kp["sid"])]}
        else:
            kp["copies"].append(cite_key(h["sid"]))
    return kept


def page_details(page: str) -> dict:
    """One page: its owners (CODEOWNERS), section outline, pages it links to and pages linking to it,
    and the last 5 merged MRs that changed it. Use when the question names a page/policy, or to find who owns it."""
    path = _resolve_page(page)
    if not path:
        return {"error": f"no page matching '{page}'"}
    info = rows(
        """
        MATCH (p:Page {path:$path})
        OPTIONAL MATCH (p)-[ow:OWNED_BY]->(o)
        OPTIONAL MATCH (p)-[:LINKS_TO]->(out:Page)
        OPTIONAL MATCH (inn:Page)-[:LINKS_TO]->(p)
        RETURN p.title AS title, p.url AS url, collect(DISTINCT o.handle) AS owners,
               collect(DISTINCT ow.section)[0] AS codeowners_section,
               collect(DISTINCT out.title)[..10] AS links_to, collect(DISTINCT inn.title)[..10] AS linked_from
        """,
        {"path": path},
    )[0]
    secs = rows(
        "MATCH (:Page {path:$path})-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL "
        "RETURN s.vid AS vid, s.sid AS sid, s.heading AS heading ORDER BY s.ord",
        {"path": path},
    )
    changes = rows(
        """
        MATCH (:Page {path:$path})-[:HAS_SECTION]->(:Section)<-[:CHANGED]-(c:Commit)
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (a:Person)-[:AUTHORED]->(c)
        OPTIONAL MATCH (mg:Person)-[:MERGED]->(c)
        RETURN DISTINCT c.ts AS ts, c.title AS title, m.iid AS mr, a.handle AS author, mg.name AS merged_by
        ORDER BY ts DESC LIMIT 5
        """,
        {"path": path},
    )
    return {**info, "sections": [{"cite": cite_key(s["sid"]), "heading": s["heading"]} for s in secs],
            "recent_changes": changes, "sources": _src(secs[:1])}


def section_history(cite: str) -> dict:
    """Every version of one section (by its cite key, e.g. 'people-group/paid-time-off#pto'), newest first,
    with the exact DIFF each version introduced, the MR that made it, who authored and merged it.
    Use for 'what changed / when did this change / what was the old rule' questions."""
    sid = sid_for(cite)
    hist = rows(
        """
        MATCH (s:Section {sid:$sid})
        OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:Section)
        OPTIONAL MATCH (c:Commit)-[ch:CHANGED]->(s) WHERE ch.kind <> 'removed'
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (a:Person)-[:AUTHORED]->(c)
        OPTIONAL MATCH (mg:Person)-[:MERGED]->(c)
        RETURN s.vid AS vid, s.sid AS sid, s.valid_from_ts AS since, s.valid_to_ts AS until, s.text AS text,
               prev.text AS prev_text, c.title AS change, m.iid AS mr, m.url AS mr_url, a.handle AS author,
               mg.name AS merged_by
        ORDER BY since DESC
        """,
        {"sid": sid},
    )
    if not hist:
        return {"error": f"unknown section '{cite}'"}
    versions = []
    for h in hist:
        v = {k: h[k] for k in ("since", "until", "change", "mr", "mr_url", "author", "merged_by")}
        v["diff"] = _diff(h["prev_text"], h["text"]) if h["prev_text"] else "(first version in graph)"
        versions.append(v)
    return {"current_text": best_window(hist[0]["text"] or "", "", 1500) if hist[0]["until"] is None else None,
            "versions": versions, "sources": {cite: hist[0]["vid"]}}


def who_knows(topic: str, k: int = 5) -> dict:
    """People who have changed the handbook sections on a topic (ranked by number of sections touched),
    plus the teams that own those pages. Use for 'who should I ask / who knows about X'."""
    hits = rows(
        """
        CALL db.idx.fulltext.queryNodes('Section', $q) YIELD node
        WITH node LIMIT 30
        MATCH (p:Page)-[:HAS_SECTION]->(node)
        OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
        OPTIONAL MATCH (person:Person)-[:AUTHORED]->(:Commit)-[:CHANGED]->(v:Section {sid:node.sid})
        RETURN node.vid AS vid, node.sid AS sid, node.valid_to IS NULL AS current,
               collect(DISTINCT o.handle) AS owners, collect(DISTINCT person.handle) AS people
        """,
        {"q": _ft(topic)},
    )
    count: dict[str, int] = {}
    owners: set[str] = set()
    for h in hits:
        owners |= set(h["owners"])
        for p in h["people"]:
            count[p] = count.get(p, 0) + 1
    ranked = sorted(count.items(), key=lambda x: -x[1])[:k]
    cur = [h for h in hits if h["current"]]
    return {"people": [{"person": p, "sections_changed": n} for p, n in ranked],
            "owning_teams": sorted(owners), "sources": _src(cur[:3])}


def recent_changes(topic: str = "", since: str = "", limit: int = 10) -> dict:
    """Merged MRs that changed handbook sections, newest first, optionally filtered by topic words
    and an ISO date. Use for 'what changed recently / since X' questions."""
    res = rows(
        """
        MATCH (c:Commit)-[ch:CHANGED]->(s:Section)
        WHERE ($since = '' OR c.ts >= $since)
          AND ($topic = '' OR toLower(s.heading + ' ' + s.text + ' ' + c.title) CONTAINS toLower($topic))
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (a:Person)-[:AUTHORED]->(c)
        OPTIONAL MATCH (mg:Person)-[:MERGED]->(c)
        RETURN c.ts AS ts, c.title AS change, m.iid AS mr, m.url AS mr_url, a.handle AS author, mg.name AS merged_by,
               collect(DISTINCT {sid:s.sid, kind:ch.kind, vid:s.vid}) AS sections
        ORDER BY ts DESC LIMIT $n
        """,
        {"topic": topic, "since": since, "n": limit},
    )
    src = {}
    for r in res:
        for s in r["sections"]:
            src.setdefault(cite_key(s["sid"]), s["vid"])
        r["sections"] = [f"{cite_key(s['sid'])} ({s['kind']})" for s in r["sections"]][:6]
    return {"rows": res, "sources": src}


def why_changed(cite: str) -> dict:
    """Trace a policy change back to where it was DECIDED: the section's versions, the MR that changed it,
    and any meeting/chat decision implemented by that change, with the verbatim quote, speaker and timestamp.
    Use for 'why did X change / who decided / when was it decided' questions (needs a cite key)."""
    sid = sid_for(cite)
    hist = section_history(cite)
    if hist.get("error"):
        return hist
    dec = rows(
        """
        MATCH (s:Section {sid:$sid})<-[:CHANGED]-(c:Commit)<-[:IMPLEMENTED_BY]-(d:Decision)-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (d)-[e:EVIDENCED_BY]->(g:Segment)
        OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
        RETURN DISTINCT d.statement AS decision, d.decided_at AS decided_at, cv.title AS meeting, cv.source AS source,
               cv.synthetic AS synthetic, by.name AS decided_by, c.ts AS implemented_at, d.lag_days AS lag_days,
               collect({speaker:g.speaker, at:g.at, text:left(g.text, 300)})[..3] AS evidence
        """,
        {"sid": sid},
    )
    return {**hist, "decisions": dec}


def decisions_about(topic: str, k: int = 5) -> dict:
    """Decisions made in meetings/chat about a topic, with status (decided / implemented), where and when
    they were made, who decided, and the handbook section they concern. AI-extracted: cite with care."""
    res = rows(
        """
        CALL db.idx.fulltext.queryNodes('Decision', $q) YIELD node, score
        MATCH (node)-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (node)-[:ABOUT]->(s:Section)
        OPTIONAL MATCH (node)-[:IMPLEMENTED_IN_MR]->(m:MergeRequest)
        RETURN node.statement AS decision, node.status AS status, node.decided_at AS decided_at, node.quote AS quote,
               cv.title AS meeting, cv.source AS source, s.sid AS sid, s.vid AS vid, m.iid AS mr, score
        ORDER BY score DESC LIMIT $k
        """,
        {"q": _ft(topic), "k": k},
    )
    return {"rows": [{k2: v for k2, v in r.items() if k2 not in ("vid", "sid", "score")} |
                     {"about": cite_key(r["sid"]) if r["sid"] else None} for r in res],
            "sources": {cite_key(r["sid"]): r["vid"] for r in res if r["sid"]},
            "note": "decisions are AI-extracted from conversations (origin=ai_extracted, unreviewed)"}


def search_conversations(query: str, k: int = 6) -> dict:
    """Search meeting transcripts, chat threads and tickets. LOWER TRUST than the handbook: use only when the
    handbook doesn't answer, and say explicitly that the information comes from a conversation, not policy."""
    res = rows(
        """
        CALL db.idx.fulltext.queryNodes('Segment', $q) YIELD node, score
        MATCH (cv:Conversation)-[:HAS_SEGMENT]->(node)
        RETURN cv.title AS conversation, cv.source AS source, node.speaker AS speaker, node.at AS at,
               left(node.text, 600) AS text, node.gid AS gid, score
        ORDER BY score DESC LIMIT $k
        """,
        {"q": _ft(query), "k": k},
    )
    return {"rows": [{k2: v for k2, v in r.items() if k2 != "score"} for r in res], "sources": {},
            "note": "conversation content: not reviewed policy"}


def run_cypher(query: str) -> dict:
    """Read-only Cypher, validated against the live schema. Use ONLY if no other tool fits.
    Schema: (Page {path,title,url})-[:HAS_SECTION]->(Section {sid,vid,heading,text,valid_from_ts,valid_to_ts});
    (Section)-[:SUPERSEDES]->(Section); (Commit {sha,ts,title})-[:CHANGED {kind}]->(Section);
    (MergeRequest {iid,title,url})-[:MERGED_AS]->(Commit); (Person {handle})-[:AUTHORED]->(Commit); (Person {name})-[:MERGED]->(Commit);
    (Page)-[:OWNED_BY]->(Team|Person); (Page)-[:LINKS_TO]->(Page). Current sections have valid_to IS NULL.
    (Conversation {source,title,started_at})-[:HAS_SEGMENT]->(Segment {speaker,at,text});
    (Decision {statement,status,decided_at})-[:DECIDED_IN]->(Conversation); (Decision)-[:ABOUT]->(Section);
    (Decision)-[:IMPLEMENTED_BY]->(Commit); (ActionItem)-[:ASSIGNED_TO]->(Person); (Claim)-[:CONTRADICTS]->(Section)."""
    if WRITE.search(query):
        return {"error": "write operations are not allowed"}
    graph = g()
    known = {r[0] for r in graph.query("CALL db.labels()").result_set} | \
            {r[0] for r in graph.query("CALL db.relationshipTypes()").result_set}
    bad = {u for u in re.findall(r":\s*`?([A-Z]\w*)", query) if u not in known}
    if bad:
        return {"error": f"unknown labels/relationships {sorted(bad)}; valid: {sorted(known)}"}
    try:
        res = graph.ro_query(query, timeout=5000).result_set
    except Exception as e:
        return {"error": f"cypher error: {e}"}
    return {"rows": res[:50], "truncated": len(res) > 50, "sources": {}}


def systems_and_data(topic: str) -> dict:
    """Systems (e.g. Salesforce, Marketo), dashboards, fields and business metrics connected to a topic, how many
    documents rely on each, which documents and who owns them. Use for 'what systems / dashboards / fields /
    metrics does X involve', 'what would changing X affect', or when onboarding to a business area."""
    from .entities import related
    return related(topic)


TOOLS = {f.__name__: f for f in [search_handbook, page_details, section_history, why_changed, decisions_about,
                                 who_knows, recent_changes, search_conversations, systems_and_data, run_cypher]}


def _schema(fn, props: dict, required: list[str]):
    return {"type": "function", "function": {"name": fn.__name__, "description": fn.__doc__.strip(),
            "parameters": {"type": "object", "properties": props, "required": required}}}


SCHEMAS = [
    _schema(search_handbook, {"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"]),
    _schema(page_details, {"page": {"type": "string", "description": "page title, url or path"}}, ["page"]),
    _schema(section_history, {"cite": {"type": "string", "description": "cite key from another tool"}}, ["cite"]),
    _schema(who_knows, {"topic": {"type": "string"}}, ["topic"]),
    _schema(recent_changes, {"topic": {"type": "string"}, "since": {"type": "string", "description": "ISO date"},
                             "limit": {"type": "integer"}}, []),
    _schema(why_changed, {"cite": {"type": "string", "description": "cite key from another tool"}}, ["cite"]),
    _schema(decisions_about, {"topic": {"type": "string"}}, ["topic"]),
    _schema(search_conversations, {"query": {"type": "string"}}, ["query"]),
    _schema(systems_and_data, {"topic": {"type": "string"}}, ["topic"]),
    _schema(run_cypher, {"query": {"type": "string"}}, ["query"]),
]
