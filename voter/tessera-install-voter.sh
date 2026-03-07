#!/usr/bin/env bash
# tessera-install-voter.sh — Automated voter agent installer for Tessera.
#
# Installs and configures the Tessera voter agent on the local host.
# Handles: dependency checks, config generation, PSK generation, systemd
# setup, firewall rules, SELinux policy, and validation.
#
# Usage:
#   curl -sL https://tessera.example.com/voter/install.sh | sudo bash -s -- \
#     --name "$(hostname -s)" \
#     --tessera-url http://tessera-server:8780 \
#     --active-ip 192.0.2.1
#
#   Auto-register with one-time token:
#     sudo ./tessera-install-voter.sh \
#       --tessera-url http://tessera-server:8780 \
#       --active-ip 192.0.2.1 \
#       --auto-register --registration-token abc123...
#
#   Or interactively:
#     sudo ./tessera-install-voter.sh
#
# Idempotent — safe to re-run. Will update config and restart services.
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
VOTER_NAME=""
TESSERA_URL=""
PRIMARY_IP=""
PRIMARY_PORT="53443"
CHECK_TIMEOUT="5"
CHECK_METHOD="dhcp"
DHCP_INTERFACE=""
VOTER_PSK=""
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/tessera"
SYSTEMD_DIR="/etc/systemd/system"
SCRIPT_URL=""  # optional: fetch voter script from remote
SKIP_FIREWALL=false
SKIP_NMAP=false
UNINSTALL=false
DRY_RUN=false
QUIET=false

# Registration flags
AUTO_REGISTER=false
REGISTRATION_TOKEN=""
USE_STATIC_TOKEN=false
WAIT_APPROVAL=300  # seconds
ROTATE_KEY=false

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

_log()  { [[ "$QUIET" == true ]] && return; echo -e "${GREEN}[✓]${NC} $*"; }
_warn() { echo -e "${YELLOW}[!]${NC} $*" >&2; }
_err()  { echo -e "${RED}[✗]${NC} $*" >&2; }
_info() { [[ "$QUIET" == true ]] && return; echo -e "${CYAN}[i]${NC} $*"; }

_die() { _err "$@"; exit 1; }

# ── Usage ────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
Usage: tessera-install-voter.sh [OPTIONS]

Options:
  --name NAME              Voter name (default: hostname -s)
  --tessera-url URL        Tessera API base URL (required)
  --active-ip IP           Active DHCP server IP (required)
  --active-port PORT       Technitium API port (default: 53443)
  --psk PSK                Pre-shared key (default: auto-generate)
  --check-method METHOD    dhcp|http|both (default: dhcp)
  --check-timeout SEC      Health check timeout (default: 5)
  --interface IFACE        Network interface for DHCP probe (default: auto)
  --skip-firewall          Don't touch firewall rules
  --skip-nmap              Don't install nmap
  --uninstall              Remove voter agent completely
  --dry-run                Show what would be done without doing it
  --quiet                  Suppress informational output

