# Operations runbook

Day-to-day running of the Telegram → LINE bridge and dashboard.

---

## First-time setup on a fresh Ubuntu VPS

```bash
curl -fsSL https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh | sudo bash
```

With a domain, so the dashboard is served over HTTPS (section 59):

```bash
curl -fsSL https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh \
  | sudo bash -s -- --domain signals.example.com --email you@example.com
```

That is the whole thing. The installer creates the `signal` user and
`/opt/signal`, installs dependencies, runs the setup wizard, signs you in to
Telegram, lets you pick the source group, starts the service, sets up nginx and
a certificate, schedules the backups, and checks the dashboard answers.

Useful flags:

```bash
sudo bash scripts/install.sh --dir /srv/signal   # install somewhere else
sudo bash scripts/install.sh --skip-setup        # update code and deps only
sudo bash scripts/install.sh --service-only      # rewrite and restart the unit
sudo bash scripts/install.sh --branch some-branch  # install a specific branch
sudo bash scripts/install.sh --no-https          # skip nginx entirely
```

Re-run it any time to update: `.env`, the Telegram session and the database
are left alone.

### What the installer leaves behind

| Path | What it is |
|---|---|
| `/opt/signal` | Code, virtualenv, `.env`, `data/` (database + Telegram session) |
| `/etc/systemd/system/telegram-line-forwarder.service` | The service, enabled at boot |
| `/etc/cron.d/telegram-line-forwarder` | Daily 03:15 and weekly Sunday 03:30 backups |
| `/etc/nginx/sites-available/telegram-line-forwarder` | The reverse proxy, when a domain was given |
| `/opt/signal/backups/` | Where backups land |

With a domain configured, `API_HOST` is set to `127.0.0.1`, so the app is
reachable only through nginx and not directly on its port.

### Going live from test mode

If you answered yes to test mode during setup, nothing reaches LINE yet. Watch
the parser on the dashboard first, then:

```bash
sudo sed -i 's/^DRY_RUN=true/DRY_RUN=false/' /opt/signal/.env
sudo systemctl restart telegram-line-forwarder
```

### Doing it by hand

```bash
sudo adduser --system --group --home /opt/signal signal
sudo -u signal git clone <repo> /opt/signal
cd /opt/signal
sudo -u signal python3 -m venv .venv
sudo -u signal .venv/bin/pip install -r requirements.txt

# Asks the configuration questions, writes .env, signs in, picks the group.
# Interactive: the account owner types the Telegram code here.
sudo -u signal .venv/bin/python -m app.cli setup
sudo -u signal .venv/bin/python -m app.cli check

sudo cp deploy/telegram-line-forwarder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-line-forwarder
journalctl -u telegram-line-forwarder -f
```

### PostgreSQL

```bash
sudo -u postgres createuser signal --pwprompt
sudo -u postgres createdb signals --owner signal
# .env:
# DATABASE_URL=postgresql+asyncpg://signal:PASSWORD@localhost:5432/signals
```

Tables are created automatically on start. There is no migration tool: the
schema is created with `CREATE TABLE IF NOT EXISTS`, and enum-like columns are
plain `VARCHAR`, so adding a new status value needs no migration. A column
addition does need a manual `ALTER TABLE` — see *Schema changes* below.

---

## Finding the LINE group id

The LINE Messaging API only reveals a group id through a webhook event.

1. In the LINE Developers console, set a webhook URL you can read (any
   request-logging endpoint works) and enable "Use webhook".
2. Invite the channel's bot into the group and post a message.
3. The webhook payload contains `"source": {"type": "group", "groupId": "Cxxxx…"}`.
4. Put that value in `LINE_GROUP_ID`.

Verify with:

```bash
python -m app.cli check     # confirms the token and shows the bot's name
```

`python -m app.cli drain` pushes anything currently queued, which is a safe way
to confirm delivery end to end with a real message.

---

## Daily checks

```bash
systemctl status telegram-line-forwarder
journalctl -u telegram-line-forwarder --since "1 hour ago" | grep -iE "error|failed"
cd /opt/signal && sudo -u signal .venv/bin/python -m app.cli check
sudo -u signal .venv/bin/python -m app.cli stats
```

`check` is the quickest "is everything wired up?" — it reports the database,
the Telegram session, the LINE credentials and the price provider in one screen,
and exits non-zero if something is wrong.

Or open `/admin` → **Status**: database, LINE, price provider, open signals and
the delivery queue counts are all on one screen.

---

## Common situations

### Messages are not reaching LINE

1. `/admin` → **Messages**, filter `FAILED`; the last error is on the row.
2. `python -m app.cli check` — is the token still valid?
3. Common causes:
   - the bot was removed from the LINE group → re-invite it
   - the channel access token was rotated → update `.env`, restart
   - `LINE_ENABLED=false` or `DRY_RUN=true` → messages are stored with status
     `SKIPPED` and never sent; the admin overview says so in a banner
4. After fixing, requeue the failed rows from `/admin`, or:
   `python -m app.cli drain`

Nothing is lost while LINE is down: messages stay `PENDING` and are delivered
in order once it recovers.

### Telegram stopped receiving

```
telegram session is not authorised
```

The session was revoked or expired. Run `python -m app.cli login` again as the
`signal` user, then restart the service. Messages posted while the process was
down are replayed on reconnect (`catch_up=True`) and duplicates are dropped.

### A signal was parsed wrongly

