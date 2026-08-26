#!/usr/bin/env bash
#
# One-command install on an Ubuntu/Debian VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh | sudo bash
#
# or, from a clone:
#
#   sudo bash scripts/install.sh
#
# What it does:
#   1. installs python3-venv and git if they are missing
#   2. puts the code in /opt/signal, owned by a dedicated 'signal' user
#   3. creates the virtualenv and installs dependencies
#   4. runs the setup wizard (asks you a handful of questions)
#   5. signs in to Telegram (you type the code) and picks the source group
#   6. installs and starts the systemd service
#
# Re-running it is safe: it updates the code and dependencies, keeps your
# .env and your Telegram session, and restarts the service.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/siripuson160-creator/Sig.git}"
BRANCH="${BRANCH:-main}"
INSTALL_DIR="${INSTALL_DIR:-/opt/signal}"
SERVICE_USER="${SERVICE_USER:-signal}"
SERVICE_NAME="telegram-line-forwarder"

SKIP_SETUP=false
SERVICE_ONLY=false

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'

step()  { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"; }
info()  { printf '    %s%s%s\n' "$DIM" "$1" "$RESET"; }
ok()    { printf '    %s✓ %s%s\n' "$GREEN" "$1" "$RESET"; }
warn()  { printf '    %s! %s%s\n' "$YELLOW" "$1" "$RESET"; }
fail()  { printf '\n%sError: %s%s\n' "$RED" "$1" "$RESET" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: sudo bash scripts/install.sh [options]

  --dir PATH          where to install            (default: $INSTALL_DIR)
  --user NAME         service account to run as   (default: $SERVICE_USER)
  --branch NAME       git branch to install       (default: $BRANCH)
  --skip-setup        do not run the setup wizard (code and deps only)
  --service-only      only (re)install the systemd service
  -h, --help          show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)          INSTALL_DIR="$2"; shift 2 ;;
        --user)         SERVICE_USER="$2"; shift 2 ;;
        --branch)       BRANCH="$2"; shift 2 ;;
        --skip-setup)   SKIP_SETUP=true; shift ;;
        --service-only) SERVICE_ONLY=true; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              fail "unknown option: $1 (try --help)" ;;
    esac
done

[[ $EUID -eq 0 ]] || fail "run this with sudo: sudo bash scripts/install.sh"

# The wizard and the Telegram sign-in need a real terminal.
INTERACTIVE=true
[[ -t 0 ]] || INTERACTIVE=false

as_service_user() {
    # Runs a command as the service account, attached to the current terminal.
    # Proxy and CA settings are passed through, since a locked-down VPS often
    # reaches PyPI only through one.
    local passthrough=()
    local name
    for name in http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY \
                PIP_INDEX_URL PIP_CERT REQUESTS_CA_BUNDLE SSL_CERT_FILE; do
        [[ -n "${!name:-}" ]] && passthrough+=("$name=${!name}")
    done
    sudo -u "$SERVICE_USER" -H env "${passthrough[@]}" "$@"
}

