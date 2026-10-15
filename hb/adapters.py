"""Source adapters: each reads a tool's NATIVE export format and yields normalized Conversations.
Swap the synthetic folder for a real export and nothing downstream changes.

  Zoom   cloud-recording transcript (.vtt, "Name: text" cues) + meeting.json (Zoom meeting object)
         also Zoom "Save captions" .txt  ("[Name] 10:01:22" header lines)
  Teams  meeting transcript (.vtt, "<v Name>text</v>" cues) + meeting.json (Graph onlineMeeting)
  Slack  workspace export: users.json, channels.json, <channel>/<YYYY-MM-DD>.json  (one Conversation per thread)
  Tickets Zendesk-style export {"tickets": [...]}                                  (one Conversation per ticket)
  Jira   issues CSV export (Summary, Description, Reporter, Created, Comment...)  (one Conversation per issue)
  Email  .eml files or .mbox mailboxes, threaded by normalized subject           (one Conversation per thread)
  Notes  markdown meeting notes / docs exports with frontmatter (title, date, attendees, generated_by)
         -> Conversation(kind='notes'); generated_by: ai marks the whole thing as an AI summary
  Teams chat     Graph API chatMessage export {"value": [...]}, threaded by replyToId
  Google Chat    Takeout Groups/<space>/messages.json, threaded by topic_id
  Google Meet    transcript exported from Docs as .txt ("Name: text" with hh:mm:ss marks)
  WhatsApp       "Export chat" .txt; flat chat split into sessions by time gaps
  Discord        DiscordChatExporter .json; flat channel split into sessions by time gaps
TODO: raw audio/video (.mp4/.m4a) via speech-to-text - deferred (cost), transcripts cover Zoom/Teams/Meet.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

CUE_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->")
TEAMS_V = re.compile(r"<v\s+([^>]+)>(.*?)(?:</v>|$)", re.S)
ZOOM_TXT = re.compile(r"^\[(.+?)\]\s+(\d{1,2}):(\d{2}):(\d{2})\s*$")


@dataclass
class Segment:
    idx: int
    speaker: str
    at: str          # ISO-8601 UTC
    text: str


@dataclass
class Conversation:  # noqa: D101
    cid: str
    source: str      # zoom | teams | slack | ticket
    kind: str        # meeting | thread | ticket
    title: str
    started_at: str  # ISO-8601 UTC
    participants: list[str] = field(default_factory=list)
    channel: str = ""
    synthetic: bool = False
    origin: str = "conversation"   # conversation | ai_summary (AI-written meeting notes)
    segments: list[Segment] = field(default_factory=list)

    @property
    def epoch(self) -> float:
        return iso_epoch(self.started_at)


def utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _merge_runs(segs: list[tuple[float, str, str]]) -> list[tuple[float, str, str]]:
    """Transcripts split one person's turn into many cues; merge consecutive cues by the same speaker."""
    out: list[list] = []
    for off, spk, txt in segs:
        if out and out[-1][1] == spk and off - out[-1][3] < 8:
            out[-1][2] += " " + txt
            out[-1][3] = off
        else:
            out.append([off, spk, txt, off])
    return [(o, s, t) for o, s, t, _ in out]


def parse_vtt(text: str) -> list[tuple[float, str, str]]:
    """Both Zoom ('Name: text') and Teams ('<v Name>text</v>') cue styles."""
    segs, cur_t = [], None
    for block in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines or lines[0].startswith("WEBVTT"):
            continue
        body = []
        for l in lines:
            m = CUE_TIME.match(l)
            if m:
                h, mi, s, ms = map(int, m.groups())
                cur_t = h * 3600 + mi * 60 + s + ms / 1000
            elif not l.strip().isdigit() and cur_t is not None:
                body.append(l)
        if not body:
            continue
        raw = " ".join(body)
        v = TEAMS_V.findall(raw)
        if v:
            for spk, txt in v:
                segs.append((cur_t, spk.strip(), re.sub(r"<[^>]+>", "", txt).strip()))
        elif ":" in raw[:60]:
            spk, txt = raw.split(":", 1)
            segs.append((cur_t, spk.strip(), txt.strip()))
        else:
            segs.append((cur_t, "Unknown", raw.strip()))
    return _merge_runs([s for s in segs if s[2]])


