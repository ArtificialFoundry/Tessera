#!/usr/bin/env bash
# tessera-install-voter.sh — Automated voter agent installer for Tessera.
#
# Installs and configures the Tessera voter agent on the local host.
# Handles: dependency checks, registration, config generation, systemd
# setup, firewall rules, SELinux policy, and validation.
#
# The voter agent periodically checks the health of a DHCP server (via
# DHCP probe or HTTP API) and submits signed votes to the Tessera API.
#
# Usage:
#   # Register with a one-time token (recommended):
#   sudo ./tessera-install-voter.sh \
#     --tessera-url http://tessera-server:8780 \
#     --target-ip 192.168.1.1 \
#     --token abc123...
#
#   # Interactive (prompts for missing values):
#   sudo ./tessera-install-voter.sh
#
# Idempotent — safe to re-run. Will update config and restart services.
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
VOTER_NAME=""
TESSERA_URL=""
TARGET_IP=""
TARGET_PORT="53443"
CHECK_TIMEOUT="5"
CHECK_METHOD="dhcp"
DHCP_INTERFACE=""
VOTER_PSK=""
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/tessera"
SYSTEMD_DIR="/etc/systemd/system"
SKIP_FIREWALL=false
SKIP_NMAP=false
UNINSTALL=false
DRY_RUN=false
QUIET=false

# Registration flags
REGISTRATION_TOKEN=""
WAIT_APPROVAL=300  # seconds
ROTATE_KEY=false

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

_die() { _err "$@"; exit 1; }

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
tessera-install-voter.sh — Install and register a Tessera voter agent.

The voter agent monitors a DHCP server's health and submits signed
votes to Tessera. Tessera uses these votes to decide when to trigger
a failover to a standby server.

USAGE:
  sudo ./tessera-install-voter.sh [OPTIONS]

CONNECTION:
  --tessera-url URL        Tessera API URL (e.g. http://192.168.1.10:8780)
  --name NAME              Voter name (default: system hostname)

DHCP SERVER TO MONITOR:
  --target-ip IP           IP of the DHCP server this voter monitors
  --target-port PORT       Technitium API port (default: 53443)
  --check-method METHOD    How to check DHCP health:
                             dhcp  — nmap DHCP probe (default, most reliable)
                             http  — Technitium HTTP API ping
                             both  — require both to pass
  --check-timeout SEC      Health check timeout in seconds (default: 5)
  --interface IFACE        Network interface for DHCP probe (default: auto)

REGISTRATION:
  --token TOKEN            One-time registration token from Tessera admin.
                           The voter registers with Tessera, receives a PSK,
                           and waits for admin approval.
  --wait-approval SEC      How long to wait for approval (default: 300, 0=skip)
  --rotate-key             Rotate the PSK for an already-registered voter

INSTALLATION:
  --skip-firewall          Don't check or modify firewall rules
  --skip-nmap              Don't install nmap (falls back to HTTP check)
  --uninstall              Remove voter agent, systemd units, and cron jobs
  --dry-run                Show what would happen without making changes
  --quiet                  Suppress informational output

EXAMPLES:
  # Register with a token (typical first-time setup):
  sudo ./tessera-install-voter.sh \
    --tessera-url http://192.168.1.10:8780 \
    --target-ip 192.168.1.1 \
    --token eyJhbGciOi...

  # Re-run to update config (reads existing PSK from config):
  sudo ./tessera-install-voter.sh \
    --tessera-url http://192.168.1.10:8780 \
    --target-ip 192.168.1.1

  # Rotate PSK for an existing voter:
  sudo ./tessera-install-voter.sh --rotate-key

  # Uninstall everything:
  sudo ./tessera-install-voter.sh --uninstall
EOF
    exit 0
}

