#!/usr/bin/env bash
# tessera-add-server.sh — Register a Technitium DHCP server with Tessera.
#
# Adds a Technitium DNS/DHCP server to the Tessera failover pool. Each
# server can have its own unique API token for isolation.
#
# Usage:
#   ./tessera-add-server.sh \
#     --tessera-url http://tessera:8780 \
#     --admin-key YOUR_ADMIN_KEY \
#     --name u3 \
#     --url https://192.168.1.3:53443 \
#     --token TECHNITIUM_API_TOKEN
#
# Idempotent on the Tessera side (will error if server already exists).
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
TESSERA_URL=""
ADMIN_KEY=""
SERVER_NAME=""
SERVER_URL=""
SERVER_TOKEN=""
SERVER_ROLE="candidate"
SERVER_PRIORITY=10
VERIFY_CONNECTIVITY=true
DRY_RUN=false
QUIET=false

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

_log()  { [[ "$QUIET" == true ]] && return; echo -e "${GREEN}[✓]${NC} $*"; }
_warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
_err()  { echo -e "${RED}[✗]${NC} $*" >&2; }
_info() { [[ "$QUIET" == true ]] && return; echo -e "${CYAN}[i]${NC} $*"; }
_die()  { _err "$@"; exit 1; }

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
tessera-add-server.sh — Add a Technitium DHCP server to the Tessera pool.

Each Technitium server in the pool has a name, URL, role, and its own
API token. Tessera uses these servers for DHCP failover — monitoring
health, syncing scopes, and triggering promotion/demotion.

USAGE:
  ./tessera-add-server.sh [OPTIONS]

REQUIRED:
  --tessera-url URL        Tessera API URL (e.g. http://192.168.1.10:8780)
  --admin-key KEY          Tessera admin API key for authentication
  --name NAME              Server name (e.g. u3, dns-3, dc-east)
  --url URL                Technitium API URL (e.g. https://192.168.1.3:53443)

OPTIONAL:
  --token TOKEN            Technitium API token for this server.
                           Each server can have a unique token for security
                           isolation. If omitted, the global pool token is used.
  --role ROLE              Server role: active, candidate, observer (default: candidate)
                             active    — currently serving DHCP
                             candidate — standby, ready for promotion
                             observer  — monitored but never promoted
  --priority NUM           Failover priority, lower = promoted first (default: 10)
  --skip-verify            Don't test connectivity before registering
  --dry-run                Show what would happen without doing it
  --quiet                  Suppress informational output
  --help                   Show this help

EXAMPLES:
  # Add a standby server with its own API token:
  ./tessera-add-server.sh \
    --tessera-url http://192.168.1.10:8780 \
    --admin-key d6b9a031df69... \
    --name u3 \
    --url https://192.168.1.3:53443 \
    --token abc123...

  # Add an observer (monitoring only, never promoted):
  ./tessera-add-server.sh \
    --tessera-url http://192.168.1.10:8780 \
    --admin-key d6b9a031df69... \
    --name u4-observer \
    --url https://10.0.0.4:53443 \
    --role observer

  # Remove a server (via API directly):
  curl -X DELETE http://tessera:8780/api/v1/servers/u3 \
    -H "Authorization: Bearer YOUR_ADMIN_KEY"
EOF
    exit 0
}

# ── Argument parsing ─────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --tessera-url)   TESSERA_URL="$2"; shift 2 ;;
            --admin-key)     ADMIN_KEY="$2"; shift 2 ;;
            --name)          SERVER_NAME="$2"; shift 2 ;;
            --url)           SERVER_URL="$2"; shift 2 ;;
            --token)         SERVER_TOKEN="$2"; shift 2 ;;
            --role)          SERVER_ROLE="$2"; shift 2 ;;
            --priority)      SERVER_PRIORITY="$2"; shift 2 ;;
            --skip-verify)   VERIFY_CONNECTIVITY=false; shift ;;
            --dry-run)       DRY_RUN=true; shift ;;
            --quiet)         QUIET=true; shift ;;
            --help|-h)       usage ;;
            *)               _die "Unknown option: $1 (try --help)" ;;
        esac
    done
}

# ── Validation ───────────────────────────────────────────────────────────────
validate_args() {
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required"
    [[ -z "$ADMIN_KEY" ]] && _die "--admin-key is required"
    [[ -z "$SERVER_NAME" ]] && _die "--name is required"
    [[ -z "$SERVER_URL" ]] && _die "--url is required"

    case "$SERVER_ROLE" in
        active|candidate|observer) ;;
        *) _die "Invalid --role: $SERVER_ROLE (expected: active, candidate, observer)" ;;
    esac

    if ! [[ "$SERVER_PRIORITY" =~ ^[0-9]+$ ]]; then
        _die "--priority must be a non-negative integer"
    fi
}

