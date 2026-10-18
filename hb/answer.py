"""Agent: tool-calling loop over the graph -> cited draft -> citation check (code) -> independent critic
(different model, sees only question + raw evidence) -> Answer node that CITES exact section versions.

Storing the answer in the graph is what closes the loop: when a later commit supersedes a cited
section version, stale.detect() finds this answer by traversal."""
import hashlib
import json
import re
import time

from . import queries
from .config import COMPANY_NAME, CRITIC_MODEL, MODEL, RUNS
from .graph import get_meta, q
from .llm import USAGE, complete

MAX_STEPS = 10
TOOL_RESULT_CHARS = int(__import__("os").getenv("HB_TOOL_CHARS", "9000"))  # every step re-sends earlier results
MAX_REVISIONS = 2

SYSTEM = f"""You are HandbookBrain, an assistant that knows how {COMPANY_NAME} (the company) works, using ONLY its
internal documentation (the "handbook") via the tools provided.

Tool routing:
- Keep the question's specific numbers, names and terms (e.g. "10 days", "31 points") in your search queries.
- Policy / process / "how do I" questions: search_handbook at least TWICE with different phrasings, one of
  them naming the policy document you expect it to be in (e.g. "remote work stipend ineligible expenses",
  "data retention deletion schedule"). Questions are often vague; the right section may be in a policy whose
  name is not in the question. Prefer the section that answers the exact question over a merely related one: when a
  general rule and a rule for the specific case asked about both appear (e.g. the general MQL threshold vs the trial
  threshold), answer with the specific one.
- A named page or policy, or "who owns X": page_details.
- "What was the old rule / when did this change": section_history (needs a cite key from another tool).
- "Why did this change / who decided / in which meeting": why_changed (cite key) and decisions_about. When they
  return a linked decision, the answer MUST include it: what was decided, who decided it, in which meeting or chat,
  on what date, the quote, and the change (MR) that implemented it; say it comes from the meeting record. Do not
  write that the reason or the decision-maker is unknown when a linked decision was returned.
- When several decisions match, use the one whose ACTION matches the question ("added / introduced" vs "changed /
  raised / removed") and whose date fits; mention the others only if they are also part of the answer.
- Use a decision only if it is about the SAME subject as the question (the same rule, field, term or number). decisions_about
  rows are ranked by "match"; if none is about the question's subject, say no meeting decision was found and answer from the docs.
- Never add a reason, motive or benefit that the evidence does not state (no "to improve clarity", "to streamline"). If the
  evidence gives no reason beyond the decision itself, say what was decided and by whom, and stop.
- "What changed recently / since <date>": recent_changes.
- "Who should I ask / who knows about X": who_knows, and name the owning team.
- Decisions you were given up front (decisions_about) never replace the documents: unless the question is only about
  why / who decided, also search_handbook for the current documented rule, answer from it first, then add the decision
  and say whether it is documented. Cite a decision with its "about" cite key, never with a tool name.
- "Which systems does team X own / use / run on", "which systems / dashboards / fields / metrics does X involve",
  "what would a change to X affect", or
  getting oriented in a business area: systems_and_data (plus search_handbook for the rules themselves).
- Only if the handbook has no answer: search_conversations (always try it before saying the handbook doesn't say).
  Then say clearly the info comes from a conversation (meeting/chat/ticket), not from approved policy, and name
  where it was said.
- "Is X still the rule / does X still apply" and the current text no longer mentions X: look at
  older_versions_that_said_more in search_handbook results, or section_history of the most relevant section. If an
  older version said it, answer: it used to say X, it was removed or changed on <date> by <MR>, and what it says now.
- "Was decision D written into the handbook / is it in the handbook yet": compare the decision with the CURRENT text
  of the section it is about and say plainly yes or no, quoting what the handbook says now. Read tables row by row
  ("Tier 1: ...; Tier 2: ...") and take the value from the exact row and column asked about.
- run_cypher only when nothing else fits.

Answer rules:
- Answer exactly what was asked, in order, concisely. Copy numbers and lists exactly from the evidence.
- Cite after every claim with cite keys from the tools' "sources", e.g. [people-group/paid-time-off#pto].
- When it helps the reader act, end with "Owner: <team or handles>", but ONLY owners that appear in the tool results
  (page owners, who_knows). Never name a team that the evidence does not mention.
- Never infer or imply an answer that the evidence does not state (no "which implies it is the source of truth").
- Sources differ in trust: live, maintained pages beat static files (PDF/DOCX uploaded once). If a result carries
  a WARNING (outdated copy) or two sources disagree, answer from the authoritative/newer one and add one line
  naming the outdated file, e.g. "Note: <file> (uploaded <date>) still says <old value>; it is outdated."
- If the evidence does not DIRECTLY answer the main question, begin your reply with exactly
  "The handbook doesn't say." Exception: for why / who-decided questions, a linked decision about the same subject
  IS the answer; then do not begin with that sentence. If the record gives no motive beyond the decision itself, state
  the decision (what, who, where, when, quote, MR) and end with "The meeting record gives no further reason." You may then add closely related facts (with citations) and who to ask.
  Never guess or use outside knowledge."""

