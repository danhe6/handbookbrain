"""Cross-source conflicts: the live wiki says one thing, a PDF on the shared drive still says another.

When any section changes (daily sync of a live source, or a new upload), compare it with its nearest
neighbours from OTHER sources (vector search), and let a strict judge decide whether they state
incompatible facts. A conflict becomes

    (loser:Section)-[:CONFLICTS_WITH {status:'open', reason, topic, found_at}]->(winner:Section)

winner = the source with higher authority (ties: the more recently changed section). Then the loop closes:
  - stored Answers that cited the loser go stale (INVALIDATED_BY, kind 'source_conflict') and get refreshed
  - the loser's owners get a Notification: update or retire the outdated file
  - retrieval shows the agent the conflict next to the losing section, so it answers from the winner
  - when either side changes again, the conflict is re-checked and auto-resolves if it no longer holds
"""
import difflib
import json
import os
import re
import time

from .config import MODEL
from .graph import q, rows
from .llm import complete

JUDGE = """You compare two passages from DIFFERENT internal sources of the same company (e.g. a live wiki page
and a PDF on a shared drive). Decide whether they state INCOMPATIBLE facts about the SAME specific thing:
a number, amount, limit, deadline, frequency, approver/contact, vendor, tool, location or eligibility rule.
It is a conflict ONLY if both passages make a claim about the very same rule for the very same case, and both
claims cannot be true at once. NOT a conflict: different topics or sub-topics (e.g. one is about approvals, the
other about submitting claims), different scope (one office / role / category vs another), one passage simply
having more or less detail, a fact present in only one passage, wording/formatting differences.
If conflict, quote the two contradicting phrases VERBATIM (copy exact contiguous text, <= 25 words each,
no "..." - pick the single sentence or clause that carries the conflicting fact).
Return JSON only: {"conflict": true | false, "topic": "<= 8 words", "a_quote": "exact text from A",
"b_quote": "exact text from B", "reason": "<= 25 words"}"""
CONFLICT_MODEL = os.getenv("HB_CONFLICT_MODEL", MODEL)  # precision matters: a false conflict invalidates answers

MIN_CHARS = 80
PAIR_MIN_SIM = 0.4
NEIGHBOURS = 3


def _section(vid: str) -> dict | None:
    r = rows(
        """
        MATCH (p:Page)-[:HAS_SECTION]->(s:Section {vid:$vid})
        RETURN s.vid AS vid, s.sid AS sid, s.text AS text, s.heading AS heading, s.embedding AS emb,
               s.valid_from_ts AS ts, p.title AS page, p.source AS source, p.source_kind AS kind,
               coalesce(p.authority, 1) AS authority
        """,
        {"vid": vid},
    )
    return r[0] if r else None


def _neighbours(sec: dict, k: int = NEIGHBOURS) -> list[dict]:
    if sec.get("emb") is None:
        return []
    hits = rows(
        """
        CALL db.idx.vector.queryNodes('Section', 'embedding', $n, vecf32($v)) YIELD node
        MATCH (p:Page)-[:HAS_SECTION]->(node)
        WHERE node.valid_to IS NULL AND coalesce(p.source, '') <> coalesce($src, '') AND p.deleted = false
        RETURN node.vid AS vid
        """,
        {"n": k * 6, "v": list(sec["emb"]), "src": sec["source"]},
    )
    return [h["vid"] for h in hits[:k]]


def _n(t: str) -> str:
    return re.sub(r"[^a-z0-9$%.,:/ ]+", "", re.sub(r"\s+", " ", (t or "").lower())).strip(" .")


def _grounded(quote: str, text: str, need: float = 0.8) -> bool:
    """Quote appears in text verbatim, or nearly (models drop bullets/quotes, PDFs break lines)."""
    t = _n(text)
    if quote in t:
        return True
    qw, tw = quote.split(), t.split()
    if not qw:
        return False
    n = len(qw)
    return any(difflib.SequenceMatcher(None, qw, tw[i:i + n], autojunk=False).ratio() >= need
               for i in range(max(1, len(tw) - n + 1)))


def _grounded_all(quote: str, text: str) -> bool:
    """Models elide with '...' even when told not to: every fragment between ellipses must be grounded."""
    parts = [_n(p) for p in re.split(r"\.{3,}|…", quote or "")]
    parts = [p for p in parts if len(p.split()) >= 2] or [_n(quote)]
    return all(_grounded(p, text) for p in parts)


