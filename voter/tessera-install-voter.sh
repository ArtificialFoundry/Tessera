#!/usr/bin/env bash
# tessera-install-voter.sh — Install a Tessera voter agent.
#
# Registers with Tessera, writes config, installs the voter script,
# and sets up a systemd timer (or cron fallback).
#
# Usage:
#   sudo ./tessera-install-voter.sh \
#     --tessera-url https://tessera.example.com \
#     --token <registration-token>
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
VOTER_NAME=""
TESSERA_URL=""
VOTER_PSK=""
REGISTRATION_TOKEN=""
WAIT_APPROVAL=300
ROTATE_KEY=false
UNINSTALL=false
DRY_RUN=false
QUIET=false

INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/tessera"
SYSTEMD_DIR="/etc/systemd/system"

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

_log()  { [[ "$QUIET" == true ]] && return; echo -e "${GREEN}[✓]${NC} $*"; }
_warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
_err()  { echo -e "${RED}[✗]${NC} $*" >&2; }
_info() { [[ "$QUIET" == true ]] && return; echo -e "${CYAN}[i]${NC} $*"; }
_die()  { _err "$@"; exit 1; }

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
tessera-install-voter.sh — Install and register a Tessera voter agent.

The voter agent periodically asks Tessera which DHCP server is active,
health-checks it, and submits a signed vote. Tessera uses these votes
to decide when to trigger failover.

USAGE:
  sudo ./tessera-install-voter.sh [OPTIONS]

REQUIRED:
  --tessera-url URL        Tessera API URL (e.g. https://tessera.example.com)
  --token TOKEN            One-time registration token (from Tessera admin UI)

OPTIONAL:
  --name NAME              Voter name (default: hostname)
  --wait-approval SEC      Wait for admin approval (default: 300, 0=skip)
  --rotate-key             Rotate PSK for an existing voter
  --uninstall              Remove voter agent completely
  --dry-run                Show what would happen
  --quiet                  Suppress informational output

EXAMPLES:
  # First-time setup:
  sudo ./tessera-install-voter.sh \
    --tessera-url https://tessera.example.com \
    --token eyJhbGciOi...

  # Re-run (uses existing config):
  sudo ./tessera-install-voter.sh --tessera-url https://tessera.example.com

  # Rotate PSK:
  sudo ./tessera-install-voter.sh --rotate-key

  # Uninstall:
  sudo ./tessera-install-voter.sh --uninstall
EOF
    exit 0
}

# ── Parse args ───────────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name)          VOTER_NAME="$2"; shift 2 ;;
            --tessera-url)   TESSERA_URL="$2"; shift 2 ;;
            --token)         REGISTRATION_TOKEN="$2"; shift 2 ;;
            --wait-approval) WAIT_APPROVAL="$2"; shift 2 ;;
            --rotate-key)    ROTATE_KEY=true; shift ;;
            --uninstall)     UNINSTALL=true; shift ;;
            --dry-run)       DRY_RUN=true; shift ;;
            --quiet)         QUIET=true; shift ;;
            --help|-h)       usage ;;
            # Deprecated (ignored with warning)
            --active-ip|--target-ip|--primary-ip)
                _warn "$1 is no longer needed (voter fetches active server from Tessera)"; shift 2 ;;
            --active-port|--target-port|--primary-port)
                _warn "$1 is no longer needed"; shift 2 ;;
            --psk)           VOTER_PSK="$2"; _warn "--psk deprecated; use --token to register"; shift 2 ;;
            --check-method|--interface|--check-timeout)
                _warn "$1 is now configured in voter.conf after install"; shift 2 ;;
            --skip-firewall|--skip-nmap|--auto-register|--use-static-token)
                _warn "$1 is deprecated and ignored"; shift ;;
            --registration-token)
                REGISTRATION_TOKEN="$2"; _warn "--registration-token → --token"; shift 2 ;;
            *)               _die "Unknown option: $1 (try --help)" ;;
        esac
    done
}

# ── Preflight ────────────────────────────────────────────────────────────────
preflight() {
    [[ $EUID -ne 0 ]] && _die "Must run as root (or sudo)"

    for cmd in curl openssl; do
        command -v "$cmd" &>/dev/null || _die "Required: $cmd"
    done

    if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
        INIT_SYSTEM="systemd"
    else
        INIT_SYSTEM="cron"
    fi

    [[ -z "$VOTER_NAME" ]] && VOTER_NAME="$(hostname -s 2>/dev/null || echo "voter-$$")"
    _info "Voter: $VOTER_NAME | Init: $INIT_SYSTEM"
}

