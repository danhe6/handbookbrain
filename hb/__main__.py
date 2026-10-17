"""CLI:  python -m hb doctor | init [--at DATE] [--reset] | ask "q" | replay [--until DATE] [--limit N] | sync | upload FILES |
      sources | stale | stats"""
import argparse
import json
import os
import sys


def cmd_doctor(_):
    from .config import COMPANY, DOCS_TYPE, GRAPH, REPO, T0
    from .sources import get_source
    src = get_source()
    ok = True
    print(f"company  : {COMPANY}  graph={GRAPH}  docs={DOCS_TYPE}:{REPO}")
    try:
        print("head     :", src.head())
        print("T0       :", T0, src.rev_before(T0))
        print("files@T0 :", len(src.list_files(src.rev_before(T0))))
        print("owner rules@head:", len(src.owner_rules(src.head())))
    except Exception as e:
        ok = False
        print("docs FAIL:", e)
    try:
        from .graph import q
        print("falkordb :", q("RETURN 1").result_set)
    except Exception as e:
        ok = False
        print("falkordb FAIL:", e)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        print(f"{k}:", "set" if os.getenv(k) else "MISSING")
    try:
        from .config import MODEL
        from .llm import complete, embed
        print("llm      :", complete(MODEL, [{"role": "user", "content": "Reply OK"}], max_tokens=5).choices[0].message.content)
        print("embed dim:", len(embed(["hello"])[0]))
    except Exception as e:
        ok = False
        print("llm  FAIL:", str(e)[:300])
    sys.exit(0 if ok else 1)


def cmd_init(a):
    from . import graph, ingest
    from .config import T0
    from .sources import get_source
    src = get_source()
    if a.reset:
        graph.reset()
    graph.ensure_schema()
    sha = src.rev_before(a.at or T0)
    print(f"Snapshot at {sha[:10]} ({a.at or T0})")
    from .llm import usage_line
    print(ingest.snapshot(sha), usage_line())


def cmd_ask(a):
    from .answer import ask
    r = ask(a.question, verbose=True)
    print("\n" + r["answer"])
    print(f"\nanswerable={r['answerable']} cited={len(r['citations'])} revisions={len(r['revisions'])} "
          f"secs={r['secs']} cost=${r['cost_usd']}  (graph as of {r['graph_head']})")


def cmd_replay(a):
    from . import ingest, stale
    from .config import CONV_DIR, ROOT
    from .sources import get_source
    source = get_source()
    until = source.rev_before(a.until) if a.until else source.head()

    convs = []
    src = ROOT / "data" / a.conversations if a.conversations else CONV_DIR
    if not a.no_conversations and src.exists():
        from .adapters import load_all
        convs = load_all(src)
        print(f"{len(convs)} conversations from {src} will be interleaved by date")

    def after(c, e):
        from .conversations import reconcile
        res = stale.detect(c.sha)
        impl = reconcile(c.sha) if convs else []
        if not a.quiet or res or impl:
            print(f"{e['ts'][:10]} {e['sha']} !{e['mr'] or '-'} {e['title'][:70]}  "
                  f"+{e['added']} ~{e['modified']} -{e['removed']}")
        for r in res:
            mark = "STALE " if r["verdict"] == "outdated" else "ok    "
            to = ", ".join(x["to"] for x in (r.get("notify") or []))
            print(f"     {mark}{r['question'][:60]!r} [{','.join(r['kinds'])} x{len(r['sections'])}]: {r['reason']}"
                  + (f"  -> notify {to}" if to else ""))
        for r in impl:
            print(f"     IMPLEMENTED decision {r['decision'][:80]!r}: {r['reason']}")

    def on_conv(cv):
        from .conversations import process
        st = process(cv, verbose=a.verbose)
        interesting = {k: v for k, v in st.items() if v}
        if not a.quiet or st["decisions"] or st["conflicts"] or st["tribal"]:
            print(f"{cv.started_at[:10]} [{cv.source}] {cv.title[:60]}  {interesting}")

    from .llm import usage_line
    log = ingest.replay(until, a.limit, after, convs, on_conv)
    print(f"\nreplayed {len(log)} commits  {usage_line()}")


