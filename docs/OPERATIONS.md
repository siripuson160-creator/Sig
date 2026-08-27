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

The installer creates the `signal` user and `/opt/signal`, installs
dependencies, sets up nginx and a certificate if you gave a domain, schedules
the backups, starts the service, and prints a link:

```
      http://203.0.113.10:8000/setup?token=KvG85TyjZolxZEN6lVbJF993PYBRbyB5
```

The rest happens in a browser: Telegram credentials, the login code, the source
group, LINE, prices and scoring. Saving writes `.env`, retires the setup link
and restarts the service with every component running.

### How the setup page is protected

Two conditions, both required on every `/api/setup/*` request:

* the install is genuinely unconfigured — the wizard cannot be used to
  reconfigure a running system, and
* the request carries the token from `/opt/signal/data/setup-token`, a 0600
  file only someone with shell access can read.

So an unconfigured box on a public IP cannot be captured by whoever finds the
port first. The token is deleted the moment setup succeeds.

The Telegram code and 2FA password are typed by the account owner into their
own browser, reach their own server, and go straight to Telethon's `sign_in`.
They are never stored, never written to `.env`, and never logged — the same
guarantee the terminal wizard gives (section 3). Transport is the operator's
call: on a bare IP there is no TLS, so the page warns and offers the tunnel.

If the link is lost:

```bash
sudo cat /opt/signal/data/setup-token
```

If setup was interrupted, just open the link again — nothing is written until
the final step. To start over completely, empty `.env` and restart the service;
it will come back up in setup mode with a fresh token in the journal.

### Setup mode

An unconfigured install runs the web tier only. The listener has no credentials
to connect with and the LINE worker has no destination, so starting them would
crash-loop the unit; serving `/setup` is the one useful thing to do. The
journal says so at startup:

```
not configured yet — starting in setup mode, only the web tier is running
open http://<this-server>:8000/setup?token=…
```

Prefer the terminal? `--cli-setup` asks the same questions there, and
`python -m app.cli setup` still works on an existing install.

### A private repository

`curl: (22) The requested URL returned error: 404` on the command above means
the repository is private — GitHub answers 404 rather than 403 for a repository
an anonymous request may not know exists. The file is there; the request is not
authenticated.

Create a fine-grained token at
<https://github.com/settings/personal-access-tokens>, scoped to this repository
with **Contents: Read-only**, then:

```bash
export GITHUB_TOKEN=github_pat_xxx
curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://raw.githubusercontent.com/siripuson160-creator/Sig/main/scripts/install.sh \
  | sudo -E bash
```

The token is needed twice: once by `curl` to read the script, and once by the
installer to clone. `sudo -E` passes it through; `--token github_pat_xxx` does
the same when running from a clone. The installer resets the remote to the
plain URL right after cloning, so the token never lands in `.git/config` — but
it is still a credential, so use a read-only one and delete it when the install
is done. Making the repository public removes the need for it entirely.

Useful flags:

```bash
sudo bash scripts/install.sh --dir /srv/signal   # install somewhere else
sudo bash scripts/install.sh --skip-setup        # update code and deps only
sudo bash scripts/install.sh --service-only      # rewrite and restart the unit
sudo bash scripts/install.sh --branch some-branch  # install a specific branch
sudo bash scripts/install.sh --no-https          # skip nginx entirely
sudo bash scripts/install.sh --token github_pat_xxx  # private repository
sudo bash scripts/install.sh --cli-setup         # configure in the terminal
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

# The unit needs this file to exist and to be writable by the service account.
sudo -u signal touch /opt/signal/.env && sudo chmod 600 /opt/signal/.env

sudo cp deploy/telegram-line-forwarder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-line-forwarder

# Starts in setup mode and prints the /setup link with its token:
journalctl -u telegram-line-forwarder -f
```

To configure in the terminal instead, do it before starting the unit:

