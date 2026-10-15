"""Closed-loop core.

detect(sha)  - after a commit lands: find stored Answers that cited a section version this commit
               superseded or removed (graph traversal), then ask a judge whether the change is
               MATERIAL to that answer.
                 still_valid -> re-point CITES to the new version, record REVALIDATED_BY (nobody is bothered)
                 outdated    -> Answer.status='stale', INVALIDATED_BY + Notification to the page owners
                                (CODEOWNERS) and the MR author
refresh()    - re-answer every stale question; the new Answer SUPERSEDES the old one
retry_gaps() - re-ask questions the handbook couldn't answer; a newly answerable one = gap closed
"""
import json
import time

from .config import CRITIC_MODEL
from .graph import q, rows
from .llm import complete

JUDGE = """A user previously got ANSWER to QUESTION, citing a handbook section that has now changed.
You get the EXACT CHANGES (REMOVED = old text, ADDED = new text). Check every removed fact: if the ANSWER states,
relies on, or is computed from a removed/changed fact (a number, threshold, frequency, approver, vendor, deadline,
eligibility rule), the answer is outdated - even if the change looks small.
- "still_valid": the answer is still fully correct and complete given NEW (rewording, formatting, links, unrelated additions).
- "outdated": any fact in the answer is now wrong, incomplete in a way that matters, or the section was removed.
Return JSON only: {"verdict": "still_valid" | "outdated", "reason": "<= 25 words, concrete: what changed and why it matters"}"""


def _candidates(sha: str) -> list[dict]:
    return rows(
        """
        MATCH (c:Commit {sha:$sha})-[ch:CHANGED]->(s:Section)
        OPTIONAL MATCH (s)-[:SUPERSEDES]->(prev:Section)
        WITH c, ch, s, CASE WHEN ch.kind = 'removed' THEN s ELSE prev END AS cited
        WHERE cited IS NOT NULL
        MATCH (a:Answer {status:'fresh'})-[:CITES]->(cited)
        MATCH (a)-[:ANSWERS]->(qn:Question)
        RETURN a.id AS answer, a.text AS answer_text, qn.text AS question, ch.kind AS kind,
               cited.sid AS sid, cited.vid AS old_vid, cited.text AS old_text,
               CASE WHEN ch.kind = 'removed' THEN null ELSE s.vid END AS new_vid,
               CASE WHEN ch.kind = 'removed' THEN null ELSE s.text END AS new_text
        """,
        {"sha": sha},
    )


def judge(question: str, answer: str, changes: list[dict]) -> dict:
    """One verdict per answer per commit, over every cited section that commit touched."""
    if all(c["new_text"] is None for c in changes):
        return {"verdict": "outdated", "reason": "The cited section was removed from the handbook."}
    from .queries import _diff
    blocks = []
    for c in changes:
        if c["new_text"] is None:
            blocks.append(f"SECTION {c['sid'].split('/')[-1]}: REMOVED from the handbook")
            continue
        d = _diff(c["old_text"], c["new_text"], 3000) or "(only whitespace/formatting changed)"
        blocks.append(f"SECTION {c['sid'].split('/')[-1]} ({c['kind']})\nEXACT CHANGES:\n{d}\n\n"
                      f"NEW FULL TEXT (for context):\n{c['new_text'][:2500]}")
    resp = complete(CRITIC_MODEL, [
        {"role": "system", "content": JUDGE},
        {"role": "user", "content": f"QUESTION: {question}\n\nANSWER: {answer}\n\n" + "\n\n=====\n\n".join(blocks)}],
        response_format={"type": "json_object"})
    try:
        out = json.loads(resp.choices[0].message.content)
        if out.get("verdict") in ("still_valid", "outdated"):
            return out
    except Exception:
        pass
    return {"verdict": "outdated", "reason": "Could not verify the change; flagged for review."}


