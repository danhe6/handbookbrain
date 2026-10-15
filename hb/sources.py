"""Document sources behind one interface, so ingest/replay never care where versions come from.

  git        a repository; versions = first-parent commits, owners = CODEOWNERS      (GitLab handbook)
  snapshots  dated folders  <root>/YYYY-MM-DD/...  of exported docs in any format;  (most companies:
             versions = snapshot dates, changes = content-hash diff between snapshots,  Confluence/SharePoint/
             owners = in-document owner fields (explicit) or file author (inferred)     Drive exports)
  uploads    static files (PDF/DOCX/...) dropped into  <root>/YYYY-MM-DD/  on the day they were uploaded;
             cumulative: a file stays current until a newer upload of the same path replaces it
  multi      several of the above at once (live wiki + static shared drive + git ...), merged into one
             timeline; paths are prefixed with the source name, every source carries kind + authority

Freshness model: live sources are re-synced daily (`hb sync`); static uploads change only when someone
uploads a new file. Both feed the same versioned graph, so stale detection and cross-source conflict
checks work the same way for both.

Interface (duck-typed, mirrors gitrepo): rev_before(date), head(), list_files(v), show(v, path),
commits_between(a, b), commit_info(v), owner_rules(v).
"""
import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

from . import config


class SnapshotSource:
    PREFIX = "snap:"

    def __init__(self, root: Path, scope: list | None = None):
        self.root = root
        self.scope = scope or []

    # ---- versions
    def _dates(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name[:4].isdigit())

    def _dir(self, v: str) -> Path:
        return self.root / v.removeprefix(self.PREFIX)

    def rev_before(self, date: str, ref: str = "") -> str:
        ds = self._dates()
        older = [d for d in ds if d <= date[:10]]
        return self.PREFIX + (older[-1] if older else ds[0])

    def head(self, ref: str = "") -> str:
        return self.PREFIX + self._dates()[-1]

    def commit_info(self, v: str):
        from .gitrepo import Commit
        d = v.removeprefix(self.PREFIX)
        return Commit(v, f"{d}T12:00:00+00:00", "snapshot", f"Snapshot {d}", "", None)

    # ---- files
    def list_files(self, v: str) -> list[str]:
        base = self._dir(v)
        return sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                      if p.is_file() and config.in_scope(p.relative_to(base).as_posix(), "snapshots", self.scope))

    @lru_cache(maxsize=4096)
    def _load(self, v: str, path: str) -> tuple[str, dict, str] | None:
        from .convert import convert
        f = self._dir(v) / path
        if not f.exists():
            return None
        data = f.read_bytes()
        text, meta = convert(path, data)
        # hash the extracted TEXT, not the bytes: PDF/DOCX re-exports change timestamps without changing content
        norm = " ".join(text.split())
        return text, meta, hashlib.sha1(norm.encode()).hexdigest()

    def show(self, v: str, path: str) -> str | None:
        x = self._load(v, path)
        return x[0] if x else None

    def meta(self, v: str, path: str) -> dict:
        x = self._load(v, path)
        return x[1] if x else {}

    # ---- changes
    def commits_between(self, old: str, new: str) -> list:
        ds = self._dates()
        a, b = old.removeprefix(self.PREFIX), new.removeprefix(self.PREFIX)
        out, prev = [], a
        for d in [d for d in ds if a < d <= b]:
            pv, cv = self.PREFIX + prev, self.PREFIX + d
            before = {p: self._load(pv, p)[2] for p in self.list_files(pv)}
            after = {p: self._load(cv, p)[2] for p in self.list_files(cv)}
            c = self.commit_info(cv)
            c.files = ([("A", p) for p in after if p not in before] +
                       [("M", p) for p in after if p in before and after[p] != before[p]] +
                       [("D", p) for p in before if p not in after])
            if c.files or self._owners_file(cv) != self._owners_file(pv):
                out.append(c)
            prev = d
        return out

    # ---- ownership
    def _owners_file(self, v: str) -> dict:
        f = self._dir(v) / "OWNERS.yaml"
        return yaml.safe_load(f.read_text()) or {} if f.exists() else {}

    def owner_rules(self, v: str) -> list:
        """Explicit: OWNERS.yaml or an owner field inside the document. Inferred: file author metadata."""
        from .parse import OwnerRule, owner_handle
        rules, table = [], self._owners_file(v)
        for path in self.list_files(v):
            owner, src = table.get(path), "explicit"
            if not owner:
                m = self.meta(v, path)
                owner, src = m.get("owner"), m.get("owner_source") or "explicit"
            if owner:
                for o in str(owner).replace(" and ", ",").split(","):
                    if o.strip():
                        rules.append(OwnerRule(path, [owner_handle(o)], "document", src))
        return rules


    def pull(self) -> str:
        """Live snapshot sources are filled by the company's own export job (one dated folder per day)."""
        return f"latest export {self.head().removeprefix(self.PREFIX)}"