Registration:
  --auto-register          Register with Tessera using a one-time token
  --registration-token TOK One-time or static registration token
  --use-static-token       Use the static registration token (bootstrap)
  --wait-approval SEC      Wait for approval (default: 300s, 0=don't wait)
  --rotate-key             Rotate PSK for an existing voter

  --help                   Show this help

Examples:
  # Minimal — auto-detects hostname, generates PSK
  sudo ./tessera-install-voter.sh \
    --tessera-url http://192.0.2.10:8780 \
    --active-ip 192.0.2.1

  # Auto-register with one-time token
  sudo ./tessera-install-voter.sh \
    --tessera-url http://192.0.2.10:8780 \
    --active-ip 192.0.2.1 \
    --auto-register --registration-token abc123...

  # Rotate PSK for existing voter
  sudo ./tessera-install-voter.sh \
    --tessera-url http://192.0.2.10:8780 \
    --active-ip 192.0.2.1 \
    --rotate-key

  # Uninstall
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
            --active-ip)          PRIMARY_IP="$2"; shift 2 ;;
            --primary-ip)         PRIMARY_IP="$2"; _warn "--primary-ip is deprecated, use --active-ip"; shift 2 ;;
            --active-port)        PRIMARY_PORT="$2"; shift 2 ;;
            --primary-port)       PRIMARY_PORT="$2"; _warn "--primary-port is deprecated, use --active-port"; shift 2 ;;
            --psk)                VOTER_PSK="$2"; shift 2 ;;
            --check-method)       CHECK_METHOD="$2"; shift 2 ;;
            --check-timeout)      CHECK_TIMEOUT="$2"; shift 2 ;;
            --interface)          DHCP_INTERFACE="$2"; shift 2 ;;
            --skip-firewall)      SKIP_FIREWALL=true; shift ;;
            --skip-nmap)          SKIP_NMAP=true; shift ;;
            --uninstall)          UNINSTALL=true; shift ;;
            --dry-run)            DRY_RUN=true; shift ;;
            --quiet)              QUIET=true; shift ;;
            --auto-register)      AUTO_REGISTER=true; shift ;;
            --registration-token) REGISTRATION_TOKEN="$2"; shift 2 ;;
            --use-static-token)   USE_STATIC_TOKEN=true; shift ;;
            --wait-approval)      WAIT_APPROVAL="$2"; shift 2 ;;
            --rotate-key)         ROTATE_KEY=true; shift ;;
            --help|-h)            usage ;;
            *)                    _die "Unknown option: $1 (try --help)" ;;
        esac
    done
}

# ── Preflight checks ────────────────────────────────────────────────────────
preflight() {
    # Must be root
    if [[ $EUID -ne 0 ]]; then
        _die "This script must be run as root (or with sudo)"
    fi

    # OS detection
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        OS_ID="${ID:-unknown}"
        OS_FAMILY="${ID_LIKE:-$OS_ID}"
    else
        OS_ID="unknown"
        OS_FAMILY="unknown"
    fi

    # Package manager detection
    if command -v dnf &>/dev/null; then
        PKG_MGR="dnf"
    elif command -v yum &>/dev/null; then
        PKG_MGR="yum"
    elif command -v apt-get &>/dev/null; then
        PKG_MGR="apt"
    elif command -v apk &>/dev/null; then
        PKG_MGR="apk"
    elif command -v zypper &>/dev/null; then
        PKG_MGR="zypper"
    else
        PKG_MGR="none"
    fi

    # Init system detection
    if command -v systemctl &>/dev/null && systemctl --version &>/dev/null 2>&1; then
        INIT_SYSTEM="systemd"
    elif [[ -d /etc/init.d ]]; then
        INIT_SYSTEM="sysv"
    else
        INIT_SYSTEM="unknown"
    fi

    # Required commands
    for cmd in curl openssl; do
        if ! command -v "$cmd" &>/dev/null; then
            _die "Required command not found: $cmd"
        fi
    done

    # Default voter name
    if [[ -z "$VOTER_NAME" ]]; then
        VOTER_NAME="$(hostname -s 2>/dev/null || cat /etc/hostname 2>/dev/null || echo "voter-$$")"
    fi

    # Validate check method
    case "$CHECK_METHOD" in
        dhcp|http|both) ;;
        *) _die "Invalid --check-method: $CHECK_METHOD (expected: dhcp, http, both)" ;;
    esac

    _info "OS: $OS_ID | Package manager: $PKG_MGR | Init: $INIT_SYSTEM"
    _info "Voter: $VOTER_NAME | Method: $CHECK_METHOD"
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
    if [[ "$SKIP_NMAP" == true ]]; then
        _info "Skipping nmap install (--skip-nmap)"
        return
    fi

    if [[ "$CHECK_METHOD" == "http" ]]; then
        return
    fi

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
            _warn "No package manager found — install nmap manually for DHCP probe"
            _warn "Falling back to HTTP check method"
            CHECK_METHOD="http"
            ;;
    esac

    if command -v nmap &>/dev/null; then
        _log "nmap installed successfully"
    else
        _warn "nmap install failed — falling back to HTTP check method"
        CHECK_METHOD="http"
    fi
}

