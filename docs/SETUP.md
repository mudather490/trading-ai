# Setup Runbook

Everything you must do, in order. Steps marked **← YOU** need your accounts.
Everything else I run.

**Time to first live price on your phone: about 3 days**, most of it waiting
for cloud account provisioning.

---

## Step 1 — Supabase project ← YOU

1. <https://supabase.com/dashboard> → **New project**.
2. Name it `trading-command-center`. Pick the region closest to the GCP VM
   you will create (both should be in the same region for latency).
3. Wait for provisioning (~2 minutes).
4. **Settings → Database → Connection string → URI.** Copy it. It looks like:
   ```
   postgresql://postgres.PROJECT_REF:PASSWORD@db.PROJECT_REF.supabase.co:5432/postgres
   ```
5. **Settings → API → Project URL** and **`anon` public key**. Copy both.
6. **Settings → API → `service_role` key.** Copy it. It bypasses RLS.
   Treat it like a password: it goes in the worker's environment and NOWHERE
   else.
7. **Settings → Auth → Providers.** Email is on by default. Leave it.
8. **Settings → Storage → Buckets → Create.** Name it `backups`.

> **Important:** the free plan pauses a project after 1 week of inactivity.
> The live worker's daily heartbeat prevents that. If you ever stop the
> worker for more than a week, the project may pause and need a manual
> resume.

---

## Step 2 — Apply the migrations ← ME

I run this against your project:

```bash
# With the Supabase CLI installed
supabase link --project-ref YOUR_PROJECT_REF
supabase db push
```

Or, if you prefer not to install the CLI, open the SQL editor in the
dashboard and run each file in `supabase/migrations/` in numeric order.
There are fifteen, and they are idempotent.

**Then verify it worked:**

```bash
cd E:\trading-ai
npm run db:check     # applies the migrations to a local PGlite and checks 13 invariants
npm run db:test      # 69 RLS tests: anon / trusted / owner isolation
```

You should also create your user account now, because the RLS tests and the
app both need one:

1. Go to your Supabase URL → **Authentication → Users → Add user**.
2. Create yourself with email + password, and **confirm the email**.
3. Give yourself the owner role. In the SQL editor:

```sql
insert into public.user_profiles (user_id, role, display_name, capital_usd)
select id, 'owner', 'Owner', 100000
from auth.users
where email = 'you@example.com'
on conflict (user_id) do update
  set role = 'owner', capital_usd = 100000;
```

Set `capital_usd` to whatever you actually intend to trade. It is the number
the sizing function divides.

**For each trusted person**, create their auth user the same way, then:

```sql
insert into public.user_profiles (user_id, role, display_name, capital_usd)
select id, 'trusted', 'Friend', 10000
from auth.users
where email = 'friend@example.com'
on conflict (user_id) do update set role = 'trusted';
```

---

## Step 3 — GCP free VM ← YOU + ME

1. <https://console.cloud.google.com> → create a project, then enable the
   **Compute Engine API**.
2. **Create instance**:
   - Name: `trading-worker`
   - Region: the same region as your Supabase project
   - Machine type: **e2-micro**
   - Boot disk: **Ubuntu 24.04**, 30 GB standard persistent disk
   - Firewall: allow **SSH from your own IP only**
3. **Point to it** (I need the external IP):
   ```
   gcloud compute instances describe trading-worker --zone=YOUR_ZONE \
     --format='value(networkInterfaces[0].accessConfigs[0].natIP)'
   ```

**Cost control:** the e2-micro is free in `us-east1`, `us-central1` and
`us-west1`. In other regions it is **not** free and will start billing. Also
confirm the free-tier checkbox on your billing account.

Then I install the worker:

```bash
sudo apt update && sudo apt install -y python3-venv postgresql-client
sudo useradd -m -s /bin/bash trader
sudo mkdir -p /opt/trading-ai && sudo chown trader:trader /opt/trading-ai
# I copy the worker code up, create the venv, install deps, write .env
sudo cp /opt/trading-ai/deploy/trading-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-worker
journalctl -u trading-worker -f
```

You should see the Binance stream connect within seconds.

---

## Step 4 — Venue credentials ← YOU

All optional in v1 — the worker starts with crypto only and the others are
added as you supply keys.

| Venue | Where to get it | What you get |
|---|---|---|
| **Alpaca** | <https://app.alpaca.markets> → sign up → **Paper Trading** → API keys | Free. IEX-only real-time. 30 WS symbols. We use 6. |
| **OANDA** | <https://developer.oanda.com> → create a **fxTrade Practice** account → REST API key | Free practice. Indicative rates. We use 3. |
| **Finnhub** | <https://finnhub.io> → register | Free key, 60 calls/min. News, earnings dates and the **economic calendar**. |
| **Groq** | <https://console.groq.com> → API keys | Free tier, 30 RPM / 1,000/day. Chat and narration. |
| **Gemini** | <https://aistudio.google.com> → API key | Free tier. Bulk news extraction. |
| **Telegram** | Message `@BotFather` → `/newbot` → copy the token | Free. The bot: signals on demand and alerts. |

