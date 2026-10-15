"""Build the graph at T0 (snapshot), then replay real commits forward."""
import time

from . import graph
from .parse import parse_page
from .sources import all_sources, get_source


def _as_of(src, v: str, path: str):
    d = src.file_date(v, path) if hasattr(src, "file_date") else None
    if d:
        graph.q("MATCH (p:Page {path:$p}) SET p.as_of = $d", {"p": path, "d": d})


def snapshot(sha: str) -> dict:
    """Load every in-scope page as of commit `sha`."""
    t = time.time()
    src = get_source()
    c = src.commit_info(sha)
    graph.upsert_commit(c)
    files = src.list_files(sha)
    stats = {"pages": 0, "sections": 0}
    for i, path in enumerate(files, 1):
        raw = src.show(sha, path)
        if raw is None:
            continue
        page = parse_page(path, raw)
        r = graph.upsert_page(page, sha, c.ts)
        _as_of(src, sha, path)
        stats["pages"] += 1
        stats["sections"] += r["added"]
        if i % 50 == 0:
            print(f"  {i}/{len(files)} pages")
    graph.relink_pages()
    graph.tag_sources(all_sources(src), hasattr(src, "sources"))
    stats["sources"] = [s["name"] + ":" + s["kind"] for s in all_sources(src)]
    stats["owner_edges"] = graph.apply_owners(src.owner_rules(sha))
    stats["embedded"] = graph.embed_missing()
    if len(all_sources(src)) > 1:  # initial import: does anything on the shared drive already disagree with the wiki?
        from .crosscheck import check_all
        stats["source_conflicts"] = len(check_all(sha))
    graph.set_meta(head=sha, head_ts=c.ts, head_epoch=graph._epoch(c.ts), t0=sha, t0_ts=c.ts)
    stats["seconds"] = round(time.time() - t, 1)
    return stats


def replay(until_sha: str, limit: int | None = None, on_commit=None, conversations=None,
           on_conversation=None, crosscheck: bool | None = None) -> list[dict]:
    """Apply real first-parent commits (head, until] one at a time, versioning sections.
    Conversations (meetings/chat/tickets) dated before each commit are processed first, so they are
    linked against the handbook as it was at that moment.
    `on_commit(commit, diff_stats)` lets the caller run stale detection after each step."""
    src = get_source()
    if crosscheck is None:
        crosscheck = len(all_sources(src)) > 1
    meta = graph.get_meta()
    head = meta["head"]
    commits = src.commits_between(head, until_sha)
    if limit:
        commits = commits[:limit]
    done = {r["cid"] for r in graph.rows("MATCH (cv:Conversation) RETURN cv.cid AS cid")}
    pending = [cv for cv in (conversations or []) if cv.cid not in done and cv.epoch > meta.get("head_epoch", 0)]
    log = []

    def flush_conversations(until_epoch: float):
        while pending and pending[0].epoch <= until_epoch:
            cv = pending.pop(0)
            if on_conversation:
                on_conversation(cv)

    for c in commits:
        flush_conversations(graph._epoch(c.ts))
        graph.upsert_commit(c)
        totals = {"added": 0, "modified": 0, "removed": 0, "pages": []}
        owners_changed = False
        for status, path in c.files:
            if path.endswith(("CODEOWNERS", "OWNERS.yaml")):
                owners_changed = True
                continue
            if status == "D":
                graph.delete_page(path, c.sha, c.ts)
                totals["pages"].append(path)
                continue
            raw = src.show(c.sha, path)
            if raw is None:
                continue
            r = graph.upsert_page(parse_page(path, raw), c.sha, c.ts)
            _as_of(src, c.sha, path)
            for k in ("added", "modified", "removed"):
                totals[k] += r[k]
            if r["added"] or r["modified"] or r["removed"]:
                totals["pages"].append(path)
        if any(st == "A" for st, _ in c.files):
            graph.tag_sources(all_sources(src), hasattr(src, "sources"))
        if owners_changed or any(st in ("A", "M") for st, _ in c.files):
            graph.relink_pages()
            graph.apply_owners(src.owner_rules(c.sha))
        if totals["added"] or totals["modified"]:
            graph.embed_missing()
        graph.set_meta(head=c.sha, head_ts=c.ts, head_epoch=graph._epoch(c.ts))
        entry = {"sha": c.sha[:10], "ts": c.ts, "title": c.title, "mr": c.mr_iid, **totals}
        log.append(entry)
        if on_commit:
            on_commit(c, entry)
        if crosscheck and (totals["added"] or totals["modified"]):
            from .crosscheck import check_commit
            entry["conflicts"] = check_commit(c.sha)
    if not limit:  # caught up with HEAD: process everything that happened since the last commit
        import time as _t
        flush_conversations(_t.time())
    return log
