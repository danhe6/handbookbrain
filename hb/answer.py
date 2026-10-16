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

MAX_STEPS = 8
TOOL_RESULT_CHARS = int(__import__("os").getenv("HB_TOOL_CHARS", "9000"))  # every step re-sends earlier results
MAX_REVISIONS = 2

SYSTEM = f"""You are HandbookBrain, an assistant that knows how {COMPANY_NAME} (the company) works, using ONLY its
internal documentation (the "handbook") via the tools provided.

Tool routing:
- Policy / process / "how do I" questions: search_handbook at least TWICE with different phrasings, one of
  them naming the policy document you expect it to be in (e.g. "remote work stipend ineligible expenses",
  "data retention deletion schedule"). Questions are often vague; the right section may be in a policy whose
  name is not in the question. Prefer the section that answers the exact question over a merely related one.
- A named page or policy, or "who owns X": page_details.
- "What was the old rule / when did this change": section_history (needs a cite key from another tool).
- "Why did this change / who decided / in which meeting": why_changed (cite key) and decisions_about.
- "What changed recently / since <date>": recent_changes.
- "Who should I ask / who knows about X": who_knows, and name the owning team.
- "Which systems / dashboards / fields / metrics does X involve", "what would a change to X affect", or
  getting oriented in a business area: systems_and_data (plus search_handbook for the rules themselves).
- Only if the handbook has no answer: search_conversations. Then say clearly the info comes from a
  conversation (meeting/chat/ticket), not from approved policy, and name where it was said.
- run_cypher only when nothing else fits.

Answer rules:
- Answer exactly what was asked, in order, concisely. Copy numbers and lists exactly from the evidence.
- Cite after every claim with cite keys from the tools' "sources", e.g. [people-group/paid-time-off#pto].
- When it helps the reader act, end with "Owner: <team>" from the evidence.
- Sources differ in trust: live, maintained pages beat static files (PDF/DOCX uploaded once). If a result carries
  a WARNING (outdated copy) or two sources disagree, answer from the authoritative/newer one and add one line
  naming the outdated file, e.g. "Note: <file> (uploaded <date>) still says <old value>; it is outdated."
- If the evidence does not DIRECTLY answer the main question, begin your reply with exactly
  "The handbook doesn't say." You may then add closely related facts (with citations) and who to ask.
  Never guess or use outside knowledge."""

CRITIC = """You are a strict reviewer. You get a QUESTION, the raw EVIDENCE returned by tools, and a DRAFT answer.
Using only the evidence (not your own knowledge), check:
1. Every part of the question is answered. If the draft says the handbook doesn't say, check the evidence:
   if the evidence does contain the answer, return "revise" and say which tool result contains it.
2. Every factual claim (numbers, conditions, names) is supported by the evidence it cites.
3. Nothing is padded with content the question did not ask for.
Return JSON: {"verdict": "pass" | "revise", "issues": ["short, specific instruction", ...]}.
Use "revise" only for real problems. Never state the correct answer yourself."""

CITE_RE = re.compile(r"\[([^\[\]]+)\]")
ABSTAIN = "The handbook doesn't say."
ABSTAIN_RE = re.compile(r"^\W*(the\s+)?(handbook|documentation|docs|evidence|policy)\s+(doesn'?t|does\s+not|do\s+not)\s+"
                        r"(say|specify|mention|cover|state|provide|include|address|contain)", re.I)


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
        return ["Add [cite-key] citations from the tools' sources after each claim."]
    bad = [c for c in cited if c not in allowed]
    return [f"Invalid citations (not in tool results): {bad[:5]}. Use only keys from 'sources'."] if bad else []


def critic(question: str, evidence: str, draft: str) -> list[str]:
    resp = complete(CRITIC_MODEL, [
        {"role": "system", "content": CRITIC},
        {"role": "user", "content": f"QUESTION:\n{question}\n\nEVIDENCE:\n{evidence[:20000]}\n\nDRAFT:\n{draft}"}],
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
            if abstains(draft) and searches < 2:
                issues, stage = [f"You abstained after {searches} tool call(s). Search at least twice with different "
                                 "phrasings (and page_details on the most likely page) before concluding the handbook "
                                 "doesn't say."], "abstain_guard"
            else:
                issues = check_citations(draft, allowed)
                stage = "citations"
            if not issues:
                issues, stage = critic(question, "\n".join(evidence), draft), "critic"
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


def ask(question: str, verbose: bool = False, hint: str = "", preload: list | None = None) -> dict:
    t0, u0 = time.time(), dict(USAGE)
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
