#!/usr/bin/env bash
# Voter agent for Tessera DHCP failover
# Deployed to each voting VM — checks if primary DHCP is healthy
# and submits a signed vote to the Tessera controller.
set -euo pipefail

VOTER_NAME="$(hostname -s)"
PSK_FILE="/etc/tessera/voter.key"
CONTROLLER_URL="${TESSERA_URL:-http://192.0.2.20:8780}"
CHECK_HOST="${TESSERA_CHECK_HOST:-192.0.2.1}"
CHECK_PORT="${TESSERA_CHECK_PORT:-67}"

# Read PSK
if [[ ! -f "$PSK_FILE" ]]; then
    echo "ERROR: PSK file not found: $PSK_FILE" >&2
    exit 1
fi
PSK="$(cat "$PSK_FILE")"

# Check if primary DHCP is reachable (TCP to DNS port as proxy for health)
if timeout 5 bash -c "echo >/dev/tcp/$CHECK_HOST/53" 2>/dev/null; then
    STATUS="up"
else
    STATUS="down"
fi

# Build signed payload
TIMESTAMP="$(date +%s)"
MESSAGE="${VOTER_NAME}|${STATUS}|${TIMESTAMP}"
SIGNATURE="$(echo -n "$MESSAGE" | openssl dgst -sha256 -hmac "$PSK" | sed 's/^.* //')"

# Submit vote
PAYLOAD=$(cat <<EOF
{"voter":"${VOTER_NAME}","status":"${STATUS}","timestamp":${TIMESTAMP},"signature":"${SIGNATURE}"}
EOF
)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
    "${CONTROLLER_URL}/api/v1/vote" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD" \
    --max-time 10 2>&1) || true

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "$(date -Iseconds) [${VOTER_NAME}] Vote submitted: ${STATUS}"
else
    echo "$(date -Iseconds) [${VOTER_NAME}] Vote FAILED (HTTP ${HTTP_CODE}): ${BODY}" >&2
fi