# ── Argument parsing ─────────────────────────────────────────────────────────
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --name)               VOTER_NAME="$2"; shift 2 ;;
            --tessera-url)        TESSERA_URL="$2"; shift 2 ;;
            --target-ip)          TARGET_IP="$2"; shift 2 ;;
            --target-port)        TARGET_PORT="$2"; shift 2 ;;
            --token)              REGISTRATION_TOKEN="$2"; shift 2 ;;
            --check-method)       CHECK_METHOD="$2"; shift 2 ;;
            --check-timeout)      CHECK_TIMEOUT="$2"; shift 2 ;;
            --interface)          DHCP_INTERFACE="$2"; shift 2 ;;
            --skip-firewall)      SKIP_FIREWALL=true; shift ;;
            --skip-nmap)          SKIP_NMAP=true; shift ;;
            --uninstall)          UNINSTALL=true; shift ;;
            --dry-run)            DRY_RUN=true; shift ;;
            --quiet)              QUIET=true; shift ;;
            --wait-approval)      WAIT_APPROVAL="$2"; shift 2 ;;
            --rotate-key)         ROTATE_KEY=true; shift ;;
            # Deprecated aliases (backward compat)
            --active-ip)          TARGET_IP="$2"; _warn "--active-ip is deprecated, use --target-ip"; shift 2 ;;
            --active-port)        TARGET_PORT="$2"; _warn "--active-port is deprecated, use --target-port"; shift 2 ;;
            --primary-ip)         TARGET_IP="$2"; _warn "--primary-ip is deprecated, use --target-ip"; shift 2 ;;
            --primary-port)       TARGET_PORT="$2"; _warn "--primary-port is deprecated, use --target-port"; shift 2 ;;
            --psk)                VOTER_PSK="$2"; _warn "--psk is deprecated; use --token for registration instead"; shift 2 ;;
            --auto-register)      _warn "--auto-register is deprecated; use --token directly"; shift ;;
            --registration-token) REGISTRATION_TOKEN="$2"; _warn "--registration-token is deprecated, use --token"; shift 2 ;;
            --use-static-token)   _warn "--use-static-token is removed (static tokens no longer supported)"; shift ;;
            --help|-h)            usage ;;
            *)                    _die "Unknown option: $1 (try --help)" ;;
        esac
    done
}

# ── Preflight checks ────────────────────────────────────────────────────────
preflight() {
    if [[ $EUID -ne 0 ]]; then
        _die "This script must be run as root (or with sudo)"
    fi

    # OS detection
    OS_ID="unknown"
    OS_FAMILY="unknown"
    if [[ -f /etc/os-release ]]; then
        while IFS='=' read -r key val; do
            [[ "$key" =~ ^[[:space:]]*# ]] && continue
            [[ -z "$key" ]] && continue
            key=$(echo "$key" | xargs)
            val=$(echo "$val" | sed 's/^["'"'"']//' | sed 's/["'"'"']$//')
            case "$key" in
                ID) OS_ID="$val" ;;
                ID_LIKE) OS_FAMILY="$val" ;;
            esac
        done < /etc/os-release
        OS_FAMILY="${OS_FAMILY:-$OS_ID}"
    fi

    # Package manager
    if command -v dnf &>/dev/null; then PKG_MGR="dnf"
    elif command -v yum &>/dev/null; then PKG_MGR="yum"
    elif command -v apt-get &>/dev/null; then PKG_MGR="apt"
    elif command -v apk &>/dev/null; then PKG_MGR="apk"
    elif command -v zypper &>/dev/null; then PKG_MGR="zypper"
    else PKG_MGR="none"
    fi

    # Init system
    if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
        INIT_SYSTEM="systemd"
    elif [[ -d /etc/init.d ]]; then
        INIT_SYSTEM="sysv"
    else
        INIT_SYSTEM="unknown"
    fi

    for cmd in curl openssl; do
        command -v "$cmd" &>/dev/null || _die "Required command not found: $cmd"
    done

    if [[ -z "$VOTER_NAME" ]]; then
        VOTER_NAME="$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null || echo "voter-$$")"
    fi

    case "$CHECK_METHOD" in
        dhcp|http|both) ;;
        *) _die "Invalid --check-method: $CHECK_METHOD (expected: dhcp, http, both)" ;;
    esac

    _info "OS: $OS_ID | Package manager: $PKG_MGR | Init: $INIT_SYSTEM"
    _info "Voter: $VOTER_NAME | Check method: $CHECK_METHOD"
}

