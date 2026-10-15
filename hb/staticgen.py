"""Static files for a generated company: the stuff that sits on a shared drive and is never updated.

    python -m hb --company larkspur staticgen

Writes (no LLM calls, deterministic):
  data/companies/<name>/drive/2025-12-01/hr-archive/Larkspur_Employee_Handbook_2025.pdf
      a consolidated PDF exported from the wiki in Dec 2025. Most sections match the wiki at T0, so they only
      start to disagree when the wiki changes (expected conflicts on each change date). One section was already
      out of date when it was exported (expected conflict at T0). Unchanged sections are negative controls.
  data/companies/<name>/drive/2026-02-10/onboarding/New_Hire_Orientation_Guide.docx
      knowledge that exists ONLY in a static file (the agent must still find and cite it)
  eval/static_labels.json, eval/static_questions.jsonl   ground truth for `hb bench`
"""
import json
from datetime import datetime
from pathlib import Path

from . import config
from .fakeco import doc_path, doc_state, render_docx, render_pdf

DRIVE_HANDBOOK_DAY = "2025-12-01"
DRIVE_ONBOARDING_DAY = "2026-02-10"
CONTROLS = [("pto-policy", "2. PTO Accrual Rates"), ("pto-policy", "3. PTO Carryover and Forfeiture"),
            ("relocation-assistance", "Non-Covered Expenses")]
DRIFTED = ("relocation-assistance", "Covered Expenses",
           [("$7,500", "$10,000"), ("up to 21 days", "up to 30 days")])

ORIENTATION = {
    "id": "new-hire-orientation", "title": "New Hire Orientation Guide", "owner_name": "People Operations team",
    "owner_style": "author", "folder": "onboarding", "format": "docx",
    "sections": [
        {"heading": "Your First Day", "body": "New hires report to the front desk of their office at 8:30 AM on their "
         "first Monday. Orientation runs until 12:00 PM and is led by the People Operations coordinator. Lunch on "
         "day one is provided by the company.\n- Bring a government-issued photo ID for your I-9 verification.\n"
         "- Your laptop is handed out by IT at 1:00 PM after orientation."},
        {"heading": "Badges and Building Access", "body": "Building badges are issued by Facilities on your first day. "
         "A lost badge must be reported to Facilities within 24 hours. The replacement fee is $25, deducted from your "
         "next paycheck. Visitors must be signed in at the front desk and escorted at all times."},
        {"heading": "Onboarding Buddy Program", "body": "Every new hire is paired with an onboarding buddy from their own "
         "team for the first 60 days. Buddies meet with the new hire at least once a week and are the first contact "
         "for questions about tools, people and unwritten norms. Buddies are chosen by the hiring manager."},
    ],
}
STATIC_QUESTIONS = [
    {"type": "static_only", "ask_at": "end", "q": "I lost my building badge. What do I need to do and does it cost anything?",
     "gold": "Report it to Facilities within 24 hours; the replacement fee is $25 (deducted from the next paycheck).",
     "doc": "onboarding/New_Hire_Orientation_Guide.docx", "format": "docx", "id": "s01"},
    {"type": "static_only", "ask_at": "end", "q": "How long does the onboarding buddy program last and how often do we meet?",
     "gold": "60 days; buddies meet the new hire at least once a week.",
     "doc": "onboarding/New_Hire_Orientation_Guide.docx", "format": "docx", "id": "s02"},
    {"type": "authority", "ask_at": "end", "q": "What's the maximum relocation reimbursement per move?",
     "gold": "$7,500 per move (the live relocation policy). An older handbook PDF that says $10,000 is outdated.",
     "doc": "hr/relocation-assistance.html", "format": "html", "id": "s03"},
    {"type": "authority", "ask_at": "end", "q": "How long can relocation temporary housing last at most?",
     "gold": "Up to 21 days through StayBridge Corporate (Seattle can be extended to 30 days with approval); "
             "the 30-day figure in the old handbook PDF is not the general rule.",
     "doc": "hr/relocation-assistance.html", "format": "html", "id": "s04"},
]


def _short(heading: str) -> str:
    import re
    return re.sub(r"^\d+(\.\d+)*\.?\s*", "", heading)


def generate(name: str = "larkspur") -> dict:
    base = config.ROOT / "data" / "companies" / name
    comp = json.loads((base / "eval" / "company.json").read_text())
    events = json.loads((base / "eval" / "events.json").read_text())
    docs = {d["id"]: d for d in comp["docs"]}
    t0 = datetime.fromisoformat(str(config.T0)[:10])
    at_export = {i: doc_state(d, events, t0) for i, d in docs.items()}  # nothing changes before T0

    picks, labels = [], []
    for e in [e for e in events if e["kind"] == "material"]:
        picks.append((e["doc"], e["heading"]))
        labels.append({"scenario": "source_conflict", "doc": doc_path(docs[e["doc"]]), "heading": e["heading"],
                       "expected_on": e["implemented_on"], "why": e["change_summary"]})
    for did, h in CONTROLS:
        picks.append((did, h))
        labels.append({"scenario": "source_consistent", "doc": doc_path(docs[did]), "heading": h})
    picks.append(DRIFTED[:2])
    labels.append({"scenario": "source_conflict", "doc": doc_path(docs[DRIFTED[0]]), "heading": DRIFTED[1],
                   "expected_on": f"{str(config.T0)[:10]}T00:00:00", "why": "stale already when exported"})
    for e in [e for e in events if e["kind"] == "unimplemented"]:  # wiki never changes these: no conflict
        picks.append((e["doc"], e["heading"]))
        labels.append({"scenario": "source_consistent", "doc": doc_path(docs[e["doc"]]), "heading": e["heading"]})

    sections = []
    for did, h in picks:
        d = at_export.get(did)
        sec = next((s for s in (d or {}).get("sections", []) if s["heading"] == h), None)
        if not sec:
            continue
        body = sec["body"]
        if (did, h) == DRIFTED[:2]:
            for old, new in DRIFTED[2]:
                body = body.replace(old, new)
        title = docs[did]["title"].replace(" Policy", "")
        head = f"{title}: {_short(h)}"
        sections.append({"heading": head if len(head) <= 55 else _short(h), "body": body})

    handbook = {"id": "employee-handbook-2025", "title": "Larkspur Freight Employee Handbook 2025",
                "owner_name": "HR Admin", "owner_style": "author", "sections": sections}
    out1 = base / "drive" / DRIVE_HANDBOOK_DAY / "hr-archive" / "Larkspur_Employee_Handbook_2025.pdf"
    out2 = base / "drive" / DRIVE_ONBOARDING_DAY / "onboarding" / "New_Hire_Orientation_Guide.docx"
    for p, data in ((out1, render_pdf(handbook, comp["people"])), (out2, render_docx(ORIENTATION, comp["people"]))):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (base / "eval" / "static_labels.json").write_text(json.dumps({"synthetic": True, "items": labels}, indent=1))
    (base / "eval" / "static_questions.jsonl").write_text("\n".join(json.dumps(q) for q in STATIC_QUESTIONS) + "\n")
    print(f"wrote {out1.relative_to(config.ROOT)} ({len(sections)} sections), {out2.relative_to(config.ROOT)}, "
          f"{len(labels)} labels, {len(STATIC_QUESTIONS)} questions")
    return {"sections": len(sections), "labels": len(labels)}
