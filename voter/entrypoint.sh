#!/usr/bin/env bash
# Tessera voter container entrypoint — runs voter script in a loop.
set -euo pipefail

: "${VOTER_INTERVAL:=30}"

echo "Tessera voter agent starting (interval=${VOTER_INTERVAL}s)"

# Validate config exists
if [[ ! -f /etc/tessera/voter.conf ]]; then
    echo "ERROR: /etc/tessera/voter.conf not found. Mount it as a volume." >&2
    exit 1
fi

while true; do
    /usr/local/bin/tessera-voter.sh || echo "WARN: voter script exited with $?" >&2
    sleep "$VOTER_INTERVAL"
done
