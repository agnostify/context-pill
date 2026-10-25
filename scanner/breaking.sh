#!/usr/bin/env bash
# Breaking AI News refresh (every BREAKING_REFRESH_HOURS, default 3): runs only scanner.breaking, which re-renders only the
# Breaking block of latest.md/latest.json. The checked-claims drop stays daily (scanner/run.sh). Scheduling and pushing
# these refreshes is a separate, maintainer-approved step. A failed refresh keeps the last good section (non-zero exit).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${CP_PYTHON:-python3}"
[ -f "${CP_SECRETS:-/nonexistent}" ] && { set -a; . "$CP_SECRETS"; set +a; }
"$PY" -m scanner.tests.test_breaking
"$PY" -m scanner.breaking "$@"
"$PY" -m scanner.gate