class UploadSource(SnapshotSource):
    """Static files. <root>/<upload date>/<path>; a path's current version is its most recent upload."""
    PREFIX = "up:"
    NONE = "0000-00-00"

    def rev_before(self, date: str, ref: str = "") -> str:
        older = [d for d in self._dates() if d <= date[:10]]
        return self.PREFIX + (older[-1] if older else self.NONE)

    def head(self, ref: str = "") -> str:
        ds = self._dates()
        return self.PREFIX + (ds[-1] if ds else self.NONE)

    def commit_info(self, v: str):
        from .gitrepo import Commit
        d = v.removeprefix(self.PREFIX)
        return Commit(v, f"{d}T12:00:00+00:00", "upload", f"Files uploaded {d}", "", None)

    def _where(self, v: str) -> dict:
        """path -> upload folder holding its current version as of v."""
        cut, out = v.removeprefix(self.PREFIX), {}
        for d in self._dates():
            if d > cut:
                break
            base = self.root / d
            for p in base.rglob("*"):
                rel = p.relative_to(base).as_posix()
                if p.is_file() and rel != "OWNERS.yaml" and config.in_scope(rel, "uploads", self.scope):
                    out[rel] = d
        return out

    def list_files(self, v: str) -> list[str]:
        return sorted(self._where(v))

    @lru_cache(maxsize=4096)
    def _load(self, v: str, path: str):
        d = self._where(v).get(path)
        return SnapshotSource._load.__wrapped__(self, self.PREFIX + d, path) if d else None

    def commits_between(self, old: str, new: str) -> list:
        a, b = old.removeprefix(self.PREFIX), new.removeprefix(self.PREFIX)
        out = []
        for d in [d for d in self._dates() if a < d <= b]:
            c = self.commit_info(self.PREFIX + d)
            before = set(self.list_files(self.PREFIX + a)) if a != self.NONE else set()
            c.files = [("M" if p in before else "A", p) for p in sorted(self._where(self.PREFIX + d))
                       if self._where(self.PREFIX + d)[p] == d]
            if c.files:
                out.append(c)
            a = d
        return out

    def _owners_file(self, v: str) -> dict:
        table = {}
        for d in self._dates():
            if d <= v.removeprefix(self.PREFIX) and (f := self.root / d / "OWNERS.yaml").exists():
                table.update(yaml.safe_load(f.read_text()) or {})
        return table

    def pull(self) -> str:
        return f"latest upload {self.head().removeprefix(self.PREFIX)}"

    def file_date(self, v: str, path: str) -> str | None:
        return self._where(v).get(path)


class GitSource:
    """The git module behind the same object interface (one git repo per company: config.REPO)."""
    def __getattr__(self, name):
        from . import gitrepo
        return getattr(gitrepo, name)


def _ep(ts: str) -> float:
    from datetime import datetime, timezone
    d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _ts(c) -> float:
    return _ep(c.ts)