> **Do NOT put an Alpaca or OANDA key in Vercel.** Only the worker holds
> venue credentials. That separation is the custody boundary.

The Telegram bot needs THREE values, and they live in TWO places:

| Value | Where it goes | Why |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | the worker `.env` **and** the Vercel env | the worker sends alerts; the webhook answers commands |
| `TELEGRAM_WEBHOOK_SECRET` | the worker `.env` **and** the Vercel env | a random string you invent; Telegram sends it back on every update |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Vercel env (optional) | a comma-separated list of chat ids allowed to use the bot. Leave it EMPTY until you have linked your chat, then lock it down. |

Generate the secret once and paste the SAME value in both places:

```bash
openssl rand -hex 32
```

The chat id is **not** configured anywhere by hand. You link the chat from
the app (Step 6), which mints a single-use code. A chat id typed into an env
file is a chat id that eventually ends up in the wrong place.

When the keys are in the worker's `.env`, switch the venues on:

```env
ENABLED_VENUES=binance,alpaca,oanda
```

A venue whose keys are missing is **skipped with a warning**, never fatal:
the worker starts with whatever it can reach. So you can add Alpaca today and
OANDA next week without touching anything else.

What each venue contributes, and what the app will say about it:

| Venue | Symbols | What the UI must show |
|---|---|---|
| Binance | BTCUSDT, ETHUSDT, SOLUSDT | *official* — the full market |
| Alpaca | QQQ, SPY, GLD, AAPL, NVDA, TLT | *partial* — IEX only, a fraction of consolidated volume |
| OANDA | EURUSD, GBPUSD, XAUUSD | *indicative* — practice rates, not an executable feed |

---

## Step 5 — Vercel deployment ← YOU + ME

1. <https://vercel.com> → import the repository.
2. **Root directory:** `apps/web`
3. **Environment variables**:

   | Variable | Value | Needed for |
   |---|---|---|
   | `NEXT_PUBLIC_SUPABASE_URL` | your project URL | everything |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | your anon key | everything |
   | `NEXT_PUBLIC_SITE_URL` | your Vercel domain | the magic-link redirect |
   | `SUPABASE_SERVICE_ROLE_KEY` | your service-role key | the daily cron heartbeat and the Telegram webhook — **and nothing else** |
   | `CRON_SECRET` | any long random string | authenticates the cron route |
   | `TELEGRAM_BOT_TOKEN` | from @BotFather | the bot |
   | `TELEGRAM_WEBHOOK_SECRET` | the same value as in the worker | authenticates Telegram updates |
   | `TELEGRAM_ALLOWED_CHAT_IDS` | optional, empty at first | restricts who may use the bot |

   The service-role key IS present on the web tier, and that is a deliberate
   exception with two named uses: `/api/cron/heartbeat` writes a system row
   nobody should be able to forge, and `/api/telegram/webhook` handles a
   request that does not carry a Supabase session (a Telegram chat is not a
   session). Both are in server-only code paths; the key is never exposed to
   the browser, and it is never used to read a user's data on their behalf.
   User-scoped reads always go through the user's own session, so RLS still
   decides.

   **Do not add** `ALPACA_KEY_ID`, `ALPACA_SECRET_KEY`, `OANDA_TOKEN` or
   `OANDA_ACCOUNT_ID`. Those are venue credentials and they belong to the
   worker alone.

4. Deploy. Then **Settings → Environment → Cron Jobs**, add one job:
   ```
   Path:     /api/cron/heartbeat
   Schedule: 0 8 * * *
   ```
   This writes a row once a day so the free Supabase project never goes idle.

5. Add your custom domain if you want one (~`$10/year`).

---

## Step 5b — Point Telegram at the webhook ← ME (one command)

Once the app is deployed and the Telegram env vars are set:

```bash
cd E:\trading-ai
npm run telegram:setup -- set https://YOUR-APP.vercel.app/api/telegram/webhook
npm run telegram:setup -- info
```

The first command registers the webhook URL together with the secret token;
the second prints what Telegram has stored. If `url` is empty in that output,
the registration did not take.

`getUpdates` and `setWebhook` are mutually exclusive. If you are testing
locally instead, delete the webhook first (`npm run telegram:setup -- delete`)
and use the local relay (Step 7).

---

## Step 6 — Link your Telegram chat ← YOU

The bot does not know who you are until you link it, and the link is a
two-sided handshake:

1. Open the app → **Settings → Telegram → Generate link code**.
2. The app shows a 10-character code. It works once and expires in 10
   minutes.
