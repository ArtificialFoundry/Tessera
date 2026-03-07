#!/usr/bin/env bash
# Tessera DHCP failover voter agent.
# Deployed to VMs as a systemd timer. Checks primary DHCP health
# and submits a signed vote to the Tessera API.
#
# Config: /etc/tessera/voter.conf
# Required: curl, openssl
# Optional: nmap (for real DHCP probe — requires root/sudo)
#
# DHCP probe uses `nmap --script broadcast-dhcp-discover` to send a
# DHCP DISCOVER and verify the primary server responds with a DHCP OFFER.
# This proves DHCP is actually serving leases, not just that the web API is up.
# Fallback to HTTP check if nmap is not installed.
#
# Config variables (voter.conf):
#   VOTER_NAME      - (required) Voter identifier
#   VOTER_PSK       - (required) Pre-shared key for HMAC signing
#   TESSERA_URL     - (required) Tessera API base URL
#   PRIMARY_IP      - (required) Primary DHCP server IP
#   PRIMARY_PORT    - (optional, default: 53443) Technitium API port
#   CHECK_TIMEOUT   - (optional, default: 5) Timeout for checks in seconds
#   DHCP_INTERFACE  - (optional, default: auto-detect) Network interface for DHCP probe
#   CHECK_METHOD    - (optional, default: dhcp) One of: dhcp, http, both
#                     dhcp  = nmap probe, fallback to http if nmap missing
#                     http  = HTTP API check only (legacy behavior)
#                     both  = require both dhcp AND http to pass
set -euo pipefail

CONF="/etc/tessera/voter.conf"
if [[ ! -f "$CONF" ]]; then
    echo "ERROR: $CONF not found" >&2
    exit 1
fi
# shellcheck source=/dev/null
while IFS='=' read -r key val; do
    # Skip comments and empty lines
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    # Strip quotes and leading/trailing whitespace
    key=$(echo "$key" | xargs)
    val=$(echo "$val" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//')
    # Only accept known variables
    case "$key" in
        VOTER_NAME|VOTER_PSK|TESSERA_URL|PRIMARY_IP|PRIMARY_PORT|CHECK_TIMEOUT|DHCP_INTERFACE|CHECK_METHOD)
            export "$key=$val"
            ;;
    esac
done < "$CONF"

# Required vars: VOTER_NAME, VOTER_PSK, TESSERA_URL, PRIMARY_IP
: "${VOTER_NAME:?VOTER_NAME not set}"
: "${VOTER_PSK:?VOTER_PSK not set}"
: "${TESSERA_URL:?TESSERA_URL not set}"
: "${PRIMARY_IP:?PRIMARY_IP not set}"
: "${PRIMARY_PORT:=53443}"
: "${CHECK_TIMEOUT:=5}"
: "${DHCP_INTERFACE:=}"
: "${CHECK_METHOD:=dhcp}"

# Auto-detect primary network interface if not set
_detect_interface() {
    if [[ -n "$DHCP_INTERFACE" ]]; then
        echo "$DHCP_INTERFACE"
        return
    fi
    # Linux: ip route, macOS/BSD: route + ifconfig
    if command -v ip &>/dev/null; then
        ip route show default 2>/dev/null | awk '{print $5; exit}'
    elif command -v route &>/dev/null; then
        route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}'
    fi
}

# DHCP probe via nmap broadcast-dhcp-discover
# Requires: nmap, root/sudo privileges
# Returns 0 if PRIMARY_IP responds with a DHCP OFFER, 1 otherwise
_check_dhcp() {
    local iface
    iface=$(_detect_interface)
    if [[ -z "$iface" ]]; then
        echo "WARN: Cannot detect network interface for DHCP probe" >&2
        return 1
    fi

    local nmap_out
    # nmap broadcast-dhcp-discover needs raw sockets → root/sudo
    if ! nmap_out=$(sudo nmap --script broadcast-dhcp-discover \
        -e "$iface" --script-args timeout="${CHECK_TIMEOUT}s" 2>/dev/null); then
        echo "WARN: nmap DHCP probe failed" >&2
        return 1
    fi

    # Check if the primary IP appears as the DHCP server in the response
    if echo "$nmap_out" | grep -q "Server Identifier: ${PRIMARY_IP}"; then
        return 0
    else
        echo "WARN: DHCP OFFER not from ${PRIMARY_IP}" >&2
        return 1
    fi
}

# HTTP API check (legacy method)
# Returns 0 if Technitium web API responds, 1 otherwise
_check_http() {
    local http_code
    http_code=$(curl -sk --max-time "$CHECK_TIMEOUT" \
        "https://${PRIMARY_IP}:${PRIMARY_PORT}/api/dhcp/scopes/list?token=dummy" \
        -o /dev/null -w '%{http_code}' 2>/dev/null || echo "000")
    if echo "$http_code" | grep -qE '^(200|401|403)$'; then
        return 0
    fi
    return 1
}

# Determine status based on CHECK_METHOD
STATUS="down"
case "$CHECK_METHOD" in
    dhcp)
        if command -v nmap &>/dev/null; then
            _check_dhcp && STATUS="up"
        else
            echo "WARN: nmap not found, falling back to HTTP check" >&2
            _check_http && STATUS="up"
        fi
        ;;
    http)
        _check_http && STATUS="up"
        ;;
    both)
        dhcp_ok=false
        http_ok=false
        if command -v nmap &>/dev/null; then
            _check_dhcp && dhcp_ok=true
        else
            echo "WARN: nmap not found, DHCP check skipped in 'both' mode" >&2
        fi
        _check_http && http_ok=true
        if $dhcp_ok && $http_ok; then
            STATUS="up"
        fi
        ;;
    *)
        echo "ERROR: Invalid CHECK_METHOD: $CHECK_METHOD (expected: dhcp, http, both)" >&2
        exit 1
        ;;
esac

TIMESTAMP=$(date +%s)
SIGNATURE=$(printf '%s|%s|%s' "$VOTER_NAME" "$STATUS" "$TIMESTAMP" \
    | openssl dgst -sha256 -hmac "$VOTER_PSK" -hex 2>/dev/null \
    | awk '{print $NF}')

curl -sk --max-time 10 \
    -X POST "${TESSERA_URL}/api/v1/vote" \
    -H "Content-Type: application/json" \
    -d "{\"voter\":\"${VOTER_NAME}\",\"status\":\"${STATUS}\",\"timestamp\":${TIMESTAMP},\"signature\":\"${SIGNATURE}\"}" \
    -o /dev/null -w "Vote submitted: %{http_code}\n"
