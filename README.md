# Trading Command Center

A live trading dashboard, strategy lab and news engine. **Vercel** for the
interface, **Supabase** for the state, and a small **always-on worker** for the
engine that touches the market.

The app advises. **It does not place orders.** The primary button is
"Journal this ticket" — the human decides.

> Not financial advice. Trading carries loss risk. No figure in this
> repository is a return promise.

---

## What it does

| | |
|---|---|
| **Live prices** | Crypto (Binance, full market), US equities (Alpaca IEX — *partial*), forex and metals (OANDA practice — *indicative*). Every row carries its data quality so a partial feed is never mistaken for the whole market. |
| **Decision Center** | A complete trade ticket — entry, stop, targets, size, R:R, the measured history behind it, and an honest probability band. Or it says **WAIT / NO_MATCH / STALE / ENGINE_UNAVAILABLE**, and shows why. |
| **Strategy lab** | Create, compare, version and promote strategies. The promotion gate is a **versioned predicate in SQL**: no conversation can promote a strategy. |
| **Markets** | Every instrument, with a real candlestick chart, the indicators the engine actually uses, and the closed-bar history the decision was made on. |
| **Telegram bot** | Ask the bot for a signal on any instrument and timeframe **from inside the chat**. It answers with the same ticket the app shows, and it can never see anything else. |
| **News and catalysts** | A two-stage pipeline: deterministic entity and event typing (SEC 8-K item codes), then local FinBERT sentiment, then a model for the residue. News is a **risk flag, never a trigger**. An **economic calendar** puts releases on the clock, so "NFP in 25 minutes" reaches the ticket as a warning. |
| **Buyers vs sellers** | Crypto order flow, funding and open interest; CFTC positioning for FX; delayed short interest and insider filings for equities — each labelled with its freshness. |
| **Alerts** | Rules decide what fires. The model writes the wording. |

---

## The one idea

