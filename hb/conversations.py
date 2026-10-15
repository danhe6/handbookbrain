"""Meetings / chat / tickets -> graph, and the knowledge extracted from them.

  (Conversation)-[:HAS_SEGMENT]->(Segment)-[:SAID_BY]->(Person {name})
  (AISummary {origin:'ai_summary', review_status:'unreviewed'})-[:DERIVED_FROM]->(Conversation)
  (Decision {origin:'ai_extracted'})-[:EVIDENCED_BY {quote}]->(Segment)
  (Decision)-[:DECIDED_IN]->(Conversation), (Decision)-[:DECIDED_BY]->(Person)
  (Decision)-[:ABOUT]->(Section)                       linked by search + LLM pick, as of the decision date
  (Decision)-[:IMPLEMENTED_BY {verified}]->(MergeRequest)   reconciled whenever a later commit changes that section
  (ActionItem)-[:FROM]->(Decision), (ActionItem)-[:ASSIGNED_TO]->(Person)
  (Claim)-[:CONTRADICTS {reason}]->(Section)           someone states a rule that the CURRENT handbook contradicts
  (Question)-[:ASKED_IN]->(Conversation)               questions people actually asked -> answered / gap / tribal knowledge

Conversations are ingested in time order, interleaved with commits during replay, so every link is
made against the handbook AS IT WAS when the conversation happened.
"""
import hashlib
import json
import os
import time

from .adapters import Conversation
from .config import CRITIC_MODEL, MODEL
from .graph import q, rows
from .llm import complete
from .queries import cite_key, search_handbook

EXTRACT = """You extract durable company knowledge from a workplace conversation (meeting transcript, chat thread,
or helpdesk ticket). Segment ids look like [g12]. Only extract what is actually in the text.

- decisions: a group or an authorized person settles on a change or rule ("we'll go with...", "decided", "approved",
  "from next month X"). NOT proposals that were left open. Include the segment ids that show the decision.
- action_items: concrete follow-ups with an owner.
- claims: statements presented as FACT about how company policy/benefits/process CURRENTLY are
  (e.g. "the employer match is 5%"), INCLUDING answers people give to a colleague's question
  ("it's 25 days paid", "you need VP approval"). Do NOT include proposals, options being discussed, planned changes, or
  anything that is part of a decision ("we'll change it to 3%") - those are decisions, not claims.
  Skip opinions and questions.
- questions: genuine information-seeking questions about company policy, benefits or HR/People process that an
  employee could look up. Skip small talk, rhetorical questions, and internal editing/coordination questions
  ("should we update the table?", "who's taking this?"). answered_in_conversation=true only if someone gave a
  substantive answer; put that answer in "answer".
For each decision/claim, "policy_query" is a short search query to find the relevant handbook policy.
changes_policy=true only if the decision changes a written company policy, benefit, rule or process that belongs in
the handbook (amounts, eligibility, deadlines, providers, required steps). Operational to-dos (renew a vendor,
draft an FAQ, schedule a meeting) are false.

Return JSON only:
{"summary": "2-3 sentences",
 "decisions": [{"statement": str, "decided_by": str, "evidence": ["g3"], "quote": "short verbatim quote", "policy_query": str,
                "changes_policy": bool}],
 "action_items": [{"task": str, "owner": str, "due": "YYYY-MM-DD or null", "decision_index": int or null, "evidence": ["g9"]}],
 "claims": [{"statement": str, "speaker": str, "evidence": ["g4"], "policy_query": str}],
 "questions": [{"question": str, "asker": str, "is_policy_question": bool, "answered_in_conversation": bool,
                "answer": str or null, "evidence": ["g1"]}]}
is_policy_question=false for small talk, tech glitches ("is Slack down?"), social plans, and coordination."""

PICK = """Which handbook section (if any) is this statement about? Pick the single best match, or none if no
candidate is clearly about the same policy. Return JSON: {"pick": <candidate number or null>, "confidence": 0-1}"""

CONFLICT = """Compare a statement someone made in chat/meeting with the CURRENT handbook section.
- "contradicts": ONLY if the statement asserts a specific fact (a number, amount, duration, date, threshold, named
  person/role/vendor, or yes/no eligibility rule) that is directly INCOMPATIBLE with what the section says.
- "consistent": agrees with, paraphrases, simplifies, or only partially covers the section. Vague, approximate,
  incomplete or hedged statements ("I think it's in the handbook", "you need approval") are consistent.
- "unrelated": the section doesn't cover what the statement says.
When unsure, do not say "contradicts".
Return JSON: {"verdict": "consistent" | "contradicts" | "unrelated", "reason": "<= 25 words"}"""