# --------------------------------------------------------------- packages
install_packages() {
    step "Checking system packages"
    local missing=()
    command -v python3 >/dev/null || missing+=(python3)
    python3 -c "import venv" 2>/dev/null || missing+=(python3-venv)
    command -v git >/dev/null || missing+=(git)
    command -v curl >/dev/null || missing+=(curl)

    if [[ ${#missing[@]} -eq 0 ]]; then
        ok "python3, venv, git and curl are present"
        return
    fi

    if ! command -v apt-get >/dev/null; then
        fail "missing: ${missing[*]} — install them with your package manager and re-run"
    fi
    info "installing: ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}" >/dev/null
    ok "installed ${missing[*]}"
}

check_python_version() {
    local version
    version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if [[ "$(printf '%s\n3.10\n' "$version" | sort -V | head -1)" != "3.10" ]]; then
        fail "Python 3.10+ is required, found $version"
    fi
    ok "Python $version"
}

# ------------------------------------------------------------------ user
create_user() {
    step "Service account"
    if id "$SERVICE_USER" &>/dev/null; then
        ok "user '$SERVICE_USER' already exists"
    else
        useradd --system --create-home --home-dir "$INSTALL_DIR" --shell /bin/bash "$SERVICE_USER"
        ok "created system user '$SERVICE_USER'"
    fi
}

# ------------------------------------------------------------------ code
fetch_code() {
    step "Application code"
    local script_repo=""
    if [[ -f "$(dirname "${BASH_SOURCE[0]}")/../app/main.py" ]]; then
        script_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    fi

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "updating existing clone in $INSTALL_DIR"
        # The checkout belongs to the service user, so git needs to be told
        # that root working on it is expected.
        local git_dir=(git -c "safe.directory=$INSTALL_DIR" -C "$INSTALL_DIR")
        "${git_dir[@]}" remote set-url origin "$REPO_URL"
        "${git_dir[@]}" fetch --quiet origin "$BRANCH"
        "${git_dir[@]}" checkout --quiet "$BRANCH"
        "${git_dir[@]}" reset --hard --quiet "origin/$BRANCH"
        ok "updated to the latest $BRANCH"
    elif [[ -n "$script_repo" && "$script_repo" != "$INSTALL_DIR" ]]; then
        info "copying from $script_repo"
        mkdir -p "$INSTALL_DIR"
        # A copy is not a clone: excluding .git keeps the next run on this same
        # path instead of trying to git-pull a directory that has no remote.
        # .env, the Telegram session and the database are left untouched.
        tar -C "$script_repo" --exclude=.git --exclude=.venv --exclude=data --exclude=.env -cf - . \
            | tar -C "$INSTALL_DIR" -xf -
        ok "copied into $INSTALL_DIR"
    elif [[ -n "$script_repo" ]]; then
        ok "already running from $INSTALL_DIR"
    else
        info "cloning $REPO_URL ($BRANCH)"
        git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
        ok "cloned into $INSTALL_DIR"
    fi

    mkdir -p "$INSTALL_DIR/data"
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
    chmod 700 "$INSTALL_DIR/data"
}

# --------------------------------------------------------- dependencies
install_deps() {
    step "Python environment"
    if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
        as_service_user python3 -m venv "$INSTALL_DIR/.venv"
        ok "created virtualenv"
    else
        ok "virtualenv already present"
    fi
    info "installing dependencies (this takes a minute)"
    local log="/tmp/signal-install-pip.log"
    if ! as_service_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip >"$log" 2>&1 ||
       ! as_service_user "$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" >>"$log" 2>&1; then
        warn "installing dependencies failed. Last lines of $log:"
        tail -n 12 "$log" | sed 's/^/      /'
        fail "could not install dependencies — usually no internet access, a proxy, or a full disk"
    fi
    ok "dependencies installed"
}

# ---------------------------------------------------------------- wizard
run_wizard() {
    step "Configuration"
    if [[ "$SKIP_SETUP" == true ]]; then
        warn "skipped (--skip-setup)"
        return
    fi
    if [[ "$INTERACTIVE" != true ]]; then
        warn "no terminal attached, so the wizard cannot ask questions"
        info "finish the setup by running:"
        info "  sudo -u $SERVICE_USER $INSTALL_DIR/.venv/bin/python -m app.cli setup"
        info "  (run it from $INSTALL_DIR)"
        return
    fi
    if [[ -f "$INSTALL_DIR/.env" ]]; then
        info "$INSTALL_DIR/.env already exists"
        read -rp "    Run the setup wizard again? [y/N]: " answer
        [[ "${answer,,}" == y* ]] || { ok "keeping the existing configuration"; return; }
    fi
    ( cd "$INSTALL_DIR" && as_service_user "$INSTALL_DIR/.venv/bin/python" -m app.cli setup ) || \
        warn "the wizard exited early — you can re-run it any time"
}

# --------------------------------------------------------------- service
install_service() {
    step "Background service"
    # Some cheap VPS plans run containers without systemd.
    if ! command -v systemctl >/dev/null || [[ ! -d /run/systemd/system ]]; then
        warn "systemd is not available on this server"
        info "start it manually instead, for example inside tmux:"
        info "  cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python -m app.main"
        return
    fi

    cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
# Generated by scripts/install.sh
[Unit]
Description=Telegram -> LINE signal bridge and dashboard
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m app.main
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}/data
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    ok "installed /etc/systemd/system/${SERVICE_NAME}.service"

    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        warn "no .env yet, so the service was not started"
        info "run the wizard, then: sudo systemctl enable --now ${SERVICE_NAME}"
        return
    fi

    systemctl enable --quiet "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    sleep 3
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        ok "service is running"
    else
        warn "the service is not running. Recent log:"
        journalctl -u "$SERVICE_NAME" -n 15 --no-pager | sed 's/^/      /'
    fi
}

summary() {
    local port ip
    port="$(grep -E '^API_PORT=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2 || true)"
    port="${port:-8000}"
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    ip="${ip:-<your-server-ip>}"

    step "Done"
    printf '    Dashboard    http://%s:%s/dashboard\n' "$ip" "$port"
    printf '    Admin        http://%s:%s/admin\n' "$ip" "$port"
    echo
    info "Status:   sudo systemctl status ${SERVICE_NAME}"
    info "Logs:     sudo journalctl -u ${SERVICE_NAME} -f"
    info "Restart:  sudo systemctl restart ${SERVICE_NAME}"
    info "Re-configure: cd ${INSTALL_DIR} && sudo -u ${SERVICE_USER} .venv/bin/python -m app.cli setup"
    echo
    warn "Port ${port} is open to anyone who can reach this server."
    info "Put nginx and TLS in front of it — see deploy/nginx.conf.example."
}

main() {
    if [[ "$SERVICE_ONLY" == true ]]; then
        id "$SERVICE_USER" &>/dev/null || fail "user '$SERVICE_USER' does not exist yet"
        install_service
        summary
        return
    fi

    printf '\n%sTelegram → LINE signal bridge · installer%s\n' "$BOLD" "$RESET"
    info "installing into $INSTALL_DIR as user '$SERVICE_USER'"

    install_packages
    check_python_version
    create_user
    fetch_code
    install_deps
    run_wizard
    install_service
    summary
}

main "$@"