def parse_zoom_captions(text: str) -> list[tuple[float, str, str]]:
    segs, cur, base = [], None, None
    for line in text.splitlines():
        m = ZOOM_TXT.match(line.strip())
        if m:
            spk, h, mi, s = m.group(1), *map(int, m.groups()[1:])
            t = h * 3600 + mi * 60 + s
            base = t if base is None else base
            cur = [t - base, spk, ""]
            segs.append(cur)
        elif cur is not None and line.strip():
            cur[2] = (cur[2] + " " + line.strip()).strip()
    return _merge_runs([tuple(s) for s in segs if s[2]])


def _meeting(folder: Path, source: str) -> Conversation | None:
    vtt = next(iter(sorted(folder.glob("*.vtt"))), None)
    txt = next(iter(sorted(folder.glob("*.txt"))), None)
    meta = json.loads((folder / "meeting.json").read_text()) if (folder / "meeting.json").exists() else {}
    if source == "zoom":
        title, start = meta.get("topic", folder.name), meta.get("start_time")
        people = [p.get("name") for p in meta.get("participants", [])]
    else:
        title, start = meta.get("subject", folder.name), meta.get("startDateTime")
        att = meta.get("participants", {}).get("attendees", [])
        people = [a.get("identity", {}).get("user", {}).get("displayName") for a in att]
    if vtt:
        raw = parse_vtt(vtt.read_text(errors="replace"))
    elif txt:
        raw = parse_zoom_captions(txt.read_text(errors="replace"))
    else:
        return None
    start_dt = datetime.fromisoformat((start or "1970-01-01T00:00:00Z").replace("Z", "+00:00"))
    segs = [Segment(i, spk, utc_iso(start_dt + timedelta(seconds=off)), t) for i, (off, spk, t) in enumerate(raw)]
    people = [p for p in people if p] or sorted({s.speaker for s in segs})
    return Conversation(f"{source}:{meta.get('uuid') or meta.get('id') or folder.name}", source, "meeting", title,
                        utc_iso(start_dt), people, synthetic=bool(meta.get("synthetic")), segments=segs)


def load_meetings(root: Path, source: str) -> list[Conversation]:
    d = root / source
    if not d.exists():
        return []
    return [c for f in sorted(p for p in d.iterdir() if p.is_dir()) if (c := _meeting(f, source))]


def load_slack(root: Path) -> list[Conversation]:
    d = root / "slack"
    if not d.exists():
        return []
    users = {u["id"]: (u.get("real_name") or u.get("profile", {}).get("real_name") or u.get("name"))
             for u in json.loads((d / "users.json").read_text())}
    threads: dict[tuple[str, str], list[dict]] = {}
    for ch_dir in sorted(p for p in d.iterdir() if p.is_dir()):
        for day in sorted(ch_dir.glob("*.json")):
            for m in json.loads(day.read_text()):
                if m.get("type") != "message" or m.get("subtype") in ("channel_join", "bot_message"):
                    continue
                key = (ch_dir.name, m.get("thread_ts") or m["ts"])
                threads.setdefault(key, []).append(m)
    out = []
    for (ch, tts), msgs in threads.items():
        msgs.sort(key=lambda m: float(m["ts"]))
        segs = [Segment(i, users.get(m.get("user"), m.get("user", "unknown")),
                        utc_iso(datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc)),
                        re.sub(r"<@(\w+)>", lambda x: "@" + str(users.get(x.group(1), x.group(1))), m.get("text", "")))
                for i, m in enumerate(msgs)]
        first = segs[0].text
        out.append(Conversation(f"slack:{ch}:{tts}", "slack", "thread", f"#{ch}: {first[:70]}", segs[0].at,
                                sorted({s.speaker for s in segs}), channel=ch,
                                synthetic=any(m.get("synthetic") for m in msgs), segments=segs))
    return out


