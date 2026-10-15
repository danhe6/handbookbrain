"""End-to-end benchmark on a company the system has never seen:  python -m hb --company larkspur bench

  1. build the graph at T0 from the company's own sources (only companies/<name>.yaml differs)
  2. ask the 't0' questions (they should go stale exactly when the underlying policy changes)
  3. replay every later snapshot + every meeting/chat/email/ticket in date order
  4. refresh stale answers, then ask the 'end' questions
  5. score against eval/questions.jsonl + eval/labels.json (never visible to the agent)
"""
import json
import time
from datetime import datetime, timezone

from . import config, graph, ingest, stale
from .adapters import load_all
from .answer import ABSTAIN, ask
from .conversations import process, reconcile
from .graph import rows
from .llm import complete, usage_line
from .sources import get_source

JUDGE = """Grade an answer against a gold answer for the same question about internal company policy.
- "correct": contains the gold facts (numbers, names, rules); extra correct detail is fine.
- "partial": some gold facts present, others missing or vague.
- "wrong": contradicts the gold answer or misses the key fact.
If the gold says the topic is not covered, "correct" means the answer says it is not documented (and does not invent a policy).
Return JSON: {"grade": "correct" | "partial" | "wrong", "why": "<= 15 words"}"""


def _ep(s: str) -> float:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def grade(q: str, gold: str, answer: str) -> dict:
    r = complete(config.CRITIC_MODEL, [{"role": "system", "content": JUDGE},
                                       {"role": "user", "content": f"QUESTION: {q}\nGOLD: {gold}\nANSWER: {answer}"}],
                 response_format={"type": "json_object"})
    try:
        return json.loads(r.choices[0].message.content)
    except Exception:
        return {"grade": "wrong", "why": "unparseable"}


_SRC_NAMES = {s["name"] for s in config.SOURCES}


def _rel(path: str) -> str:
    """'wiki/hr/pto-policy.html' -> 'hr/pto-policy.html' (multi-source paths carry the source name)."""
    head, _, rest = (path or "").partition("/")
    return rest if head in _SRC_NAMES and rest else path


def _cited_paths(answer_id: str) -> list[str]:
    return [_rel(r["p"]) for r in rows("MATCH (:Answer {id:$id})-[:CITES]->(s:Section) RETURN DISTINCT s.path AS p",
                                       {"id": answer_id})]


def _cited_sources(answer_id: str) -> list[str]:
    return [r["s"] for r in rows("MATCH (:Answer {id:$id})-[:CITES]->(:Section)<-[:HAS_SECTION]-(p:Page) "
                                 "RETURN DISTINCT p.source AS s", {"id": answer_id}) if r["s"]]


def _heading_match(label_heading: str, heading: str) -> bool:
    import re
    a = re.sub(r"^\d+(\.\d+)*\.?\s*", "", label_heading or "").lower().strip()
    return bool(a) and a in (heading or "").lower()


def source_conflict_report() -> dict | None:
    f = config.LABELS.parent / "static_labels.json" if config.LABELS else None
    if not f or not f.exists():
        return None
    labels = json.loads(f.read_text())["items"]
    found = rows("""MATCH (l:Section)-[c:CONFLICTS_WITH]->(w:Section)
                    MATCH (lp:Page)-[:HAS_SECTION]->(l) MATCH (wp:Page)-[:HAS_SECTION]->(w)
                    RETURN l.path AS lpath, l.heading AS lh, lp.source AS lsrc, w.path AS wpath, w.heading AS wh,
                           wp.source AS wsrc, c.found_epoch AS at, c.status AS status, c.topic AS topic""")
    live_side = []
    for c in found:  # the live (non-static) side of each conflict: that's what the labels name
        for path, h, src, won in ((c["wpath"], c["wh"], c["wsrc"], True), (c["lpath"], c["lh"], c["lsrc"], False)):
            if src != "drive":
                live_side.append({"doc": _rel(path), "heading": h, "at": c["at"], "live_won": won, "topic": c["topic"]})
    pos = [l for l in labels if l["scenario"] == "source_conflict"]
    neg = [l for l in labels if l["scenario"] == "source_consistent"]
    hit, on_time, live_won, detail = 0, 0, 0, []
    for l in pos:
        m = [x for x in live_side if x["doc"] == l["doc"] and _heading_match(l["heading"], x["heading"])]
        ok_time = [x for x in m if x["at"] and _ep(l["expected_on"]) - 86400 <= x["at"] <= _ep(l["expected_on"]) + 15 * 86400]
        hit += bool(m)
        on_time += bool(ok_time)
        live_won += any(x["live_won"] for x in m)
        if not m:
            detail.append(f"missed {l['doc']} > {l['heading']} ({l.get('why', '')[:60]})")
    fa = [l for l in neg if any(x["doc"] == l["doc"] and _heading_match(l["heading"], x["heading"]) for x in live_side)]
    detail += [f"false alarm {l['doc']} > {l['heading']}" for l in fa]
    matched = {(x["doc"], x["heading"]) for x in live_side
               if any(x["doc"] == l["doc"] and _heading_match(l["heading"], x["heading"]) for l in labels)}
    unlabeled = sorted({f"{x['doc']} > {x['heading']} [{x['topic']}]" for x in live_side if (x["doc"], x["heading"]) not in matched})
    return {"found": f"{hit}/{len(pos)}", "on_time": f"{on_time}/{len(pos)}", "live_source_won": f"{live_won}/{max(hit, 1)}",
            "false_alarms_on_consistent_sections": f"{len(fa)}/{len(neg)}", "unlabeled_conflicts": unlabeled,
            "open_now": sum(c["status"] == "open" for c in found), "detail": detail}


