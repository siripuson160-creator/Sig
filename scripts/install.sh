#!/usr/bin/env bash
#
# One-command install on an Ubuntu/Debian VPS.
#
#   curl -fsSL https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh | sudo bash
#
# The repository is private, so both the script and the clone need a GitHub
# token with read access to it:
#
#   export GITHUB_TOKEN=github_pat_...
#   curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
#     https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh \
#     | sudo -E bash
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
#   7. optionally puts nginx and a Let's Encrypt certificate in front
#   8. schedules the daily and weekly backups
#   9. checks that the dashboard actually answers
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
# Setup happens in the browser by default; --cli-setup keeps the old terminal
# wizard, which is the only option when the server has no reachable port.
CLI_SETUP=false
GITHUB_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
DOMAIN="${DOMAIN:-}"
LETSENCRYPT_EMAIL="${LETSENCRYPT_EMAIL:-}"
NO_HTTPS=false

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
  --token TOKEN       GitHub token, for a private repository
                      (or set GITHUB_TOKEN before running)
  --domain NAME       serve over HTTPS at this domain (nginx + Let's Encrypt)
  --email ADDRESS     contact address for the certificate
  --no-https          skip nginx entirely; serve on http://IP:PORT
  --cli-setup         ask the setup questions here instead of in the browser
  --skip-setup        do not configure anything (code and deps only)
  --service-only      only (re)install the systemd service
  -h, --help          show this help

By default this installs everything, starts the service and prints a link.
You finish the setup in a browser: Telegram, LINE, prices, all of it.

Examples:
  sudo bash scripts/install.sh
  sudo bash scripts/install.sh --domain signals.example.com --email me@example.com
  sudo bash scripts/install.sh --token github_pat_xxx       # private repository
  sudo bash scripts/install.sh --cli-setup                  # configure in the terminal
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)          INSTALL_DIR="$2"; shift 2 ;;
        --user)         SERVICE_USER="$2"; shift 2 ;;
        --branch)       BRANCH="$2"; shift 2 ;;
        --token)        GITHUB_TOKEN="$2"; shift 2 ;;
        --domain)       DOMAIN="$2"; shift 2 ;;
        --email)        LETSENCRYPT_EMAIL="$2"; shift 2 ;;
        --no-https)     NO_HTTPS=true; shift ;;
        --cli-setup)    CLI_SETUP=true; shift ;;
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
auth_url() {
    # A token has to travel in the URL for HTTPS git, but it must not be
    # persisted: every caller resets the remote to the clean URL afterwards.
    if [[ -n "$GITHUB_TOKEN" ]]; then
        printf 'https://x-access-token:%s@github.com/%s' "$GITHUB_TOKEN" "${REPO_URL#https://github.com/}"
    else
        printf '%s' "$REPO_URL"
    fi
}

repo_is_reachable() {
    git ls-remote --exit-code "$(auth_url)" "$BRANCH" >/dev/null 2>&1
}

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
        "${git_dir[@]}" remote set-url origin "$(auth_url)"
        if ! "${git_dir[@]}" fetch --quiet origin "$BRANCH"; then
            "${git_dir[@]}" remote set-url origin "$REPO_URL"
            fail "could not fetch $BRANCH — the repository is private; pass --token or set GITHUB_TOKEN"
        fi
        "${git_dir[@]}" checkout --quiet "$BRANCH"
        "${git_dir[@]}" reset --hard --quiet "origin/$BRANCH"
        # Put the clean URL back so the token is not left in .git/config.
        "${git_dir[@]}" remote set-url origin "$REPO_URL"
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
        if ! repo_is_reachable; then
            if [[ -n "$GITHUB_TOKEN" ]]; then
                fail "cannot reach $REPO_URL on branch $BRANCH — check the token has read access to this repository"
            fi
            printf '\n%sThe repository is private.%s\n' "$YELLOW" "$RESET" >&2
            info "Give the installer a GitHub token with read access:" >&2
            info "  1. github.com/settings/personal-access-tokens -> Generate new token" >&2
            info "  2. pick this repository, set Contents: Read-only" >&2
            info "  3. re-run with:  sudo bash scripts/install.sh --token github_pat_xxx" >&2
            info "" >&2
            info "Or make the repository public, and no token is needed." >&2
            fail "cannot clone a private repository without a token"
        fi
        git clone --quiet --branch "$BRANCH" "$(auth_url)" "$INSTALL_DIR"
        # Never leave the token in .git/config.
        git -c "safe.directory=$INSTALL_DIR" -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
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
#: Set by prepare_web_setup when the browser wizard is the one that will run.
SETUP_TOKEN=""