CRITIC = """You are a strict reviewer. You get a QUESTION, the raw EVIDENCE returned by tools, and a DRAFT answer.
Using only the evidence (not your own knowledge), check:
1. Every part of the question is answered. If the draft says the handbook doesn't say, check the evidence:
   if the evidence does contain the answer, return "revise" and say which tool result contains it.
2. Every factual claim (numbers, conditions, names) is supported by the evidence it cites.
3. Nothing is padded with content the question did not ask for.
Decisions returned by why_changed / decisions_about (statement, decided_by, meeting, date, quote, MR) ARE evidence:
a draft may state them; ask for them to be included if the question asks why or who decided and the draft omits them,
but ONLY a decision about the change the question asks about (e.g. its numbers or rule match the question). Never ask
the draft to present an unrelated or earlier decision as the reason, and never ask it to add a reason or motive the
evidence does not state. Match the ACTION: if the question asks why something was added / introduced, the decision that
added it is the answer and a LATER decision that changed it (e.g. raised a number) is not; if it asks why something
changed, the decision that made that change is the answer. If the draft already states a decision from the evidence,
never ask it to replace it with, or add, a different decision: only ask for a decision when the draft names none.
Return JSON: {"verdict": "pass" | "revise", "issues": ["short, specific instruction", ...]}.
Use "revise" only for real problems. Never state the correct answer yourself."""

CITE_RE = re.compile(r"\[([^\[\]]+)\]")
ABSTAIN = "The handbook doesn't say."
ABSTAIN_RE = re.compile(r"^\W*((the\s+)?(handbook|documentation|docs|evidence|policy)\s+(doesn'?t|does\s+not|do\s+not)\s+"
                        r"(say|specify|mention|cover|state|provide|include|address|contain)"
                        r"|(there\s+is\s+)?no\s+(relevant\s+|specific\s+)?(information|details|documentation|mention|policy|figure|data|record|reference)\b[^.]{0,80}"
                        r"\b(found|available|in\s+the\s+(handbook|documentation)))", re.I)


def abstains(text: str) -> bool:
    return bool(ABSTAIN_RE.match(text or ""))


def _cites(text: str) -> list[str]:
    out = []
    for grp in CITE_RE.findall(text or ""):
        out += [x.strip() for x in re.split(r"[,;]\s*", grp) if "#" in x]
    return out


def check_citations(draft: str, allowed: dict) -> list[str]:
    if abstains(draft):
        return []
    cited = _cites(draft)
    if not cited:
        return [f"Add [cite-key] citations from the tools' sources after each claim (a cite key looks like "
                "[area/page#section]; for a decision use its 'about' key; never a tool name). If the evidence does not directly "
                f"answer the question, begin your reply with exactly \"{ABSTAIN}\" instead, and do not name an owner or "
                "team that the evidence does not mention."]
    bad = [c for c in cited if c not in allowed]
    return [f"Invalid citations (not in tool results): {bad[:5]}. Use only keys from 'sources'."] if bad else []


