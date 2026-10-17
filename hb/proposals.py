"""Human-in-the-loop updates: a decision made in a meeting/chat that no document reflects yet becomes a
drafted change, routed to the people who must approve it, with its impact computed from the graph.

  (Proposal {status:'pending'|'approved'|'rejected', new_text, diff, summary})
      -[:FROM]->(Decision)  -[:FOR]->(Section version it would replace)
      -[:NEEDS_APPROVAL_FROM]->(Team|Person)      page owners from CODEOWNERS / document owner fields

Only an OWNER's sign-off is final: anyone else can endorse or object, and that is recorded, but the proposal
stays pending until an owner decides (in production, identity comes from SSO; the demo asks who is signing).

Nothing is written to the source documents: approval records who approved and produces the patch to
merge. When the change is merged, the normal sync loop picks it up (reconcile links it to the decision,
stale detection re-checks the answers that cited the old version).
"""
import hashlib
import json
import time

from .config import MODEL
from .graph import q, rows
from .llm import complete
from .parse import cite_key

DRAFT = """You maintain an internal company document. A decision was made in a meeting or chat. Rewrite the
CURRENT SECTION so it reflects the DECISION, changing as little as possible: keep the structure, wording,
links and everything the decision does not touch exactly as they are. Do not invent facts beyond the decision.
If the decision does not apply to this section, return the section unchanged and say so in "summary".
Return JSON only: {"new_text": "...", "summary": "<= 20 words: what changes", "applies": true | false}"""


def _impact(vid: str, sid: str) -> dict:
    ents = rows(
        """
        MATCH (:Section {vid:$vid})-[:MENTIONS]->(e)
        OPTIONAL MATCH (e)<-[:MENTIONS]-(o:Section)<-[:HAS_SECTION]-(p:Page) WHERE o.valid_to IS NULL AND o.sid <> $sid
        RETURN labels(e)[0] AS type, coalesce(e.name, e.url) AS name, count(DISTINCT p) AS other_docs,
               collect(DISTINCT p.title)[..4] AS docs
        ORDER BY other_docs DESC
        """, {"vid": vid, "sid": sid})
    answers = rows("""MATCH (a:Answer {status:'fresh'})-[:CITES]->(:Section {vid:$vid})
                      MATCH (a)-[:ANSWERS]->(qn:Question) RETURN a.id AS id, qn.text AS question""", {"vid": vid})
    return {"entities": ents, "answers": answers}


def draft(decision_id: str) -> dict:
    d = rows(
        """
        MATCH (d:Decision {id:$id})-[:DECIDED_IN]->(cv:Conversation)
        OPTIONAL MATCH (d)-[:DECIDED_BY]->(by:Person)
        OPTIONAL MATCH (d)-[:ABOUT]->(a:Section)
        RETURN d.statement AS statement, d.quote AS quote, d.decided_at AS at, d.about_sid AS about_sid,
               cv.title AS where, cv.source AS src, by.name AS by, collect(DISTINCT a.sid) AS sids
        """, {"id": decision_id})
    if not d:
        return {"error": "unknown decision"}
    d = d[0]
    sids = [s for s in d["sids"] if s] or ([d["about_sid"]] if d["about_sid"] else [])
    if not sids:
        return {"error": "This decision is not linked to any document section yet."}
    from .queries import _diff, sid_for
    sid = sid_for(sids[0])
    cur = rows(
        """
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section {sid:$sid}) WHERE s.valid_to IS NULL
        RETURN s.vid AS vid, s.sid AS sid, s.heading AS heading, s.text AS text, p.path AS path, p.title AS page,
               p.url AS url, [(p)-[:OWNED_BY]->(o) | {handle:o.handle, name:coalesce(o.name, o.handle)}] AS owners
        """, {"sid": sid})
    if not cur:
        return {"error": "The linked section no longer exists."}
    s = cur[0]
    resp = complete(MODEL, [{"role": "system", "content": DRAFT},
                            {"role": "user", "content": f"DECISION ({d['at'][:10]}, {d['where']}): {d['statement']}\n"
                                                        f"QUOTE: {d['quote'] or ''}\n\nCURRENT SECTION '{s['heading']}' "
                                                        f"of '{s['page']}':\n{s['text'][:6000]}"}],
                    response_format={"type": "json_object"})
    try:
        out = json.loads(resp.choices[0].message.content)
    except Exception:
        return {"error": "Could not draft an update."}
    new = out.get("new_text") or s["text"]
    pid = hashlib.sha1(f"{decision_id}{s['vid']}".encode()).hexdigest()[:12]
    diff = _diff(s["text"], new, 4000)
    approvers = s["owners"] or []
    q(
        """
        MATCH (d:Decision {id:$did}), (s:Section {vid:$vid})
        MERGE (pr:Proposal {id:$id})
        SET pr.new_text=$new, pr.diff=$diff, pr.summary=$summary, pr.applies=$applies, pr.status='pending',
            pr.signoffs='[]', pr.decided_by=NULL, pr.decided_at=NULL, pr.created=$now, pr.origin='ai_drafted'
        MERGE (pr)-[:FROM]->(d) MERGE (pr)-[:FOR]->(s)
        WITH pr
        UNWIND $owners AS h
        MATCH (o {handle:h}) WHERE o:Team OR o:Person
        MERGE (pr)-[:NEEDS_APPROVAL_FROM]->(o)
        """,
        dict(did=decision_id, vid=s["vid"], id=pid, new=new, diff=diff, summary=out.get("summary", ""),
             applies=bool(out.get("applies", True)), now=time.strftime("%Y-%m-%dT%H:%M:%S"),
             owners=[o["handle"] for o in approvers]),
    )
    return {"id": pid, "status": "pending", "decision": d, "section": {k: s[k] for k in ("heading", "page", "path", "url")} |
            {"cite": cite_key(s["sid"])}, "summary": out.get("summary"), "applies": out.get("applies", True), "diff": diff,
            "new_text": new, "approvers": approvers, "impact": _impact(s["vid"], s["sid"])}