def detect(sha: str) -> list[dict]:
    by_answer: dict[str, list[dict]] = {}
    for cand in _candidates(sha):
        by_answer.setdefault(cand["answer"], []).append(cand)
    results = []
    for aid, changes in by_answer.items():
        first = changes[0]
        v = judge(first["question"], first["answer_text"], changes)
        res = {"answer": aid, "question": first["question"], "sections": [c["sid"] for c in changes],
               "kinds": sorted({c["kind"] for c in changes}), **v}
        if v["verdict"] == "still_valid":
            q(
                """
                MATCH (a:Answer {id:$aid}), (c:Commit {sha:$sha})
                UNWIND $ch AS x
                MATCH (n:Section {vid:x.new_vid})
                MERGE (a)-[:CITES {via:'revalidated'}]->(n)
                MERGE (a)-[r:REVALIDATED_BY {section:x.sid}]->(c) SET r.reason = $reason
                """,
                dict(aid=aid, sha=sha, reason=v["reason"],
                     ch=[{"new_vid": c["new_vid"], "sid": c["sid"]} for c in changes if c["new_vid"]]),
            )
        else:
            q(
                """
                MATCH (a:Answer {id:$aid}), (c:Commit {sha:$sha})
                SET a.status = 'stale', a.stale_since = c.ts, a.stale_reason = $reason
                WITH a, c
                UNWIND $ch AS x
                MERGE (a)-[r:INVALIDATED_BY {section:x.sid}]->(c) SET r.kind = x.kind, r.reason = $reason
                """,
                dict(aid=aid, sha=sha, reason=v["reason"], ch=[{"sid": c["sid"], "kind": c["kind"]} for c in changes]),
            )
            # Route: who owns the affected pages (CODEOWNERS) + who authored the change (MR branch)
            owners = rows("UNWIND $ps AS p MATCH (:Page {path:p})-[:OWNED_BY]->(o) RETURN DISTINCT o.handle AS h",
                          {"ps": sorted({c["sid"].split("#")[0] for c in changes})})
            authors = rows("MATCH (au:Person)-[:AUTHORED]->(:Commit {sha:$sha}) RETURN au.handle AS h", {"sha": sha})
            own = {o["h"] for o in owners}
            recips = [{"to": h, "role": "page_owner"} for h in sorted(own)] + \
                     [{"to": r["h"], "role": "mr_author"} for r in authors if r["h"] not in own]
            q(
                """
                MATCH (a:Answer {id:$aid})
                UNWIND $recips AS r
                MATCH (w {handle:r.to}) WHERE w:Person OR w:Team
                MERGE (n:Notification {answer:$aid, to:r.to})
                ON CREATE SET n.created = $now, n.status = 'open'
                SET n.role = r.role, n.reason = $reason, n.commit = $sha
                MERGE (n)-[:ABOUT]->(a)
                MERGE (n)-[:TO]->(w)
                """,
                dict(aid=aid, recips=recips, reason=v["reason"], sha=sha, now=time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
            res["notify"] = recips
        results.append(res)
    return results


def refresh_hint(aid: str) -> str:
    """Graph context for re-answering: why it went stale, and where the knowledge it relied on lives
    NOW (follow SUPERSEDES forward from each previously cited version to its current successor)."""
    from .queries import cite_key
    rs = rows(
        """
        MATCH (a:Answer {id:$aid})-[:CITES]->(s:Section)
        OPTIONAL MATCH (cur:Section)-[:SUPERSEDES*1..20]->(s) WHERE cur.valid_to IS NULL
        OPTIONAL MATCH (a)-[inv:INVALIDATED_BY]->(c:Commit)
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        RETURN s.sid AS was, s.valid_to IS NULL AS still_current, cur.sid AS now,
               collect(DISTINCT c.title + coalesce(' (MR !' + toString(m.iid) + ')', '')) AS changes,
               a.stale_reason AS reason
        """,
        {"aid": aid},
    )
    if not rs:
        return ""
    lines = [f"Context: this question was answered before, but the handbook changed since: {rs[0]['reason']}",
             "Changes: " + "; ".join(sorted({c for r in rs for c in r["changes"] if c})),
             "The previous answer relied on these sections; check their CURRENT versions first "
             "(page_details / search_handbook / section_history):"]
    for r in rs:
        if r["still_current"]:
            lines.append(f"- {cite_key(r['was'])} (unchanged)")
        elif r["now"]:
            lines.append(f"- {cite_key(r['was'])} -> now {cite_key(r['now'])}")
        else:
            lines.append(f"- {cite_key(r['was'])} (removed)")
    lines.append("Your answer must reflect what changed. Only say the handbook doesn't say if you verified that.")
    return "\n".join(lines)


def refresh_preload(aid: str) -> list:
    """Hand the agent the history (with diffs) of every current successor of a section the stale answer cited."""
    from .queries import cite_key
    rs = rows(
        """
        MATCH (a:Answer {id:$aid})-[:CITES]->(s:Section)
        OPTIONAL MATCH (cur:Section)-[:SUPERSEDES*1..20]->(s) WHERE cur.valid_to IS NULL
        RETURN DISTINCT coalesce(cur.sid, CASE WHEN s.valid_to IS NULL THEN s.sid END) AS sid
        """,
        {"aid": aid},
    )
    return [("section_history", {"cite": cite_key(r["sid"])}) for r in rs if r["sid"]][:4]


def refresh(verbose: bool = False) -> list[dict]:
    from .answer import ask
    stale = rows("MATCH (a:Answer {status:'stale'})-[:ANSWERS]->(qn:Question) "
                 "RETURN a.id AS id, qn.text AS question, a.text AS old, a.stale_reason AS reason")
    out = []
    for s in stale:
        new = ask(s["question"], verbose=verbose, hint=refresh_hint(s["id"]), preload=refresh_preload(s["id"]))
        q("MATCH (n:Notification)-[:ABOUT]->(:Answer)-[:ANSWERS]->(:Question {text:$q}) "
          "WHERE n.status = 'open' SET n.status = 'resolved', n.resolved_by = $id",
          {"q": s["question"], "id": new["id"]})
        out.append({"question": s["question"], "why": s["reason"], "old": s["old"], "new": new["answer"]})
    return out


def retry_gaps(verbose: bool = False) -> list[dict]:
    from .answer import ask
    gaps = rows("MATCH (qn:Question {gap:true}) RETURN qn.text AS question")
    out = []
    for g in gaps:
        r = ask(g["question"], verbose=verbose)
        out.append({"question": g["question"], "closed": r["answerable"], "answer": r["answer"]})
    return out


def inbox() -> list[dict]:
    """Per-recipient queue: what went stale, why, and the MR that caused it."""
    return rows(
        """
        MATCH (n:Notification)-[:ABOUT]->(a:Answer)-[:ANSWERS]->(qn:Question)
        MATCH (a)-[inv:INVALIDATED_BY]->(c:Commit)
        OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
        OPTIONAL MATCH (mg:Person)-[:MERGED]->(c)
        RETURN n.to AS to, n.role AS role, n.status AS status, qn.text AS question, inv.reason AS reason,
               collect(DISTINCT inv.section) AS sections, c.title AS change, m.iid AS mr, m.url AS mr_url,
               mg.name AS merged_by, c.ts AS changed_at
        ORDER BY changed_at DESC
        """
    )


def summary() -> dict:
    r = rows(
        """
        OPTIONAL MATCH (:Answer)-[rv:REVALIDATED_BY]->() WITH count(rv) AS revalidated
        OPTIONAL MATCH (:Answer)-[iv:INVALIDATED_BY]->() WITH revalidated, count(iv) AS invalidated
        OPTIONAL MATCH (a:Answer) WITH revalidated, invalidated, a.status AS st, count(a) AS n
        RETURN revalidated, invalidated, collect([st, n]) AS answers
        """
    )[0]
    gaps = rows("MATCH (qn:Question {gap:true}) RETURN count(qn) AS n")[0]["n"]
    return {**r, "open_gaps": gaps}