# ── Connectivity check ──────────────────────────────────────────────────────
check_connectivity() {
    if [[ "$VERIFY_CONNECTIVITY" != true ]]; then
        _info "Skipping connectivity check (--skip-verify)"
        return
    fi

    # Check Tessera
    _info "Testing Tessera connectivity..."
    local code
    code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "$TESSERA_URL/api/v1/ping" 2>/dev/null || echo "000")"
    if [[ "$code" == "200" ]]; then
        _log "Tessera API reachable"
    else
        _die "Cannot reach Tessera at $TESSERA_URL (HTTP $code)"
    fi

    # Check Technitium
    _info "Testing Technitium server connectivity..."
    local token_param=""
    if [[ -n "$SERVER_TOKEN" ]]; then
        token_param="?token=$SERVER_TOKEN"
    fi
    code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "${SERVER_URL}/api/dhcp/scopes/list${token_param}" 2>/dev/null || echo "000")"
    case "$code" in
        200) _log "Technitium API reachable and authenticated" ;;
        401|403)
            if [[ -n "$SERVER_TOKEN" ]]; then
                _die "Technitium API reachable but token rejected (HTTP $code)"
            else
                _warn "Technitium API reachable but no token provided — will use pool default"
            fi
            ;;
        000) _die "Cannot reach Technitium at $SERVER_URL" ;;
        *) _warn "Technitium returned HTTP $code — proceeding anyway" ;;
    esac
}

# ── Register server ─────────────────────────────────────────────────────────
register_server() {
    _info "Adding server '$SERVER_NAME' to Tessera pool..."

    local payload="{\"name\":\"$SERVER_NAME\",\"url\":\"$SERVER_URL\",\"role\":\"$SERVER_ROLE\",\"priority\":$SERVER_PRIORITY"
    if [[ -n "$SERVER_TOKEN" ]]; then
        payload="$payload,\"token\":\"$SERVER_TOKEN\""
    fi
    payload="$payload}"

    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would POST to $TESSERA_URL/api/v1/servers"
        _info "[dry-run] Payload: $payload"
        return
    fi

    local response http_code body
    response="$(curl -sk --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/servers" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $ADMIN_KEY" \
        -d "$payload" \
        2>/dev/null)" || _die "Failed to reach Tessera"

    http_code="$(echo "$response" | tail -1)"
    body="$(echo "$response" | sed '$d')"

    case "$http_code" in
        200|201) _log "Server added successfully" ;;
        401)     _die "Admin key rejected — check --admin-key" ;;
        409)     _die "Server '$SERVER_NAME' already exists in the pool" ;;
        *)       _die "Failed to add server (HTTP $http_code): $body" ;;
    esac
}

# ── Summary ──────────────────────────────────────────────────────────────────
summary() {
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  DHCP Server Added to Tessera${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Name:          ${CYAN}$SERVER_NAME${NC}"
    echo -e "  URL:           ${CYAN}$SERVER_URL${NC}"
    echo -e "  Role:          ${CYAN}$SERVER_ROLE${NC}"
    echo -e "  Priority:      ${CYAN}$SERVER_PRIORITY${NC}"
    echo -e "  Token:         ${CYAN}${SERVER_TOKEN:+per-server}${SERVER_TOKEN:-pool default}${NC}"
    echo ""
    echo -e "  ${BOLD}Next steps:${NC}"
    echo "    • Verify in Tessera UI → Servers page"
    if [[ "$SERVER_ROLE" == "candidate" ]]; then
        echo "    • Server will be promoted automatically if the active server fails"
        echo "    • Or promote manually: Servers → Promote"
    fi
    echo "    • Set up voter agents to monitor this server's health"
    echo ""
    echo -e "  ${BOLD}Manage:${NC}"
    echo "    # Promote to active:"
    echo "    curl -X POST $TESSERA_URL/api/v1/servers/$SERVER_NAME/promote \\"
    echo "      -H 'Authorization: Bearer \$ADMIN_KEY'"
    echo ""
    echo "    # Remove from pool:"
    echo "    curl -X DELETE $TESSERA_URL/api/v1/servers/$SERVER_NAME \\"
    echo "      -H 'Authorization: Bearer \$ADMIN_KEY'"
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    parse_args "$@"
    validate_args
    check_connectivity
    register_server
    summary
}

main "$@"