# ── Auto-register with Tessera ───────────────────────────────────────────────
do_auto_register() {
    if [[ -z "$REGISTRATION_TOKEN" ]]; then
        _die "--registration-token is required with --auto-register"
    fi
    if [[ -z "$TESSERA_URL" ]]; then
        _die "--tessera-url is required with --auto-register"
    fi

    _info "Registering voter '$VOTER_NAME' with Tessera..."

    local response http_code body
    response="$(curl -sk --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/register" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"$VOTER_NAME\", \"token\": \"$REGISTRATION_TOKEN\"}" \
        2>/dev/null)" || _die "Failed to reach Tessera at $TESSERA_URL"

    http_code="$(echo "$response" | tail -1)"
    body="$(echo "$response" | sed '$d')"

    if [[ "$http_code" == "401" ]]; then
        _die "Registration token rejected (invalid, expired, or already used)"
    elif [[ "$http_code" == "409" ]]; then
        _die "Voter '$VOTER_NAME' is already registered"
    elif [[ "$http_code" != "200" ]]; then
        _die "Registration failed (HTTP $http_code): $body"
    fi

    local status psk
    status="$(echo "$body" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
    psk="$(echo "$body" | grep -o '"psk":"[^"]*"' | cut -d'"' -f4)"

    if [[ "$status" == "active" && -n "$psk" ]]; then
        VOTER_PSK="$psk"
        _log "Registration approved — PSK received"
        return
    fi

    if [[ "$status" == "pending" ]]; then
        _info "Registration pending approval..."
        if [[ "$WAIT_APPROVAL" -le 0 ]]; then
            _warn "Not waiting for approval (--wait-approval 0)"
            _warn "Run this installer again after approval"
            exit 0
        fi

        _info "Waiting up to ${WAIT_APPROVAL}s for approval..."
        local elapsed=0
        local interval=5
        while [[ $elapsed -lt $WAIT_APPROVAL ]]; do
            sleep "$interval"
            elapsed=$((elapsed + interval))

            # Poll voter status
            local poll_resp poll_code poll_body
            poll_resp="$(curl -sk --max-time 5 -w '\n%{http_code}' \
                "$TESSERA_URL/api/v1/voters" 2>/dev/null)" || continue
            poll_code="$(echo "$poll_resp" | tail -1)"
            poll_body="$(echo "$poll_resp" | sed '$d')"

            if [[ "$poll_code" == "200" ]]; then
                # Check if our voter is now active with a PSK
                local voter_status
                voter_status="$(echo "$poll_body" | grep -o "\"name\":\"$VOTER_NAME\"[^}]*" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)"
                if [[ "$voter_status" == "active" ]]; then
                    # Fetch PSK (need to re-register or admin gave PSK out-of-band)
                    _log "Voter approved after ${elapsed}s"
                    _warn "PSK was provided during approval — check Tessera admin for your PSK"
                    _warn "Set it with: --psk <your-psk>"
                    exit 0
                fi
            fi

            _info "Still pending... (${elapsed}/${WAIT_APPROVAL}s)"
        done

        _die "Approval timeout after ${WAIT_APPROVAL}s. Contact your Tessera admin."
    fi

    _die "Unexpected registration status: $status"
}