3. Send it to your bot: `/link ABC1234567`.
4. The bot answers "Linked". Open **Settings → Telegram** again: it now says
   *linked*.

After that, from your phone:

```
/signal BTCUSDT 1h      the current ticket
/symbols                browse the universe
/status                 engine and feed health
/unlink                 stop the messages
```

To lock the bot down once you know your chat id, put it in the Vercel
environment as `TELEGRAM_ALLOWED_CHAT_IDS` and redeploy. Any chat that is
not on that list is ignored, silently.

---

## Step 7 — First run ← ME

```bash
cd E:\trading-ai
npm test          # migrations + RLS + worker + research
```

Then:

1. Open the Vercel URL → sign in with the magic link.
2. You should see the Command Center with 12 instruments and the engine
   badge.
3. **If the badge says OFFLINE**, the worker is not running or cannot reach
   Supabase. Check `journalctl -u trading-worker`.

### The Phase 0 acceptance test

| Check | Expected |
|---|---|
| Engine badge | **LIVE**, green, updating every 10s |
| BTCUSDT in the watchlist | price changing every few seconds |
| Click BTCUSDT | Decision Center loads |
| The ticket panel | shows **WAIT** or **NO_MATCH** — *not* a ticket, because no strategy is promoted yet |
| Kill the worker (`sudo systemctl stop trading-worker`) | the badge turns **STALE** within ~2 minutes and the Decision Center says **ENGINE_UNAVAILABLE** |

That last row is the most important one: **the app must refuse to advise
when the engine is down.** If it shows a ticket anyway, something is wrong.

### Step 7b — Promote a strategy ← ME, with you pressing the button

The app starts with **no champion**, so every instrument answers
`no_champion`. That is the honest state, not a bug: a signal with no
verified edge is not a signal.

To give it one:

```bash
cd services/research-worker
python -m research.promote --symbol SOLUSDT --timeframe 1d --template donchian_breakout
```

The runner publishes a **candidate**. It never promotes it. Then open the
app → **Strategies** → **Promote**, and the gate answers: either the
candidate is promoted, or every failed rule is listed with its value, its
threshold and its reason.

The rules it applies, and what each one means:

| Rule | Meaning |
|---|---|
| `min_trades` | Enough trades to judge. Thirty on a daily bar. |
| `min_dsr` | The edge survives the number of things you tried. The deflated Sharpe. |
| `max_pbo` | The result is not an isolated peak in parameter space. |
| `min_sharpe` / `min_calmar` | The risk-adjusted return is worth something. |
| `max_drawdown` | The losing path is survivable. |
| `neighbour_stability` | The parameters one step away also work. |
| `max_holdout_reads` | The holdout was read once, by this candidate. |
| `holdout_contaminated` | **The tail of this dataset had already been read by an earlier run.** A holdout read twice is a validation set, and the gate refuses it. |

> **A dataset's holdout may be read once.** If you re-run the research on
> the same instrument and timeframe, the second candidate is marked
> contaminated and the gate will refuse it, with the reason shown. That is
> the point: picking the best of three templates by reading the same tail
> is how a research pipeline convinces itself of an edge it does not have.
> Use a different instrument, or a different timeframe.

### The promotion acceptance test

| Check | Expected |
|---|---|
| Strategies page | the promoted version shows as **champion**, with its DSR, PBO and drawdown |
| The worker log | `champion registry: crypto/1d` within a minute of the promotion |
| `/api/ticket?instrument=SOLUSDT&timeframe=1d` | a ticket, or **WAIT** with `reason_code: no_signal` — both are correct answers |
| `/api/ticket?instrument=SOLUSDT&timeframe=1h` | **NO_TRADE**: there is no champion on 1h, and the app says so |
| Strategies → Promote on a weak candidate | a rejection listing every failed rule |

### The provider contract check

The worker checks every configured provider's **response shape** every six
hours and records it. If a provider changes what it returns, the feed
banner says **contract changed** instead of quietly showing stale prices.

| Check | Expected |
|---|---|
| The worker's log | `contract binance GET /api/v3/ticker/price ok=True latency=…` |
| `provider_contracts` in the database | one row per check, with the key set and its hash |
| The feed banner | unchanged while the shape is the same |

This is a hash comparison, not a model reading changelogs. It cannot
hallucinate a change, and it cannot miss one.

### The Phase 1 acceptance test (all three venues)

Run this after the worker has the Alpaca and OANDA keys.

| Check | Expected |
|---|---|
| Worker log at startup | one line per enabled venue, e.g. `alpaca stream opened for QQQ, SPY, … (IEX, partial)` |
| Watchlist | 12 instruments, each with a price |
| Any US symbol (QQQ) | price moves during US market hours; the row is labelled **partial (IEX)** |
| Any FX symbol (EURUSD) | price moves during the week; the row is labelled **indicative (practice)** |
| BTCUSDT | labelled **official** |
| `journalctl -u trading-worker \| grep reconnect` | a handful of reconnects is normal; a tight loop is not |
| Kill the worker | every symbol goes **STALE** — not just the crypto ones |

