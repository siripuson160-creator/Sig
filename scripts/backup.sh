#!/usr/bin/env bash
#
# Database and configuration backup (section 55).
#
#   bash scripts/backup.sh                     # daily backup into ./backups
#   bash scripts/backup.sh --dir /backup       # somewhere else
#   bash scripts/backup.sh --weekly            # tag it as the weekly copy
#
# Install as a cron job (as the service user):
#
#   crontab -e
#   15 3 * * *  cd /opt/signal && bash scripts/backup.sh          >> data/backup.log 2>&1
#   30 3 * * 0  cd /opt/signal && bash scripts/backup.sh --weekly >> data/backup.log 2>&1
#
# What it copies:
#   * the database (PostgreSQL dump, or a consistent SQLite snapshot)
#   * .env and the Telegram session, as a separate 0600 archive
#
# The credentials archive is a login to your Telegram account and your LINE
# channel. Keep it off public storage and encrypt it before moving it anywhere.

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAILY="${KEEP_DAILY:-14}"
KEEP_WEEKLY="${KEEP_WEEKLY:-8}"
LABEL="daily"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)    BACKUP_DIR="$2"; shift 2 ;;
        --weekly) LABEL="weekly"; shift ;;
        --daily)  LABEL="daily"; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 1 ;;
    esac
done

cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a

STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="${BACKUP_DIR}/${LABEL}"
mkdir -p "$DEST"

DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./data/signals.db}"

# ------------------------------------------------------------------ database
if [[ "$DATABASE_URL" == postgresql* ]]; then
    # postgresql+asyncpg://user:pass@host:port/name
    URL="${DATABASE_URL#*://}"
    CREDS="${URL%%@*}"; REST="${URL#*@}"
    PGUSER="${CREDS%%:*}"; PGPASSWORD="${CREDS#*:}"
    HOSTPORT="${REST%%/*}"; PGDATABASE="${REST#*/}"
    PGHOST="${HOSTPORT%%:*}"; PGPORT="${HOSTPORT#*:}"
    [[ "$PGPORT" == "$PGHOST" ]] && PGPORT=5432
    export PGPASSWORD

    OUT="${DEST}/db-${STAMP}.sql.gz"
    pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE" | gzip > "$OUT"
    echo "database  -> $OUT"
else
    DB_PATH="${DATABASE_URL#*///}"
    if [[ -f "$DB_PATH" ]]; then
        OUT="${DEST}/db-${STAMP}.sqlite"
        # The sqlite3 CLI is not installed everywhere, but Python always is
        # here — and its backup API takes the same consistent snapshot, which
        # a plain file copy would not while the service is writing.
        PYTHON="${PYTHON:-}"
        for candidate in ./.venv/bin/python python3 python; do
            if [[ -z "$PYTHON" ]] && command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; fi
        done
        [[ -n "$PYTHON" ]] || { echo "database  -> skipped (no python found)" >&2; exit 1; }

        "$PYTHON" - "$DB_PATH" "$OUT" <<'PYBACKUP'
import sqlite3, sys

source, target = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
PYBACKUP
        gzip -f "$OUT"
        echo "database  -> ${OUT}.gz"
    else
        echo "database  -> skipped (no file at $DB_PATH)" >&2
    fi
fi

# ------------------------------------------------------------- credentials
CRED_OUT="${DEST}/credentials-${STAMP}.tar.gz"
FILES=()
[[ -f .env ]] && FILES+=(.env)
SESSION="${TELEGRAM_SESSION:-./data/telegram.session}"
[[ -f "$SESSION" ]] && FILES+=("$SESSION")

if [[ ${#FILES[@]} -gt 0 ]]; then
    tar -czf "$CRED_OUT" "${FILES[@]}"
    chmod 600 "$CRED_OUT"
    echo "config    -> $CRED_OUT (mode 600 — encrypt before moving it off this box)"
fi

# ---------------------------------------------------------------- rotation
KEEP=$([[ "$LABEL" == "weekly" ]] && echo "$KEEP_WEEKLY" || echo "$KEEP_DAILY")
for pattern in "db-*" "credentials-*"; do
    # shellcheck disable=SC2012  # filenames are timestamps, so ls sorts correctly
    ls -1t "${DEST}"/${pattern} 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
        rm -f "$old"
        echo "pruned    -> $old"
    done
done

echo "done: ${LABEL} backup ${STAMP}"
