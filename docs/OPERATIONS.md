# Operations runbook

Day-to-day running of the Telegram → LINE bridge and dashboard.

---

## First-time setup on a fresh Ubuntu VPS

```bash
sudo adduser --system --group --home /opt/signal signal
sudo -u signal git clone <repo> /opt/signal
cd /opt/signal
sudo -u signal python3 -m venv .venv
sudo -u signal .venv/bin/pip install -r requirements.txt

sudo -u signal cp .env.example .env
sudo -u signal chmod 600 .env
sudo -u signal nano .env          # fill in credentials

# Interactive: the account owner types the Telegram code here.
sudo -u signal .venv/bin/python -m app.cli login
sudo -u signal .venv/bin/python -m app.cli chats     # note the group id
sudo -u signal nano .env                             # TELEGRAM_SOURCE_CHAT_ID
sudo -u signal .venv/bin/python -m app.cli check

sudo cp deploy/signal-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now signal-bridge
journalctl -u signal-bridge -f
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
4. Put that value in `LINE_TARGET_ID`.

Verify with:

```bash
python -m app.cli check     # confirms the token and shows the bot's name
```

`python -m app.cli drain` pushes anything currently queued, which is a safe way
to confirm delivery end to end with a real message.

---

## Daily checks

```bash
systemctl status signal-bridge
journalctl -u signal-bridge --since "1 hour ago" | grep -iE "error|failed"
python -m app.cli stats
```

Or open `/admin` → **Status**: database, LINE, price provider, open signals and
the delivery queue counts are all on one screen.

---

## Common situations

### Messages are not reaching LINE

1. `/admin` → **LINE queue**, filter `FAILED`; the last error is on the row.
2. `python -m app.cli check` — is the token still valid?
3. Common causes:
   - the bot was removed from the LINE group → re-invite it
   - the channel access token was rotated → update `.env`, restart
   - `LINE_ENABLED=false` → messages are stored with status `SKIPPED`
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
3. If it is a one-off, `/admin` → **Override** and record why. The signal is
   then frozen and shown as manually set.

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

Everything that matters is the database plus two files.

```bash
# PostgreSQL
pg_dump -U signal signals | gzip > /backup/signals-$(date +%F).sql.gz

# SQLite (safe while running)
sqlite3 data/signals.db ".backup '/backup/signals-$(date +%F).db'"

# Credentials — back up separately and encrypted.
#   .env
#   data/telegram.session   <- this file is a login to the Telegram account
```

Restore is `psql < dump` or copying the SQLite file back. Keep at least a
week of daily dumps; the message history is the audit trail behind every
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
| `giving up on … after 5 attempts` | Marked `FAILED`, needs a requeue |
| `signal … created from …` | A message parsed as a trade |
| `signal … already resolved; edit v3 recorded only` | Verdict kept, history appended |
| `result engine updated N signal(s)` | Results changed on this pass |

Logs are timestamped in Asia/Bangkok and carry no credentials.

---

## Restarting safely

```bash
sudo systemctl restart signal-bridge
```

A restart is always safe: delivery state lives in the database, duplicate
detection stops anything from being re-sent, and Telegram replays what was
missed.