# systemd needs the EnvironmentFile to exist, and the service needs to be able
# to write it when the browser wizard finishes. An empty, service-owned file
# satisfies both, and reads as "not configured" so /setup opens.
ensure_env_file() {
    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 600 /dev/null "$INSTALL_DIR/.env"
    fi
    chown "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
}

is_configured() {
    # Mirrors app/setup_state.py: all three present and non-empty.
    local key
    for key in TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_SOURCE_CHAT_ID; do
        grep -qE "^${key}=.+" "$INSTALL_DIR/.env" 2>/dev/null || return 1
    done
    return 0
}

prepare_web_setup() {
    # The token is what makes the setup page safe to expose: without it the
    # page refuses every request, so a stranger who finds the port cannot
    # point this install at their own Telegram account.
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$INSTALL_DIR/data"
    SETUP_TOKEN="$(as_service_user env "SIGNAL_DIR=$INSTALL_DIR" \
        "$INSTALL_DIR/.venv/bin/python" - <<'PY'
import os, secrets
# Absolute, because this runs from whichever directory the installer was
# started in, not from the install directory.
root = os.environ["SIGNAL_DIR"]
path = os.path.join(root, "data", "setup-token")
os.makedirs(os.path.join(root, "data"), exist_ok=True)
if os.path.exists(path):
    with open(path) as handle:
        existing = handle.read().strip()
    if existing:
        print(existing)
        raise SystemExit
token = secrets.token_urlsafe(24)
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write(token + "\n")
print(token)
PY
)" || fail "could not create the setup token"
    ok "setup link prepared"
}

