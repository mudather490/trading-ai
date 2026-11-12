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
| **News and catalysts** | A two-stage pipeline: deterministic entity and event typing (SEC 8-K item codes), then local FinBERT sentiment, then a model for the residue. News is a **risk flag, never a trigger**. |
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
| `npm run db:check` | 6 migrations apply to real Postgres; 12 structural invariants hold |
| `npm run db:test` | 27 RLS tests: anon sees nothing, a trusted user cannot read the owner's journal, only the owner can promote |
| `npm run worker:test` | 53 tests: bars close exactly once, signal keys are stable, the worker refuses forbidden writes, the AI sidecar grounds its numbers |
| `npm run research:test` | The engine reproduces the verified S18 result and agrees with an independent implementation |

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
tools/                        apply_migrations · backup · lint_sql
docs/                         MASTER_SPEC · ARCHITECTURE · SETUP · DASHBOARD_SPEC
research/                     the original source analysis
```

---

## Cost

**$0–10/month.** Vercel Hobby, Supabase Free, a free GCP e2-micro, and
public market data. The AI runs on free tiers with a local FinBERT for the
bulk of the work; the free tiers only ever receive **public market news**.

---

## Status

**Phase 0 complete.** Phases 1–5 are specified in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) §7.

| Phase | Scope | Status |
|---|---|---|
| 0 | Schema, RLS, gate functions, worker, web app, AI sidecar, ops | ✅ |
| 1 | Alpaca + OANDA connectors, 12 symbols, retention, local FinBERT | ⬜ |
| 2 | Signal engine, Decision Center, odds band, journal, alerts, chat | ⬜ |
| 3 | Backtester, promotion gate, AI Feature Gate, AI Ranking Score | ⬜ |
| 4 | News pipeline, event studies, catalyst cards, positioning, PWA | ⬜ |
| 5 | Paper execution (only if you choose it later) | ⬜ |
