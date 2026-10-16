#!/usr/bin/env bash
# Daily scanner run on the box. Keys come from the environment only (e.g. GEMINI_API_KEY); nothing secret lives in the repo.
# Push is a separate step, gated in the routine prompt (first public push needs the maintainer's review).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${CP_PYTHON:-python3}"
[ -f "${CP_SECRETS:-/nonexistent}" ] && { set -a; . "$CP_SECRETS"; set +a; }
"$PY" -m scanner.tests.test_trust
"$PY" -m scanner.tests.test_tools
"$PY" -m scanner.run "$@"