run_wizard() {
    step "Configuration"
    ensure_env_file

    if [[ "$SKIP_SETUP" == true ]]; then
        warn "skipped (--skip-setup)"
        return
    fi
    if is_configured; then
        ok "already configured — leaving $INSTALL_DIR/.env alone"
        return
    fi

    if [[ "$CLI_SETUP" == true ]]; then
        if [[ "$INTERACTIVE" != true ]]; then
            warn "--cli-setup needs a terminal, and there is none attached"
            info "finish it later with:"
            info "  cd $INSTALL_DIR && sudo -u $SERVICE_USER .venv/bin/python -m app.cli setup"
            return
        fi
        ( cd "$INSTALL_DIR" && as_service_user "$INSTALL_DIR/.venv/bin/python" -m app.cli setup ) || \
            warn "the wizard exited early — you can re-run it any time"
        return
    fi

    prepare_web_setup
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
# The leading '-' lets the service start before the file has any content,
# which is what makes the browser setup wizard reachable on a fresh install.
EnvironmentFile=-${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m app.main
Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=30

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
# data/ holds the database and the Telegram session; .env is listed because
# the setup wizard writes it, and the process then exits so systemd restarts
# it with the new configuration. The code itself stays read-only.
ReadWritePaths=${INSTALL_DIR}/data ${INSTALL_DIR}/.env
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

    # Started even when unconfigured: that is exactly when the browser setup
    # page has to be reachable. In that state only the web tier runs.
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

# ---------------------------------------------------------------- helpers
env_value() {
    # Read one key out of the installed .env.
    grep -E "^$1=" "$INSTALL_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true
}

set_env_value() {
    # Set one key in the installed .env, keeping the rest of the file intact.
    local key="$1" value="$2"
    [[ -f "$INSTALL_DIR/.env" ]] || return 0
    if grep -qE "^${key}=" "$INSTALL_DIR/.env"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$INSTALL_DIR/.env"
    else
        printf '%s=%s\n' "$key" "$value" >> "$INSTALL_DIR/.env"
    fi
    chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/.env"
    chmod 600 "$INSTALL_DIR/.env"
}

# ------------------------------------------------------------ nginx + TLS
setup_https() {
    step "HTTPS"

    if [[ "$NO_HTTPS" == true ]]; then
        warn "skipped (--no-https)"
        return
    fi
    if [[ ! -f "$INSTALL_DIR/.env" ]]; then
        warn "no configuration yet, so nginx was not set up"
        return
    fi

    if [[ -z "$DOMAIN" && "$INTERACTIVE" == true ]]; then
        info "A domain gets you https:// with a free certificate. Point its DNS"
        info "A record at this server first. Leave blank to serve on the raw port."
        read -rp "    Domain name (blank to skip): " DOMAIN
    fi

    if [[ -z "$DOMAIN" ]]; then
        warn "no domain given — the dashboard will be served over plain HTTP"
        info "Add it later with: sudo bash scripts/install.sh --domain your.domain"
        return
    fi

    if ! command -v apt-get >/dev/null; then
        warn "cannot install nginx automatically on this system; do it by hand"
        info "A working sample is in deploy/nginx.conf.example"
        return
    fi

    info "installing nginx and certbot"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx certbot python3-certbot-nginx >/dev/null

    local port
    port="$(env_value API_PORT)"; port="${port:-8000}"

    # Behind nginx the app should not be reachable on its own port. The
    # service is already running by now, so it has to be restarted for the
    # new bind address to take effect.
    set_env_value API_HOST 127.0.0.1
    if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
        systemctl restart "$SERVICE_NAME"
    fi
    info "bound the app to 127.0.0.1:${port}; only nginx can reach it"

    cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
# Generated by scripts/install.sh
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    # Certbot rewrites this block to redirect to HTTPS.
    location / {
        proxy_pass         http://127.0.0.1:${port};
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 90s;
    }

    # The dashboard's live updates are a server-sent event stream, so this
    # one location must not be buffered.
    location /api/public/stream {
        proxy_pass         http://127.0.0.1:${port};
        proxy_http_version 1.1;
        proxy_set_header   Host \$host;
        proxy_set_header   Connection '';
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 24h;
    }
}
EOF

    ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" "/etc/nginx/sites-enabled/${SERVICE_NAME}"
    rm -f /etc/nginx/sites-enabled/default

    if ! nginx -t >/dev/null 2>&1; then
        warn "the nginx configuration did not validate; leaving it in place to inspect"
        nginx -t 2>&1 | sed 's/^/      /'
        return
    fi
    systemctl reload nginx || systemctl restart nginx
    ok "nginx is serving ${DOMAIN}"

    open_firewall
    request_certificate
}

request_certificate() {
    if [[ -z "$LETSENCRYPT_EMAIL" && "$INTERACTIVE" == true ]]; then
        read -rp "    Email for certificate expiry warnings (blank to skip): " LETSENCRYPT_EMAIL
    fi

    local args=(--nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect)
    if [[ -n "$LETSENCRYPT_EMAIL" ]]; then
        args+=(-m "$LETSENCRYPT_EMAIL")
    else
        args+=(--register-unsafely-without-email)
    fi

    info "asking Let's Encrypt for a certificate"
    if certbot "${args[@]}" >/tmp/certbot-install.log 2>&1; then
        ok "HTTPS is on — https://${DOMAIN}/dashboard"
        info "certbot renews it automatically via its systemd timer"
    else
        warn "certbot could not issue a certificate. Last lines of /tmp/certbot-install.log:"
        tail -n 8 /tmp/certbot-install.log | sed 's/^/      /'
        info "Usually DNS for ${DOMAIN} does not point here yet. Fix that, then run:"
        info "  sudo certbot --nginx -d ${DOMAIN}"
    fi
}

open_firewall() {
    command -v ufw >/dev/null || return 0
    ufw status 2>/dev/null | grep -q "Status: active" || return 0
    ufw allow "Nginx Full" >/dev/null 2>&1 || ufw allow 80,443/tcp >/dev/null 2>&1 || true
    ok "opened ports 80 and 443 in ufw"
}

# ---------------------------------------------------------------- backups
install_backup_cron() {
    step "Backups"
    cat > "/etc/cron.d/${SERVICE_NAME}" <<EOF
# Generated by scripts/install.sh — database and credentials backups.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
15 3 * * *  ${SERVICE_USER} cd ${INSTALL_DIR} && bash scripts/backup.sh          >> ${INSTALL_DIR}/data/backup.log 2>&1
30 3 * * 0  ${SERVICE_USER} cd ${INSTALL_DIR} && bash scripts/backup.sh --weekly >> ${INSTALL_DIR}/data/backup.log 2>&1
EOF
    chmod 644 "/etc/cron.d/${SERVICE_NAME}"
    ok "daily 03:15 and weekly Sunday 03:30, into ${INSTALL_DIR}/backups"
    info "Those archives include your credentials — keep the server backed up too."
}

# ------------------------------------------------------------ final check
verify_running() {
    step "Checking it works"
    local port attempt
    port="$(env_value API_PORT)"; port="${port:-8000}"

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        if curl -fsS --max-time 3 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
            ok "the dashboard is answering on port ${port}"
            if [[ -n "$DOMAIN" ]] && curl -fsS --max-time 5 "https://${DOMAIN}/healthz" >/dev/null 2>&1; then
                ok "https://${DOMAIN} is answering too"
            fi
            return 0
        fi
        sleep 2
    done

    warn "the dashboard did not answer on port ${port}"
    info "Look at the log with: sudo journalctl -u ${SERVICE_NAME} -n 40 --no-pager"
    return 0
}

summary() {
    local port ip base password dry
    port="$(env_value API_PORT)"; port="${port:-8000}"
    ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    ip="${ip:-<your-server-ip>}"
    password="$(env_value ADMIN_PASSWORD)"
    dry="$(env_value DRY_RUN)"

    if [[ -n "$DOMAIN" ]]; then
        base="https://${DOMAIN}"
    else
        base="http://${ip}:${port}"
    fi

    # The install is not finished yet: the remaining questions are answered in
    # the browser, so the link is the only thing that matters on screen.
    if [[ -n "$SETUP_TOKEN" ]]; then
        step "Almost done — finish in your browser"
        echo
        printf '    %sOpen this link:%s\n\n' "$BOLD" "$RESET"
        printf '      %s/setup?token=%s\n\n' "$base" "$SETUP_TOKEN"
        info "It asks for your Telegram API details, sends you a login code,"
        info "lets you pick the signal group, then LINE and the price source."
        echo
        info "เปิดลิงก์ข้างบนในเบราว์เซอร์ แล้วตั้งค่าต่อได้เลย"
        info "(Telegram → เลือกกลุ่ม → LINE → แหล่งราคา)"
        echo
        info "The link works once, and stops working as soon as setup finishes."
        info "Lost it?  sudo cat ${INSTALL_DIR}/data/setup-token"
        echo
        if [[ -z "$DOMAIN" ]]; then
            warn "This link is plain HTTP, so what you type crosses the network"
            warn "unencrypted — including your Telegram login code. On an untrusted"
            warn "network, tunnel it over SSH instead and use http://localhost:${port}/setup :"
            info "  ssh -L ${port}:localhost:${port} root@${ip}"
            echo
            info "Or install with a domain to get HTTPS:"
            info "  sudo bash ${INSTALL_DIR}/scripts/install.sh --domain your.domain --email you@example.com"
        fi
        echo
        info "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
        return
    fi

    step "Done"
    printf '    Members      %s/dashboard\n' "$base"
    printf '    Admin        %s/admin\n' "$base"
    [[ -n "$password" ]] && printf '    Password     %s\n' "$password"
    echo
    info "Status:    sudo systemctl status ${SERVICE_NAME}"
    info "Logs:      sudo journalctl -u ${SERVICE_NAME} -f"
    info "Restart:   sudo systemctl restart ${SERVICE_NAME}"
    info "Reconfigure: cd ${INSTALL_DIR} && sudo -u ${SERVICE_USER} .venv/bin/python -m app.cli setup"
    info "Update:    sudo bash ${INSTALL_DIR}/scripts/install.sh --skip-setup"
    echo

    if [[ "$dry" == "true" ]]; then
        warn "Test mode is on: messages are stored but nothing is sent to LINE."
        info "Go live by setting DRY_RUN=false in ${INSTALL_DIR}/.env, then restarting."
    fi
    if [[ -z "$DOMAIN" ]]; then
        warn "Served over plain HTTP on port ${port}, reachable by anyone who can"
        warn "reach this server. Add a domain for HTTPS:"
        info "  sudo bash ${INSTALL_DIR}/scripts/install.sh --domain your.domain --email you@example.com"
    fi
}

main() {
    if [[ "$SERVICE_ONLY" == true ]]; then
        id "$SERVICE_USER" &>/dev/null || fail "user '$SERVICE_USER' does not exist yet"
        install_service
        install_backup_cron
        verify_running
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
    setup_https
    install_backup_cron
    verify_running
    summary
}

main "$@"
