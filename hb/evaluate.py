"""Score the graph against data/synthetic/labels.json (ground truth written by the generator, never
read by the agent) and summarize the real-data stale-detection run. Output = the tables for the README."""
import json
from collections import Counter

from .config import LABELS, ROOT, RUNS
from .graph import rows


def _cid(label: dict) -> str:
    lid, src = label["id"], label["source"]
    if src in ("zoom", "teams"):
        return f"{src}:{lid}"
    if src == "slack":                          # slack-<channel>-<ts>
        _, rest = lid.split("-", 1)
        ch, ts = rest.rsplit("-", 1)
        return f"slack:{ch}:{ts}"
    return "ticket:" + lid.split("-", 1)[1]     # ticket-4012


def _pr(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": None if p is None else round(p, 2),
            "recall": None if r is None else round(r, 2)}


def run() -> dict:
    labels = json.loads(LABELS.read_text())["items"]
    by_cid = {(l.get("cid") or _cid(l)): l for l in labels}
    present = {r["cid"] for r in rows("MATCH (cv:Conversation) RETURN cv.cid AS cid")}

    # 1. decision -> implementing MR
    dec = rows("""MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
                  OPTIONAL MATCH (d)-[:IMPLEMENTED_IN_MR]->(m:MergeRequest)
                  RETURN cv.cid AS cid, d.status AS status, collect(m.iid) AS mrs""")
    per_conv: dict[str, dict] = {}
    for d in dec:
        x = per_conv.setdefault(d["cid"], {"implemented_mrs": set(), "n": 0})
        x["n"] += 1
        x["implemented_mrs"] |= set(d["mrs"])
    tp = fp = fn = 0
    unimpl_ok = unimpl_bad = 0
    detail = []
    for cid, l in by_cid.items():
        if l["scenario"] == "decision_implemented":
            got = per_conv.get(cid, {}).get("implemented_mrs", set())
            hit = l["mr"] in got
            tp += hit
            fn += not hit
            fp += len(got - {l["mr"]})
            detail.append({"meeting": cid, "expected_mr": l["mr"], "linked_mrs": sorted(got), "ok": hit})
        elif l["scenario"] == "decision_unimplemented":
            got = per_conv.get(cid, {}).get("implemented_mrs", set())
            if cid in per_conv and not got:
                unimpl_ok += 1
            else:
                unimpl_bad += 1
            fp += len(got)
            detail.append({"meeting": cid, "expected_mr": None, "linked_mrs": sorted(got), "ok": not got})
    decisions = {"implemented_link": _pr(tp, fp, fn), "unimplemented_correctly_open": f"{unimpl_ok}/{unimpl_ok + unimpl_bad}",
                 "detail": detail}

    # 2. chat-vs-handbook conflicts (thread level)
    conf = {r["cid"] for r in rows("MATCH (k:Claim)-[:CONTRADICTS]->() MATCH (k)-[:STATED_IN]->(cv:Conversation) "
                                   "RETURN DISTINCT cv.cid AS cid")}
    exp = {cid for cid, l in by_cid.items() if l["scenario"] == "conflict" and cid in present}
    chat = {cid for cid, l in by_cid.items() if l["source"] in ("slack", "ticket") and cid in present}
    conflicts = _pr(len(exp & conf), len((conf & chat) - exp), len(exp - conf))
    conflicts["flagged_in_meetings"] = len(conf - chat)

    # 3. knowledge gaps / tribal knowledge / answerable (question level, by conversation)
    qs = rows("""MATCH (qn:Question)-[r:ASKED_IN]->(cv:Conversation)
                 RETURN cv.cid AS cid, coalesce(qn.gap, false) AS gap, coalesce(qn.tribal, false) AS tribal""")
    agg: dict[str, Counter] = {}
    for x in qs:
        agg.setdefault(x["cid"], Counter())["gap" if x["gap"] else "answered"] += 1
        if x["tribal"]:
            agg[x["cid"]]["tribal"] += 1
    def rate(scen, key):
        cids = [c for c, l in by_cid.items() if l["scenario"] == scen and c in present]
        hit = sum(1 for c in cids if agg.get(c, Counter())[key] > 0)
        return f"{hit}/{len(cids)}"
    gaps = {"gap_tickets_flagged_as_gap": rate("gap", "gap"),
            "tribal_threads_flagged_tribal": rate("tribal", "tribal"),
            "answerable_threads_answered": rate("answerable", "answered"),
            "noise_threads_with_false_gap": rate("noise", "gap")}

    # 4. real handbook history: stale detection outcome counts (no synthetic data involved)
    real = rows("""OPTIONAL MATCH ()-[rv:REVALIDATED_BY]->() WITH count(rv) AS revalidated
                   OPTIONAL MATCH (a:Answer)-[iv:INVALIDATED_BY]->() WITH revalidated, count(DISTINCT a) AS invalidated
                   OPTIONAL MATCH (c:Commit) WITH revalidated, invalidated, count(c) AS commits
                   OPTIONAL MATCH (:Commit)-[ch:CHANGED]->() RETURN revalidated, invalidated, commits,
                   count(ch) AS section_changes""")[0]
    return {"decisions": decisions, "conflicts": conflicts, "questions": gaps, "real_history": real}


def print_report():
    r = run()
    d = r["decisions"]
    print("== Decision -> implementing MR (synthetic meetings anchored to real MRs) ==")
    print(f"  link precision/recall: {d['implemented_link']}")
    print(f"  never-implemented decisions correctly left open: {d['unimplemented_correctly_open']}")
    for x in d["detail"]:
        print(f"    {'OK ' if x['ok'] else 'XX '} {x['meeting']:<32} expected !{x['expected_mr']}  got {x['linked_mrs']}")
    print("\n== Chat statements contradicting the handbook (thread level) ==")
    print(f"  {r['conflicts']}")
    print("\n== Questions from chat/tickets ==")
    for k, v in r["questions"].items():
        print(f"  {k:<36} {v}")
    print("\n== Real GitLab handbook history replay ==")
    print(f"  {r['real_history']}")
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / "eval.json").write_text(json.dumps(r, indent=2, default=str))
    print(f"\nsaved {RUNS / 'eval.json'}")
