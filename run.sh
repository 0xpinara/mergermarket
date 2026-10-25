#!/usr/bin/env bash
# MergeProof: build the frontend and serve app + API from one process.
#   ./run.sh            -> http://127.0.0.1:8000
#   PORT=9000 ./run.sh  -> custom port
#   ./run.sh test       -> backend test suite
#   ./run.sh eval       -> ground-truth evaluation (SEEDS=30): backend/eval/results.json (engine) and
#                          backend/eval/results_integrate.json (stress cells, mapping planner, dry run)
#   ./run.sh mcp        -> MergeProof MCP server on stdio, for AI agents
#   MERGEPROOF_AI=off   -> rules only, no local model
set -euo pipefail
cd "$(dirname "$0")"
# Setup chatter goes to stderr so stdout stays clean (the MCP stdio transport speaks on stdout).
exec 3>&1 1>&2

PY="${PYTHON:-python3.12}"
if [ ! -x backend/.venv/bin/python ]; then
  echo "› creating backend virtualenv with $PY"
  "$PY" -m venv backend/.venv
fi
backend/.venv/bin/pip install -q -r backend/requirements-dev.txt

case "${1:-}" in
  test) cd backend && exec .venv/bin/python -m pytest -q 1>&3 ;;
  eval) cd backend && .venv/bin/python -m eval.harness --seeds "${SEEDS:-30}" 1>&3 \
          && exec .venv/bin/python -m eval.integrate_eval --seeds "${SEEDS:-30}" 1>&3 ;;
  mcp)  cd backend && exec .venv/bin/python -m app.mcp_server 1>&3 ;;
esac

# Fetch the local AI model once (about 130 MB, free). Optional: without it MergeProof runs rules only.
if [ "${MERGEPROOF_AI:-on}" != "off" ]; then
  (cd backend && .venv/bin/python -m app.ai.embeddings >/dev/null 2>&1) \
    && echo "› local AI model ready" || echo "› local AI model unavailable — running rules only"
fi

if [ ! -d frontend/node_modules ]; then
  echo "› installing frontend dependencies"
  (cd frontend && npm install --no-audit --no-fund)
fi
echo "› building frontend"
(cd frontend && npm run build >/dev/null)

echo "› MergeProof on http://127.0.0.1:${PORT:-8000}"
cd backend && exec .venv/bin/uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" 1>&3
