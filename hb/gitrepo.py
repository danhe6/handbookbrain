"""Thin wrapper over the git CLI. We read files *at a commit* (git show sha:path)
so the graph can be built at any point in history and replayed forward."""
import re
import subprocess
from dataclasses import dataclass, field

from .config import CODEOWNERS_PATHS, REPO, SCOPE, in_scope

SEP = "\x1f"
REC = "\x1e"


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def rev_before(date: str, ref: str = "main") -> str:
    sha = git("rev-list", "-1", f"--before={date}", ref).strip()
    if not sha:
        raise RuntimeError(f"no commit on {ref} before {date}")
    return sha


def head(ref: str = "main") -> str:
    return git("rev-parse", ref).strip()


def list_files(sha: str) -> list[str]:
    out = git("ls-tree", "-r", "--name-only", sha, "--", *SCOPE)
    return [p for p in out.splitlines() if in_scope(p, "git")]


def show(sha: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{sha}:{path}"], capture_output=True)
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def codeowners(sha: str) -> str:
    for p in CODEOWNERS_PATHS:
        txt = show(sha, p)
        if txt:
            return txt
    return ""


@dataclass
class Commit:
    sha: str
    ts: str
    author: str
    subject: str
    body: str
    mr_iid: int | None = None
    files: list[tuple[str, str]] = field(default_factory=list)  # (status, path)

    @property
    def branch(self) -> str:
        m = re.match(r"Merge branch '([^']+)' into", self.subject)
        return m.group(1) if m else ""

    @property
    def mr_author(self) -> str | None:
        """GitLab web-IDE branches are '<handle>-main-patch-<n>' / '<handle>-master-patch-<n>'.
        Other branch names don't reveal the author, so we return None rather than guess."""
        m = re.match(r"^([A-Za-z0-9_.-]+?)-(?:main|master)-patch-\w+$", self.branch)
        return "@" + m.group(1).lower() if m else None

    @property
    def title(self) -> str:
        # Merge commits: "Merge branch 'x' into 'main'" -> real title is first body line
        if self.subject.startswith("Merge branch") and self.body.strip():
            return self.body.strip().splitlines()[0]
        return self.subject


MR_RE = re.compile(r"See merge request [\w./-]+!(\d+)")


def commits_between(old: str, new: str) -> list[Commit]:
    """First-parent commits in (old, new] that touch our scope, oldest first."""
    fmt = SEP.join(["%H", "%cI", "%an", "%s", "%b"]) + REC
    out = git("log", "--first-parent", "--reverse", f"--format={fmt}", f"{old}..{new}", "--", *SCOPE, ".gitlab/CODEOWNERS")
    commits = []
    for rec in out.split(REC):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, ts, author, subject, body = (rec.split(SEP) + [""] * 5)[:5]
        m = MR_RE.search(body) or MR_RE.search(subject)
        c = Commit(sha, ts, author, subject, body.strip(), int(m.group(1)) if m else None)
        # diff vs first parent so merge commits show the whole MR
        diff = git("diff", "--name-status", "--no-renames", f"{sha}^1", sha, "--", *SCOPE, ".gitlab/CODEOWNERS")
        for line in diff.splitlines():
            st, _, path = line.partition("\t")
            if path.endswith("CODEOWNERS") and not _codeowners_touches_scope(sha):
                continue  # ownership change for another department
            c.files.append((st[0], path))
        if c.files:
            commits.append(c)
    return commits


def _codeowners_touches_scope(sha: str) -> bool:
    d = git("diff", "-U0", f"{sha}^1", sha, "--", *CODEOWNERS_PATHS)
    changed = [l for l in d.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    return any(s.rstrip("/") in l for l in changed for s in SCOPE)


def owner_rules(sha: str) -> list:
    from .parse import parse_codeowners
    return parse_codeowners(codeowners(sha))


def commit_info(sha: str) -> Commit:
    fmt = SEP.join(["%H", "%cI", "%an", "%s", "%b"])
    sha, ts, author, subject, body = (git("show", "-s", f"--format={fmt}", sha).split(SEP) + [""] * 5)[:5]
    m = MR_RE.search(body)
    return Commit(sha, ts, author, subject, body.strip(), int(m.group(1)) if m else None)


def pull() -> str:
    """Daily sync for a live git source: fast-forward to the remote. Returns the new head."""
    before = head()
    git("fetch", "--quiet", "origin")
    git("merge", "--ff-only", "--quiet", "origin/main")
    after = head()
    return f"git {before[:8]} -> {after[:8]}" if after != before else "git up to date"