# ── PSK rotation ─────────────────────────────────────────────────────────────
do_rotate_key() {
    if [[ -z "$TESSERA_URL" ]]; then
        _die "--tessera-url is required with --rotate-key"
    fi
    if [[ -z "$VOTER_NAME" ]]; then
        _die "Voter name required for key rotation"
    fi

    _info "Rotating PSK for voter '$VOTER_NAME'..."

    local response http_code body
    response="$(curl -sk --max-time 10 -w '\n%{http_code}' \
        -X POST "$TESSERA_URL/api/v1/voters/$VOTER_NAME/rotate-key" \
        -H "Content-Type: application/json" \
        2>/dev/null)" || _die "Failed to reach Tessera at $TESSERA_URL"

    http_code="$(echo "$response" | tail -1)"
    body="$(echo "$response" | sed '$d')"

    if [[ "$http_code" != "200" ]]; then
        _die "Key rotation failed (HTTP $http_code): $body"
    fi

    local new_psk grace_period
    new_psk="$(echo "$body" | grep -o '"new_psk":"[^"]*"' | cut -d'"' -f4)"
    grace_period="$(echo "$body" | grep -o '"grace_period":[0-9]*' | cut -d':' -f2)"

    if [[ -z "$new_psk" ]]; then
        _die "No PSK in rotation response"
    fi

    _log "PSK rotated (grace period: ${grace_period}s)"
    VOTER_PSK="$new_psk"

    # Update config file
    if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
        sed -i "s|^VOTER_PSK=.*|VOTER_PSK=\"$new_psk\"|" "$CONFIG_DIR/voter.conf"
        _log "Updated PSK in $CONFIG_DIR/voter.conf"
    fi
}

# ── Generate or validate PSK ────────────────────────────────────────────────
setup_psk() {
    # PSK already set by registration or rotation
    if [[ -n "$VOTER_PSK" ]]; then
        _log "Using provided PSK"
        return
    fi

    # Check existing config for PSK
    if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
        existing_psk=$(grep -oP '^VOTER_PSK="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)
        if [[ -n "$existing_psk" ]]; then
            VOTER_PSK="$existing_psk"
            _log "Reusing existing PSK from $CONFIG_DIR/voter.conf"
            return
        fi
    fi

    # Generate new PSK
    VOTER_PSK="$(openssl rand -hex 32)"
    _log "Generated new PSK (save this for Tessera voters.json):"
    echo ""
    echo -e "  ${CYAN}\"$VOTER_NAME\": \"$VOTER_PSK\"${NC}"
    echo ""
    _warn "Add this entry to your Tessera voters.json and restart Tessera"
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
PRIMARY_IP="$PRIMARY_IP"
PRIMARY_PORT="$PRIMARY_PORT"
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

    # Determine script source
    local script_src=""

    # Option 1: Fetch from Tessera server
    if [[ -n "$SCRIPT_URL" ]]; then
        _info "Fetching voter script from $SCRIPT_URL..."
        if curl -sfL "$SCRIPT_URL" -o "$INSTALL_DIR/tessera-voter.sh"; then
            script_src="remote"
        else
            _warn "Failed to fetch from $SCRIPT_URL"
        fi
    fi

    # Option 2: Script bundled alongside installer
    if [[ -z "$script_src" ]]; then
        local dir
        dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [[ -f "$dir/tessera-voter.sh" ]]; then
            cp "$dir/tessera-voter.sh" "$INSTALL_DIR/tessera-voter.sh"
            script_src="local"
        fi
    fi

    # Option 3: Fetch from Tessera API
    if [[ -z "$script_src" && -n "$TESSERA_URL" ]]; then
        _info "Fetching voter script from Tessera API..."
        if curl -sfL "$TESSERA_URL/voter/tessera-voter.sh" -o "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null; then
            script_src="api"
        fi
    fi

    if [[ -z "$script_src" ]]; then
        _die "Cannot find voter script. Place tessera-voter.sh next to this installer, or pass --tessera-url"
    fi

    chmod 755 "$INSTALL_DIR/tessera-voter.sh"
    _log "Voter script installed to $INSTALL_DIR/tessera-voter.sh (source: $script_src)"
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
    _log "Systemd timer enabled and started"
}

# ── Cron fallback (non-systemd) ─────────────────────────────────────────────
setup_cron() {
    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would install cron job"
        return
    fi

    # cron can only do 1-minute minimum; run every minute
    cat > /etc/cron.d/tessera-voter <<EOF
# Tessera voter agent — runs every minute (cron minimum)
* * * * * root $INSTALL_DIR/tessera-voter.sh >> /var/log/tessera-voter.log 2>&1
EOF

    chmod 644 /etc/cron.d/tessera-voter
    _log "Cron job installed (/etc/cron.d/tessera-voter)"
    _warn "Cron only supports 1-minute intervals (systemd timer uses 30s)"

    # Set up log rotation
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
        _log "Log rotation configured"
    fi
}

# ── SELinux policy ───────────────────────────────────────────────────────────
setup_selinux() {
    if ! command -v getenforce &>/dev/null; then
        return
    fi

    local mode
    mode="$(getenforce 2>/dev/null || echo "Disabled")"

    if [[ "$mode" == "Disabled" ]]; then
        return
    fi

    _info "SELinux is $mode — setting file contexts..."

    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would set SELinux contexts"
        return
    fi

    # Label the voter script as bin_t
    if command -v semanage &>/dev/null; then
        semanage fcontext -a -t bin_t "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null || true
    fi
    restorecon -v "$INSTALL_DIR/tessera-voter.sh" 2>/dev/null || true

    # Allow the script to make network connections
    if command -v setsebool &>/dev/null; then
        setsebool -P nis_enabled on 2>/dev/null || true
    fi

    _log "SELinux contexts applied"
}

# ── Firewall (outbound to Tessera) ───────────────────────────────────────────
setup_firewall() {
    if [[ "$SKIP_FIREWALL" == true ]]; then
        _info "Skipping firewall setup (--skip-firewall)"
        return
    fi

    if [[ "$DRY_RUN" == true ]]; then
        _info "[dry-run] Would check firewall rules"
        return
    fi

    # Extract Tessera host and port
    local tessera_host tessera_port
    tessera_host="$(echo "$TESSERA_URL" | sed -E 's|https?://||;s|:[0-9]+.*||;s|/.*||')"
    tessera_port="$(echo "$TESSERA_URL" | grep -oP ':\K[0-9]+' || echo "8780")"

    # firewalld (RHEL/AlmaLinux/Fedora)
    if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
        _info "firewalld is active — outbound to $tessera_host:$tessera_port should be allowed by default"
        if [[ "$CHECK_METHOD" != "http" ]] && command -v nmap &>/dev/null; then
            _info "DHCP probe requires raw socket access (nmap) — ensure no outbound restrictions"
        fi
        return
    fi

    # nftables
    if command -v nft &>/dev/null; then
        local output_policy
        output_policy="$(nft list chain inet filter output 2>/dev/null | grep "policy" | awk '{print $NF}' | tr -d ';')"
        if [[ "$output_policy" == "drop" ]]; then
            _warn "nftables output policy is DROP — voter may not be able to reach Tessera"
            _warn "Add a rule: nft add rule inet filter output ip daddr $tessera_host tcp dport $tessera_port accept"
        fi
        return
    fi

    # iptables
    if command -v iptables &>/dev/null; then
        local output_policy
        output_policy="$(iptables -L OUTPUT -n 2>/dev/null | head -1 | awk -F'[()]' '{print $2}')"
        if [[ "$output_policy" == "DROP" ]]; then
            _warn "iptables OUTPUT policy is DROP — voter may not be able to reach Tessera"
            _warn "Add a rule: iptables -A OUTPUT -d $tessera_host -p tcp --dport $tessera_port -j ACCEPT"
        fi
        return
    fi
}

# ── Validate installation ───────────────────────────────────────────────────
validate() {
    _info "Validating installation..."

    local errors=0

    # Config exists and is readable
    if [[ ! -f "$CONFIG_DIR/voter.conf" ]]; then
        _err "Config file missing: $CONFIG_DIR/voter.conf"
        ((errors++))
    fi

    # Script exists and is executable
    if [[ ! -x "$INSTALL_DIR/tessera-voter.sh" ]]; then
        _err "Voter script not executable: $INSTALL_DIR/tessera-voter.sh"
        ((errors++))
    fi

    # Systemd or cron is active
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        if ! systemctl is-active --quiet tessera-voter.timer 2>/dev/null; then
            _err "Systemd timer is not active"
            ((errors++))
        fi
    elif [[ ! -f /etc/cron.d/tessera-voter ]]; then
        _err "Cron job not found"
        ((errors++))
    fi

    # Test connectivity to Tessera
    _info "Testing connectivity to Tessera..."
    local http_code
    http_code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "$TESSERA_URL/api/v1/ping" 2>/dev/null || echo "000")"
    if [[ "$http_code" == "200" ]]; then
        _log "Tessera API reachable ($TESSERA_URL)"
    else
        _warn "Cannot reach Tessera API at $TESSERA_URL (HTTP $http_code)"
        _warn "Voter will retry on each timer tick — ensure Tessera is running"
    fi

    # Test active DHCP server connectivity
    _info "Testing active DHCP server..."
    http_code="$(curl -sk --max-time 5 -o /dev/null -w '%{http_code}' \
        "https://${PRIMARY_IP}:${PRIMARY_PORT}/" 2>/dev/null || echo "000")"
    if [[ "$http_code" != "000" ]]; then
        _log "Active DHCP server reachable at $PRIMARY_IP:$PRIMARY_PORT"
    else
        _warn "Cannot reach active DHCP at $PRIMARY_IP:$PRIMARY_PORT"
        _warn "Check firewall rules and network connectivity"
    fi

    # Run a dry vote to test the full chain
    _info "Submitting test vote..."
    local test_out
    if test_out="$("$INSTALL_DIR/tessera-voter.sh" 2>&1)"; then
        _log "Test vote successful: $test_out"
    else
        _warn "Test vote failed: $test_out"
        _warn "Check config and connectivity"
    fi

    if [[ $errors -gt 0 ]]; then
        _err "$errors validation error(s) found"
        return 1
    fi

    _log "All checks passed"
}

# ── Summary ──────────────────────────────────────────────────────────────────
summary() {
    echo ""
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  Tessera Voter Agent — Installed Successfully${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "  Voter:         ${CYAN}$VOTER_NAME${NC}"
    echo -e "  Tessera:       ${CYAN}$TESSERA_URL${NC}"
    echo -e "  Active DHCP:   ${CYAN}$PRIMARY_IP:$PRIMARY_PORT${NC}"
    echo -e "  Check method:  ${CYAN}$CHECK_METHOD${NC}"
    echo -e "  Config:        ${CYAN}$CONFIG_DIR/voter.conf${NC}"
    echo -e "  Script:        ${CYAN}$INSTALL_DIR/tessera-voter.sh${NC}"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        echo -e "  Timer:         ${CYAN}tessera-voter.timer (30s)${NC}"
    else
        echo -e "  Cron:          ${CYAN}/etc/cron.d/tessera-voter (1min)${NC}"
    fi
    echo ""
    if [[ "$AUTO_REGISTER" != true ]]; then
        echo -e "  ${YELLOW}PSK for voters.json:${NC}"
        echo -e "  ${CYAN}\"$VOTER_NAME\": \"$VOTER_PSK\"${NC}"
        echo ""
    fi
    echo -e "  Useful commands:"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        echo "    systemctl status tessera-voter.timer"
        echo "    journalctl -u tessera-voter -f"
        echo "    systemctl restart tessera-voter.timer"
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

    if [[ "$UNINSTALL" == true ]]; then
        do_uninstall
    fi

    # Handle key rotation (standalone operation)
    if [[ "$ROTATE_KEY" == true ]]; then
        # Interactive prompt for missing Tessera URL
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
        # Check existing config
        if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
            TESSERA_URL="$(grep -oP '^TESSERA_URL="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)"
        fi
        if [[ -z "$TESSERA_URL" ]]; then
            echo -n "Tessera API URL (e.g. http://192.0.2.10:8780): "
            read -r TESSERA_URL
        fi
    fi
    [[ -z "$TESSERA_URL" ]] && _die "--tessera-url is required"

    if [[ -z "$PRIMARY_IP" ]]; then
        if [[ -f "$CONFIG_DIR/voter.conf" ]]; then
            PRIMARY_IP="$(grep -oP '^PRIMARY_IP="\K[^"]+' "$CONFIG_DIR/voter.conf" 2>/dev/null || true)"
        fi
        if [[ -z "$PRIMARY_IP" ]]; then
            echo -n "Active DHCP server IP: "
            read -r PRIMARY_IP
        fi
    fi
    [[ -z "$PRIMARY_IP" ]] && _die "--active-ip is required"

    # Handle auto-registration
    if [[ "$AUTO_REGISTER" == true ]]; then
        do_auto_register
    fi

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