def load_tickets(root: Path) -> list[Conversation]:
    f = root / "tickets" / "tickets.json"
    if not f.exists():
        return []
    out = []
    for t in json.loads(f.read_text()).get("tickets", []):
        req = t.get("requester", {}).get("name", "requester")
        segs = [Segment(0, req, t["created_at"], f"{t.get('subject', '')}\n{t.get('description', '')}".strip())]
        for i, c in enumerate(t.get("comments", []), 1):
            segs.append(Segment(i, c.get("author", {}).get("name", "agent"), c.get("created_at", t["created_at"]), c.get("body", "")))
        out.append(Conversation(f"ticket:{t['id']}", "ticket", "ticket", t.get("subject", f"Ticket {t['id']}"),
                                t["created_at"], sorted({s.speaker for s in segs}), channel=t.get("status", ""),
                                synthetic=bool(t.get("synthetic")), segments=segs))
    return out


def _parse_date(s: str) -> datetime:
    from email.utils import parsedate_to_datetime
    s = str(s).strip()
    for f in (lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")), parsedate_to_datetime,
              lambda x: datetime.strptime(x, "%d/%b/%y %I:%M %p"), lambda x: datetime.strptime(x, "%Y-%m-%d %H:%M"),
              lambda x: datetime.strptime(x, "%m/%d/%Y %H:%M"),
              lambda x: datetime.strptime(x, "%A, %B %d, %Y at %I:%M:%S %p UTC"),   # Google Chat Takeout
              lambda x: datetime.strptime(x, "%B %d, %Y %I:%M:%S %p"),
              lambda x: datetime.strptime(x, "%b %d, %Y"), lambda x: datetime.strptime(x, "%B %d, %Y"),
              lambda x: datetime.strptime(x, "%Y-%m-%d")):
        try:
            return f(s)
        except Exception:
            continue
    raise ValueError(f"unparseable date: {s}")


def load_jira(root: Path) -> list[Conversation]:
    import csv
    out = []
    for f in sorted((root / "jira").glob("*.csv")) if (root / "jira").exists() else []:
        rows = list(csv.reader(f.open(newline="", encoding="utf-8")))
        if not rows:
            continue
        hdr = rows[0]
        idx = {h: i for i, h in enumerate(hdr)}  # last wins; Jira repeats 'Comment'
        cmt = [i for i, h in enumerate(hdr) if h.strip().lower() == "comment"]
        for r in rows[1:]:
            get = lambda k: r[idx[k]] if k in idx and idx[k] < len(r) else ""
            created = _parse_date(get("Created"))
            segs = [Segment(0, get("Reporter") or "reporter", utc_iso(created),
                            f"{get('Summary')}\n{get('Description')}".strip())]
            for i in cmt:
                if i < len(r) and r[i].strip():
                    parts = r[i].split(";", 2)  # Jira: "date;author;body"
                    if len(parts) == 3:
                        segs.append(Segment(len(segs), parts[1], utc_iso(_parse_date(parts[0])), parts[2]))
                    else:
                        segs.append(Segment(len(segs), "commenter", utc_iso(created), r[i]))
            key = get("Issue key") or get("Key") or f"{f.stem}-{len(out)}"
            out.append(Conversation(f"jira:{key}", "jira", "ticket", get("Summary") or key, utc_iso(created),
                                    sorted({x.speaker for x in segs}), channel=get("Status"),
                                    synthetic=get("Labels").find("synthetic") >= 0, segments=segs))
    return out


def load_email(root: Path) -> list[Conversation]:
    import email
    import mailbox
    from email import policy
    d = root / "email"
    if not d.exists():
        return []
    msgs = []
    for f in sorted(d.rglob("*")):
        if f.suffix == ".eml":
            msgs.append(email.message_from_bytes(f.read_bytes(), policy=policy.default))
        elif f.suffix == ".mbox":
            msgs += [email.message_from_bytes(m.as_bytes(), policy=policy.default) for m in mailbox.mbox(str(f))]
    threads: dict[str, list] = {}
    for m in msgs:
        subj = re.sub(r"^\s*((re|fwd?|aw)\s*:\s*)+", "", str(m.get("Subject", "")), flags=re.I).strip()
        threads.setdefault(subj.lower(), []).append(m)
    out = []
    for subj, ms in threads.items():
        ms.sort(key=lambda m: _parse_date(m["Date"]))
        segs = []
        for i, m in enumerate(ms):
            body = m.get_body(preferencelist=("plain", "html"))
            text = body.get_content() if body else ""
            text = re.split(r"\n(?:On .{5,80} wrote:|-----Original Message-----|>)", text)[0].strip()  # drop quoted history
            sender = m.get("From", "")
            name = re.sub(r"\s*<.*?>", "", sender).strip().strip('"') or sender
            segs.append(Segment(i, name, utc_iso(_parse_date(m["Date"])), text))
        first = ms[0]
        out.append(Conversation(f"email:{_slug(subj)}:{segs[0].at}", "email", "thread", str(first.get("Subject", subj)),
                                segs[0].at, sorted({s.speaker for s in segs}), channel=str(first.get("To", ""))[:80],
                                synthetic=any(m.get("X-Synthetic") for m in ms), segments=segs))
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]