# ── Uninstall ────────────────────────────────────────────────────────────────
do_uninstall() {
    _info "Uninstalling Tessera voter agent..."

    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        systemctl disable --now tessera-voter.timer 2>/dev/null || true
        systemctl disable --now tessera-voter.service 2>/dev/null || true
        rm -f "$SYSTEMD_DIR/tessera-voter.service" "$SYSTEMD_DIR/tessera-voter.timer"
        systemctl daemon-reload 2>/dev/null || true
        _log "Systemd units removed"
    elif [[ -f /etc/cron.d/tessera-voter ]]; then
        rm -f /etc/cron.d/tessera-voter
        _log "Cron job removed"
    fi

    rm -f "$INSTALL_DIR/tessera-voter.sh"
    _log "Voter script removed"

    if [[ -d "$CONFIG_DIR" ]]; then
        _warn "Config directory $CONFIG_DIR left intact (contains secrets)"
        _warn "Remove manually: rm -rf $CONFIG_DIR"
    fi

    _log "Uninstall complete"
    exit 0
}

# ── Install nmap ─────────────────────────────────────────────────────────────
install_nmap() {
    [[ "$SKIP_NMAP" == true ]] && { _info "Skipping nmap install (--skip-nmap)"; return; }
    [[ "$CHECK_METHOD" == "http" ]] && return

    if command -v nmap &>/dev/null; then
        _log "nmap already installed: $(nmap --version 2>&1 | head -1)"
        return
    fi

    _info "Installing nmap for DHCP probe..."
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would install nmap via $PKG_MGR"
        return
    fi

    case "$PKG_MGR" in
        dnf|yum) "$PKG_MGR" install -y nmap ;;
        apt)     apt-get update -qq && apt-get install -y -qq nmap ;;
        apk)     apk add --no-cache nmap ;;
        zypper)  zypper install -y nmap ;;
        none)
            _warn "No package manager found — install nmap manually"
            _warn "Falling back to HTTP check method"
            CHECK_METHOD="http"
            ;;
    esac

    if command -v nmap &>/dev/null; then
        _log "nmap installed"
    else
        _warn "nmap install failed — falling back to HTTP check"
        CHECK_METHOD="http"
    fi
}

# ── Register with Tessera ────────────────────────────────────────────────────
do_register() {
    if [[ -z "$REGISTRATION_TOKEN" ]]; then
        return
    fi
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required for registration"

    _info "Registering voter '$VOTER_NAME' with Tessera..."

    local response http_code body
    response="$(curl -sk --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/register" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$VOTER_NAME\", \"token\": \"$REGISTRATION_TOKEN\"}" \
        2>/dev/null)" || _die "Failed to reach Tessera at $TESSERA_URL"

    http_code="$(echo "$response" | tail -1)"
    body="$(echo "$response" | sed '$d')"

    case "$http_code" in
        401) _die "Registration token rejected (invalid, expired, or already used)" ;;
        403) _die "Registration token not valid from this IP address" ;;
        409) _die "Voter '$VOTER_NAME' is already registered" ;;
    esac
    [[ "$http_code" != "200" ]] && _die "Registration failed (HTTP $http_code): $body"

    local status psk
    status="$(echo "$body" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
    psk="$(echo "$body" | grep -o '"psk":"[^"]*"' | cut -d'"' -f4)"

    if [[ "$status" == "active" && -n "$psk" ]]; then
        VOTER_PSK="$psk"
        _log "Registered and approved — PSK received"
        return
    fi

    if [[ "$status" == "pending" ]]; then
        _info "Registered — waiting for admin approval..."
        if [[ "$WAIT_APPROVAL" -le 0 ]]; then
            _warn "Not waiting for approval (--wait-approval 0)"
            _warn "Re-run this script after admin approves the voter"
            exit 0
        fi

        _info "Polling for approval (up to ${WAIT_APPROVAL}s)..."
        local elapsed=0 interval=5
        while [[ $elapsed -lt $WAIT_APPROVAL ]]; do
            sleep "$interval"
            elapsed=$((elapsed + interval))

            local poll_resp poll_code poll_body voter_status
            poll_resp="$(curl -sk --max-time 5 -w '\n%{http_code}' \
                "$TESSERA_URL/api/v1/voters" 2>/dev/null)" || continue
            poll_code="$(echo "$poll_resp" | tail -1)"
            poll_body="$(echo "$poll_resp" | sed '$d')"

            if [[ "$poll_code" == "200" ]]; then
                voter_status="$(echo "$poll_body" | grep -o "\"name\":\"$VOTER_NAME\"[^}]*" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
                if [[ "$voter_status" == "approved" ]]; then
                    _log "Approved after ${elapsed}s"
                    _warn "PSK was shown in the Tessera admin UI during approval"
                    _warn "Set it manually in $CONFIG_DIR/voter.conf or re-register"
                    exit 0
                fi
            fi

            printf "\r  Waiting... (%d/%ds)" "$elapsed" "$WAIT_APPROVAL"
        done
        echo ""
        _die "Approval timeout after ${WAIT_APPROVAL}s — ask your Tessera admin to approve '$VOTER_NAME'"
    fi

    _die "Unexpected registration response: $body"
}