# ── Read existing config ────────────────────────────────────────────────────
load_existing_config() {
    [[ -f "$CONFIG_DIR/voter.conf" ]] || return
    while IFS='=' read -r key val; do
        [[ "$key" =~ ^[[:space:]]*# ]] && continue
        [[ -z "$key" ]] && continue
        key=$(echo "$key" | xargs)
        val=$(echo "$val" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//')
        case "$key" in
            TESSERA_URL) [[ -z "$TESSERA_URL" ]] && TESSERA_URL="$val" ;;
            VOTER_PSK)   [[ -z "$VOTER_PSK" ]] && VOTER_PSK="$val" ;;
            VOTER_NAME)  [[ -z "$VOTER_NAME" ]] && VOTER_NAME="$val" ;;
        esac
    done < "$CONFIG_DIR/voter.conf"
}

# ── Uninstall ────────────────────────────────────────────────────────────────
do_uninstall() {
    _info "Uninstalling Tessera voter agent..."
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        systemctl disable --now tessera-voter.timer 2>/dev/null || true
        systemctl disable --now tessera-voter.service 2>/dev/null || true
        rm -f "$SYSTEMD_DIR/tessera-voter.service" "$SYSTEMD_DIR/tessera-voter.timer"
        systemctl daemon-reload 2>/dev/null || true
    fi
    rm -f /etc/cron.d/tessera-voter
    rm -f "$INSTALL_DIR/tessera-voter.sh"
    _log "Removed. Config at $CONFIG_DIR left intact (has secrets)."
    exit 0
}

# ── Register ─────────────────────────────────────────────────────────────────
do_register() {
    [[ -z "$REGISTRATION_TOKEN" ]] && return
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url required for registration"

    _info "Registering '$VOTER_NAME'..."

    local resp code body
    resp=$(curl -s --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/register" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"$VOTER_NAME\",\"token\":\"$REGISTRATION_TOKEN\"}" \
        2>/dev/null) || _die "Cannot reach Tessera"

    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')

    case "$code" in
        401) _die "Token rejected (invalid, expired, or used)" ;;
        403) _die "Token not valid from this IP" ;;
        409) _die "'$VOTER_NAME' already registered" ;;
    esac
    [[ "$code" != "200" ]] && _die "Registration failed (HTTP $code): $body"

    local status psk
    status=$(echo "$body" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    psk=$(echo "$body" | grep -o '"psk":"[^"]*"' | cut -d'"' -f4)

    if [[ "$status" == "active" && -n "$psk" ]]; then
        VOTER_PSK="$psk"
        _log "Registered and approved"
        return
    fi

    if [[ "$status" == "pending" ]]; then
        _info "Registered — pending admin approval"
        [[ "$WAIT_APPROVAL" -le 0 ]] && { _warn "Not waiting. Re-run after approval."; exit 0; }

        local elapsed=0
        while [[ $elapsed -lt $WAIT_APPROVAL ]]; do
            sleep 5; elapsed=$((elapsed + 5))
            local pr pc pb vs
            pr=$(curl -s --max-time 5 -w '\n%{http_code}' \
                "$TESSERA_URL/api/v1/voters" 2>/dev/null) || continue
            pc=$(echo "$pr" | tail -1); pb=$(echo "$pr" | sed '$d')
            [[ "$pc" == "200" ]] || continue
            vs=$(echo "$pb" | grep -o "\"name\":\"$VOTER_NAME\"[^}]*" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
            if [[ "$vs" == "approved" ]]; then
                _log "Approved after ${elapsed}s"
                _warn "PSK was shown in the admin UI. Set it in $CONFIG_DIR/voter.conf"
                exit 0
            fi
            printf "\r  Waiting... (%d/%ds)" "$elapsed" "$WAIT_APPROVAL"
        done
        echo ""
        _die "Approval timeout. Ask admin to approve '$VOTER_NAME'."
    fi

    _die "Unexpected response: $body"
}

# ── Rotate key ───────────────────────────────────────────────────────────────
do_rotate_key() {
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url required"

    local resp code body
    resp=$(curl -s --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/$VOTER_NAME/rotate-key" \
        -H "Content-Type: application/json" 2>/dev/null) || _die "Cannot reach Tessera"

    code=$(echo "$resp" | tail -1)
    body=$(echo "$resp" | sed '$d')
    [[ "$code" != "200" ]] && _die "Rotation failed (HTTP $code): $body"

    local new_psk
    new_psk=$(echo "$body" | grep -o '"new_psk":"[^"]*"' | cut -d'"' -f4)
    [[ -z "$new_psk" ]] && _die "No PSK in response"

    VOTER_PSK="$new_psk"
    _log "PSK rotated"
}

# ── Setup PSK ────────────────────────────────────────────────────────────────
setup_psk() {
    [[ -n "$VOTER_PSK" ]] && return

    echo ""
    _err "No PSK. Register first:"
    echo ""
    echo -e "  ${BOLD}sudo ./tessera-install-voter.sh \\${NC}"
    echo -e "  ${BOLD}  --tessera-url $TESSERA_URL \\${NC}"
    echo -e "  ${BOLD}  --token <token-from-admin-ui>${NC}"
    echo ""
    _die "Cannot continue without PSK"
}

# ── Write config ─────────────────────────────────────────────────────────────
write_config() {
    [[ "$DRY_RUN" == true ]] && { _info "[dry-run] Would write $CONFIG_DIR/voter.conf"; return; }

    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_DIR/voter.conf" <<EOF
# Tessera voter — generated $(date -u +"%Y-%m-%dT%H:%M:%SZ")
VOTER_NAME="$VOTER_NAME"
VOTER_PSK="$VOTER_PSK"
TESSERA_URL="$TESSERA_URL"
CHECK_TIMEOUT="5"
EOF
    chmod 600 "$CONFIG_DIR/voter.conf"
    _log "Config: $CONFIG_DIR/voter.conf"
}

# ── Install script ───────────────────────────────────────────────────────────
install_script() {
    [[ "$DRY_RUN" == true ]] && return
    local dir; dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ -f "$dir/tessera-voter.sh" ]]; then
        cp "$dir/tessera-voter.sh" "$INSTALL_DIR/tessera-voter.sh"
    elif [[ -n "$TESSERA_URL" ]]; then
        curl -sfL "$TESSERA_URL/voter/tessera-voter.sh" \
            -o "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null \
            || _die "Cannot find tessera-voter.sh"
    else
        _die "tessera-voter.sh not found"
    fi
    chmod 755 "$INSTALL_DIR/tessera-voter.sh"
    _log "Script: $INSTALL_DIR/tessera-voter.sh"
}