def load_notes(root: Path) -> list[Conversation]:
    import yaml
    d = root / "notes"
    if not d.exists():
        return []
    out = []
    for f in sorted(d.rglob("*.md")):
        raw = f.read_text(errors="replace")
        meta, body = {}, raw
        if raw.startswith("---"):
            _, fm, body = raw.split("---", 2)
            meta = yaml.safe_load(fm) or {}
        when = _parse_date(meta.get("date", "1970-01-01T00:00:00Z"))
        author = meta.get("author") or ("AI notetaker" if meta.get("generated_by") == "ai" else "notes")
        blocks = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
        segs = [Segment(i, author, utc_iso(when), b) for i, b in enumerate(blocks)]
        out.append(Conversation(f"notes:{f.stem}", "notes", "notes", meta.get("title", f.stem), utc_iso(when),
                                list(meta.get("attendees") or [author]), synthetic=bool(meta.get("synthetic")),
                                origin="ai_summary" if meta.get("generated_by") == "ai" else "conversation",
                                segments=segs))
    return out


def _strip_html(t: str) -> str:
    t = re.sub(r"<br\s*/?>|</p>", "\n", t or "", flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"&nbsp;", " ", re.sub(r"&amp;", "&", t)).strip()


def _sessionize(msgs: list[tuple[datetime, str, str]], gap_hours: float = 3.0) -> list[list[tuple]]:
    """Flat chats (WhatsApp, Discord) have no threads: start a new conversation after a quiet gap."""
    msgs = sorted(msgs, key=lambda m: m[0])
    out: list[list] = []
    for m in msgs:
        if not out or (m[0] - out[-1][-1][0]).total_seconds() > gap_hours * 3600:
            out.append([])
        out[-1].append(m)
    return out


def _conv_from(prefix: str, source: str, kind: str, title_hint: str, group: list[tuple], channel: str = "",
               synthetic: bool = False) -> Conversation:
    segs = [Segment(i, spk, utc_iso(ts), txt) for i, (ts, spk, txt) in enumerate(group)]
    title = f"{title_hint}: {segs[0].text[:70]}" if title_hint else segs[0].text[:80]
    return Conversation(f"{prefix}:{segs[0].at}", source, kind, title, segs[0].at,
                        sorted({s.speaker for s in segs}), channel=channel, synthetic=synthetic, segments=segs)


def load_teams_chat(root: Path) -> list[Conversation]:
    d = root / "teams_chat"
    out = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        data = json.loads(f.read_text())
        msgs = data.get("value", data if isinstance(data, list) else [])
        threads: dict[str, list] = {}
        for m in msgs:
            if m.get("messageType", "message") != "message" or not (m.get("from") or {}).get("user"):
                continue
            root_id = m.get("replyToId") or m["id"]
            threads.setdefault(root_id, []).append(
                (_parse_date(m["createdDateTime"]), m["from"]["user"].get("displayName", "unknown"),
                 _strip_html((m.get("body") or {}).get("content", ""))))
        for rid, g in threads.items():
            g.sort(key=lambda x: x[0])
            out.append(_conv_from(f"teams_chat:{f.stem}:{rid}", "teams_chat", "thread", f.stem, g, channel=f.stem,
                                  synthetic=bool(data.get("synthetic")) if isinstance(data, dict) else False))
    return out


def load_google_chat(root: Path) -> list[Conversation]:
    d = root / "google_chat"
    out = []
    for f in sorted(d.rglob("messages.json")) if d.exists() else []:
        space = f.parent.name
        threads: dict[str, list] = {}
        for m in json.loads(f.read_text()).get("messages", []):
            if not m.get("text"):
                continue
            when = _parse_date(m["created_date"])
            threads.setdefault(m.get("topic_id") or m["created_date"], []).append(
                (when, (m.get("creator") or {}).get("name", "unknown"), m["text"]))
        for tid, g in threads.items():
            g.sort(key=lambda x: x[0])
            out.append(_conv_from(f"google_chat:{space}:{tid}", "google_chat", "thread", space, g, channel=space))
    return out