def _evidence(evidence: str, limit: int = 60000) -> str:
    """Keep the critic's view of the evidence within budget without dropping the LAST tool results
    (e.g. why_changed, which carries the decision): if too long, keep the head and the tail."""
    if len(evidence) <= limit:
        return evidence
    return evidence[: limit // 3] + "\n[...]\n" + evidence[-(limit * 2 // 3):]


def critic(question: str, evidence: str, draft: str) -> list[str]:
    resp = complete(CRITIC_MODEL, [
        {"role": "system", "content": CRITIC},
        {"role": "user", "content": f"QUESTION:\n{question}\n\nEVIDENCE:\n{_evidence(evidence)}\n\nDRAFT:\n{draft}"}],
        response_format={"type": "json_object"})
    try:
        out = json.loads(resp.choices[0].message.content)
        return [] if out.get("verdict") == "pass" else (out.get("issues") or ["revise"])
    except Exception:
        return []


def run_agent(question: str, verbose: bool = False, hint: str = "", preload: list | None = None) -> dict:
    """preload: [(tool_name, args)] executed up front and shown to the model as tool results it already has
    (used by refresh to hand over the successor sections of what the stale answer relied on)."""
    user = question + (f"\n\n---\n{hint}" if hint else "")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    trace, evidence, allowed, revisions = [], [], {}, []
    if preload:
        calls = []
        for i, (name, args) in enumerate(preload):
            try:
                result = queries.TOOLS[name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            allowed.update(result.get("sources") or {})
            payload = json.dumps({k: v for k, v in result.items() if k != "sources"} |
                                 {"sources": list((result.get("sources") or {}).keys())}, default=str)[:TOOL_RESULT_CHARS]
            evidence.append(f"{name}({args}) -> {payload}")
            trace.append({"step": "tool", "tool": name, "args": args, "preloaded": True,
                          "n_sources": len(result.get("sources") or {}), "error": result.get("error")})
            calls.append((f"pre_{i}", name, args, payload))
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for cid, n, a, _ in calls]})
        messages += [{"role": "tool", "tool_call_id": cid, "content": pl} for cid, _, _, pl in calls]
    for _ in range(MAX_STEPS):
        msg = complete(MODEL, messages, tools=queries.SCHEMAS, tool_choice="auto").choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            draft = (msg.content or "").strip()
            searches = sum(1 for t in trace if t.get("step") == "tool")
            used = {t.get("tool") for t in trace if t.get("step") == "tool"}
            if abstains(draft) and searches < 2:
                issues, stage = [f"You abstained after {searches} tool call(s). Search at least twice with different "
                                 "phrasings (and page_details on the most likely page) before concluding the handbook "
                                 "doesn't say."], "abstain_guard"
            elif abstains(draft) and "search_conversations" not in used:
                issues, stage = ["Before concluding the handbook doesn't say, call search_conversations: the answer may "
                                 "have been given in a meeting or chat. If it was, answer from it and say it comes from that "
                                 "conversation, not from documented policy."], "abstain_guard"
            else:
                issues = check_citations(draft, allowed)
                stage = "citations"
            if not issues:
                issues, stage = critic(question, "\n".join(evidence), draft), "critic"
                # the reviewer (a smaller model) sometimes asks for a motive the evidence does not contain once the
                # draft already gives the decision; that request only pushes the draft into "the handbook doesn't say"
                if re.search(r"\b(decided|decision)\b", draft, re.I) and re.search(r"\b(19|20)\d\d\b", draft):
                    issues = [i for i in issues if not re.search(r"\b(reason|rationale|why|motive|clarify)\b", i, re.I)]
            if issues and len(revisions) < MAX_REVISIONS:
                revisions.append({"stage": stage, "issues": issues})
                trace.append({"step": "revise", "stage": stage, "issues": issues})
                if verbose:
                    print(f"   !! {stage}: {issues}")
                messages += [{"role": "assistant", "content": draft},
                             {"role": "user", "content": "Revise your answer. Problems:\n- " + "\n- ".join(issues)}]
                continue
            return {"answer": draft, "trace": trace, "revisions": revisions, "allowed": allowed}
        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls]})
        for c in calls:
            args = {}
            t = time.time()
            try:
                args = json.loads(c.function.arguments or "{}")
                result = queries.TOOLS[c.function.name](**args)
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}"}
            allowed.update(result.get("sources") or {})
            payload = json.dumps({k: v for k, v in result.items() if k != "sources"} |
                                 {"sources": list((result.get("sources") or {}).keys())}, default=str)[:TOOL_RESULT_CHARS]
            evidence.append(f"{c.function.name}({args}) -> {payload}")
            trace.append({"step": "tool", "tool": c.function.name, "args": args, "ms": int((time.time() - t) * 1000),
                          "n_sources": len(result.get("sources") or {}), "error": result.get("error")})
            if verbose:
                print(f"   -> {c.function.name}({args}) {'ERROR ' + result['error'] if result.get('error') else ''}")
            messages.append({"role": "tool", "tool_call_id": c.id, "content": payload})
    return {"answer": ABSTAIN + " (step limit reached)", "trace": trace, "revisions": revisions, "allowed": allowed}


