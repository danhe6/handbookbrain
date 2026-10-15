"""Synthetic meetings / chat / tickets, anchored to REAL handbook history, written in each tool's
NATIVE export format so the same adapters would read real exports:

  data/synthetic/zoom/<id>/transcript.vtt + meeting.json      (Zoom cloud-recording transcript + meeting object)
  data/synthetic/teams/<id>/transcript.vtt + meeting.json     (Teams transcript with <v Speaker> tags + onlineMeeting)
  data/synthetic/slack/users.json, channels.json, <channel>/<YYYY-MM-DD>.json   (Slack workspace export)
  data/synthetic/tickets/tickets.json                         (Zendesk-style helpdesk export)
  data/synthetic/labels.json                                  (ground truth for eval; never read by the agent)

Everything here is FICTIONAL: speakers are invented employees (never real GitLab staff), and every file
carries synthetic=true. Real anchors: the policy changes, their dates, and the MR numbers.

Scenarios:
  decision_implemented   meeting decides a change days before a real MR implements it
  decision_unimplemented meeting decides a change that never lands in the handbook
  conflict               after a real MR, someone in Slack states the OLD rule as fact
  answerable             question in Slack/ticket the handbook answers
  tribal                 question the handbook can't answer, answered only in chat
  gap                    the same unanswered question asked repeatedly in tickets
  noise                  social chatter / off-topic
"""
import concurrent.futures as cf
import difflib
import json
import random
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from . import gitrepo
from .config import ROOT, T0
from .llm import complete, usage_line
from .parse import parse_page

OUT = ROOT / "data" / "synthetic"
SYNTH_MODEL = __import__("os").getenv("HB_SYNTH_MODEL", "openai/gpt-4.1")
rnd = random.Random(42)

ROSTER = [
    # name, slack id, role, team
    ("Maya Okafor", "U01MAYA", "Total Rewards Manager", "Total Rewards"),
    ("Lukas Brenner", "U02LUKAS", "Benefits Specialist (EMEA)", "Total Rewards"),
    ("Priya Raman", "U03PRIYA", "Benefits Specialist (APAC)", "Total Rewards"),
    ("Daniel Ferreira", "U04DANF", "Compensation Partner", "Total Rewards"),
    ("Sofia Lindqvist", "U05SOFIA", "Director, People Operations", "People Operations"),
    ("Kwame Asante", "U06KWAME", "People Operations Specialist", "People Operations"),
    ("Hannah Weiss", "U07HANNAH", "Absence Management Lead", "People Operations"),
    ("Tomás Rivera", "U08TOMAS", "Employment Counsel", "Legal"),
    ("Grace Liu", "U09GRACE", "People Business Partner", "People Business Partners"),
    ("Arjun Mehta", "U10ARJUN", "Talent Acquisition Lead", "Talent Acquisition"),
    ("Chloe Martin", "U11CHLOE", "Engineering Manager", "Engineering"),
    ("Ben Adeyemi", "U12BEN", "Senior Backend Engineer", "Engineering"),
    ("Elena Popescu", "U13ELENA", "Product Designer", "Product"),
    ("Sam Whitaker", "U14SAM", "Sales Manager", "Sales"),
    ("Yuki Tanaka", "U15YUKI", "Support Engineer", "Support"),
    ("Noah Fischer", "U16NOAH", "Finance Analyst", "Finance"),
]
PEOPLE = {r[0]: {"name": r[0], "id": r[1], "role": r[2], "team": r[3]} for r in ROSTER}
EMPLOYEES = [n for n, *_ in ROSTER if PEOPLE[n]["team"] in ("Engineering", "Product", "Sales", "Support", "Finance")]
CHANNELS = ["people-questions", "benefits", "managers", "total-rewards-team", "random"]

GAP_TOPICS = [
    "bringing a pet to a coworking space paid by GitLab",
    "reimbursement for a standing desk above the home-office limit",
    "taking PTO during the first week of a new role after an internal transfer",
    "getting a second laptop for testing on another OS",
    "whether the learning budget covers conference travel",
    "how to change the name shown in payroll after marriage",
]