# ── PSK rotation ─────────────────────────────────────────────────────────────
do_rotate_key() {
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required for key rotation"

    _info "Rotating PSK for voter '$VOTER_NAME'..."

    local response http_code body
    response="$(curl -sk --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/$VOTER_NAME/rotate-key" \
        -H "Content-Type: application/json" \
        2>/dev/null)" || _die "Failed to reach Tessera at $TESSERA_URL"

    http_code="$(echo "$response" | tail -1)"
    body="$(echo "$response" | sed '$d')"

    [[ "$http_code" != "200" ]] && _die "Key rotation failed (HTTP $http_code): $body"

    local new_psk grace_period
    new_psk="$(echo "$body" | grep -o '"new_psk":"[^"]*"' | cut -d'"' -f4)"
    grace_period="$(echo "$body" | grep -o '"grace_period":[0-9]*' | cut -d':' -f2)"

    [[ -z "$new_psk" ]] && _die "No PSK in rotation response"

    _log "PSK rotated (grace period: ${grace_period:-0}s)"
    VOTER_PSK="$new_psk"

    if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
        sed -i "s|^VOTER_PSK=.*|VOTER_PSK=\"$new_psk\"|" "$CONFIG_DIR/voter.conf"
        _log "Updated PSK in $CONFIG_DIR/voter.conf"
    fi
}