def cmd_sync(a):
    """Daily job: pull live sources, apply every change since the last sync (docs + conversations), re-check
    stored answers and cross-source conflicts, refresh what went stale. Cron, e.g.
      15 6 * * *  cd ~/handbookbrain && .venv/bin/python -m hb --company larkspur sync >> runs/sync.log 2>&1"""
    import time
    from . import graph, stale
    from .sources import get_source
    src = get_source()
    print(time.strftime("%Y-%m-%d %H:%M"), "sync")
    if not a.no_pull and hasattr(src, "pull"):
        try:
            print("pull:", src.pull())
        except Exception as e:
            print("pull failed (continuing with local copy):", str(e)[:200])
    a.until, a.limit, a.quiet, a.conversations, a.verbose = None, None, True, None, False
    cmd_replay(a)
    graph.mark_synced()
    if not a.no_refresh:
        for r in stale.refresh():
            print(f"refreshed: {r['question'][:80]}")
    from .crosscheck import open_conflicts
    print(f"open source conflicts: {len(open_conflicts())}   {json.dumps(stale.summary(), default=str)}")


def cmd_upload(a):
    """Drop static files into an uploads source (dated folder = upload day). Picked up by the next sync."""
    import shutil
    import time
    from pathlib import Path
    from .config import ROOT, SOURCES
    ups = [s for s in SOURCES if s.get("type") == "uploads" and (not a.source or s["name"] == a.source)]
    if not ups:
        sys.exit("no 'uploads' source in this company's config (add one under sources:)")
    day = a.date or time.strftime("%Y-%m-%d")
    dest = ROOT / ups[0]["path"] / day / (a.folder or "")
    dest.mkdir(parents=True, exist_ok=True)
    for f in a.files:
        shutil.copy2(f, dest / Path(f).name)
        print(f"uploaded {Path(f).name} -> {ups[0]['name']} ({day})")


def cmd_crossdebug(a):
    from .crosscheck import debug
    for r in debug(a.doc):
        print(json.dumps(r, default=str)[:900])


def cmd_serve(a):
    from .api import serve
    serve(a.port)


def cmd_sources(_):
    from .crosscheck import open_conflicts
    from .graph import rows
    for r in rows("MATCH (x:Source) OPTIONAL MATCH (x)-[:PROVIDES]->(p:Page) WHERE p.deleted = false "
                  "RETURN x.name AS n, x.kind AS kind, x.type AS type, x.authority AS auth, x.last_synced AS synced, "
                  "count(p) AS pages ORDER BY auth DESC"):
        print(f"{r['n']:<14} {r['kind']:<7} {r['type']:<10} authority={r['auth']} pages={r['pages']} last_synced={r['synced']}")
    print("\n== Sources that disagree (outdated copy -> authoritative) ==")
    for c in open_conflicts():
        print(f"  [{c['topic']}] {c['outdated']}  ->  {c['authoritative']}\n        {c['reason']}  notify={c['notify']}")


def cmd_alerts(a):
    import time
    from .conversations import unimplemented
    from .graph import get_meta, rows
    now = get_meta().get("head_epoch") or time.time()
    print("== Decided in a meeting/chat but not in the handbook ==")
    for r in unimplemented(now, a.days):
        print(f"  [{r['days_open']:.0f}d] {r['statement'][:90]}\n        in {r['source']} '{r['meeting']}' {r['decided_at'][:10]}"
              f"  section={r['section']}  owners={r['page_owners']} assignees={r['assignees']}")
    print("\n== Chat/meeting statements that contradict the current handbook ==")
    for r in rows("""MATCH (k:Claim)-[r:CONTRADICTS]->(s:Section)<-[:HAS_SECTION]-(p:Page)
                     MATCH (k)-[:STATED_IN]->(cv:Conversation)
                     OPTIONAL MATCH (p)-[:OWNED_BY]->(o)
                     RETURN k.statement AS st, k.speaker AS who, cv.title AS where, k.at AS at, r.reason AS reason,
                            s.sid AS sid, collect(DISTINCT o.handle) AS owners ORDER BY at"""):
        print(f"  {r['at'][:10]} {r['who']} in '{r['where'][:50]}': {r['st'][:80]}\n        vs {r['sid'].split('/')[-1]}: {r['reason']}  owners={r['owners']}")
    from .crosscheck import open_conflicts
    print("\n== Sources that disagree (e.g. live wiki vs an old PDF on the drive) ==")
    for c in open_conflicts():
        print(f"  [{c['topic']}] {c['outdated']} ({c['outdated_kind']}) is outdated vs {c['authoritative']}\n        {c['reason']}")
    print("\n== Knowledge gaps (asked, handbook can't answer) ==")
    for r in rows("""MATCH (qn:Question {gap:true})
                     OPTIONAL MATCH (qn)-[ai:ASKED_IN]->(cv:Conversation)
                     RETURN qn.text AS q, count(cv) AS asked, coalesce(qn.tribal, false) AS tribal,
                            collect(ai.chat_answer)[0] AS chat_answer ORDER BY asked DESC"""):
        tag = "TRIBAL (answer only in chat)" if r["tribal"] else ""
        print(f"  x{r['asked']} {r['q'][:90]} {tag}" + (f"\n        chat says: {r['chat_answer'][:120]}" if r["chat_answer"] else ""))


