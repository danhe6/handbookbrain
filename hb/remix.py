"""Re-render part of an already generated company's Slack threads / meetings into other native formats
(Teams chat, Google Chat, WhatsApp, Discord, Google Meet) - no LLM calls - and update eval/labels.json so
the benchmark exercises every adapter.   python -m hb --company larkspur remix
"""
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import adapters
from .config import CONV_DIR, LABELS

TARGETS = ["teams_chat", "google_chat", "whatsapp", "discord"]


def _slack_threads(sd: Path) -> tuple[dict, dict]:
    users = {u["id"]: u.get("real_name") or u["name"] for u in json.loads((sd / "users.json").read_text())}
    threads: dict[tuple, list] = {}
    for ch in [p for p in sd.iterdir() if p.is_dir()]:
        for day in ch.glob("*.json"):
            for m in json.loads(day.read_text()):
                threads.setdefault((ch.name, m.get("thread_ts") or m["ts"]), []).append((day, m))
    return threads, users


def _remove(entries):
    for day in {d for d, _ in entries}:
        keep = [m for m in json.loads(day.read_text()) if not any(m is e or m == e for _, e in entries)]
        if keep:
            day.write_text(json.dumps(keep, indent=2))
        else:
            day.unlink()


def _write(fmt: str, base: Path, ch: str, tts: str, msgs: list[tuple[datetime, str, str]]):
    if fmt == "teams_chat":
        f = base / "teams_chat" / f"{ch}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(f.read_text()) if f.exists() else {"value": [], "synthetic": True}
        rid = tts.replace(".", "")
        for i, (t, who, txt) in enumerate(msgs):
            data["value"].append({"id": rid if i == 0 else f"{rid}{i}", **({"replyToId": rid} if i else {}),
                                  "messageType": "message", "createdDateTime": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                  "from": {"user": {"displayName": who}},
                                  "body": {"contentType": "html", "content": f"<p>{txt}</p>"}})
        f.write_text(json.dumps(data, indent=2))
    elif fmt == "google_chat":
        f = base / "google_chat" / ch / "messages.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(f.read_text()) if f.exists() else {"messages": []}
        for t, who, txt in msgs:
            data["messages"].append({"creator": {"name": who, "email": who.lower().replace(" ", ".") + "@larkspur.example"},
                                     "created_date": t.strftime("%A, %B %-d, %Y at %-I:%M:%S %p UTC"),
                                     "text": txt, "topic_id": tts.replace(".", "")})
        f.write_text(json.dumps(data, indent=2))
    elif fmt == "whatsapp":
        f = base / "whatsapp" / f"{ch}-{tts.split('.')[0]}.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("\n".join(f"[{t:%m/%d/%y}, {t.strftime('%-I:%M:%S %p')}] {who}: {txt}" for t, who, txt in msgs) + "\n")
    elif fmt == "discord":
        f = base / "discord" / f"{ch}-{tts.split('.')[0]}.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"channel": {"name": ch}, "messages": [
            {"timestamp": t.isoformat(), "author": {"name": who.split()[0].lower(), "nickname": who}, "content": txt}
            for t, who, txt in msgs]}, indent=2))


LOADER = {"teams_chat": adapters.load_teams_chat, "google_chat": adapters.load_google_chat,
          "whatsapp": adapters.load_whatsapp, "discord": adapters.load_discord, "meet": adapters.load_meet}


def _cid_of(fmt: str, write_fn) -> str:
    """Write into a scratch root, load it back with the real adapter, return the cid it produces."""
    with tempfile.TemporaryDirectory() as tmp:
        write_fn(Path(tmp))
        cs = LOADER[fmt](Path(tmp))
        return cs[0].cid


def remix(share: float = 0.4) -> dict:
    labels = json.loads(LABELS.read_text())
    sd = CONV_DIR / "slack"
    threads, users = _slack_threads(sd)
    moved, n = {}, 0
    for lab in labels["items"]:
        cid = lab.get("cid") or ""
        if not cid.startswith("slack:") or lab["scenario"] == "decision_implemented":
            continue
        if n % round(1 / share) != 0:
            n += 1
            continue
        _, ch, tts = cid.split(":", 2)
        entries = threads.get((ch, tts))
        if not entries:
            continue
        fmt = TARGETS[len(moved) % len(TARGETS)]
        msgs = sorted([(datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc), users.get(m["user"], m["user"]), m["text"])
                       for _, m in entries], key=lambda x: x[0])
        new_cid = _cid_of(fmt, lambda root: _write(fmt, root, ch, tts, msgs))
        _write(fmt, CONV_DIR, ch, tts, msgs)
        _remove(entries)
        lab["cid"], lab["format"] = new_cid, fmt
        moved[fmt] = moved.get(fmt, 0) + 1
        n += 1

    # one Zoom meeting -> Google Meet transcript (.txt exported from Docs)
    for lab in labels["items"]:
        if (lab.get("cid") or "").startswith("zoom:") and (CONV_DIR / "zoom" / lab["cid"].split(":", 1)[1] / "transcript.vtt").exists():
            mdir = CONV_DIR / "zoom" / lab["cid"].split(":", 1)[1]
            meta = json.loads((mdir / "meeting.json").read_text())
            start = datetime.fromisoformat(meta["start_time"].replace("Z", "+00:00"))
            segs = adapters.parse_vtt((mdir / "transcript.vtt").read_text())

            def write_meet(root: Path, mdir=mdir, meta=meta, start=start, segs=segs):
                (root / "meet").mkdir(parents=True, exist_ok=True)
                out = [f"{meta['topic']} - Transcript", f"{start:%b %d, %Y}", "Attendees",
                       ", ".join(p["name"] for p in meta.get("participants", [])), "Transcript"]
                last = -60
                for off, spk, txt in segs:
                    if off - last >= 60:
                        out.append(str(timedelta(seconds=int(off))).rjust(8, "0"))
                        last = off
                    out.append(f"{spk}: {txt}")
                (root / "meet" / f"{mdir.name}.txt").write_text("\n".join(out) + "\n")

            lab["cid"] = _cid_of("meet", write_meet)
            lab["format"] = "meet"
            write_meet(CONV_DIR)
            shutil.rmtree(mdir)
            moved["meet"] = 1
            break
    LABELS.write_text(json.dumps(labels, indent=2, default=str))
    return moved