MEET_TS = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def load_meet(root: Path) -> list[Conversation]:
    """Google Meet transcript (Docs -> .txt): title line, date line, 'Attendees' block, then
    'hh:mm:ss' marks and 'Name: text' lines."""
    d = root / "meet"
    out = []
    for f in sorted(d.glob("*.txt")) if d.exists() else []:
        lines = [l.rstrip() for l in f.read_text(errors="replace").splitlines()]
        title = re.sub(r"\s*-\s*Transcript\s*$", "", lines[0]).strip() if lines else f.stem
        start = None
        for l in lines[1:6]:
            try:
                start = _parse_date(l.strip())
                break
            except ValueError:
                continue
        start = start or datetime(1970, 1, 1, tzinfo=timezone.utc)
        off, segs = 0, []
        for l in lines:
            s = l.strip()
            if MEET_TS.match(s):
                parts = [int(x) for x in s.split(":")]
                off = parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
            elif ":" in s[:50] and not s.lower().startswith(("attendees", "transcript")):
                spk, txt = s.split(":", 1)
                if txt.strip() and len(spk.split()) <= 4:
                    segs.append((start + timedelta(seconds=off), spk.strip(), txt.strip()))
        if segs:
            c = _conv_from(f"meet:{f.stem}", "meet", "meeting", "", segs)
            c.title = title
            out.append(c)
    return out


WA_LINE = re.compile(r"^\[?(\d{1,2}/\d{1,2}/\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AP]M)?)\]?\s*(?:-\s*)?([^:]{1,40}):\s(.*)$")


def load_whatsapp(root: Path) -> list[Conversation]:
    d = root / "whatsapp"
    out = []
    for f in sorted(d.glob("*.txt")) if d.exists() else []:
        msgs, cur = [], None
        for line in f.read_text(errors="replace").splitlines():
            m = WA_LINE.match(line.strip().lstrip("\u200e"))
            if m:
                date, tm, who, txt = m.groups()
                fmt_d = "%m/%d/%y" if len(date.split("/")[-1]) == 2 else "%m/%d/%Y"
                fmt_t = "%I:%M:%S %p" if tm.count(":") == 2 and "M" in tm else "%I:%M %p" if "M" in tm else \
                    "%H:%M:%S" if tm.count(":") == 2 else "%H:%M"
                when = datetime.strptime(f"{date} {tm.strip()}", f"{fmt_d} {fmt_t}").replace(tzinfo=timezone.utc)
                cur = [when, who.strip(), txt]
                msgs.append(cur)
            elif cur is not None and line.strip():
                cur[2] += "\n" + line.strip()   # multi-line message
        msgs = [tuple(m) for m in msgs if "<Media omitted>" not in m[2]]
        for g in _sessionize(msgs):
            out.append(_conv_from(f"whatsapp:{f.stem}", "whatsapp", "thread", f.stem, g, channel=f.stem))
    return out


def load_discord(root: Path) -> list[Conversation]:
    d = root / "discord"
    out = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        data = json.loads(f.read_text())
        ch = (data.get("channel") or {}).get("name", f.stem)
        msgs = [(_parse_date(m["timestamp"]), (m.get("author") or {}).get("nickname") or (m.get("author") or {}).get("name", "?"),
                 m.get("content", "")) for m in data.get("messages", []) if m.get("content")]
        for g in _sessionize(msgs):
            out.append(_conv_from(f"discord:{ch}", "discord", "thread", f"#{ch}", g, channel=ch))
    return out


def load_all(root: Path) -> list[Conversation]:
    convs = (load_meetings(root, "zoom") + load_meetings(root, "teams") + load_meet(root) + load_slack(root)
             + load_teams_chat(root) + load_google_chat(root) + load_whatsapp(root) + load_discord(root)
             + load_tickets(root) + load_jira(root) + load_email(root) + load_notes(root))
    return sorted(convs, key=lambda c: c.epoch)