class MultiSource:
    """Several sources merged into one timeline. Versions:
         'm:<iso ts>'          the state of every source as of that time
         '<name>|<version>'    one source's own version (what a merged commit carries as its sha)
    Paths are '<name>/<path>' so two sources can't collide; cite keys inherit the prefix."""

    def __init__(self, specs: list[dict]):
        self.specs = {s["name"]: s for s in specs}
        self.srcs = {s["name"]: _make(s) for s in specs}

    # ---- helpers
    def info(self, name: str) -> dict:
        s = self.specs[name]
        return {"name": name, "type": s.get("type"), "kind": s.get("kind", "live"),
                "authority": int(s.get("authority", 1)), "label": s.get("label") or name}

    def _split(self, path: str):
        name, _, rest = path.partition("/")
        return name, rest

    def _ts_of(self, v: str) -> str:
        if v.startswith("m:"):
            return v[2:]
        name, _, sub = v.partition("|")
        return self.srcs[name].commit_info(sub).ts

    def _sub(self, name: str, v: str) -> str | None:
        """This source's version as of multi version v (None: the source has nothing yet)."""
        if "|" in v and v.split("|", 1)[0] == name:
            return v.split("|", 1)[1]
        try:
            sv = self.srcs[name].rev_before(self._ts_of(v))
        except Exception:
            return None
        return None if sv.endswith(UploadSource.NONE) else sv

    # ---- interface
    def rev_before(self, date: str, ref: str = "") -> str:
        return "m:" + (date if "T" in date else f"{date[:10]}T23:59:59+00:00")

    def head(self, ref: str = "") -> str:
        return "m:" + max((self.srcs[n].commit_info(self.srcs[n].head()).ts for n in self.srcs), key=_ep)

    def commit_info(self, v: str):
        if v.startswith("m:"):
            from .gitrepo import Commit
            return Commit(v, v[2:], "multi", f"State of all sources at {v[2:16]}", "", None)
        name, _, sub = v.partition("|")
        c = self.srcs[name].commit_info(sub)
        c.sha = v
        return c

    def list_files(self, v: str) -> list[str]:
        out = []
        for n, s in self.srcs.items():
            sv = self._sub(n, v)
            if sv:
                out += [f"{n}/{p}" for p in s.list_files(sv)]
        return out

    def show(self, v: str, path: str):
        n, p = self._split(path)
        sv = self._sub(n, v)
        return self.srcs[n].show(sv, p) if sv else None

    def commits_between(self, old: str, new: str) -> list:
        a, b = self._ts_of(old), self._ts_of(new)
        ea, eb = _ep(a), _ep(b)
        out = []
        for n, s in self.srcs.items():
            sa, sb = s.rev_before(a), s.rev_before(b)
            for c in s.commits_between(sa, sb):
                if not (ea < _ep(c.ts) <= eb):
                    continue
                c.sha = f"{n}|{c.sha}"
                c.files = [(st, f"{n}/{p}") for st, p in c.files]
                c.source = n
                out.append(c)
        return sorted(out, key=_ts)

    def owner_rules(self, v: str) -> list:
        from .parse import OwnerRule
        rules = []
        for n, s in self.srcs.items():
            sv = self._sub(n, v)
            if not sv:
                continue
            for r in s.owner_rules(sv):
                pat = r.pattern
                pat = f"/{n}{pat}" if pat.startswith("/") else (pat if "*" in pat and "/" not in pat.strip("/") else f"{n}/{pat}")
                rules.append(OwnerRule(pat, r.owners, r.section, r.source))
        return rules

    def file_date(self, v: str, path: str) -> str | None:
        """Upload date of a static file (live sources: None -> the section's own change date is used)."""
        n, p = self._split(path)
        s, sv = self.srcs[n], self._sub(n, v)
        return s._where(sv).get(p) if sv and isinstance(s, UploadSource) else None

    def source_of(self, path: str) -> dict:
        return self.info(self._split(path)[0])

    def sources(self) -> list[dict]:
        return [self.info(n) for n in self.srcs]

    def pull(self) -> str:
        out = []
        for n, s in self.srcs.items():
            if self.specs[n].get("kind", "live") != "live" or not hasattr(s, "pull"):
                continue
            try:
                out.append(f"{n}: {s.pull()}")
            except Exception as e:
                out.append(f"{n}: pull failed ({str(e)[:120]})")
        return "; ".join(out)


def _make(spec: dict):
    t = spec.get("type", "snapshots")
    if t == "git":
        return GitSource()
    root = config.ROOT / spec["path"]
    return UploadSource(root, spec.get("scope")) if t == "uploads" else SnapshotSource(root, spec.get("scope"))


def get_source():
    if config.DOCS_TYPE == "multi":
        return MultiSource(config.SOURCES)
    if config.DOCS_TYPE == "git":
        return GitSource()
    return _make({**config.DOCS, "path": str(config.REPO.relative_to(config.ROOT))})


def source_info(src, path: str) -> dict:
    """kind/authority of the source a page came from (single-source companies: one live source)."""
    if hasattr(src, "source_of"):
        return src.source_of(path)
    spec = config.DOCS
    return {"name": spec.get("name", "docs"), "type": config.DOCS_TYPE, "kind": spec.get("kind", "live"),
            "authority": int(spec.get("authority", 2)), "label": spec.get("label") or spec.get("name", "docs")}


def all_sources(src) -> list[dict]:
    return src.sources() if hasattr(src, "sources") else [source_info(src, "")]