1. Open the signal on `/dashboard` — the parse of every version is shown.
2. If the parser should have handled it, add the pattern
   (`app/signals/patterns.py`), add a test, deploy, then `/admin` → **Re-parse**.
3. If it is a one-off, `/admin` → **Signals** → **Correct**, and write why. The
   reason is required. The signal is then frozen, shown as manually set on the
   public dashboard, and the change appears in the audit log with the old and
   new values.

### A result looks wrong

- Check the note on the signal: it records ambiguity, tie-breaks and
  mark-to-market closes.
- `/admin` → **Evaluate** re-runs the engine for that signal.
- Cached candles live in `price_candles`; deleting the rows for a symbol and
  timeframe forces a re-fetch on the next evaluation.

### Signals are stuck at PENDING

Expected when `PRICE_DATA_PROVIDER=none` — the system records signals but does
not invent results. Configure a provider and they are judged retroactively:

```bash
python -m app.cli evaluate
```

---

## Backups

The installer already scheduled these in `/etc/cron.d/telegram-line-forwarder`.
Check they are running with `tail /opt/signal/data/backup.log`.

`scripts/backup.sh` handles both databases and the credentials, and prunes old
copies. To run one by hand:

```bash
bash scripts/backup.sh                 # daily  -> ./backups/daily
bash scripts/backup.sh --weekly        # weekly -> ./backups/weekly
bash scripts/backup.sh --dir /backup   # somewhere else
```

It keeps 14 daily and 8 weekly copies by default (`KEEP_DAILY`, `KEEP_WEEKLY`).

Two things come out of it:

* **The database** — a `pg_dump` for PostgreSQL, or a consistent SQLite
  snapshot taken with the backup API (safe while the service is writing).
* **The credentials** — `.env` and `data/telegram.session`, in a separate
  archive at mode 600. That session file is a login to the Telegram account:
  encrypt it before it leaves the server, and never put it on public storage.

Restore is `gunzip -c dump.sql.gz | psql`, or copying the SQLite file back.
Keep at least a week; the message history is the audit trail behind every
published number.

---

## Schema changes

Adding a status or result value needs no migration — those columns are
`VARCHAR` with an application-level check.

Adding a *column* to an existing deployment:

```sql
ALTER TABLE signals ADD COLUMN my_column DOUBLE PRECISION;  -- PostgreSQL
ALTER TABLE signals ADD COLUMN my_column REAL;              -- SQLite
```

then deploy the code that uses it. `Base.metadata.create_all` only creates
missing tables; it never alters existing ones.

---

## Log reference

| Message | Meaning |
|---|---|
| `NEW -100…/500 v1 queued for LINE` | New Telegram message stored |
| `EDIT -100…/500 v2 queued for LINE` | Edit stored as a new version |
| `duplicate … ignored` | Replay after restart; nothing sent |
| `delivered …/… v1 to LINE (id)` | Push accepted by LINE |
| `retrying …/… v1 in 5s` | Transient LINE failure, will retry |
| `giving up on … after 3 attempts` | Marked `FAILED`, needs a requeue |
| `Old hash: … / New hash: …` | An edit was detected; both revisions logged |
| `Telegram reconnecting` / `Telegram reconnected` | Connection dropped and came back |
| `signal … created from …` | A message parsed as a trade |
| `signal … already resolved; edit v3 recorded only` | Verdict kept, history appended |
| `result engine updated N signal(s)` | Results changed on this pass |

Logs are timestamped in Asia/Bangkok and carry no credentials.

Under systemd the journal rotates them for you. To also write a rotating file,
set `LOG_FILE=./logs/app.log` (`LOG_MAX_BYTES`, `LOG_BACKUP_COUNT` control the
rotation).

### Audit log

Longer-lived than the logs, and the record behind the published numbers:
`/admin` → **Audit log**, or `GET /api/admin/audit`. It captures signal
creation and edits, every TP and SL hit, cancellations, manual corrections with
old and new values, admin sign-ins including failed ones, LINE sends and
failures, and Telegram reconnects. Nothing deletes from it.

---

## Monitoring

`/admin` → **Overview** shows a light per component (section 56):

| Light | Meaning |
|---|---|
| GREEN | The component checked in within the last two minutes |
| YELLOW | Running but degraded — LINE in test mode, or a large queue |
| RED | No heartbeat, or the check failed |

Components write a heartbeat to the database every 30 seconds, so the lights
are accurate even when the API runs as a separate process from the listener.
**System status** shows the raw heartbeats and how long ago each arrived.

---

## HTTPS

Set up during installation when a domain is given. To add or change it later:

```bash
sudo bash /opt/signal/scripts/install.sh --domain signals.example.com --email you@example.com
```

Certbot installs a systemd timer that renews the certificate automatically;
`sudo certbot renew --dry-run` confirms renewal works.

If certbot failed because DNS was not pointing here yet, fix the DNS and run:

```bash
sudo certbot --nginx -d signals.example.com
```

The generated site keeps `/api/public/stream` unbuffered — that is the live
update stream, and buffering it would make the dashboard stop refreshing by
itself. A hand-written config should do the same; see
`deploy/nginx.conf.example`, which also shows how to put an IP allow-list in
front of `/admin`.

---

## Restarting safely

```bash
sudo systemctl restart telegram-line-forwarder
```

A restart is always safe: delivery state lives in the database, duplicate
detection stops anything from being re-sent, and Telegram replays what was
missed.
