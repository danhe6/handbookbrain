"""Runtime config. Everything company-specific lives in companies/<name>.yaml; pick one with
`python -m hb --company <name> ...` (or HB_COMPANY). Onboarding a new company = writing that one file."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# ---------------------------------------------------------------- models / infra (shared)
MODEL = os.getenv("HB_MODEL", "anthropic/claude-sonnet-5-5")          # agent / writer
if MODEL.startswith("anthropic/") and not os.getenv("ANTHROPIC_API_KEY"):
    MODEL = os.getenv("HB_FALLBACK_MODEL", "openai/gpt-4.1")           # no Anthropic key -> OpenAI
CRITIC_MODEL = os.getenv("HB_CRITIC_MODEL", "openai/gpt-4o-mini")      # independent reviewer
EMBED_MODEL = os.getenv("EMBED_MODEL", "openai/text-embedding-3-small")
EMBED_DIM = int(os.getenv("EMBED_DIM", "1536"))
FALKOR_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.getenv("FALKORDB_PORT", "6379"))

# ---------------------------------------------------------------- company
COMPANY = os.getenv("HB_COMPANY", "gitlab")
_cfg_file = ROOT / "companies" / f"{COMPANY}.yaml"
CFG: dict = yaml.safe_load(_cfg_file.read_text()) if _cfg_file.exists() else {}

GITLAB_DEFAULTS = {
    "graph": "handbookbrain",
    "docs": {"type": "git", "path": "data/handbook",
             "scope": ["content/handbook/people-group/", "content/handbook/total-rewards/",
                       "content/handbook/hiring/", "content/handbook/people-policies/"],
             "codeowners": [".gitlab/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"],
             "cite_strip": "content/handbook/",
             "doc_base_url": "https://handbook.gitlab.com",
             "mr_url": "https://gitlab.com/gitlab-com/content-sites/handbook/-/merge_requests/",
             "commit_url": "https://gitlab.com/gitlab-com/content-sites/handbook/-/commit/"},
    "t0": "2026-04-01",
    "conversations": "data/synthetic",
    "labels": "data/synthetic/labels.json",
}
if COMPANY == "gitlab":
    CFG = {**GITLAB_DEFAULTS, **CFG, "docs": {**GITLAB_DEFAULTS["docs"], **CFG.get("docs", {})}}

# Several document sources per company (optional). Each: {name, type: git|snapshots|uploads, path, kind: live|static,
# authority: higher wins when sources disagree}. Without `sources:` the single `docs:` entry is the only source.
SOURCES: list[dict] = CFG.get("sources") or []
DOCS = CFG.get("docs") or next((s for s in SOURCES if s.get("type") == "git"), None) or (SOURCES[0] if SOURCES else {})
DOCS_TYPE = "multi" if len(SOURCES) > 1 else DOCS.get("type", "snapshots")   # git | snapshots | uploads | multi
REPO = ROOT / DOCS.get("path", "data/handbook")
SCOPE = DOCS.get("scope", [])
CODEOWNERS_PATHS = DOCS.get("codeowners", [])
CITE_STRIP = DOCS.get("cite_strip", "")
HANDBOOK_URL = DOCS.get("doc_base_url", "")
MR_URL = DOCS.get("mr_url", "")
COMMIT_URL = DOCS.get("commit_url", "")
GRAPH = os.getenv("HB_GRAPH") if COMPANY == "gitlab" and os.getenv("HB_GRAPH") else CFG.get("graph", COMPANY)
T0 = os.getenv("HB_T0") if COMPANY == "gitlab" and os.getenv("HB_T0") else str(CFG.get("t0", "2026-04-01"))
CONV_DIR = ROOT / CFG.get("conversations", "data/synthetic")
LABELS = ROOT / CFG["labels"] if CFG.get("labels") else None
QUESTIONS = ROOT / CFG["questions"] if CFG.get("questions") else None
COMPANY_NAME = CFG.get("name") or ("GitLab" if COMPANY == "gitlab" else COMPANY.replace("-", " ").title())
RUNS = ROOT / "runs" / COMPANY  # JSONL agent traces, eval output

DOC_EXTS = (".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".docx", ".csv")


def in_scope(path: str, typ: str | None = None, scope: list | None = None) -> bool:
    typ, scope = typ or DOCS_TYPE, SCOPE if scope is None else scope
    if typ == "git":
        return path.endswith(".md") and any(path.startswith(s) for s in scope)
    return path.lower().endswith(DOC_EXTS) and (not scope or any(path.startswith(s) for s in scope))