def judge(a: dict, b: dict) -> dict:
    if _n(a["text"]) == _n(b["text"]):
        return {"conflict": False}  # identical copies
    resp = complete(CONFLICT_MODEL, [
        {"role": "system", "content": JUDGE},
        {"role": "user", "content": f"A ({a['source']}, {a['kind']}) - {a['page']} > {a['heading']}:\n{a['text'][:3000]}\n\n"
                                    f"B ({b['source']}, {b['kind']}) - {b['page']} > {b['heading']}:\n{b['text'][:3000]}"}],
        response_format={"type": "json_object"})
    try:
        out = json.loads(resp.choices[0].message.content)
    except Exception:
        return {"conflict": False}
    if out.get("conflict") is not True:
        return {"conflict": False}
    qa, qb = _n(out.get("a_quote")), _n(out.get("b_quote"))
    # grounding check in code: both quotes must really be in their passages, and must differ
    if not qa or not qb or qa == qb or not _grounded_all(out.get("a_quote"), a["text"]) \
            or not _grounded_all(out.get("b_quote"), b["text"]):
        return {"conflict": False, "rejected": out}
    out["a_says"], out["b_says"] = out["a_quote"], out["b_quote"]
    return out


def _winner(a: dict, b: dict) -> tuple[dict, dict]:
    if a["authority"] != b["authority"]:
        return (a, b) if a["authority"] > b["authority"] else (b, a)
    return (a, b) if (a["ts"] or "") >= (b["ts"] or "") else (b, a)


def resolve_closed(sha: str) -> int:
    """Conflicts whose sides were superseded/removed no longer hold as stated: close them."""
    r = rows(
        """
        MATCH (l:Section)-[c:CONFLICTS_WITH {status:'open'}]->(w:Section)
        WHERE l.valid_to IS NOT NULL OR w.valid_to IS NOT NULL
        SET c.status = 'resolved', c.resolved_by = $sha
        RETURN count(c) AS n
        """,
        {"sha": sha},
    )
    return r[0]["n"] if r else 0


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _n(a), _n(b), autojunk=False).ratio()


def _head_overlap(a: str, b: str) -> float:
    wa = {w for w in re.findall(r"[a-z]{3,}", (a or "").lower())}
    wb = {w for w in re.findall(r"[a-z]{3,}", (b or "").lower())}
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def counterpart(a: dict) -> dict | None:
    """The section in another source that is the SAME piece of knowledge (an export/copy/summary of it).
    Comparing a section with merely related neighbours makes the judge 'find' conflicts between two different
    rules of the same policy (approvals vs submissions) - so only the best-matching counterpart is judged."""
    best, best_s = None, 0.0
    for nb in _neighbours(a, k=8):
        b = _section(nb)
        if not b or len(b["text"] or "") < MIN_CHARS:
            continue
        s = _sim(a["text"], b["text"])
        if _head_overlap(a["heading"], b["heading"]) >= 0.6:
            s = max(s, 0.5)
        if s > best_s:
            best, best_s = b, s
    return best if best_s >= PAIR_MIN_SIM else None