# ── Resolve PSK ──────────────────────────────────────────────────────────────
setup_psk() {
    # Already set by registration or --psk
    if [[ -n "$VOTER_PSK" ]]; then
        _log "Using PSK from registration"
        return
    fi

    # Check existing config
    if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
        local existing
        existing=$(grep -oP '^VOTER_PSK="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)
        if [[ -n "$existing" ]]; then
            VOTER_PSK="$existing"
            _log "Reusing existing PSK from $CONFIG_DIR/voter.conf"
            return
        fi
    fi

    # No PSK — user needs to register
    echo ""
    _err "No PSK available."
    echo ""
    echo -e "  To get a PSK, register with a one-time token:"
    echo ""
    echo -e "    ${BOLD}sudo ./tessera-install-voter.sh \\${NC}"
    echo -e "    ${BOLD}  --tessera-url $TESSERA_URL \\${NC}"
    echo -e "    ${BOLD}  --target-ip $TARGET_IP \\${NC}"
    echo -e "    ${BOLD}  --token <token-from-tessera-admin>${NC}"
    echo ""
    echo -e "  Generate a token in the Tessera web UI → Voters → Generate Token."
    echo ""
    _die "Cannot continue without a PSK"
}

# ── Write config ─────────────────────────────────────────────────────────────
write_config() {
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would write config to $CONFIG_DIR/voter.conf"
        return
    fi

    mkdir -p "$CONFIG_DIR"

    cat > "$CONFIG_DIR/voter.conf" <<EOF
# Tessera voter configuration
# Generated by tessera-install-voter.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")

VOTER_NAME="$VOTER_NAME"
VOTER_PSK="$VOTER_PSK"
TESSERA_URL="$TESSERA_URL"
PRIMARY_IP="$TARGET_IP"
PRIMARY_PORT="$TARGET_PORT"
CHECK_TIMEOUT="$CHECK_TIMEOUT"
CHECK_METHOD="$CHECK_METHOD"
DHCP_INTERFACE="$DHCP_INTERFACE"
EOF

    chmod 600 "$CONFIG_DIR/voter.conf"
    _log "Config written to $CONFIG_DIR/voter.conf"
}

# ── Install voter script ────────────────────────────────────────────────────
install_script() {
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would install voter script to $INSTALL_DIR/tessera-voter.sh"
        return
    fi

    local script_src=""
    local dir
    dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Option 1: Script bundled alongside installer
    if [[ -f "$dir/tessera-voter.sh" ]]; then
        cp "$dir/tessera-voter.sh" "$INSTALL_DIR/tessera-voter.sh"
        script_src="local"
    fi

    # Option 2: Fetch from Tessera API
    if [[ -z "$script_src" && -n "$TESSERA_URL" ]]; then
        _info "Fetching voter script from Tessera..."
        if curl -sfL "$TESSERA_URL/voter/tessera-voter.sh" -o "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null; then
            script_src="api"
        fi
    fi

    [[ -z "$script_src" ]] && _die "Cannot find voter script. Place tessera-voter.sh next to this installer."

    chmod 755 "$INSTALL_DIR/tessera-voter.sh"
    _log "Voter script installed ($script_src)"
}

# ── Systemd setup ───────────────────────────────────────────────────────────
setup_systemd() {
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would install systemd units"
        return
    fi

    cat > "$SYSTEMD_DIR/tessera-voter.service" <<'EOF'
[Unit]
Description=Tessera DHCP failover voter agent
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
Description=Tessera voter agent timer (every 30s)

[Timer]
OnBootSec=10s
OnUnitActiveSec=30s
AccuracySec=1s

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable --now tessera-voter.timer
    _log "Systemd timer enabled (30s interval)"
}

# ── Cron fallback ────────────────────────────────────────────────────────────
setup_cron() {
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would install cron job"
        return
    fi

    cat > /etc/cron.d/tessera-voter <<EOF
* * * * * root $INSTALL_DIR/tessera-voter.sh >> /var/log/tessera-voter.log 2>&1
EOF

    chmod 644 /etc/cron.d/tessera-voter
    _log "Cron job installed (1-minute interval — systemd recommended for 30s)"

    if [[ -d /etc/logrotate.d ]]; then
        cat > /etc/logrotate.d/tessera-voter <<'EOF'
/var/log/tessera-voter.log {
    daily
    missingok
    rotate 7
    compress
    notifempty
}
EOF
    fi
}

# ── SELinux ──────────────────────────────────────────────────────────────────
setup_selinux() {
    command -v getenforce &>/dev/null || return
    local mode
    mode="$(getenforce 2>/dev/null || echo "Disabled")"
    [[ "$mode" == "Disabled" ]] && return

    _info "SELinux is $mode — setting file contexts..."
    [[ "$DRY_RUN" == true ]] && return

    if command -v semanage &>/dev/null; then
        semanage fcontext -a -t bin_t "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null || true
    fi
    restorecon -v "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null || true
    if command -v setsebool &>/dev/null; then
        setsebool -P nis_enabled on 2>/dev/null || true
    fi
    _log "SELinux contexts applied"
}

# ── Firewall ─────────────────────────────────────────────────────────────────
setup_firewall() {
    [[ "$SKIP_FIREWALL" == true ]] && return
    [[ "$DRY_RUN" == true ]] && return

    local tessera_host tessera_port
    tessera_host="$(echo "$TESSERA_URL" | sed -E 's|https?://||;s|:[0-9]+.*||;s|/.*||')"
    tessera_port="$(echo "$TESSERA_URL" | grep -oP ':\K[0-9]+' || echo "8780")"

    if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
        _info "firewalld active — outbound to $tessera_host:$tessera_port allowed by default"
        return
    fi

    if command -v nft &>/dev/null; then
        local policy
        policy="$(nft list chain inet filter output 2>/dev/null | grep "policy" | awk '{print $NF}' | tr -d ';')"
        if [[ "$policy" == "drop" ]]; then
            _warn "nftables output DROP — add rule for $tessera_host:$tessera_port"
        fi
    fi
}

# ── Validate ─────────────────────────────────────────────────────────────────
validate() {
    _info "Validating installation..."
    local errors=0

    [[ ! -f "$CONFIG_DIR/voter.conf" ]] && { _err "Config missing"; ((errors++)); }
    [[ ! -x "$INSTALL_DIR/tessera-voter.sh" ]] && { _err "Voter script not executable"; ((errors++)); }

    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        systemctl is-active --quiet tessera-voter.timer 2>/dev/null || { _err "Timer not active"; ((errors++)); }
    fi

    _info "Testing Tessera connectivity..."
    local code
    code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "$TESSERA_URL/api/v1/ping" 2>/dev/null || echo "000")"
    [[ "$code" == "200" ]] && _log "Tessera API reachable" || _warn "Cannot reach Tessera (HTTP $code)"

    _info "Testing DHCP server connectivity..."
    code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "https://${TARGET_IP}:${TARGET_PORT}/" 2>/dev/null || echo "000")"
    [[ "$code" != "000" ]] && _log "DHCP server reachable at $TARGET_IP:$TARGET_PORT" || \
        _warn "Cannot reach DHCP at $TARGET_IP:$TARGET_PORT"

    _info "Running test vote..."
    local out
    if out="$("$INSTALL_DIR/tessera-voter.sh" 2>&1)"; then
        _log "Test vote: $out"
    else
        _warn "Test vote failed: $out"
    fi

    [[ $errors -gt 0 ]] && { _err "$errors error(s)"; return 1; }
    _log "All checks passed"
}