IMPLEMENTS = """A decision was made in a meeting/chat. Later the handbook changed; you get the exact DIFF
(REMOVED = old text, ADDED = new text). Does this diff implement the CORE of the decision (the policy change itself:
the new amount, rule, eligibility, provider, process)? Side items (links, FAQs, wording) may be missing.
If the diff is about something else (other topic, only links/formatting), answer false.
Return JSON: {"implemented": bool, "reason": "<= 20 words"}"""


def _json(model: str, system: str, user: str) -> dict:
    resp = complete(model, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    response_format={"type": "json_object"})
    try:
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return {}


def _id(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:12]


# ------------------------------------------------------------------ write conversation

def write_conversation(c: Conversation):
    q(
        """
        MERGE (cv:Conversation {cid:$cid})
        SET cv.source=$source, cv.kind=$kind, cv.title=$title, cv.started_at=$at, cv.epoch=$epoch,
            cv.channel=$channel, cv.synthetic=$syn, cv.origin=$origin, cv.n_segments=$n
        WITH cv
        UNWIND $segs AS s
        MERGE (g:Segment {gid: $cid + '#' + toString(s.idx)})
        SET g.idx=s.idx, g.speaker=s.speaker, g.at=s.at, g.text=s.text, g.cid=$cid
        MERGE (cv)-[:HAS_SEGMENT]->(g)
        MERGE (p:Person {name:s.speaker})
        ON CREATE SET p.synthetic=$syn
        MERGE (g)-[:SAID_BY]->(p)
        MERGE (p)-[:PARTICIPATED_IN]->(cv)
        """,
        dict(cid=c.cid, source=c.source, kind=c.kind, title=c.title, at=c.started_at, epoch=c.epoch,
             channel=c.channel, syn=c.synthetic, n=len(c.segments), origin=c.origin,
             segs=[{"idx": s.idx, "speaker": s.speaker, "at": s.at, "text": s.text[:4000]} for s in c.segments]),
    )


def _transcript(c: Conversation, limit: int = 60000) -> str:
    lines = [f"[g{s.idx}] {s.speaker}: {s.text}" for s in c.segments]
    return f"{c.kind.upper()} '{c.title}' ({c.source}, {c.started_at})\n" + "\n".join(lines)[:limit]


def _link_section(statement: str, query: str) -> dict | None:
    """Hybrid search (current versions = as of the conversation date during replay) + LLM pick."""
    hits = search_handbook(f"{query} {statement}", k=5)
    cands = hits["rows"]
    if not cands:
        return None
    listing = "\n\n".join(f"[{i + 1}] {h['page']} > {h['heading']}\n{h['text'][:700]}" for i, h in enumerate(cands))
    out = _json(CRITIC_MODEL, PICK, f"STATEMENT: {statement}\n\nCANDIDATES:\n{listing}")
    i = out.get("pick")
    if not isinstance(i, int) or not 1 <= i <= len(cands) or (out.get("confidence") or 0) < 0.5:
        return None
    h = cands[i - 1]
    return {"vid": hits["sources"][h["cite"]], "cite": h["cite"], "text": h["text"], "confidence": out.get("confidence")}


def _candidate_paths(statement: str, query: str) -> list[str]:
    hits = search_handbook(f"{query} {statement}", k=6)
    return sorted({h["path"] for h in hits["rows"] if h.get("path")})


def _gid(c: Conversation, ev: list) -> list[str]:
    ok = {s.idx for s in c.segments}
    out = []
    for e in ev or []:
        try:
            n = int(str(e).lstrip("[g").rstrip("]"))
        except ValueError:
            continue
        if n in ok:
            out.append(f"{c.cid}#{n}")
    return out


# ------------------------------------------------------------------ extract + link

