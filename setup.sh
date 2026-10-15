#!/usr/bin/env bash
# HandbookBrain local setup. Run from the project root:  bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

# Departments we ingest (keeps LLM cost + graph size sane). Edit hb/config.py to change.
SPARSE_DIRS="content/handbook/people-group content/handbook/total-rewards content/handbook/hiring content/handbook/people-policies .gitlab"

echo "==> 1/4 Clone GitLab handbook (markdown only, images skipped)"
if [ ! -d data/handbook/.git ]; then
  mkdir -p data
  # blob:limit keeps every markdown file but skips large images/PDFs
  git clone --filter=blob:limit=200k --no-checkout \
    https://gitlab.com/gitlab-com/content-sites/handbook.git data/handbook
  git -C data/handbook sparse-checkout set $SPARSE_DIRS
  git -C data/handbook checkout main
else
  echo "    already cloned, fetching"
  git -C data/handbook sparse-checkout set $SPARSE_DIRS
  git -C data/handbook pull -q origin main
fi
git -C data/handbook log -1 --format='    HEAD %h %ci %s'

echo "==> 2/4 Start FalkorDB (Docker Desktop must be running)"
if ! docker ps --format '{{.Names}}' | grep -q '^falkordb$'; then
  docker rm -f falkordb >/dev/null 2>&1 || true
  docker run -d --name falkordb -p 6379:6379 -p 3000:3000 \
    -v "$PWD/data/falkordb:/var/lib/falkordb/data" falkordb/falkordb:latest
fi
echo "    FalkorDB Browser: http://localhost:3000"

echo "==> 3/4 Python env -> .venv (needs Python >= 3.10)"
PY=""
for c in "${CONDA_PREFIX:+$CONDA_PREFIX/bin/python}" python3.12 python3.11 python3.13 python3.10 python3; do
  [ -z "$c" ] && continue
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(sys.version_info < (3,10))' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "    No Python >= 3.10 found. Run: conda create -y -n hb python=3.12 && conda activate hb, then re-run."
  exit 1
fi
echo "    base interpreter: $("$PY" --version) ($PY)"
"$PY" -m venv --clear .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python -c "import falkordb, litellm, dotenv; print('    deps OK')"

echo "==> 4/4 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Created .env  ->  put your ANTHROPIC_API_KEY in it"
fi

echo
echo "Done. Next:"
echo "  source .venv/bin/activate"
echo "  python -m hb doctor        # checks git, FalkorDB, API key"
echo "  python -m hb init          # builds the graph at T0"
echo "  python -m hb ask \"How many days of PTO can I take?\""
