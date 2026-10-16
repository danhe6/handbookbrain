"""Markdown -> Page + Sections, CODEOWNERS / document owners -> ownership rules."""
import hashlib
import re
from dataclasses import dataclass, field

import yaml

HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*(\{#([\w-]+)\})?\s*$")
LINK_RE = re.compile(r"\]\((/handbook/[^)#\s]*)")
SHORTCODE_RE = re.compile(r"\{\{[<%].*?[>%]\}\}", re.S)
MAX_SECTION_CHARS = 6000


@dataclass
class Section:
    sid: str          # stable id: path#anchor
    anchor: str
    heading: str
    level: int
    order: int
    text: str
    hash: str
    cite: str = ""    # short, human-readable citation key shown to the agent


DOC_EXT_RE = re.compile(r"\.(md|markdown|html?|pdf|docx|txt|csv)$", re.I)


def cite_key(sid: str) -> str:
    """content/handbook/people-group/paid-time-off.md#pto -> people-group/paid-time-off#pto
    hr/travel-policy.pdf#per-diem -> hr/travel-policy#per-diem"""
    from .config import CITE_STRIP
    path, _, anchor = sid.partition("#")
    path = DOC_EXT_RE.sub("", path.removeprefix(CITE_STRIP))
    return f"{path}#{anchor}"


@dataclass
class Page:
    path: str
    url: str
    title: str
    dept: str
    sections: list[Section] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


def slug(s: str) -> str:
    s = re.sub(r"[`*_\[\]()]", "", s.lower())
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s]+", "-", s.strip())[:80] or "section"


def path_to_url(path: str) -> str:
    # content/handbook/people-group/paid-time-off.md -> /handbook/people-group/paid-time-off/
    if not path.startswith("content/"):
        return "/" + path
    p = path.removeprefix("content").removesuffix(".md")
    if p.endswith("/_index") or p.endswith("/index"):
        p = p.rsplit("/", 1)[0]
    return p.rstrip("/") + "/"


def norm_url(u: str) -> str:
    return u.split("?")[0].rstrip("/") + "/"


H4_RE = re.compile(r"^####\s+(.+?)\s*(\{#([\w-]+)\})?\s*$")


def _split_long(chunks):
    """Long sections are split instead of truncated (a rule buried after a big table must stay findable):
    first at h4 sub-headings, then at paragraph boundaries."""
    out = []
    for anchor, heading, level, lines in chunks:
        if len("\n".join(lines)) <= MAX_SECTION_CHARS:
            out.append((anchor, heading, level, lines))
            continue
        parts, cur = [], (anchor, heading, level, [])
        in_code = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code = not in_code
            m = None if in_code else H4_RE.match(line)
            if m:
                parts.append(cur)
                h = m.group(1).strip()
                cur = (m.group(3) or slug(h), h, 4, [])
            else:
                cur[3].append(line)
        parts.append(cur)
        for a, h, lv, ls in parts:
            text = "\n".join(ls)
            if len(text) <= MAX_SECTION_CHARS:
                out.append((a, h, lv, ls))
                continue
            buf, size, n = [], 0, 1        # then at line boundaries (markdown paragraphs and table rows are lines)
            for line in ls:
                if buf and size + len(line) + 1 > MAX_SECTION_CHARS and "".join(buf).strip():
                    out.append((a if n == 1 else f"{a}-part-{n}", h if n == 1 else f"{h} (part {n})", lv, buf))
                    buf, size, n = [], 0, n + 1
                buf.append(line)
                size += len(line) + 1
            if "".join(buf).strip():
                out.append((a if n == 1 else f"{a}-part-{n}", h if n == 1 else f"{h} (part {n})", lv, buf))
    return out


def parse_page(path: str, raw: str) -> Page:
    fm, body = {}, raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                fm = {}
            body = parts[2]
    body = SHORTCODE_RE.sub("", body)
    url = path_to_url(path)
    parts = path.split("/")
    dept = (parts[2] if len(parts) > 3 else "") if path.startswith("content/handbook/") else \
        (parts[0] if len(parts) > 1 else "")
    h1 = re.search(r"^#\s+(.+)$", body, re.M)
    title = str(fm.get("title") or (h1.group(1).strip() if h1 else None)
                or re.sub(r"\.\w+$", "", url.strip("/").split("/")[-1]).replace("-", " ").title())
    page = Page(path=path, url=url, title=title, dept=dept)
    page.links = sorted({norm_url(u) for u in LINK_RE.findall(body)})

    # split on headings; text before first heading = "overview"
    chunks: list[tuple[str, str, int, list[str]]] = [("intro", title, 1, [])]
    in_code = False
    for line in body.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else HEADING_RE.match(line)
        if m and len(m.group(1)) <= 3:
            heading = m.group(2).strip()
            anchor = m.group(4) or slug(heading)
            chunks.append((anchor, heading, len(m.group(1)), []))
        else:
            chunks[-1][3].append(line)

    chunks = _split_long(chunks)
    seen: dict[str, int] = {}
    for order, (anchor, heading, level, lines) in enumerate(chunks):
        text = "\n".join(lines).strip()
        if not text:  # heading with only sub-headings under it
            continue
        n = seen.get(anchor, 0)
        seen[anchor] = n + 1
        if n:
            anchor = f"{anchor}-{n}"
        text = text[:MAX_SECTION_CHARS]
        # Hash the *normalized* text: whitespace/blank-line/trailing-space edits are not new versions.
        norm = re.sub(r"\s+", " ", f"{heading}\n{text}").strip()
        h = hashlib.sha1(norm.encode()).hexdigest()[:12]
        sid = f"{path}#{anchor}"
        page.sections.append(Section(sid, anchor, heading, level, order, text, h, cite_key(sid)))
    return page


# ---------- CODEOWNERS ----------

@dataclass
class OwnerRule:
    pattern: str
    owners: list[str]       # "@user", "@group/subgroup", or "@handle|Display Name"
    section: str            # [Section name] header in CODEOWNERS, or 'document'
    source: str = "explicit"  # explicit (declared) | inferred (e.g. file author metadata)


TEAM_WORDS = re.compile(r"\b(team|group|department|dept|committee|council|ops|office)\b", re.I)


def owner_handle(raw: str) -> str:
    """'Priya Shah <priya.shah@larkspur.example>' -> '@priya.shah|Priya Shah'
       'People Operations team'                 -> '@team/people-operations-team|People Operations team'"""
    raw = raw.strip().strip("*_`\"'")
    if raw.startswith("@"):
        return raw.lower()
    m = re.match(r"^(.*?)\s*<([^>]+@[^>]+)>$", raw) or re.match(r"^()([\w.+-]+@[\w.-]+)$", raw)
    if m:
        name, email = m.group(1).strip(), m.group(2)
        h = "@" + email.split("@")[0].lower()
        return f"{h}|{name}" if name else h
    name = re.sub(r"\s*\(.*?\)", "", raw).strip()
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if TEAM_WORDS.search(name):
        return f"@team/{s}|{name}"
    return f"@{s.replace('-', '.')}|{name}"


def parse_codeowners(txt: str) -> list[OwnerRule]:
    rules, section, default_owners = [], "", []
    for line in txt.splitlines():
        line = line.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\^?\[([^\]]+)\](?:\[\d+\])?\s*(.*)$", line)
        if m:
            section = m.group(1)
            default_owners = [o for o in m.group(2).split() if o.startswith("@")]
            continue
        parts = line.split()
        pat, owners = parts[0], [o for o in parts[1:] if o.startswith("@")]
        rules.append(OwnerRule(pat, owners or default_owners, section))
    return rules


def _matches(pattern: str, path: str) -> bool:
    p = pattern.lstrip("/")
    if p.endswith("/"):
        return path.startswith(p)
    if "*" in p:
        rx = re.escape(p).replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        return re.fullmatch(rx, path) is not None or re.match(rx + "/", path) is not None
    return path == p or path.startswith(p + "/")


def owners_for(path: str, rules: list[OwnerRule]) -> OwnerRule | None:
    """GitLab semantics: last matching rule wins."""
    hit = None
    for r in rules:
        if _matches(r.pattern, path):
            hit = r
    return hit