def process(c: Conversation, answer_questions: bool = True, verbose: bool = False) -> dict:
    write_conversation(c)
    ex = _json(MODEL, EXTRACT, _transcript(c))
    stats = {"decisions": 0, "linked": 0, "action_items": 0, "claims": 0, "conflicts": 0, "questions": 0,
             "gaps": 0, "tribal": 0}

    q(
        """
        MATCH (cv:Conversation {cid:$cid})
        MERGE (s:AISummary {id:$id})
        SET s.text=$text, s.origin='ai_summary', s.review_status='unreviewed', s.model=$model, s.at=$at
        MERGE (s)-[:DERIVED_FROM]->(cv)
        """,
        dict(cid=c.cid, id=_id("sum", c.cid), text=ex.get("summary", ""), model=MODEL, at=c.started_at),
    )

    dec_ids = []
    for i, d in enumerate(ex.get("decisions") or []):
        did = _id("dec", c.cid, i)
        dec_ids.append(did)
        link = _link_section(d["statement"], d.get("policy_query", ""))
        cand_paths = _candidate_paths(d["statement"], d.get("policy_query", ""))
        q(
            """
            MATCH (cv:Conversation {cid:$cid})
            MERGE (d:Decision {id:$id})
            SET d.statement=$st, d.quote=$quote, d.decided_at=$at, d.epoch=$epoch, d.status='decided',
                d.origin='ai_extracted', d.review_status='unreviewed', d.synthetic=$syn, d.about_sid=$sid,
                d.cand_paths=$cand_paths, d.changes_policy=$cp
            MERGE (d)-[:DECIDED_IN]->(cv)
            MERGE (p:Person {name:$by}) MERGE (d)-[:DECIDED_BY]->(p)
            WITH d
            UNWIND $gids AS gid
            MATCH (g:Segment {gid:gid})
            MERGE (d)-[e:EVIDENCED_BY]->(g) SET e.quote=$quote
            """,
            dict(cid=c.cid, id=did, st=d["statement"], quote=d.get("quote", ""), at=c.started_at, epoch=c.epoch,
                 syn=c.synthetic, by=d.get("decided_by") or "unknown", gids=_gid(c, d.get("evidence")),
                 sid=link["cite"] if link else None, cand_paths=cand_paths, cp=bool(d.get("changes_policy", True))),
        )
        stats["decisions"] += 1
        if link:
            q("MATCH (d:Decision {id:$id}), (s:Section {vid:$vid}) MERGE (d)-[r:ABOUT]->(s) SET r.confidence=$cf",
              {"id": did, "vid": link["vid"], "cf": link["confidence"]})
            stats["linked"] += 1
        if verbose:
            print(f"   DECISION {d['statement'][:90]}  -> {link['cite'] if link else 'unlinked'}")

    for i, a in enumerate(ex.get("action_items") or []):
        di = a.get("decision_index")
        q(
            """
            MERGE (ai:ActionItem {id:$id})
            SET ai.task=$task, ai.due=$due, ai.status='open', ai.origin='ai_extracted', ai.created=$at
            MERGE (p:Person {name:$owner}) MERGE (ai)-[:ASSIGNED_TO]->(p)
            WITH ai
            MATCH (cv:Conversation {cid:$cid}) MERGE (ai)-[:RAISED_IN]->(cv)
            WITH ai
            OPTIONAL MATCH (d:Decision {id:$did})
            FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (ai)-[:FROM]->(d))
            """,
            dict(id=_id("ai", c.cid, i), task=a["task"], due=a.get("due"), owner=a.get("owner") or "unknown",
                 at=c.started_at, cid=c.cid,
                 did=dec_ids[di] if isinstance(di, int) and 0 <= di < len(dec_ids) else ""),
        )
        stats["action_items"] += 1

    decided_sids = {r["cite"] for r in rows(
        "MATCH (d:Decision)-[:DECIDED_IN]->(:Conversation {cid:$cid}) MATCH (d)-[:ABOUT]->(s:Section) RETURN s.cite AS cite",
        {"cid": c.cid})}
    for i, cl in enumerate(ex.get("claims") or []):
        link = _link_section(cl["statement"], cl.get("policy_query", ""))
        stats["claims"] += 1
        if not link or link["cite"] in decided_sids:
            continue  # no policy match, or it restates a proposal decided in this same conversation
        v = _json(CRITIC_MODEL, CONFLICT, f"STATEMENT: {cl['statement']}\n\nCURRENT SECTION:\n{link['text']}")
        if v.get("verdict") != "contradicts":
            continue
        stats["conflicts"] += 1
        q(
            """
            MATCH (cv:Conversation {cid:$cid}), (s:Section {vid:$vid})
            MERGE (k:Claim {id:$id})
            SET k.statement=$st, k.speaker=$spk, k.at=$at, k.origin='ai_extracted'
            MERGE (k)-[:STATED_IN]->(cv)
            MERGE (k)-[r:CONTRADICTS]->(s) SET r.reason=$reason
            WITH k
            UNWIND $gids AS gid MATCH (g:Segment {gid:gid}) MERGE (k)-[:EVIDENCED_BY]->(g)
            """,
            dict(cid=c.cid, vid=link["vid"], id=_id("claim", c.cid, i), st=cl["statement"],
                 spk=cl.get("speaker", ""), at=c.started_at, reason=v.get("reason", ""), gids=_gid(c, cl.get("evidence"))),
        )
        if verbose:
            print(f"   CONFLICT {cl['statement'][:80]} vs {link['cite']}: {v.get('reason')}")

    if answer_questions and c.kind in ("thread", "ticket") and c.source != "email":  # employee questions only
        from .answer import ask
        sample = float(os.getenv("HB_ANSWER_SAMPLE", "1"))  # dev mode: answer only a deterministic subset
        for qu in ex.get("questions") or []:
            if not qu.get("is_policy_question", True):
                continue
            if sample < 1 and int(hashlib.sha1(qu["question"].encode()).hexdigest(), 16) % 1000 >= sample * 1000:
                continue
            stats["questions"] += 1
            r = ask(qu["question"])
            q(
                """
                MATCH (qn:Question {text:$q}), (cv:Conversation {cid:$cid})
                MERGE (qn)-[r:ASKED_IN]->(cv)
                SET r.answered_in_conversation=$aic, r.chat_answer=$chat_answer, r.at=$at
                SET qn.times_asked_in_conversations = coalesce(qn.times_asked_in_conversations, 0) + 1
                """,
                dict(q=qu["question"], cid=c.cid, aic=bool(qu.get("answered_in_conversation")),
                     chat_answer=qu.get("answer"), at=c.started_at),
            )
            if not r["answerable"]:
                stats["gaps"] += 1
                if qu.get("answered_in_conversation") and qu.get("answer"):
                    stats["tribal"] += 1  # the answer exists only in chat -> candidate handbook addition
                    q("MATCH (qn:Question {text:$q}) SET qn.tribal = true", {"q": qu["question"]})
    return stats