def _fresh_answer(q: str) -> dict | None:
    r = rows("MATCH (a:Answer {status:'fresh'})-[:ANSWERS]->(:Question {text:$q}) RETURN a.id AS id, a.text AS text "
             "ORDER BY a.created DESC LIMIT 1", {"q": q})
    return r[0] if r else None


def run(skip_build: bool = False, verbose: bool = False) -> dict:
    t_start = time.time()
    qs = [json.loads(l) for l in config.QUESTIONS.read_text().splitlines() if l.strip()]
    extra = config.QUESTIONS.parent / "static_questions.jsonl"
    if config.SOURCES and extra.exists():
        qs += [json.loads(l) for l in extra.read_text().splitlines() if l.strip()]
    labels = json.loads(config.LABELS.read_text())["items"]
    src = get_source()

    if not skip_build:
        graph.reset()
        graph.ensure_schema()
        print("build:", ingest.snapshot(src.rev_before(config.T0)))
        t0_answers = {}
        for q in [q for q in qs if q["ask_at"] == "t0"]:
            t0_answers[q["id"]] = ask(q["q"])["id"]
        print(f"asked {len(t0_answers)} questions at T0")
        convs = load_all(config.CONV_DIR)
        print(f"replaying snapshots + {len(convs)} conversations ...")

        def on_commit(c, e):
            res = stale.detect(c.sha)
            impl = reconcile(c.sha)
            if verbose and (res or impl):
                print(f"  {c.ts[:10]} stale={[(r['question'][:40], r['verdict']) for r in res]} implemented={len(impl)}")

        def on_conv(cv):
            st = process(cv)
            if verbose:
                print(f"  {cv.started_at[:10]} [{cv.source}] {cv.title[:50]} {({k: v for k, v in st.items() if v})}")

        ingest.replay(src.head(), None, on_commit, convs, on_conv)
        stale.refresh()
        stale.retry_gaps()
        (config.RUNS).mkdir(parents=True, exist_ok=True)
        (config.RUNS / "t0_answers.json").write_text(json.dumps(t0_answers))
    t0_answers = json.loads((config.RUNS / "t0_answers.json").read_text())

    # ---------------------------------------------------------------- answer quality
    results = []
    for q in qs:
        if q["ask_at"] == "t0":
            cur = _fresh_answer(q["q"])
            ans = {"id": cur["id"], "answer": cur["text"]} if cur else {"id": None, "answer": ""}
            gold = q.get("gold_after") or q["gold"]
        else:
            r = ask(q["q"])
            ans, gold = {"id": r["id"], "answer": r["answer"]}, q["gold"]
        g = grade(q["q"], gold, ans["answer"])
        cited = _cited_paths(ans["id"]) if ans["id"] else []
        results.append({**{k: q.get(k) for k in ("id", "type", "format", "q", "doc")}, "gold": gold,
                        "answer": ans["answer"], "grade": g["grade"], "why": g.get("why"),
                        "doc_cited": (q.get("doc") in cited) if q.get("doc") else None,
                        "sources_cited": _cited_sources(ans["id"]) if ans["id"] else []})

    by_type: dict = {}
    for r in results:
        t = by_type.setdefault(r["type"], {"n": 0, "correct": 0, "partial": 0, "doc_cited": 0, "doc_n": 0})
        t["n"] += 1
        t[r["grade"]] = t.get(r["grade"], 0) + 1
        if r["doc_cited"] is not None:
            t["doc_n"] += 1
            t["doc_cited"] += bool(r["doc_cited"])
    by_fmt: dict = {}
    for r in results:
        if r.get("format"):
            f = by_fmt.setdefault(r["format"], {"n": 0, "correct": 0})
            f["n"] += 1
            f["correct"] += r["grade"] == "correct"

    # ---------------------------------------------------------------- staleness on time-travel questions
    stale_rows = []
    for q in [q for q in qs if q["ask_at"] == "t0"]:
        aid = t0_answers.get(q["id"])
        # Question level: if the same question was asked again later (e.g. in chat), the newer answer replaces
        # the T0 one and is the answer that must go stale. Any answer to this question counts.
        inv = rows("MATCH (:Question {text:$q})<-[:ANSWERS]-(:Answer)-[r:INVALIDATED_BY]->(c:Commit) "
                   "RETURN min(c.epoch) AS e, collect(DISTINCT r.reason)[..2] AS why", {"q": q["q"]})
        got = inv[0]["e"] if inv and inv[0]["e"] else None
        rev = rows("MATCH (:Question {text:$q})<-[:ANSWERS]-(:Answer)-[r:REVALIDATED_BY]->() RETURN collect(r.reason) AS rs",
                   {"q": q["q"]})
        n_ans = rows("MATCH (:Question {text:$q})<-[:ANSWERS]-(a:Answer) RETURN count(a) AS n", {"q": q["q"]})[0]["n"]
        # did the T0 answer actually rely on the section that later changed? (otherwise staying silent is correct)
        relied = q.get("doc") in _cited_paths(aid) if aid else False
        exp = q.get("expect_stale_on")
        row = {"q": q["id"], "type": q["type"], "relied_on_gold_doc_at_t0": relied, "flagged": got is not None,
               "invalidated_reasons": inv[0]["why"] if inv else [], "revalidated_reasons": rev[0]["rs"] if rev else [],
               "answers_to_question": n_ans}
        if exp:
            row.update(expected="stale", on_time=got is not None and _ep(exp) <= got <= _ep(exp) + 15 * 86400)
        else:
            row.update(expected="valid")
        stale_rows.append(row)
    scope = [r for r in stale_rows if r["relied_on_gold_doc_at_t0"]]
    tp = sum(r["flagged"] for r in scope if r["expected"] == "stale")
    fp = sum(r["flagged"] for r in stale_rows if r["expected"] == "valid")
    fn = sum(not r["flagged"] for r in scope if r["expected"] == "stale")
    on_time = sum(r.get("on_time", False) for r in scope)
    t0_retrieval_miss = sum(not r["relied_on_gold_doc_at_t0"] for r in stale_rows if r["expected"] == "stale")

    # ---------------------------------------------------------------- conversation-level ground truth
    conv = {}
    dec = rows("""MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
                  OPTIONAL MATCH (d)-[:IMPLEMENTED_BY]->(c:Commit)
                  RETURN cv.cid AS cid, d.changes_policy AS cp, collect(c.epoch) AS impl""")
    impl_by = {}
    for d in dec:
        impl_by.setdefault(d["cid"], []).extend(d["impl"])
    di = [l for l in labels if l["scenario"] == "decision_implemented" and l.get("cid")]
    du = [l for l in labels if l["scenario"] == "decision_unimplemented" and l.get("cid")]
    hit = sum(1 for l in di if any(_ep(l["implemented_on"]) <= e <= _ep(l["implemented_on"]) + 15 * 86400
                                   for e in impl_by.get(l["cid"], [])))
    open_ok = sum(1 for l in du if l["cid"] in impl_by and not impl_by[l["cid"]])
    conv["decision_to_change_recall"] = f"{hit}/{len(di)}"
    conv["unimplemented_left_open"] = f"{open_ok}/{len(du)}"
    contra = {r["cid"] for r in rows("MATCH (k:Claim)-[:CONTRADICTS]->() MATCH (k)-[:STATED_IN]->(cv:Conversation) "
                                      "RETURN DISTINCT cv.cid AS cid")}
    cl = [l["cid"] for l in labels if l["scenario"] == "conflict"]
    neg = [l["cid"] for l in labels if l["scenario"] in ("answerable", "noise")]
    conv["conflicts_found"] = f"{sum(c in contra for c in cl)}/{len(cl)}"
    conv["false_conflicts_in_clean_threads"] = f"{sum(c in contra for c in neg)}/{len(neg)}"
    ai = [l for l in labels if l.get("planted_error")]
    conv["ai_note_error_caught"] = f"{sum(l['cid'] in contra for l in ai)}/{len(ai)}"
    gaps = rows("MATCH (qn:Question)-[:ASKED_IN]->(cv:Conversation) RETURN cv.cid AS cid, coalesce(qn.gap,false) AS gap, "
                "coalesce(qn.tribal,false) AS tribal")
    g_by = {}
    for x in gaps:
        g_by.setdefault(x["cid"], []).append(x)
    def frac(scen, pred):
        cids = [l["cid"] for l in labels if l["scenario"] == scen]
        return f"{sum(1 for c in cids if any(pred(x) for x in g_by.get(c, [])))}/{len(cids)}"
    conv["gap_tickets_flagged"] = frac("gap", lambda x: x["gap"])
    conv["tribal_threads_flagged"] = frac("tribal", lambda x: x["tribal"])
    conv["answerable_threads_answered"] = frac("answerable", lambda x: not x["gap"])

    report = {"company": config.COMPANY, "questions": len(results),
              "answer_accuracy": round(sum(r["grade"] == "correct" for r in results) / len(results), 2),
              "answer_correct_or_partial": round(sum(r["grade"] in ("correct", "partial") for r in results) / len(results), 2),
              "by_type": by_type, "by_format": by_fmt,
              "staleness": {"tp": tp, "fp": fp, "fn": fn, "flagged_on_time": on_time,
                            "t0_answers_citing_other_docs": t0_retrieval_miss, "detail": stale_rows},
              "conversations": conv, "source_conflicts": source_conflict_report(),
              "t0_answers_citing_static_files": sum(1 for a in t0_answers.values() if "drive" in _cited_sources(a)),
              "minutes": round((time.time() - t_start) / 60, 1), "usage": usage_line(),
              "answers": results}
    config.RUNS.mkdir(parents=True, exist_ok=True)
    (config.RUNS / "bench.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def print_report(r: dict):
    print(f"\n==== {r['company']}: {r['questions']} questions  accuracy={r['answer_accuracy']}  "
          f"correct+partial={r['answer_correct_or_partial']}  ({r['minutes']} min, {r['usage']})")
    print("\nby question type:")
    for t, x in r["by_type"].items():
        cite = f"  gold doc cited {x['doc_cited']}/{x['doc_n']}" if x["doc_n"] else ""
        print(f"  {t:<18} {x.get('correct', 0)}/{x['n']} correct, {x.get('partial', 0)} partial{cite}")
    print("\nby source format (lookup/changed questions):")
    for f, x in r["by_format"].items():
        print(f"  {f:<6} {x['correct']}/{x['n']}")
    s = r["staleness"]
    print(f"\nstale detection (answers that relied on the changed doc): TP={s['tp']} FP={s['fp']} FN={s['fn']} "
          f"on-time={s['flagged_on_time']}   | T0 answers that cited another doc (retrieval miss): "
          f"{s['t0_answers_citing_other_docs']}")
    for d in s["detail"]:
        if d["expected"] == "stale" and d["relied_on_gold_doc_at_t0"] and not d["flagged"]:
            print(f"    missed {d['q']} ({d['answers_to_question']} answers): revalidated because {d['revalidated_reasons'][:1]}")
        if d["expected"] == "valid" and d["flagged"]:
            print(f"    false alarm {d['q']}: {d['invalidated_reasons'][:1]}")
    print("\nconversations:")
    for k, v in r["conversations"].items():
        print(f"  {k:<36} {v}")
    sc = r.get("source_conflicts")
    if sc:
        print(f"\ncross-source conflicts (live wiki vs static drive): found={sc['found']} on-time={sc['on_time']} "
              f"live-won={sc['live_source_won']} false-alarms={sc['false_alarms_on_consistent_sections']} "
              f"open-now={sc['open_now']}  | T0 answers citing static files: {r.get('t0_answers_citing_static_files')}")
        for d in sc["detail"]:
            print(f"    {d}")
        for u in sc["unlabeled_conflicts"][:5]:
            print(f"    unlabeled: {u}")
    print("\nwrong answers:")
    for a in r["answers"]:
        if a["grade"] == "wrong":
            print(f"  [{a['id']} {a['type']}] {a['q'][:70]}\n      gold: {a['gold'][:100]}\n      got : {a['answer'][:100]}  ({a['why']})")
    print(f"\nsaved {config.RUNS / 'bench.json'}")