SYSTEM = """You write realistic internal workplace communication for a FICTIONAL training dataset.
People write and talk naturally: filler words, half sentences, interruptions, tangents, typos in chat,
vague references to "the handbook page" or "that doc". They paraphrase policies instead of quoting them.
Use only the speakers given. Never mention that anything is fictional. Return JSON only."""


def _llm(prompt: str, max_tokens: int = 6000) -> dict:
    resp = complete(SYNTH_MODEL, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}, max_tokens=max_tokens)
    return json.loads(resp.choices[0].message.content)


def _speaker(name: str) -> str:
    """LLMs sometimes drift from the roster; snap to the closest real roster name."""
    if name in PEOPLE:
        return name
    hit = difflib.get_close_matches(name or "", list(PEOPLE), n=1, cutoff=0.3)
    return hit[0] if hit else rnd.choice(EMPLOYEES)


def _clean(utts: list[dict]) -> list[dict]:
    return [{"speaker": _speaker(u.get("speaker", "")), "text": str(u.get("text", "")).strip()}
            for u in utts if str(u.get("text", "")).strip()]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


NUM_RE = re.compile(r"\d+(?:[.,]\d+)?%?")
# Demo anchors: real MRs with clear factual changes (pension %, premiums, leave pay, parental leave)
HERO_MRS = {21230, 20954, 20638, 20279, 20541}
SKIP_TITLE = re.compile(r"link|typo|spaces|broken|rename|format|cleanup", re.I)


def _strip_links(t: str) -> str:
    t = re.sub(r"\]\([^)]*\)", "]", t or "")          # markdown link targets
    t = re.sub(r"https?://\S+", "", t)
    return _norm(t).lower()


# ---------------------------------------------------------------- real anchors

def policy_changes(max_n: int = 10) -> list[dict]:
    """Real MRs since T0 that materially changed a policy section (not bulk renames)."""
    t0 = gitrepo.rev_before(T0)
    out = []
    for c in gitrepo.commits_between(t0, gitrepo.head()):
        md = [p for st, p in c.files if st == "M" and p.endswith(".md")]
        hero = c.mr_iid in HERO_MRS
        if not md or (len(md) > 3 and not hero) or not c.mr_iid:
            continue
        changes = []
        for path in md:
            old_raw, new_raw = gitrepo.show(c.sha + "^1", path), gitrepo.show(c.sha, path)
            if not old_raw or not new_raw:
                continue
            old = {s.sid: s for s in parse_page(path, old_raw).sections}
            new = parse_page(path, new_raw)
            for s in new.sections:
                o = old.get(s.sid)
                if not o:
                    continue
                a, b = _strip_links(o.text), _strip_links(s.text)
                if a == b:
                    continue  # link / formatting only
                nums = set(NUM_RE.findall(a)) ^ set(NUM_RE.findall(b))
                ratio = difflib.SequenceMatcher(None, a, b).ratio()
                if nums or ratio < 0.9 or hero:
                    changes.append({"sid": s.sid, "page": new.title, "heading": s.heading,
                                    "old": o.text[:3000], "new": s.text[:3000], "ratio": round(ratio, 2),
                                    "score": len(nums) * 2 + (1 - ratio) * 10})
        if hero and changes:
            changes = sorted(changes, key=lambda x: -x["score"])[:3]
        if (1 <= len(changes) <= 4 and not SKIP_TITLE.search(c.title)) or (hero and changes):
            out.append({"hero": hero, "mr": c.mr_iid, "sha": c.sha, "merged_at": c.ts, "title": c.title,
                        "path": md[0], "changes": changes})
    # most substantive first (changed numbers/dates weigh most)
    out.sort(key=lambda x: (not x["hero"], -max(ch["score"] for ch in x["changes"])))
    return sorted(out[:max_n], key=lambda x: x["merged_at"])