def cmd_decisions(_):
    from .graph import rows
    for r in rows("""MATCH (d:Decision)-[:DECIDED_IN]->(cv:Conversation)
                     OPTIONAL MATCH (d)-[:IMPLEMENTED_IN_MR]->(m:MergeRequest)
                     RETURN d.decided_at AS at, d.status AS status, d.statement AS st, cv.source AS src, cv.title AS t,
                            d.about_sid AS sid, m.iid AS mr, d.lag_days AS lag ORDER BY at"""):
        print(f"{r['at'][:10]} {r['status']:<11} [{r['src']}] {r['st'][:80]}\n           about={r['sid']} mr=!{r['mr']} lag={r['lag']}d")


def cmd_stale(_):
    from . import stale
    print(json.dumps(stale.inbox(), indent=2, default=str))
    print(json.dumps(stale.summary(), default=str))


def cmd_refresh(a):
    from . import stale
    for r in stale.refresh(verbose=a.verbose):
        print(f"\nQ: {r['question']}\n  why stale: {r['why']}\n  OLD: {r['old'][:300]}\n  NEW: {r['new'][:300]}")
    if a.gaps:
        for r in stale.retry_gaps(verbose=a.verbose):
            print(f"\nGAP {'CLOSED' if r['closed'] else 'still open'}: {r['question']}")


def cmd_fakeco(a):
    from .fakeco import generate
    generate(a.name)


def cmd_staticgen(a):
    from .staticgen import generate
    generate(a.name)


def cmd_remix(_):
    from .remix import remix
    print("re-rendered into other native formats:", remix())


def cmd_bench(a):
    from .bench import print_report, run
    print_report(run(skip_build=a.skip_build, verbose=a.verbose))


def cmd_eval(_):
    from .evaluate import print_report
    print_report()


def cmd_synth(a):
    from .synth import generate
    generate(a.implemented, a.unimplemented, a.answerable)


def cmd_seed(a):
    """Ask every question in a file (one per line, # comments) so replay has answers to watch."""
    from pathlib import Path
    from .answer import ask
    from .llm import usage_line
    qs = [l.strip() for l in Path(a.file).read_text().splitlines() if l.strip() and not l.startswith("#")]
    for i, qtext in enumerate(qs, 1):
        r = ask(qtext)
        print(f"[{i}/{len(qs)}] {'ok ' if r['answerable'] else 'GAP'} cites={len(r['citations'])} {qtext[:80]}")
    print(usage_line())


def cmd_learn(a):
    """Pre-write onboarding content so the Learn page is instant in a demo: area briefs + glossary."""
    from .glossary import build
    from .learn import areas, brief
    from .llm import usage_line
    for ar in areas():
        if a.area and a.area not in ar["id"]:
            continue
        print(f"{ar['name']}: brief")
        brief(ar["id"], refresh=a.refresh)
        print(f"{ar['name']}: glossary")
        build(limit=a.terms, area=ar["id"])
    print(usage_line())


def cmd_proposals(a):
    from .proposals import listing, reset_all
    if a.reset:
        print(f"reset {reset_all()} proposal(s) to pending")
    for p in listing():
        print(f"{p['status']:9} {p['id']}  {p['page']} › {p['heading']}  ({len(p['signoffs'])} sign-offs)")


def cmd_stats(_):
    from .graph import rows
    for r in rows("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC"):
        print(f"{r['label']:>14} {r['n']}")
    for r in rows("MATCH ()-[e]->() RETURN type(e) AS rel, count(*) AS n ORDER BY n DESC"):
        print(f"{r['rel']:>14} {r['n']}")