def _log(rec: dict):
    from .config import RUNS
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(RUNS / "crosscheck.jsonl", "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def debug(doc: str) -> list[dict]:
    """Show, for every current section of pages whose path contains `doc`, the counterpart picked in other
    sources, the similarity and the judge's verdict (incl. rejected, ungrounded quotes)."""
    out = []
    for r in rows("MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND p.path CONTAINS $d "
                  "RETURN s.vid AS vid ORDER BY p.path, s.ord", {"d": doc}):
        a = _section(r["vid"])
        cands = []
        for nb in _neighbours(a, k=8):
            b = _section(nb)
            if b:
                cands.append((round(_sim(a["text"], b["text"]), 2), round(_head_overlap(a["heading"], b["heading"]), 2), b["sid"]))
        b = counterpart(a)
        v = judge(a, b) if b else None
        out.append({"section": a["sid"], "source": a["source"], "len": len(a["text"] or ""),
                    "candidates": sorted(cands, reverse=True)[:3], "counterpart": b["sid"] if b else None, "verdict": v})
    return out


def check_sections(vids: list[str], sha: str) -> list[dict]:
    found, seen = [], set()
    for vid in vids:
        a = _section(vid)
        if not a or len(a["text"] or "") < MIN_CHARS:
            continue
        b0 = counterpart(a)
        if b0 is None:
            _log({"sha": sha, "a": a["sid"], "b": None, "note": "no counterpart in other sources"})
        for b in filter(None, [b0]):
            key = tuple(sorted((vid, b["vid"])))
            if key in seen:
                continue
            seen.add(key)
            v = judge(a, b)
            _log({"sha": sha, "a": a["sid"], "b": b["sid"], "sim": round(_sim(a["text"], b["text"]), 2),
                  "conflict": bool(v.get("conflict")), "topic": v.get("topic"), "rejected": v.get("rejected")})
            if not v.get("conflict"):
                continue
            w, l = _winner(a, b)
            rec = {"winner": w["sid"], "loser": l["sid"], "winner_source": w["source"], "loser_source": l["source"],
                   "topic": v.get("topic", ""), "reason": v.get("reason", ""),
                   "winner_says": v.get("a_says") if w is a else v.get("b_says"),
                   "loser_says": v.get("b_says") if w is a else v.get("a_says")}
            _record(w, l, rec, sha)
            found.append(rec)
    return found


def _record(w: dict, l: dict, rec: dict, sha: str):
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    reason = (f"{l['source']} ({l['kind']}) says {rec['loser_says']!r} but {w['source']} ({w['kind']}, higher authority"
              f" or newer) says {rec['winner_says']!r}")
    q(
        """
        MATCH (l:Section {vid:$lv}), (w:Section {vid:$wv}), (cm:Commit {sha:$sha})
        MERGE (l)-[c:CONFLICTS_WITH]->(w)
        ON CREATE SET c.found_at = $sha, c.found_ts = cm.ts, c.found_epoch = cm.epoch
        SET c.status = 'open', c.topic = $topic, c.reason = $reason
        """,
        dict(lv=l["vid"], wv=w["vid"], sha=sha, now=now, topic=rec["topic"], reason=reason),
    )
    # answers that relied on the losing copy are no longer safe
    stale = rows(
        """
        MATCH (a:Answer {status:'fresh'})-[:CITES]->(:Section {vid:$lv})
        MATCH (c:Commit {sha:$sha})
        SET a.status = 'stale', a.stale_since = c.ts, a.stale_reason = $reason
        MERGE (a)-[r:INVALIDATED_BY {section:$lsid}]->(c) SET r.kind = 'source_conflict', r.reason = $reason
        RETURN a.id AS id
        """,
        dict(lv=l["vid"], lsid=l["sid"], sha=sha, reason=reason),
    )
    rec["answers_invalidated"] = len(stale)
    # whoever owns the losing file should update or retire it
    q(
        """
        MATCH (p:Page)-[:HAS_SECTION]->(l:Section {vid:$lv})
        MATCH (p)-[:OWNED_BY]->(o)
        MERGE (n:Notification {conflict:$lv + '>' + $wv, to:o.handle})
        ON CREATE SET n.created = $now, n.status = 'open'
        SET n.role = 'file_owner', n.kind = 'source_conflict', n.reason = $reason, n.commit = $sha
        WITH n, o, l
        MERGE (n)-[:ABOUT]->(l)
        MERGE (n)-[:TO]->(o)
        """,
        dict(lv=l["vid"], wv=w["vid"], now=now, reason=reason, sha=sha),
    )


def check_commit(sha: str) -> list[dict]:
    """After a sync/upload: re-check every section this change added or modified."""
    resolve_closed(sha)
    vids = [r["vid"] for r in rows(
        "MATCH (:Commit {sha:$sha})-[ch:CHANGED]->(s:Section) WHERE ch.kind <> 'removed' AND s.valid_to IS NULL "
        "RETURN s.vid AS vid", {"sha": sha})]
    return check_sections(vids, sha)


def check_all(sha: str) -> list[dict]:
    """Initial import: check every section of the lower-authority sources against the rest."""
    top = rows("MATCH (x:Source) RETURN max(x.authority) AS a")[0]["a"]
    vids = [r["vid"] for r in rows(
        "MATCH (p:Page)-[:HAS_SECTION]->(s:Section) WHERE s.valid_to IS NULL AND coalesce(p.authority, 1) < $top "
        "RETURN s.vid AS vid", {"top": top})]
    return check_sections(vids, sha)


def open_conflicts() -> list[dict]:
    return rows(
        """
        MATCH (lp:Page)-[:HAS_SECTION]->(l:Section)-[c:CONFLICTS_WITH {status:'open'}]->(w:Section)<-[:HAS_SECTION]-(wp:Page)
        OPTIONAL MATCH (lp)-[:OWNED_BY]->(o)
        RETURN c.topic AS topic, c.reason AS reason, c.found_ts AS found, l.sid AS outdated, lp.source AS outdated_source,
               lp.source_kind AS outdated_kind, w.sid AS authoritative, wp.source AS authoritative_source,
               collect(DISTINCT o.handle) AS notify ORDER BY found DESC
        """
    )