# ------------------------------------------------------------------ reconcile decisions with commits

def reconcile(sha: str) -> list[dict]:
    """After a commit: for open decisions about a section this commit changed (and decided before it),
    check whether the new text implements the decision."""
    cands = rows(
        """
        MATCH (c:Commit {sha:$sha})-[:CHANGED]->(n:Section)
        MATCH (d:Decision {status:'decided'})
        WHERE d.epoch <= c.epoch AND c.epoch - d.epoch < 90 * 86400 AND coalesce(d.changes_policy, true)
        OPTIONAL MATCH (d)-[:ABOUT]->(ab:Section)
        WITH c, n, d, collect(ab.sid) AS about
        WHERE n.sid IN about OR n.path IN coalesce(d.cand_paths, [])
        OPTIONAL MATCH (n)-[:SUPERSEDES]->(prev:Section)
        WITH d, c, collect(n.heading + ':\n' + n.text)[..4] AS news, collect(coalesce(prev.text, ''))[..4] AS olds
        RETURN d.id AS did, d.statement AS st, reduce(t = '', x IN news | t + x + '\n---\n') AS new,
               reduce(t = '', x IN olds | t + x + '\n---\n') AS old, c.epoch AS epoch
        """,
        {"sha": sha},
    )
    out = []
    for x in cands:
        from .queries import _diff
        diff = _diff(x["old"], x["new"], 4000) or "(no textual difference)"
        v = _json(CRITIC_MODEL, IMPLEMENTS, f"DECISION: {x['st']}\n\nDIFF:\n{diff}")
        if not v.get("implemented"):
            continue
        q(
            """
            MATCH (d:Decision {id:$did}), (c:Commit {sha:$sha})
            SET d.status='implemented', d.implemented_at=c.ts, d.lag_days=round(($epoch - d.epoch) / 86400.0)
            MERGE (d)-[r:IMPLEMENTED_BY]->(c) SET r.reason=$reason
            WITH d, c
            OPTIONAL MATCH (m:MergeRequest)-[:MERGED_AS]->(c)
            FOREACH (_ IN CASE WHEN m IS NULL THEN [] ELSE [1] END | MERGE (d)-[:IMPLEMENTED_IN_MR]->(m))
            WITH d
            OPTIONAL MATCH (ai:ActionItem)-[:FROM]->(d)
            FOREACH (a IN CASE WHEN ai IS NULL THEN [] ELSE [ai] END | SET a.status='done')
            """,
            dict(did=x["did"], sha=sha, reason=v.get("reason", ""), epoch=x["epoch"]),
        )
        out.append({"decision": x["st"], "reason": v.get("reason")})
    return out


def unimplemented(now_epoch: float, after_days: int = 21) -> list[dict]:
    """Decisions older than N days with no handbook change implementing them."""
    return rows(
        """
        MATCH (d:Decision {status:'decided'})-[:DECIDED_IN]->(cv:Conversation)
        WHERE d.epoch < $cutoff AND coalesce(d.changes_policy, true)
        OPTIONAL MATCH (d)-[:ABOUT]->(s:Section)<-[:HAS_SECTION]-(p:Page)
        OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
        OPTIONAL MATCH (ai:ActionItem)-[:FROM]->(d)
        OPTIONAL MATCH (ai)-[:ASSIGNED_TO]->(who:Person)
        RETURN d.id AS decision, d.statement AS statement, d.decided_at AS decided_at, cv.title AS meeting,
               cv.source AS source, d.about_sid AS section, collect(DISTINCT o.handle) AS page_owners,
               collect(DISTINCT who.name) AS assignees, round(($now - d.epoch) / 86400.0) AS days_open
        ORDER BY days_open DESC
        """,
        {"cutoff": now_epoch - after_days * 86400, "now": now_epoch},
    )