def stable_sections(exclude: set[str], n: int) -> list[dict]:
    """Policy sections whose text is identical at T0 and HEAD -> safe targets for decisions that are
    never implemented, and for 'answerable' questions with a stable answer. One per page."""
    t0, head = gitrepo.rev_before(T0), gitrepo.head()
    pool = []
    for path in gitrepo.list_files(head):
        if path in exclude:
            continue
        old_raw = gitrepo.show(t0, path)
        if not old_raw:
            continue
        before = {x.sid: _norm(x.text) for x in parse_page(path, old_raw).sections}
        page = parse_page(path, gitrepo.show(head, path) or "")
        good = [x for x in page.sections if before.get(x.sid) == _norm(x.text) and 400 < len(x.text) < 3000
                and NUM_RE.search(x.text) and not re.search(r"[éèàçü]", x.heading + x.text[:200])]
        if good:
            x = rnd.choice(good)
            pool.append({"sid": x.sid, "page": page.title, "heading": x.heading, "text": x.text})
    rnd.shuffle(pool)
    return pool[:n]


# ---------------------------------------------------------------- native writers

def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _vtt_time(sec: float) -> str:
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def _timeline(utts: list[dict]) -> list[tuple[float, float, dict]]:
    t, out = 2.0, []
    for u in utts:
        dur = max(1.5, len(u["text"].split()) / 2.6)
        out.append((t, t + dur, u))
        t += dur + rnd.uniform(0.2, 1.5)
    return out


