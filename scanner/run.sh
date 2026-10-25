#!/usr/bin/env bash
# Daily scanner run on the box. Keys come from the environment only (e.g. GEMINI_API_KEY); nothing secret lives in the repo.
# Push is a separate step, gated in the routine prompt (first public push needs the maintainer's review).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${CP_PYTHON:-python3}"
[ -f "${CP_SECRETS:-/nonexistent}" ] && { set -a; . "$CP_SECRETS"; set +a; }
# Gemini video notes: route approved by the maintainer (live since Oct 23); the key itself still comes only from the environment.
export CP_GEMINI_APPROVED="${CP_GEMINI_APPROVED:-1}" GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"
"$PY" -m scanner.tests.test_trust
"$PY" -m scanner.tests.test_tools
"$PY" -m scanner.tests.test_repeats
"$PY" -m scanner.tests.test_breaking || echo "breaking tests failed; the daily drop continues (Breaking AI News never blocks it)"
"$PY" -m scanner.run "$@"