# ── Systemd ──────────────────────────────────────────────────────────────────
setup_systemd() {
    [[ "$DRY_RUN" == true ]] && return

    cat > "$SYSTEMD_DIR/tessera-voter.service" <<'EOF'
[Unit]
Description=Tessera voter agent
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/tessera-voter.sh
TimeoutStartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tessera-voter
EOF

    cat > "$SYSTEMD_DIR/tessera-voter.timer" <<'EOF'
[Unit]
Description=Tessera voter (30s)
[Timer]
OnBootSec=10s
OnUnitActiveSec=30s
AccuracySec=1s
[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now tessera-voter.timer
    _log "Timer: tessera-voter.timer (30s)"
}

setup_cron() {
    [[ "$DRY_RUN" == true ]] && return
    echo "* * * * * root $INSTALL_DIR/tessera-voter.sh >> /var/log/tessera-voter.log 2>&1" \
        > /etc/cron.d/tessera-voter
    chmod 644 /etc/cron.d/tessera-voter
    _log "Cron: /etc/cron.d/tessera-voter (1min)"
}

# ── Validate ─────────────────────────────────────────────────────────────────
validate() {
    _info "Validating..."
    local code
    code=$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' \
        "$TESSERA_URL/api/v1/ping" 2>/dev/null || echo "000")
    [[ "$code" == "200" ]] && _log "Tessera reachable" || _warn "Tessera unreachable (HTTP $code)"

    _info "Test vote..."
    local out
    if out=$("$INSTALL_DIR/tessera-voter.sh" 2>&1); then
        _log "$out"
    else
        _warn "Test failed: $out"
    fi
}

# ── Summary ──────────────────────────────────────────────────────────────────
summary() {
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Tessera Voter Installed${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Voter:     ${CYAN}$VOTER_NAME${NC}"
    echo -e "  Tessera:   ${CYAN}$TESSERA_URL${NC}"
    echo -e "  Config:    ${CYAN}$CONFIG_DIR/voter.conf${NC}"
    echo ""
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        echo "  systemctl status tessera-voter.timer"
        echo "  journalctl -u tessera-voter -f"
    else
        echo "  tail -f /var/log/tessera-voter.log"
    fi
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    parse_args "$@"
    preflight
    load_existing_config

    [[ "$UNINSTALL" == true ]] && do_uninstall

    if [[ "$ROTATE_KEY" == true ]]; then
        [[ -z "$TESSERA_URL" ]] && _die "--tessera-url required"
        do_rotate_key
        write_config
        exit 0
    fi

    if [[ -z "$TESSERA_URL" ]]; then
        echo -n "Tessera URL: "
        read -r TESSERA_URL
    fi
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url required"

    do_register
    setup_psk
    write_config
    install_script

    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        setup_systemd
    else
        setup_cron
    fi

    validate
    summary
}

main "$@"