```bash
# Asks the configuration questions, writes .env, signs in, picks the group.
# Interactive: the account owner types the Telegram code here.
sudo -u signal .venv/bin/python -m app.cli setup
sudo -u signal .venv/bin/python -m app.cli check
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

### The archive of what went to LINE

`/admin` → **Sent to LINE** is the record of every message the LINE group
received, newest first. It shows the *delivered* text rather than the raw
Telegram content, so the `EDITED` prefix and the 4900-character cap are visible
exactly as members saw them. Search matches the message body or a Telegram
message id, so an id pasted from a complaint finds the entry.

This is the page to check when someone says a signal never arrived: the entry
carries its delivery status, the number of attempts, the LINE message id on
success, and the last error on failure.

It is not the same as **Messages**, which is the delivery queue — that view is
for requeuing something that failed.

To show members the same archive on the public dashboard:

```bash
PUBLIC_BROADCAST_ENABLED=true
```

Off by default, deliberately: the signal text is what members pay for, and the
public dashboard is readable by anyone who has the URL.

### Changing settings from the admin page

`/admin` → **Settings** edits the connection settings without an SSH session:
the Telegram credentials, the LINE token and destination, test mode, the price
provider and its key, the scoring rules. Saving rewrites `.env` and restarts
the service, so what is running is always what is on disk.

Three things fence that write path:

* **A fixed allow-list.** Only the keys in `EDITABLE` (`app/api/routes_settings.py`)
  can be written; anything else posted is refused. No statistic is on that
  list, so section 43 still holds — nothing on the dashboard can be typed in.
* **Secrets are never sent back to the browser.** A token shows as
  `abcd…wxyz` and an empty box means "keep what is stored", so the form can be
  saved without re-typing it.
* **Every change is audited**, naming the keys that moved, who moved them and
  from where — never a secret's value.

Anything not on the page is still edited in `.env` and picked up on restart.

### Language

The dashboard and the admin console are in Thai and English, Thai by default.
The switch is in the top bar and the choice is remembered per browser.
Translations live in one file, `app/web/static/js/i18n.js`, keyed by the
English text: a string with no Thai entry falls back to English rather than
breaking, so adding a phrase to the UI never breaks the other language.

### Where results come from — RESULT_SOURCE

Two ways to decide whether a signal won:

| `RESULT_SOURCE` | How a verdict is reached | Needs a price feed |
|---|---|---|
| `price` (default) | Each signal is replayed against price history | Yes |
| `message` | The provider's own report decides it | No |

In `message` mode, a follow-up the provider posts *as a reply* to its own
signal is read for an outcome:

```
+70Pips making profit again.           ->  win, +7 points, still held
90Pips ! Can secure as TP2 now guys.   ->  TP2 hit, +9 points, closed
SL hit guys, next one soon             ->  loss, entry to stop
Be secure and set your breakeven.      ->  nothing published (no figure given)
cancel this one, no trade              ->  CANCELLED, not counted as a loss
```

**An announced pip count is the result.** This desk reports its wins as
"+70Pips" and rarely names a target, so waiting for the words "TP1" would leave
a won trade at PENDING for ever. The figure is booked as a win immediately, and
the signal stays ACTIVE because it has not been closed — they are still holding.

A later, larger figure on the same trade replaces the earlier one: "+50Pips"
then "+90Pips" is one trade improving, not two results. A smaller later figure
does not shrink what was already counted. Naming a target ("secure as TP2")
closes the trade for good.

**The Telegram reply is the link.** A report that does not reply to the signal
cannot be attached to a trade and is ignored, because guessing which trade was
meant would put a number on the wrong signal.

A trade is decided once: a later "SL hit" after a reported TP does not rewrite
the booked result. Announced pips are converted with `PIP_SIZE`, so "90 Pips"
becomes 9 points of gold. Where no number is announced, the distance from the
posted entry to the posted level is used instead.

In `message` mode the result engine does not consult price history at all — it
reports itself as DEGRADED on the admin overview, which is expected.

**What this costs you.** These figures are *self-reported*. If the provider
overstates a result, the dashboard repeats it. So the provenance travels with
the data: every signal records `result_source` (`PRICE` / `MESSAGE` / `MANUAL`),
the API exposes `verified`, and the member dashboard carries a banner on the
overview saying the results are the provider's own and unverified. Do not remove
that banner while running in this mode — it is the difference between reporting
a claim and passing a claim off as a measurement.

Switching mode needs a restart. Signals already decided keep their verdict and
their recorded source; only new ones follow the new setting.

### Targets quoted in pips, and PIP_SIZE

Some desks post the targets as a distance rather than a price:

```
Gold Buy Now @ 4601 - 4596  Sl: 4590  TP: 50/100Pips
```

That means 50 and 100 pips *away from entry*, so with `PIP_SIZE=0.1` the
targets are 4606 and 4611. Read as prices instead, the same message produces a
"result" of 100 − 4601 = −4501 points, which is how this was found.

`PIP_SIZE` is what one pip is worth in price, and it only affects messages
written this way. Check it against your own source's wording before trusting
the numbers:

| The source calls a $1 move on gold | `PIP_SIZE` |
|---|---|
| 10 pips | `0.1` (default) |
| 100 pips | `0.01` |
| 1 pip | `1.0` |

The quickest check is a message where they announce the profit: if they say
"+50 Pips" when price has moved from 4601.20 to 4606.33, a $5 move is 50 pips,
so a pip is $0.10.

Every converted signal says so in its notes on the dashboard, e.g.
*"TP quoted in pips (50/100); converted at 0.1 per pip"*. After changing
`PIP_SIZE`, restart and use **Re-parse** on the affected signals.

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

If a provider *is* configured and results are still pending, check the log for
the reason. The two common ones:

* **`yahoo cannot price XAUUSD`** — Yahoo has no spot gold, only the futures
  contract. Switch to `twelvedata` with a free key.
* **`twelvedata error: ... run out of API credits`** — the free plan allows 800
  requests a day. Raise `RESULT_ENGINE_INTERVAL_SECONDS`, or move to a paid plan.

### Watching the price feed budget

One request per symbol per pass, and none when no signal is open:

| Interval | Requests/day with one symbol |
|---|---|
| 60s | ~1440 — over the free plan |
| 120s (default) | ~720 |
| 300s | ~290 |

Results are only as fresh as the interval, so 120s means a TP hit shows up on
the dashboard within about two minutes.

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
