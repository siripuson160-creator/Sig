# Telegram Signal → LINE + Trading Performance Dashboard

Forwards every message from a Telegram gold-signal group into a LINE group,
keeps the full edit history, parses the signals, checks them against price
data, and publishes the results on a dashboard members can read.

- **Timezone:** Asia/Bangkok
- **Runs:** 24/7 on Ubuntu (Windows works as a fallback)
- **Units:** points only — never money (see [Why no money figures](#why-no-money-figures))

---

## How it works

```
Telegram group
      │  (your own account, MTProto/Telethon — you are a normal member, not an admin)
      ▼
Telegram listener ──► Message processor ──┬──► PostgreSQL / SQLite
                                          │      telegram_messages (every version)
                                          │      signals, signal_versions, price_candles
                                          │
                                          └──► LINE queue ──► LINE group

signals ──► Result engine ──► Statistics engine ──► Dashboard API ──┬──► /dashboard (members, read-only)
              ▲                                                     └──► /admin     (operators, API key)
              │
        Price provider (none / csv / twelvedata)
```

Four components run in one process (`python -m app.main`) and can be split
with `--only`:

| Component  | What it does                                                    |
|------------|-----------------------------------------------------------------|
| `listener` | Reads the source group, stores every new and edited message      |
| `line`     | Pushes queued messages to the LINE group, with retries           |
| `results`  | Replays price history and decides TP/SL                          |
| `api`      | Serves the dashboard and its JSON API                            |

---

## Quick start

```bash
git clone <this repo> /opt/signal && cd /opt/signal
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

cp .env.example .env && chmod 600 .env
# Fill in TELEGRAM_API_ID / TELEGRAM_API_HASH from https://my.telegram.org
# and the LINE credentials. Leave PRICE_DATA_PROVIDER=none for now.

.venv/bin/python -m app.cli login    # you type the code Telegram sends you
.venv/bin/python -m app.cli chats    # find the source group's chat id
# put that id in TELEGRAM_SOURCE_CHAT_ID

.venv/bin/python -m app.cli check    # verify everything is wired up
.venv/bin/python -m app.main         # run it
```

Open `http://localhost:8000/dashboard`.

### เริ่มใช้งานอย่างย่อ (ภาษาไทย)

1. `cp .env.example .env` แล้วกรอก `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`
   (ขอได้ที่ https://my.telegram.org) และ token ของ LINE
2. รัน `python -m app.cli login` — **คุณเป็นคนกรอก OTP และรหัส 2FA เอง**
   ระบบไม่เก็บและไม่ส่งต่อให้ใคร
3. รัน `python -m app.cli chats` เพื่อดู chat id ของกลุ่มต้นทาง
   แล้วใส่ใน `TELEGRAM_SOURCE_CHAT_ID`
4. รัน `python -m app.cli check` เพื่อตรวจการตั้งค่า
5. รัน `python -m app.main` แล้วเปิด `/dashboard`

ถ้ายังไม่มีข้อมูลราคา ให้ตั้ง `PRICE_DATA_PROVIDER=none` ระบบจะเก็บ signal ไว้
และแสดงสถานะ `PENDING` จนกว่าจะต่อ price provider แล้วจึงย้อนกลับไปตัดสินผลให้

---

## Telegram sign-in

The source is a group where your account is an ordinary member, so a bot token
cannot read it. The system therefore signs in as **your user account** over
MTProto (Telethon).

`python -m app.cli login` asks for your phone number, the login code and — if
you use one — your 2FA password. **You type those yourself, once.** They are
passed straight to Telegram and are never stored, logged or transmitted
anywhere else. There is no configuration setting for them, and nobody
(developer, administrator or AI assistant) should ever be given them.

What *is* stored is `data/telegram.session`. Treat that file as a credential:
anyone who copies it is logged into your account. It is in `.gitignore`, and
the systemd unit restricts the process to its own directory.

If the session is revoked (Telegram → Settings → Devices), just run `login`
again.

---

## LINE setup

1. Create a **Messaging API** channel in the LINE Developers console.
2. Issue a long-lived channel access token → `LINE_CHANNEL_ACCESS_TOKEN`.
3. Invite the channel's bot into your LINE group.
4. Get the group id (`Cxxxxxxxx…`) → `LINE_TARGET_ID`.
   See [docs/OPERATIONS.md](docs/OPERATIONS.md#finding-the-line-group-id).
5. `python -m app.cli check` confirms the token works.

LINE Notify is not used: it was discontinued, and the Messaging API is what
allows pushing into a group.

---

## Behaviour, rule by rule

**Everything is forwarded.** A signal, a greeting, a photo caption — all of it
reaches LINE. Media without a caption is forwarded as `[photo]`, `[video]`,
`[file]` or `[voice message]` so the group's timeline stays complete.

**Edits become new LINE messages.** Telegram edits are not deleted or rewritten
in LINE, because LINE has no equivalent operation and members would lose the
history. Each edit is pushed as a **new** message prefixed with `EDITED`:

```
Telegram v1: Sell now                LINE #1: Sell now
Telegram v2: SELL GOLD 3340          LINE #2: EDITED
             SL 3330                          (blank line)
             TP1 3350                         SELL GOLD 3340
                                              SL 3330
                                              TP1 3350
```

Four edits produce four LINE messages. No version is ever removed from the
database.

**Nothing is sent twice.** A message version is identified by
`chat_id + message_id + version + content_hash`. On restart, Telegram replays
missed updates (`catch_up=True`); anything already stored is dropped. Each push
also carries a deterministic `X-Line-Retry-Key`, so a crash between "LINE
accepted it" and "we recorded it" cannot double-post.

**Identical text in two different messages is two messages.** The hash includes
the message id, so "Sell now" posted twice is delivered twice.

**Incomplete signals are kept.** "Sell now" is stored as a signal with a
direction and nothing else. When the message is later edited into a full setup,
the *same* signal row is filled in — no duplicate, and every parse is
snapshotted in `signal_versions`.

**Trade-management messages do not create signals.** "close half", "TP1 hit",
"move SL to BE" are forwarded to LINE but are not counted as new trades.

---

## The signal parser

Handles multi-line and single-line layouts, `SL` / `S/L` / `STOP LOSS`,
`TP1` / `T/P 1` / `TAKE PROFIT 1`, unlabelled ladders (`TP 3350 3360 3370`),
entry zones (`3340-3342` → midpoint), thousands separators, emoji decoration,
and Thai direction words.

```
BUY GOLD 3340          SELL GOLD 3340 SL 3350 TP1 3330 TP2 3320
SL 3330
TP1 3350               🔥 BUY XAUUSD @3,340.00
TP2 3360               🛑 S/L 3330  ✅ T/P1 3350  ✅ T/P2 3360
```

Levels that contradict the direction (a BUY with its stop above entry) are
**flagged, not silently corrected** — the note appears on the signal and its
confidence drops.

Adding a new layout means adding one class in `app/signals/parser.py`:

```python
@register(priority=20)
class MyGroupFormat:
    """One-line description shown on the Methodology page."""
    name = "my_group_format"

    def parse(self, text: str) -> ParsedSignal | None:
        ...
```

Vocabulary (symbol aliases, direction words, label spellings) lives in
`app/signals/patterns.py`, so most changes are a one-line edit there.

---

## Result engine

| Situation                                   | Outcome                                         |
|---------------------------------------------|-------------------------------------------------|
| Price never touches entry within 12 h       | `CANCELLED` — not counted as a win or a loss    |
| TP1 then TP2 reached                        | `TP2_HIT`, `WIN`, +(TP2 − entry) points         |
| SL reached first                            | `SL_HIT`, `LOSS`, −(entry − SL) points          |
| SL reached *after* TP1                      | Booked at TP1 (`RESULT_MODE=BEST_TP`)           |
| TP and SL inside the same candle            | See below                                       |
| Still open after 72 h                       | `CLOSED` at the last price, marked to market    |

**Same-candle TP and SL** (brief section 19): the outcome is never guessed at
random. The engine first re-checks the conflicting candle on a finer timeframe
to establish the real order. If that is impossible, the configured
`AMBIGUITY_RULE` applies:

- `SL_FIRST` *(default)* — assume the worse outcome
- `TP_FIRST` — assume the better outcome
- `AMBIGUOUS` — record as `AMBIGUOUS`, excluded from the win rate but shown

The entry-fill candle itself is not used for TP/SL, because it cannot be known
whether the level printed before or after the entry was taken.

### Price providers

`PRICE_DATA_PROVIDER` selects the source, and each signal records which
provider judged it (`signals.price_source`).

| Value        | Behaviour                                                        |
|--------------|------------------------------------------------------------------|
| `none`       | MVP default — signals stay `PENDING_RESULT`, nothing is invented  |
| `csv`        | `PRICE_CSV_PATH/XAUUSD_1m.csv` with `timestamp,open,high,low,close` |
| `twelvedata` | Twelve Data API, needs `PRICE_API_KEY`                            |

Adding a provider is one class with a `get_candles()` method plus
`@register_provider("name")` in `app/prices/providers.py`. Candles are cached
in `price_candles`, so a result can always be re-checked against the same data.

When a provider is configured later, every signal recorded until then is
evaluated retroactively — the history is not lost.

---

## Dashboard

`/dashboard` — public, read-only, mobile-first. No write route exists for
members: the public API only answers `GET`.

- **Overview** — total signals, win rate, total P/L, wins, losses, profit
  factor, max drawdown, plus average win/loss, expectancy and streaks
- **Signals** — filterable list; click through for full detail
- **Signal detail** — levels, timings, and the complete Telegram edit history
  with how each version was parsed
- **Daily / Weekly / Monthly** — signals, wins, losses, win rate and P/L per
  period, bucketed in Asia/Bangkok
- **Analytics** — by direction, hour of day, weekday, symbol; how far trades
  ran; distribution of results; equity curve
- **Methodology** — every rule above, published verbatim from the live
  configuration

`/admin` — operators only, `X-Admin-Key` (`ADMIN_API_KEY`). System status, the
LINE delivery queue with requeue, re-parse, force re-evaluation, and manual
result correction. A manual result freezes the signal and is labelled as
manually set on the public dashboard.

### Why no money figures

Lot size, contract size, spread, commission, swap, slippage and account
currency are unknown to this system. Converting a points result into "+$500"
would be a guess presented as a fact, so the dashboard shows `+500 Points`
everywhere. Open, ambiguous and cancelled signals are counted and displayed
separately rather than dropped, so the headline win rate cannot be inflated by
hiding them.

---

## Configuration

Every setting, with defaults and comments, is in [`.env.example`](.env.example).
The ones worth thinking about:

| Setting | Default | Notes |
|---|---|---|
| `DATABASE_URL` | SQLite | PostgreSQL for production |
| `AMBIGUITY_RULE` | `SL_FIRST` | How same-candle TP/SL is resolved |
| `RESULT_MODE` | `BEST_TP` | SL after a TP books at that TP |
| `ENTRY_FILL_WINDOW_HOURS` | 12 | After this, an unfilled signal is cancelled |
| `SIGNAL_EXPIRY_HOURS` | 72 | After this, an open trade is marked to market |
| `POINT_SIZE` | 1.0 | 1 point of price movement |
| `ADMIN_API_KEY` | *(empty)* | Empty disables `/admin` entirely |

---

## Operations

```bash
python -m app.cli check       # configuration and connectivity
python -m app.cli chats       # list groups and their ids
python -m app.cli evaluate    # run the result engine once
python -m app.cli drain       # flush the LINE queue once
python -m app.cli stats       # print the overview as JSON
python -m app.main --only api # run just the web tier
```

Deployment files are in [`deploy/`](deploy/): a systemd unit, a Dockerfile, a
`docker-compose.yml` with PostgreSQL, and an nginx sample that keeps `/admin`
behind an IP allow-list. The runbook — backups, log locations, common failures
— is in [docs/OPERATIONS.md](docs/OPERATIONS.md).

---

## Development

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The suite covers the parser against the brief's examples, duplicate and edit
handling, LINE delivery and retries, the result engine (including the
same-candle rules), the statistics, and the API.

To look at the dashboard with plausible data before going live:

```bash
DATABASE_URL="sqlite+aiosqlite:///./data/demo/demo.db" \
PRICE_CSV_PATH=./data/demo/prices PRICE_DATA_PROVIDER=csv LINE_ENABLED=false \
  .venv/bin/python scripts/seed_demo.py --days 30 --reset
```

It generates synthetic candles, pushes synthetic messages (some of them
edited) through the real ingestion path, and runs the real result engine.

### Layout

```
app/
  config.py          settings (env-driven)
  main.py            supervisor for the four components
  cli.py             operator commands (login, chats, check, evaluate, …)
  db/models.py       telegram_messages, signals, signal_versions, price_candles
  telegram/          Telethon listener
  processor/         duplicate detection, versioning, LINE rendering
  line/              Messaging API client + outbox worker
  signals/           parser (patterns + strategies) and persistence
  prices/            provider interface, providers, candle cache
  engine/            result engine and statistics engine
  api/               FastAPI routes (public + admin)
  web/               dashboard and admin front-end (no build step)
tests/               pytest suite
scripts/seed_demo.py demo data generator
deploy/              systemd, Docker, nginx
```

## Security notes

- `.env` and `*.session` are git-ignored; keep `.env` at `chmod 600`.
- The public API is `GET`-only; every mutating route requires `ADMIN_API_KEY`.
- The admin key is held in `sessionStorage` in the browser, never in a URL.
- Credentials are never logged; database URLs are stripped before logging.
- Put `/admin` behind an IP allow-list or VPN as well as the API key.