def write_zoom(mid: str, meta: dict, utts: list[dict]):
    d = OUT / "zoom" / mid
    d.mkdir(parents=True, exist_ok=True)
    tl = _timeline(utts)
    lines = ["WEBVTT", ""]
    for i, (a, b, u) in enumerate(tl, 1):
        lines += [str(i), f"{_vtt_time(a)} --> {_vtt_time(b)}", f"{u['speaker']}: {u['text']}", ""]
    (d / "transcript.vtt").write_text("\n".join(lines))
    start = datetime.fromisoformat(meta["start"])
    (d / "meeting.json").write_text(json.dumps({
        "uuid": mid, "topic": meta["title"], "start_time": _ts(start), "duration": int(tl[-1][1] // 60) + 1,
        "host_email": meta["host"].lower().replace(" ", ".") + "@example.com",
        "participants": [{"name": n, "user_email": n.lower().replace(" ", ".") + "@example.com"} for n in meta["attendees"]],
        "synthetic": True}, indent=2))


def write_teams(mid: str, meta: dict, utts: list[dict]):
    d = OUT / "teams" / mid
    d.mkdir(parents=True, exist_ok=True)
    tl = _timeline(utts)
    lines = ["WEBVTT", ""]
    for a, b, u in tl:
        lines += [f"{_vtt_time(a)} --> {_vtt_time(b)}", f"<v {u['speaker']}>{u['text']}</v>", ""]
    (d / "transcript.vtt").write_text("\n".join(lines))
    start = datetime.fromisoformat(meta["start"])
    (d / "meeting.json").write_text(json.dumps({
        "id": mid, "subject": meta["title"], "startDateTime": _ts(start),
        "endDateTime": _ts(start + timedelta(seconds=tl[-1][1])),
        "participants": {"organizer": {"identity": {"user": {"displayName": meta["host"]}}},
                         "attendees": [{"identity": {"user": {"displayName": n}}} for n in meta["attendees"]]},
        "synthetic": True}, indent=2))


class SlackWriter:
    def __init__(self):
        self.days: dict[tuple[str, str], list] = {}

    def thread(self, channel: str, start: datetime, msgs: list[dict]) -> str:
        t = start
        root_ts = None
        for i, m in enumerate(msgs):
            ts = f"{t.timestamp():.6f}"
            rec = {"type": "message", "user": PEOPLE[m["speaker"]]["id"], "text": m["text"], "ts": ts,
                   "synthetic": True}
            if i == 0:
                root_ts = ts
                rec.update({"thread_ts": ts, "reply_count": len(msgs) - 1})
            else:
                rec.update({"thread_ts": root_ts, "parent_user_id": PEOPLE[msgs[0]["speaker"]]["id"]})
            self.days.setdefault((channel, t.strftime("%Y-%m-%d")), []).append(rec)
            t += timedelta(minutes=rnd.uniform(2, 90))
        return root_ts

    def flush(self):
        d = OUT / "slack"
        d.mkdir(parents=True, exist_ok=True)
        (d / "users.json").write_text(json.dumps([
            {"id": p["id"], "name": p["name"].split()[0].lower(), "real_name": p["name"],
             "profile": {"title": p["role"], "real_name": p["name"]}} for p in PEOPLE.values()], indent=2))
        (d / "channels.json").write_text(json.dumps([{"id": f"C{i:03d}", "name": c} for i, c in enumerate(CHANNELS)], indent=2))
        for (ch, day), msgs in self.days.items():
            (d / ch).mkdir(exist_ok=True)
            (d / ch / f"{day}.json").write_text(json.dumps(sorted(msgs, key=lambda m: float(m["ts"])), indent=2))


# ---------------------------------------------------------------- scenario generators

def _team_for(path: str) -> tuple[str, list[str]]:
    if "total-rewards" in path:
        return "Total Rewards weekly sync", ["Maya Okafor", "Lukas Brenner", "Priya Raman", "Daniel Ferreira", "Tomás Rivera"]
    if "hiring" in path:
        return "Talent Acquisition leads sync", ["Arjun Mehta", "Grace Liu", "Sofia Lindqvist", "Chloe Martin"]
    return "People Ops policy review", ["Sofia Lindqvist", "Kwame Asante", "Hannah Weiss", "Tomás Rivera", "Grace Liu"]


def _business_time(day: datetime) -> datetime:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.replace(hour=rnd.choice([9, 10, 14, 15, 16]), minute=rnd.choice([0, 30]), second=0, microsecond=0)


def gen_meeting(idx: int, kind: str, anchor: dict, platform: str) -> dict:
    path = anchor["sid"].split("#")[0] if "sid" in anchor else anchor["path"]
    title, attendees = _team_for(path)
    host, owner = attendees[0], attendees[1]
    if kind == "decision_implemented":
        when = _business_time(datetime.fromisoformat(anchor["merged_at"]).replace(tzinfo=None) - timedelta(days=rnd.randint(4, 18)))
        items = "\n".join(
            f"- Policy '{c['page']} > {c['heading']}'. CURRENT text:\n{c['old'][:1500]}\nWHAT THE GROUP DECIDES (new text):\n{c['new'][:1500]}"
            for c in anchor["changes"])
        agenda = (f"Main item: discuss and DECIDE the following handbook policy change(s). Someone raises at least one "
                  f"concern that gets resolved. {host} states the decision clearly. {owner} takes the action item to "
                  f"update the handbook page within two weeks.\n{items}")
    else:
        when = _business_time(datetime.fromisoformat(anchor["when"]))
        agenda = (f"Main item: the group DECIDES a concrete, plausible change to this policy (invent it; it must "
                  f"change a number, eligibility rule, deadline or process step). {host} states the decision. "
                  f"{owner} takes the action item to update the handbook page.\nPolicy '{anchor['page']} > "
                  f"{anchor['heading']}' CURRENT text:\n{anchor['text'][:1500]}")
    prompt = f"""Write a {rnd.randint(35, 55)}-utterance transcript of a {platform} meeting.
Meeting: "{title}" on {when:%A %B %d, %Y}. Attendees: {json.dumps([PEOPLE[a] for a in attendees])}.
Agenda:
1. Quick round of updates (5-8 utterances, unrelated small talk / status).
2. {agenda}
3. One unrelated item (e.g. survey results, vendor renewal, team offsite) with its own small action item.
Return JSON: {{"utterances": [{{"speaker": name, "text": str}}],
 "decisions": [{{"statement": "one sentence, specific", "policy": "page > heading"}}],
 "action_items": [{{"owner": name, "task": str, "due": "YYYY-MM-DD"}}]}}"""
    out = _llm(prompt)
    out["utterances"] = _clean(out.get("utterances", []))
    meta = {"title": title, "start": when.isoformat(), "host": host, "attendees": attendees}
    mid = f"{platform}-{when:%Y%m%d}-{idx:02d}"
    (write_zoom if platform == "zoom" else write_teams)(mid, meta, out["utterances"])
    return {"id": mid, "source": platform, "scenario": kind, "date": when.isoformat(), "title": title,
            "sections": [c["sid"] for c in anchor.get("changes", [])] or [anchor.get("sid")],
            "mr": anchor.get("mr"), "decisions": out.get("decisions", []), "action_items": out.get("action_items", [])}


def gen_slack(kind: str, anchor: dict, when: datetime, slack: SlackWriter) -> dict:
    asker, answerer = rnd.sample(EMPLOYEES, 2)
    if kind == "conflict":
        c = anchor["changes"][0]
        spec = (f"{asker} asks about '{c['page']} > {c['heading']}'. {answerer} answers confidently using the OLD rule "
                f"below (which is no longer current, but nobody in the thread knows that). Someone thanks them. "
                f"OLD rule:\n{c['old'][:1500]}")
        channel = rnd.choice(["people-questions", "benefits", "managers"])
    elif kind == "answerable":
        spec = (f"{asker} asks a practical question whose answer is in this policy. {answerer} answers roughly "
                f"correctly, maybe pointing to 'the handbook'. Policy '{anchor['page']} > {anchor['heading']}':\n"
                f"{anchor['text'][:1500]}")
        channel = rnd.choice(["people-questions", "benefits"])
    elif kind == "tribal":
        answerer = rnd.choice(["Kwame Asante", "Hannah Weiss", "Lukas Brenner", "Priya Raman", "Grace Liu"])
        spec = (f"{asker} asks about: {anchor['topic']}. {answerer} ({PEOPLE[answerer]['role']}) answers with a "
                f"specific internal practice (a concrete rule, amount, approver or form) that is NOT written in the "
                f"handbook, and says something like 'it's not in the handbook yet'.")
        channel = "people-questions"
    else:
        spec = f"Casual off-topic chat between {asker} and {answerer} (and maybe one more person): weekend, pets, a meme, a bug."
        channel = "random"
    out = _llm(f"""Write a Slack thread of 3-7 messages in #{channel}. {spec}
Speakers must be from: {json.dumps(list(PEOPLE))}.
Return JSON: {{"messages": [{{"speaker": name, "text": str}}]}}""", max_tokens=1500)
    out["messages"] = _clean(out.get("messages", []))
    ts = slack.thread(channel, when, out["messages"])
    return {"id": f"slack-{channel}-{ts}", "source": "slack", "scenario": kind, "date": when.isoformat(),
            "channel": channel, "sections": [anchor["changes"][0]["sid"]] if kind == "conflict" else
            ([anchor["sid"]] if kind == "answerable" else []), "mr": anchor.get("mr"),
            "topic": anchor.get("topic")}


def gen_tickets(answerable: list[dict], start: datetime, end: datetime) -> list[dict]:
    tickets, labels = [], []
    specs = [("gap", {"topic": t}) for t in GAP_TOPICS for _ in range(rnd.randint(2, 4))] + \
            [("answerable", a) for a in answerable]
    rnd.shuffle(specs)

    def one(i_spec):
        i, (kind, a) = i_spec
        about = (f"about: {a['topic']} (the handbook does not cover this)" if kind == "gap" else
                 f"whose answer is in policy '{a['page']} > {a['heading']}':\n{a['text'][:1200]}")
        out = _llm(f"""Write one People Ops helpdesk ticket from an employee asking a question {about}.
Phrase it in your own words (different each time). Include 0-2 agent replies; for the uncovered topics the agent
may say they'll check. Requester from {json.dumps(EMPLOYEES)}; agents from ["Kwame Asante", "Hannah Weiss", "Priya Raman"].
Return JSON: {{"subject": str, "description": str, "requester": name, "comments": [{{"author": name, "body": str}}]}}""",
                   max_tokens=1200)
        when = start + (end - start) * rnd.random()
        t = {"id": 4000 + i, "subject": out["subject"], "description": out["description"],
             "requester": {"name": _speaker(out.get("requester", ""))}, "status": rnd.choice(["open", "pending", "solved"]),
             "created_at": _ts(when), "tags": ["people-ops"], "synthetic": True,
             "comments": [{"author": {"name": c["author"]}, "body": c["body"],
                           "created_at": _ts(when + timedelta(hours=rnd.randint(1, 48)))} for c in out.get("comments", [])]}
        lab = {"id": f"ticket-{t['id']}", "source": "ticket", "scenario": kind, "date": when.isoformat(),
               "sections": [a["sid"]] if kind == "answerable" else [], "topic": a.get("topic")}
        return t, lab

    with cf.ThreadPoolExecutor(6) as ex:
        for t, lab in ex.map(one, enumerate(specs)):
            tickets.append(t)
            labels.append(lab)
    (OUT / "tickets").mkdir(parents=True, exist_ok=True)
    (OUT / "tickets" / "tickets.json").write_text(json.dumps({"tickets": sorted(tickets, key=lambda t: t["created_at"])}, indent=2))
    return labels


# ---------------------------------------------------------------- main

def generate(n_implemented: int = 8, n_unimplemented: int = 3, n_answerable: int = 10) -> dict:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    t0_dt = datetime.fromisoformat(T0)
    head_dt = datetime.fromisoformat(gitrepo.commit_info(gitrepo.head()).ts).replace(tzinfo=None)
    head_dt = min(head_dt, datetime.now())

    changes = policy_changes(n_implemented)
    print(f"real policy MRs used as anchors: {[c['mr'] for c in changes]}")
    stable = stable_sections({c["path"] for c in changes}, n_unimplemented + n_answerable)
    unimpl, answerable = stable[:n_unimplemented], stable[n_unimplemented:]
    for u in unimpl:  # decided well before HEAD so "not implemented after N days" is checkable
        u["when"] = (t0_dt + (head_dt - t0_dt) * rnd.uniform(0.3, 0.7)).isoformat()

    jobs = [("decision_implemented", c) for c in changes] + [("decision_unimplemented", u) for u in unimpl]
    labels = []
    with cf.ThreadPoolExecutor(6) as ex:
        futs = [ex.submit(gen_meeting, i, kind, a, "zoom" if i % 2 == 0 else "teams") for i, (kind, a) in enumerate(jobs)]
        for f in futs:
            labels.append(f.result())
            print(f"  meeting {labels[-1]['id']} ({labels[-1]['scenario']})")

    slack = SlackWriter()
    sjobs = []
    for c in changes:  # conflict: old rule stated 3-30 days AFTER the real MR merged
        merged = datetime.fromisoformat(c["merged_at"]).replace(tzinfo=None)
        if merged + timedelta(days=3) < head_dt:
            sjobs.append(("conflict", c, merged + timedelta(days=rnd.randint(3, 30), hours=rnd.randint(1, 8))))
    for a in answerable:
        sjobs.append(("answerable", a, t0_dt + (head_dt - t0_dt) * rnd.random()))
    for t in GAP_TOPICS[:4]:
        sjobs.append(("tribal", {"topic": t}, t0_dt + (head_dt - t0_dt) * rnd.random()))
    for _ in range(8):
        sjobs.append(("noise", {}, t0_dt + (head_dt - t0_dt) * rnd.random()))
    with cf.ThreadPoolExecutor(6) as ex:
        for lab in ex.map(lambda j: gen_slack(j[0], j[1], _business_time(j[2]), slack), sjobs):
            labels.append(lab)
    slack.flush()
    print(f"  slack threads: {len(sjobs)}")

    labels += gen_tickets(answerable, t0_dt, head_dt)
    (OUT / "labels.json").write_text(json.dumps({"synthetic": True, "generator_model": SYNTH_MODEL,
                                                 "items": sorted(labels, key=lambda x: x["date"])}, indent=2))
    (OUT / "README.md").write_text("All files in this folder are SYNTHETIC (fictional people, fictional conversations), "
                                   "generated by hb/synth.py and anchored to real GitLab handbook MRs. "
                                   "labels.json is ground truth for evaluation and is never read by the agent.\n")
    summary = {}
    for l in labels:
        summary[l["scenario"]] = summary.get(l["scenario"], 0) + 1
    print(summary, usage_line())
    return summary