With `FINNHUB_API_KEY` set, the worker log also shows
`calendar refreshed: N events -> M catalysts` every six hours. A USD
release must reach QQQ, BTCUSDT, EURUSD and GBPUSD; a euro release must
reach EURUSD only. Click a symbol before a high-impact release and the
Decision Center must show a warning naming the event and its time.

If a symbol never ticks, check the worker log for `alpaca is enabled but
ALPACA_KEY_ID/ALPACA_SECRET_KEY are not set; skipping the venue`. That
message means the venue was requested and had no credentials.

### The dashboard acceptance test

| Check | Expected |
|---|---|
| **Markets** | 12 rows grouped by asset class, each with a last price and a quality badge |
| **Markets → BTCUSDT** | candlestick chart of closed bars, EMA/RSI/ATR panel, and the engine's verdict below it |
| **Strategies** | "No promoted strategy yet" and the gate thresholds, until something passes the gate |
| **Journal** | empty at first, then one entry per ticket you journal |
| **Settings** | your risk limits, the per-venue feed status, and the Telegram box |
| Change `Risk per trade` to 90 % and save | the database clamps it to 2 % and the page says so |
| **Journal this ticket** on a live ticket | asks "I took it" or "I passed", then the entry appears in **Journal** |

### The Telegram acceptance test

| Check | Expected |
|---|---|
| `/start` before linking | the bot explains how to link |
| `/link <code>` | "Linked" |
| `/signal BTCUSDT 1h` | the same ticket the dashboard shows, with the same entry, stop, target and data-quality label |
| `/signal AAPL 1h` with no Alpaca key | a refusal that says the feed is **not connected** — never "stale" |
| `/status` | the engine state and every venue's feed state |
| `/link <same code>` again | refused: the code is single use |
| Replay the same `update_id` | ignored (the dedupe ledger) |
| A POST to `/api/telegram/webhook` without the secret header | **403** |

---

## Everyday operation

| Task | Where |
|---|---|
| See what the bot is doing | The Command Center |
| Get signals and alerts on your phone | the Telegram bot (`/signal BTCUSDT 1h`) |
| Promote a strategy | Strategies → Promote, after `python -m research.promote` |
| See how a candidate was judged | the `performance` row's `notes`, and the `research_runs` ledger |
| Check the providers still answer the same shape | the feed banner, or `provider_contracts` |
| Backups | `python tools/backup.py` — daily, via cron on the VM |
| Check the engine | `journalctl -u trading-worker -f` |
| Check quotas | The heartbeat's `meta.ai_quota` field |
| Rebuild the worker | `git pull` on the VM, then `systemctl restart trading-worker` |

---

## Running the research locally

The research worker runs on your own machine, against the same database:

```bash
cd services/research-worker
export SUPABASE_URL="http://127.0.0.1:54321"          # the local emulator
export SUPABASE_SERVICE_ROLE_KEY="local-service-role-key"

python -m research.promote --symbol SOLUSDT --timeframe 1d --template donchian_breakout
python -m research.crosscheck --symbols AAPL,SPY,QQQ      # needs TWELVE_DATA_API_KEY
```

The runner holds the service-role key because a candidate belongs to nobody
until a human promotes it. Its write set is narrow and **enforced in code**:
`research/db.py` refuses `champions`, `gate_policy`, `tickets`,
`user_profiles`, `audit_log`, `alerts`, `jobs`, `worker_heartbeat` and
`signals`. The runner publishes candidates; only you promote them.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Engine badge OFFLINE | Worker not running | `systemctl status trading-worker` |
| Engine badge STALE | Worker running but cannot reach Supabase | Check the URL and service-role key in `.env` |
| No prices in the watchlist | Binance WS blocked, or the symbol is not in the universe | Check the worker log; the seed loads 12 symbols |
| Login redirects in a loop | `NEXT_PUBLIC_SUPABASE_URL` wrong in Vercel | Redeploy |
| Command Center shows "Not configured" | Env vars missing at build time | Vercel → Settings → Environment → redeploy |
| `npm run db:check` fails | A migration has a syntax error | The error names the file and line |
| Supabase project paused | 1 week of inactivity | Resume it in the dashboard; then re-enable the daily cron |
| The bot does not answer | the webhook is not registered, or the secret differs | `npm run telegram:setup -- info`; compare `TELEGRAM_WEBHOOK_SECRET` in both tiers |
| The bot says "not linked" | the code expired, or it was already used | generate a new code in Settings |
| The bot is silent about a signal | no champion, so there is no signal | `/strategies` in the app explains why |
