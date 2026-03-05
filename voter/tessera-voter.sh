#!/usr/bin/env bash
# Tessera DHCP failover voter agent.
# Deployed to VMs as a systemd timer. Checks primary DHCP health
# and submits a signed vote to the Tessera API.
#
# Config: /etc/tessera/voter.conf
# Required: curl, openssl
set -euo pipefail

CONF="/etc/tessera/voter.conf"
if [[ ! -f "$CONF" ]]; then
    echo "ERROR: $CONF not found" >&2
    exit 1
fi
# shellcheck source=/dev/null
source "$CONF"

# Required vars: VOTER_NAME, VOTER_PSK, TESSERA_URL, PRIMARY_IP
: "${VOTER_NAME:?VOTER_NAME not set}"
: "${VOTER_PSK:?VOTER_PSK not set}"
: "${TESSERA_URL:?TESSERA_URL not set}"
: "${PRIMARY_IP:?PRIMARY_IP not set}"
: "${PRIMARY_PORT:=53443}"
: "${CHECK_TIMEOUT:=5}"

# Health check: try to hit Technitium DHCP API on primary
STATUS="down"
if curl -sk --max-time "$CHECK_TIMEOUT" \
    "https://${PRIMARY_IP}:${PRIMARY_PORT}/api/dhcp/scopes/list?token=dummy" \
    -o /dev/null -w '%{http_code}' 2>/dev/null | grep -qE '^(200|401|403)$'; then
    # 200 = valid token, 401/403 = server is up but token wrong — still alive
    STATUS="up"
fi

TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s|%s|%s' "$VOTER_NAME" "$STATUS" "$TIMESTAMP" \
    | openssl dgst -sha256 -hmac "$VOTER_PSK" -hex 2>/dev/null \
    | awk '{print $NF}')

curl -sk --max-time 10 \
    -X POST "${TESSERA_URL}/api/v1/vote" \
    -H "Content-Type: application/json" \
    -d "{\"voter\":\"${VOTER_NAME}\",\"status\":\"${STATUS}\",\"timestamp\":${TIMESTAMP},\"signature\":\"${SIGNATURE}\"}" \
    -o /dev/null -w "Vote submitted: %{http_code}\n"