Seven trading courses and 226 videos were analysed and their testable claims
independently reproduced. The result is documented in
[`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md).

The finding that shaped this system:

* A Turtle 55/20 breakout claimed **3.29% max drawdown**. On a mark-to-market
  equity curve it was **−18.91%**. The number was a measurement artefact.
* A set of "market calls" scored **72%** against an **88%** naive baseline.
  Being worse than doing nothing clever is a real outcome.
* A support/resistance rule scored Sharpe **0.68–0.74** in-sample and
  **−0.04 to 0.26** out of sample.
* One rule — hold QQQ above its 200-day average, otherwise hold gold —
  **reproduced**: +28.41% CAGR, Sharpe 1.77, −18.10% max drawdown.

So the architecture puts **AI in the sidecar, never in the chassis**. The
model proposes, explains, extracts and ranks. Deterministic code measures,
sizes, decides and refuses. And the AI is *scored*: when its ranking or its
news extraction stops beating the deterministic baseline, it is automatically
demoted to explanation-only.

---

## Architecture

Four components. One source of truth. One custody boundary.

```
Vercel Hobby          the face: Next.js 16, Auth, Realtime, API routes
Supabase Free          the memory: Postgres + RLS, Realtime, Storage
GCP e2-micro           the engine: always-on, holds the venue credentials
Your PC                the research engine: backtests, sweeps, the gate
```

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### The custody boundary

| | Vercel | Worker |
|---|---|---|
| Supabase anon key | ✅ | ✅ |
| Supabase service role key | ❌ | ✅ |
| Venue credentials | ❌ | ✅ |
| Can place an order | ❌ *(no code exists)* | ❌ *(no code exists)* |

The web tier cannot trade because it has nothing to trade with.

---

## Getting started

Full step-by-step instructions, including the parts that need your accounts:
**[`docs/SETUP.md`](docs/SETUP.md)**.

```bash
npm install
npm test          # migrations, RLS, worker, research
```

`npm test` runs everything:

| Suite | Proves |
|---|---|
| `npm run ratchet` | 6 architecture rules hold: the web tier never imports the worker, nothing can place an order, the AI sidecar never names a decision table, the service-role key stays in its allowed paths, no AGPL source is in first-party code, vendored code is attributed |
| `npm run db:check` | 15 migrations apply to real Postgres; 13 structural invariants hold |
| `npm run db:test` | 69 RLS tests: anon sees nothing, a trusted user cannot read the owner's journal, only the owner can promote, a candidate whose holdout was already read is refused |
| `npm run worker:test` | 137 tests: bars close exactly once, signal keys are stable, the worker refuses forbidden writes, the AI sidecar grounds its numbers, all three venues parse and label their data quality, the budget stops a task, untrusted text cannot give orders, duplicate headlines collapse into one cluster, the economic calendar reaches the right instruments and the right clock, the provider contract check detects a changed response shape |
| `npm run research:test` | The backtester's arithmetic is pinned to numbers a human can check, the deflated Sharpe falls as the search grows, the PBO separates skill from luck, and the engine reproduces the verified S18 result |

---

## The research path, end to end

```bash
# 1. The runner evaluates a grid of candidates and publishes ONE of them.
cd services/research-worker
python -m research.promote --symbol SOLUSDT --timeframe 1d --template donchian_breakout

# 2. The owner asks the gate. In the app: Strategies → Promote.
#    Or with the API:
#    POST /api/strategies/promote  {"version_id": "..."}
```

The runner **publishes candidates and never promotes them**. It applies the
gate's own hard filters before it publishes anything, it records every run
in `research_runs` (dry runs included), and the deflated Sharpe is charged
for the **whole history** of trials — not just the last grid. A dataset's
holdout may be read **once**: a candidate whose tail was read by an earlier
run is marked contaminated and the gate refuses it, with the reason shown.

Two things are deliberately refused here:

* a **holdout that was already read** — it is a validation set, not a holdout;
* a candidate the gate would refuse anyway — publishing it would add a
  version and a trial to the record for nothing.

Current state: one champion, `donchian_breakout · SOLUSDT · 1d`
(Sharpe 1.40, DSR 0.99, PBO 0.29, max drawdown −29.2%, 31 trades, 52 trials).

---

## Layout

```
apps/web/                     Next.js 16 — the Command Center
services/live-worker/         the live engine (runs on the GCP VM)
services/research-worker/     the research engine (runs on your PC)
packages/strategy-core/       shared by both engines
packages/vendor/              third-party code, with ATTRIBUTION.md
supabase/migrations/          the schema
supabase/tests/               the custody boundary
tools/                        apply_migrations · backup · lint_sql · arch_ratchet
docs/                         MASTER_SPEC · ARCHITECTURE · SETUP · DASHBOARD_SPEC
                              PROVENANCE (what we took from where, and why)
research/                     the original source analysis
```

---

## Running it locally

```bash
npm run dev:local     # the emulator, the worker and the web app, one command
```

The local stack is **offline and real**: real Postgres (PGlite), real
migrations, real RLS, real roles, a GoTrue stub for sign-in. The database
is on disk (`.local/pgdata`), so a restart does not throw the history away,
and the worker **seeds the history it is missing** on the first start.

Sign in at `http://localhost:3000/auth/callback?code=demo`.

| Command | Does |
|---|---|
| `npm run dev:local` | The whole stack: emulator, live worker, web app |
| `npm run telegram:local` | A local relay that feeds the bot, without a public URL |
| `npm run telegram:setup -- set <url>` | Registers the webhook with Telegram |
| `npm test` | Everything above, in one command |

---

## Cost

**$0–10/month.** Vercel Hobby, Supabase Free, a free GCP e2-micro, and
public market data. The AI runs on free tiers with a local FinBERT for the
bulk of the work; the free tiers only ever receive **public market news**.

Twelve Data's free plan may be **fetched and used internally but not
displayed**, so it is a research cross-check only: it compares the app's
daily bars against an independent series and prints the difference. It
never appears on a chart, a watchlist or a ticket.

---

## Status

**Phases 0–3 substantially built.** Phases 1–5 are specified in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §7.

| Phase | Scope | Status |
|---|---|---|
| 0 | Schema, RLS, gate functions, worker, web app, AI sidecar, ops | ✅ |
| 1 | Alpaca + OANDA connectors, 12 symbols, retention, local FinBERT, economic calendar | 🟡 connectors for all three venues, backfill and the calendar done; **the Alpaca and OANDA keys are still needed**; local FinBERT to come |
| 2 | Signal engine, Decision Center, odds band, journal, alerts, chat, Telegram bot | ✅ |
| 3 | Backtester, promotion gate, AI Feature Gate, AI Ranking Score | 🟡 backtester, gate and promotion flow done; **one champion promoted**; the news pipeline and the AI ranking score are still to come |
| 4 | News pipeline, event studies, catalyst cards, positioning, PWA | ⬜ |
| 5 | Paper execution (only if you choose it later) | ⬜ |
