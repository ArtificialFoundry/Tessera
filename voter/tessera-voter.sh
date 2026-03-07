#!/usr/bin/env bash
# Tessera voter agent — checks active DHCP server health, submits vote.
#
# Config: /etc/tessera/voter.conf
#   VOTER_NAME     — Voter identifier (required)
#   VOTER_PSK      — Pre-shared key for HMAC signing (required)
#   TESSERA_URL    — Tessera API base URL (required)
#   CHECK_TIMEOUT  — HTTP check timeout in seconds (default: 5)
#   DHCP_TIMEOUT   — nmap DHCP broadcast probe timeout in seconds (default: CHECK_TIMEOUT)
#   DHCP_INTERFACE — for nmap DHCP probe (default: auto-detect)
#
# Flow:
#   1. Fetch active server from Tessera (GET /api/v1/servers)
#   2. HTTP check (Technitium API ping) — primary check
#   3. DHCP broadcast probe (nmap) — fallback if HTTP fails
#   4. Submit signed vote with both check results
#
# Overall status: "up" if either check passes, "down" if both fail.
# Both http_status and dhcp_status are sent so Tessera UI shows
# per-check badges (e.g. "HTTP ✗ / DHCP ✓" = degraded but serving).
#
# Required: curl, openssl. Optional: nmap (DHCP broadcast probe).
set -euo pipefail

CONF="/etc/tessera/voter.conf"
[[ -f "$CONF" ]] || { echo "ERROR: $CONF not found" >&2; exit 1; }

# Parse config
while IFS='=' read -r key val; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    key=$(echo "$key" | xargs)
    val=$(echo "$val" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//')
    case "$key" in
        VOTER_NAME|VOTER_PSK|TESSERA_URL|CHECK_TIMEOUT|DHCP_TIMEOUT|DHCP_INTERFACE)
            export "$key=$val" ;;
    esac
done < "$CONF"

: "${VOTER_NAME:?VOTER_NAME not set}"
: "${VOTER_PSK:?VOTER_PSK not set}"
: "${TESSERA_URL:?TESSERA_URL not set}"
: "${CHECK_TIMEOUT:=5}"
: "${DHCP_TIMEOUT:=$CHECK_TIMEOUT}"
: "${DHCP_INTERFACE:=}"

# ── Submit vote ──────────────────────────────────────────────────────────────
_submit_vote() {
    local status="$1" http_s="$2" dhcp_s="$3" target="${4:-unknown}"
    local ts nonce sig
    ts=$(date +%s)
    nonce=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | od -A n -t x1 | tr -d ' \n')
    sig=$(printf '%s|%s|%s|%s' "$VOTER_NAME" "$status" "$ts" "$nonce" \
        | openssl dgst -sha256 -hmac "$VOTER_PSK" -hex 2>/dev/null \
        | awk '{print $NF}')

    local payload="{\"voter\":\"${VOTER_NAME}\",\"status\":\"${status}\",\"timestamp\":${ts},\"signature\":\"${sig}\",\"nonce\":\"${nonce}\""
    [[ -n "$http_s" ]] && payload="${payload},\"http_status\":\"${http_s}\""
    [[ -n "$dhcp_s" ]] && payload="${payload},\"dhcp_status\":\"${dhcp_s}\""
    payload="${payload}}"

    curl -sk --max-time 10 \
        -X POST "${TESSERA_URL}/api/v1/vote" \
        -H "Content-Type: application/json" \
        -d "$payload" \
        -o /dev/null -w "Vote: ${VOTER_NAME}=${status} http=${http_s:-n/a} dhcp=${dhcp_s:-n/a} (${target}) → %{http_code}\n" 2>/dev/null \
        || echo "WARN: Failed to submit vote to Tessera" >&2
}

# ── Fetch active server from Tessera ─────────────────────────────────────────
SERVERS_JSON=$(curl -sk --max-time "$CHECK_TIMEOUT" \
    "${TESSERA_URL}/api/v1/servers" 2>/dev/null) || {
    echo "ERROR: Cannot reach Tessera at $TESSERA_URL" >&2
    exit 1
}

ACTIVE_URL=""
for pattern in \
    '"role"\s*:\s*"active"[^}]*"url"\s*:\s*"[^"]*"' \
    '"url"\s*:\s*"[^"]*"[^}]*"role"\s*:\s*"active"'; do
    ACTIVE_URL=$(echo "$SERVERS_JSON" | grep -oP "$pattern" \
        | grep -oP '"url"\s*:\s*"\K[^"]+' | head -1 || true)
    [[ -n "$ACTIVE_URL" ]] && break
done

if [[ -z "$ACTIVE_URL" ]]; then
    echo "ERROR: No active server in Tessera response" >&2
    _submit_vote "down" "down" "" "no-active-server"
    exit 1
fi

ACTIVE_IP=$(echo "$ACTIVE_URL" | sed -E 's|https?://||;s|:[0-9]+.*||;s|/.*||')
ACTIVE_PORT=$(echo "$ACTIVE_URL" | grep -oP ':\K[0-9]+' || echo "53443")

# ── Health checks ────────────────────────────────────────────────────────────
_detect_interface() {
    [[ -n "$DHCP_INTERFACE" ]] && { echo "$DHCP_INTERFACE"; return; }
    if command -v ip &>/dev/null; then
        ip route show default 2>/dev/null | awk '{print $5; exit}'
    elif command -v route &>/dev/null; then
        route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}'
    fi
}

_check_dhcp() {
    command -v nmap &>/dev/null || return 1
    local iface
    iface=$(_detect_interface)
    [[ -z "$iface" ]] && return 1
    local out
    out=$(sudo nmap --script broadcast-dhcp-discover \
        -e "$iface" --script-args timeout="${DHCP_TIMEOUT}s" 2>/dev/null) || return 1
    echo "$out" | grep -q "Server Identifier: ${ACTIVE_IP}"
}

_check_http() {
    local code
    code=$(curl -sk --max-time "$CHECK_TIMEOUT" \
        "https://${ACTIVE_IP}:${ACTIVE_PORT}/api/dhcp/scopes/list?token=dummy" \
        -o /dev/null -w '%{http_code}' 2>/dev/null || echo "000")
    echo "$code" | grep -qE '^(200|401|403)$'
}

# ── Run checks ───────────────────────────────────────────────────────────────
# HTTP is the primary check. DHCP broadcast is the fallback/secondary.
# Both are always attempted so Tessera gets the full picture.

HTTP_STATUS="down"
DHCP_STATUS=""

_check_http && HTTP_STATUS="up"

# DHCP probe: attempt if nmap is available
if command -v nmap &>/dev/null; then
    DHCP_STATUS="down"
    _check_dhcp && DHCP_STATUS="up"
fi

# Overall: up if either check passes
if [[ "$HTTP_STATUS" == "up" || "$DHCP_STATUS" == "up" ]]; then
    STATUS="up"
else
    STATUS="down"
fi

# ── Submit ───────────────────────────────────────────────────────────────────
_submit_vote "$STATUS" "$HTTP_STATUS" "$DHCP_STATUS" "$ACTIVE_IP"