def _norm(x: str) -> str:
    return (x or "").strip().lstrip("@").lower()


def decide(pid: str, status: str, by: str = "") -> dict:
    """Record a sign-off. Only a page OWNER (the people / teams the proposal NEEDS_APPROVAL_FROM) makes it final.
    Anyone else's approve / reject is kept as an endorsement or an objection, and the proposal stays pending until
    an owner decides. With no owner on record, the first sign-off decides (and says so).
    status 'pending' resets the proposal (demo / undo)."""
    if status == "pending":
        q("MATCH (pr:Proposal {id:$id}) SET pr.status='pending', pr.signoffs='[]', pr.decided_by=NULL, pr.decided_at=NULL",
          {"id": pid})
        return (listing(pid) or [{"error": "unknown proposal"}])[0]
    if status not in ("approved", "rejected"):
        return {"error": "status must be approved, rejected or pending"}
    by = (by or "").strip()
    if not by:
        return {"error": "Say who is signing off."}
    cur = rows("""MATCH (pr:Proposal {id:$id})
                  RETURN pr.status AS status, pr.signoffs AS signoffs,
                         [(pr)-[:NEEDS_APPROVAL_FROM]->(o) | [coalesce(o.handle, ''), coalesce(o.name, '')]] AS owners""",
               {"id": pid})
    if not cur:
        return {"error": "unknown proposal"}
    cur = cur[0]
    owner_keys = {_norm(x) for pair in cur["owners"] for x in pair if x}
    no_owner = not owner_keys
    is_owner = no_owner or _norm(by) in owner_keys
    signoffs = json.loads(cur["signoffs"] or "[]")
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    signoffs.append({"by": by, "vote": "approve" if status == "approved" else "reject",
                     "role": "owner" if is_owner and not no_owner else ("no owner on record" if no_owner else "not an owner"),
                     "at": now})
    if is_owner and cur["status"] == "pending":
        q("MATCH (pr:Proposal {id:$id}) SET pr.status=$st, pr.decided_by=$by, pr.decided_at=$now, pr.signoffs=$so",
          {"id": pid, "st": status, "by": by, "now": now, "so": json.dumps(signoffs)})
    else:
        q("MATCH (pr:Proposal {id:$id}) SET pr.signoffs=$so", {"id": pid, "so": json.dumps(signoffs)})
    return (listing(pid) or [{"error": "unknown proposal"}])[0]


def reset_all() -> int:
    r = rows("MATCH (pr:Proposal) WHERE pr.status <> 'pending' OR pr.decided_by IS NOT NULL "
             "OR coalesce(pr.signoffs, '[]') <> '[]' "
             "SET pr.status='pending', pr.signoffs='[]', pr.decided_by=NULL, pr.decided_at=NULL RETURN count(pr) AS n")
    return r[0]["n"] if r else 0


def listing(pid: str | None = None) -> list[dict]:
    res = rows(
        """
        MATCH (pr:Proposal)-[:FROM]->(d:Decision) WHERE $id IS NULL OR pr.id = $id
        MATCH (pr)-[:FOR]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        OPTIONAL MATCH (d)-[:DECIDED_IN]->(cv:Conversation)
        RETURN pr.id AS id, pr.status AS status, pr.summary AS summary, pr.diff AS diff, pr.created AS created,
               pr.decided_by AS decided_by, pr.decided_at AS decided_at, d.id AS decision_id, d.statement AS decision,
               cv.title AS where, s.sid AS sid, s.vid AS vid, s.heading AS heading, p.title AS page, p.path AS path,
               pr.signoffs AS signoffs,
               [(pr)-[:NEEDS_APPROVAL_FROM]->(o) | coalesce(o.name, o.handle)] AS approvers
        ORDER BY created DESC
        """, {"id": pid})
    for r in res:
        r["cite"] = cite_key(r["sid"])
        r["signoffs"] = json.loads(r.get("signoffs") or "[]")
        r["endorsements"] = sum(1 for x in r["signoffs"] if x["role"] == "not an owner" and x["vote"] == "approve")
        r["objections"] = sum(1 for x in r["signoffs"] if x["role"] == "not an owner" and x["vote"] == "reject")
        r["impact"] = _impact(r.pop("vid"), r.pop("sid"))
    return res