WHY_Q = re.compile(r"\b(why|who (decided|made|approved)|decided|decision|made the call|where was (that|it) decided)\b", re.I)


def default_preload(question: str) -> list | None:
    """Why / who-decided questions always start from the decisions about the question. Other questions get them too
    when a recorded decision matches the question strongly (e.g. a rule agreed in chat that no document states),
    so the agent cannot miss it and present a loosely related document as the answer."""
    if WHY_Q.search(question):
        return [("decisions_about", {"topic": question})]
    try:
        rows_ = queries.decisions_about(question, k=3)["rows"]
    except Exception:
        return None
    return [("decisions_about", {"topic": question, "k": 3})] if rows_ and (rows_[0].get("match") or 0) >= 2.0 else None


def ask(question: str, verbose: bool = False, hint: str = "", preload: list | None = None) -> dict:
    t0, u0 = time.time(), dict(USAGE)
    # "why / who decided" questions: always look up the decisions about the question itself, not only the
    # decisions linked to whichever section the search happened to rank first
    if preload is None:
        preload = default_preload(question)
    out = run_agent(question, verbose, hint, preload)
    answer, allowed = out["answer"], out["allowed"]
    cited = [c for c in dict.fromkeys(_cites(answer)) if c in allowed]
    # an answer only counts if it does not abstain AND is grounded in at least one verified citation
    answerable = not abstains(answer) and len(cited) > 0
    vids = [allowed[c] for c in cited]

    aid = hashlib.sha1(f"{question}{time.time()}".encode()).hexdigest()[:12]
    meta = get_meta()
    # Answer is a first-class node: CITES the exact section versions it relied on.
    # Unanswerable questions stay as Question nodes with no fresh answer = knowledge gaps.
    q(
        """
        MERGE (qn:Question {text:$question})
        ON CREATE SET qn.first_asked_ts = $now
        SET qn.times_asked = coalesce(qn.times_asked, 0) + 1, qn.gap = NOT $answerable
        WITH qn
        OPTIONAL MATCH (old:Answer)-[:ANSWERS]->(qn) WHERE old.status IN ['fresh', 'stale']
        WITH qn, collect(old) AS olds
        CREATE (a:Answer {id:$id, text:$answer, answerable:$answerable, status:'fresh', model:$model,
                          asked_at_sha:$sha, asked_at_ts:$ts, created:$now})
        CREATE (a)-[:ANSWERS]->(qn)
        FOREACH (o IN olds | SET o.status = 'replaced' CREATE (a)-[:SUPERSEDES]->(o))
        WITH a
        UNWIND $vids AS vid
        MATCH (s:Section {vid:vid})
        CREATE (a)-[:CITES]->(s)
        """,
        dict(question=question, id=aid, answer=answer, answerable=answerable, model=MODEL,
             sha=meta.get("head"), ts=meta.get("head_ts"), vids=vids, now=time.strftime("%Y-%m-%dT%H:%M:%S")),
    )
    result = {"id": aid, "question": question, "answer": answer, "answerable": answerable, "citations": cited,
              "trace": out["trace"], "revisions": out["revisions"], "graph_head": meta.get("head_ts"),
              "secs": round(time.time() - t0, 1),
              "cost_usd": round(USAGE["cost_usd"] - u0["cost_usd"], 4)}
    RUNS.mkdir(parents=True, exist_ok=True)
    with open(RUNS / "trace.jsonl", "a") as f:
        f.write(json.dumps({"type": "ask", "ts": time.time(), **result}, default=str) + "\n")
    return result