# ── Summary ──────────────────────────────────────────────────────────────────
summary() {
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Tessera Voter Agent — Installed${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Voter:         ${CYAN}$VOTER_NAME${NC}"
    echo -e "  Tessera:       ${CYAN}$TESSERA_URL${NC}"
    echo -e "  Monitors:      ${CYAN}$TARGET_IP:$TARGET_PORT${NC}"
    echo -e "  Check method:  ${CYAN}$CHECK_METHOD${NC}"
    echo -e "  Config:        ${CYAN}$CONFIG_DIR/voter.conf${NC}"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        echo -e "  Timer:         ${CYAN}tessera-voter.timer (30s)${NC}"
    else
        echo -e "  Cron:          ${CYAN}/etc/cron.d/tessera-voter (1min)${NC}"
    fi
    echo ""
    echo -e "  ${BOLD}Commands:${NC}"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        echo "    systemctl status tessera-voter.timer"
        echo "    journalctl -u tessera-voter -f"
    else
        echo "    tail -f /var/log/tessera-voter.log"
    fi
    echo "    $INSTALL_DIR/tessera-voter.sh   # manual run"
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
    parse_args "$@"
    preflight

    [[ "$UNINSTALL" == true ]] && do_uninstall

    # Key rotation is a standalone operation
    if [[ "$ROTATE_KEY" == true ]]; then
        if [[ -z "$TESSERA_URL" && -f "$CONFIG_DIR/voter.conf" ]]; then
            TESSERA_URL="$(grep -oP '^TESSERA_URL="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)"
        fi
        [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required"
        do_rotate_key
        write_config
        _log "Key rotation complete"
        exit 0
    fi

    # Interactive prompts for missing required values
    if [[ -z "$TESSERA_URL" ]]; then
        if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
            TESSERA_URL="$(grep -oP '^TESSERA_URL="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)"
        fi
        if [[ -z "$TESSERA_URL" ]]; then
            echo -n "Tessera API URL (e.g. http://192.168.1.10:8780): "
            read -r TESSERA_URL
        fi
    fi
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required"

    if [[ -z "$TARGET_IP" ]]; then
        if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
            TARGET_IP="$(grep -oP '^PRIMARY_IP="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)"
        fi
        if [[ -z "$TARGET_IP" ]]; then
            echo -n "DHCP server IP to monitor: "
            read -r TARGET_IP
        fi
    fi
    [[ -z "$TARGET_IP" ]] && _die "--target-ip is required"

    # Register if token provided
    do_register

    install_nmap
    setup_psk
    write_config
    install_script
    setup_selinux
    setup_firewall

    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        setup_systemd
    else
        setup_cron
    fi

    validate
    summary
}

main "$@"