def main():
    # --company must be set before any hb module reads config
    if "--company" in sys.argv:
        i = sys.argv.index("--company")
        os.environ["HB_COMPANY"] = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    p = argparse.ArgumentParser(prog="hb", epilog="global: --company <name> (companies/<name>.yaml)")
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor").set_defaults(f=cmd_doctor)
    s = sp.add_parser("init"); s.add_argument("--at"); s.add_argument("--reset", action="store_true"); s.set_defaults(f=cmd_init)
    s = sp.add_parser("ask"); s.add_argument("question"); s.set_defaults(f=cmd_ask)
    s = sp.add_parser("replay"); s.add_argument("--until"); s.add_argument("--limit", type=int)
    s.add_argument("--quiet", action="store_true", help="only print commits that affect stored answers")
    s.add_argument("--no-conversations", action="store_true"); s.add_argument("--conversations", help="folder under data/")
    s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(f=cmd_replay)
    s = sp.add_parser("sync", help="daily: pull live sources, apply changes, re-check answers + conflicts")
    s.add_argument("--no-pull", action="store_true"); s.add_argument("--no-refresh", action="store_true")
    s.add_argument("--no-conversations", action="store_true"); s.set_defaults(f=cmd_sync)
    s = sp.add_parser("upload", help="add static files (PDF/DOCX/...) to an uploads source")
    s.add_argument("files", nargs="+"); s.add_argument("--source"); s.add_argument("--folder"); s.add_argument("--date")
    s.set_defaults(f=cmd_upload)
    s = sp.add_parser("serve", help="web UI + API on http://localhost:8000")
    s.add_argument("--port", type=int, default=8000); s.set_defaults(f=cmd_serve)
    s = sp.add_parser("crossdebug", help="explain cross-source conflict checks for one document")
    s.add_argument("doc"); s.set_defaults(f=cmd_crossdebug)
    sp.add_parser("sources", help="sources, last sync, and cross-source conflicts").set_defaults(f=cmd_sources)
    s = sp.add_parser("alerts"); s.add_argument("--days", type=int, default=21); s.set_defaults(f=cmd_alerts)
    sp.add_parser("decisions").set_defaults(f=cmd_decisions)
    sp.add_parser("eval").set_defaults(f=cmd_eval)
    s = sp.add_parser("fakeco", help="generate a fictional company dataset"); s.add_argument("--name", default="larkspur")
    s.set_defaults(f=cmd_fakeco)
    s = sp.add_parser("staticgen", help="add static shared-drive files (old PDF handbook, onboarding docx) to a generated company")
    s.add_argument("--name", default="larkspur"); s.set_defaults(f=cmd_staticgen)
    sp.add_parser("remix", help="re-render some generated chats/meetings into Teams chat, Google Chat, "
                               "WhatsApp, Discord, Google Meet (no LLM)").set_defaults(f=cmd_remix)
    s = sp.add_parser("bench", help="build + replay + score a company end to end")
    s.add_argument("--skip-build", action="store_true"); s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(f=cmd_bench)
    s = sp.add_parser("refresh"); s.add_argument("--gaps", action="store_true"); s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(f=cmd_refresh)
    s = sp.add_parser("synth"); s.add_argument("--implemented", type=int, default=8)
    s.add_argument("--unimplemented", type=int, default=3); s.add_argument("--answerable", type=int, default=10)
    s.set_defaults(f=cmd_synth)
    s = sp.add_parser("seed"); s.add_argument("file", nargs="?", default="eval/questions.txt"); s.set_defaults(f=cmd_seed)
    sp.add_parser("stale").set_defaults(f=cmd_stale)
    sp.add_parser("stats").set_defaults(f=cmd_stats)
    s = sp.add_parser("proposals", help="drafted updates; --reset puts them all back to pending (undo demo sign-offs)")
    s.add_argument("--reset", action="store_true"); s.set_defaults(f=cmd_proposals)
    s = sp.add_parser("learn", help="pre-write onboarding briefs and glossary definitions for every area")
    s.add_argument("--area", default=""); s.add_argument("--terms", type=int, default=30)
    s.add_argument("--refresh", action="store_true"); s.set_defaults(f=cmd_learn)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
