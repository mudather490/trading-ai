# Trading Command Center — Architecture v1.1

**Status:** Implemented through Phase 0 + the strategy core. Phases 1–5 remain.
**Version:** 1.1
**Date:** 2026-11-12
**Supersedes:** the deployment topology in `MASTER_SPEC.md` v1.0 (that document
remains the record of the source research and the eight original invariants;
its topology section is superseded by this one).

---

## 0. What changed from v1.0, and why

v1.0 described a **single-machine agent** with capability removal as the
central safety mechanism: the LLM's tool schema had no order-placement tool,
so it could not trade.

That design is sound in principle, but it does not survive contact with the
deployment constraints, and it left the user with no deployed app. Four
findings forced the revision:

| Finding | Consequence |
|---|---|
| **Vercel Hobby runs 1 cron/day/job and 300s functions.** A trading loop cannot live there. | The live engine needs an always-on process. |
| **A stateless serverless tier cannot hold a custody boundary.** Every function inherits every environment variable. | Credentials must live in exactly one place, and that place is not Vercel. |
| **Free AI tiers rotate models and change quotas without notice.** | The AI boundary must be enforced in code, not by convention. |
| **Our own research showed the failure mode of self-deception.** S18 reproduces; Turtle's drawdown was understated 6×; the EA calls lose to a naive baseline by 16pp. | "Improvement" must be measured against a denominator, and the gate must be a predicate. |

**v1.1 keeps v1.0's core insight and implements it harder.** The LLM still
cannot size a position or place an order — but now that is enforced by
*schema absence in the database*, by a *grounded-number validator*, and by
*server-side code the web tier cannot reach*, rather than by a tool list.

### The v1.0 → v1.1 invariant mapping

| v1.0 | v1.1 | Enforcement |
|---|---|---|
| INV-1 capability removal | **I-1** AI writes only to `ai_runs`, `news_features`, `strategy_proposals`, `alerts.ai_summary` | DB grants + `SupabaseWriter._FORBIDDEN_TABLES` + CI test |
| INV-2 separate decisioning/execution | **I-1** + **I-18** | Web tier holds no venue credential; AI code runs only in a sandbox |
| INV-3 no auto-execution | **I-1** | There is no order endpoint in the codebase |
| INV-4 deterministic gate | **I-7**, **I-13**, **I-17** | `promote_strategy()` SQL function, versioned `gate_policy` |
| INV-5 provenance | **I-4**, **I-16** | `source` / `official` / `as_of` on every price row; point-in-time news join |
| INV-6 risk limits in code | **I-2**, **I-6** | `get_ticket()` RPC: freshness gate, news-risk gate, position caps |
| INV-7 no lookahead | **I-16**, **I-4** | Closed-bar only; `published_at ≤ bar_close_time` assertion |
| INV-8 human rules on judgment | **I-8**, **I-22** | `WAIT` / `NO_TRADE` / `STALE` / `ENGINE_UNAVAILABLE` are success states; the primary button is "Journal this ticket" |

---

## 1. Topology

Four components. One source of truth. One custody boundary.

```
┌──────────────────────────────────────────────────────────────┐
│ VERCEL HOBBY — the face (stateless, global)                  │
│  Next.js 16 · Supabase Auth (magic link) · Realtime client  │
│  Command Center · Decision Center · Strategies · News        │
│  /api/ticket → calls the get_ticket() RPC                    │
│  NO venue credentials · NO order endpoint · NO live streams  │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTPS
┌──────────────────────────▼───────────────────────────────────┐
│ SUPABASE FREE — the memory                                   │
│  Postgres + RLS · Auth · Realtime · Storage                  │
│  prices · bars · signals · tickets · news_features          │
│  strategies · versions · performance · champions             │
│  ai_runs · strategy_proposals · holdout_ledger · audit_log   │
└──────────────────────────▲───────────────────────────────────┘
                           │ service role (write-only)
┌──────────────────────────┴───────────────────────────────────┐
│ GCP e2-micro — the LIVE ENGINE (always-on, systemd)          │
│  Binance WS · Alpaca IEX WS · OANDA practice stream          │
│  1s/1m bar builder · indicators · strategy rules             │
│  AI SIDECAR: deterministic entity → local FinBERT → LLM      │
│  heartbeat 10s · Realtime bundle 5s · job consumer           │
│  The ONLY holder of venue credentials.                        │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ YOUR PC — the RESEARCH ENGINE (batch)                        │
│  vectorbt · Optuna · quantstats · local FinBERT              │
│  AI Tier-A mutations · Tier-B sandboxed code generation       │
│  promotion gate · DSR/PBO · AI Feature Gate · AI Ranking    │
│  QuantConnect second opinion · Time Machine odds band         │
└──────────────────────────────────────────────────────────────┘
```

### Why the worker is separate from the web tier

The web tier must be able to scale to zero and be redeployed at any moment.
The worker must not stop for a second, because a stale heartbeat makes the
app refuse to issue tickets — correctly. They also have *different
credentials*, and that separation is the custody boundary:

| | Vercel | Worker |
|---|---|---|
| Supabase anon key | ✅ | ✅ |
| Supabase service role key | ❌ | ✅ |
| Alpaca / OANDA / Binance credentials | ❌ | ✅ |
| Can place an order | ❌ (no code exists) | ❌ (no code exists) |
| Holds a broker write credential | ❌ | ❌ (v1 is ticket-only) |

---

## 2. The invariants

Twenty-four rules. Each is enforced in code and, where it matters, has a test.

### The execution path

| ID | Rule | Enforced by |
|---|---|---|
| **I-1** | The worker never writes to a decision table | `SupabaseWriter._FORBIDDEN_TABLES` — `champions`, `tickets`, `performance`, `strategy_versions`, `holdout_ledger`, `audit_log` raise `ForbiddenWrite` |
| **I-2** | No ticket on stale data | `get_ticket()` checks heartbeat age (< 2 min) and quote age per asset class |
| **I-3** | No raw ticks in Postgres | `latest_quotes` is one row per symbol; bars only. Retention: 1m 14d, 5m 90d, 1h 2y, 1d forever |
| **I-4** | Every price row carries provenance | `source`, `official`, `as_of` on `bars` and `latest_quotes`; `data_quality` on `instruments` |
| **I-5** | No LLM-generated number reaches a ticket | Grounded-number validator; `get_ticket()` is pure SQL |
| **I-6** | News is a warning, never a sizing input | `get_ticket()` reads `catalysts` only to append a warning; news features are legal at 1h/1d, forbidden at 1m. The economic calendar publishes into `catalysts`, so event risk reaches the ticket as a warning through the same path — it never changes a size |
| **I-7** | Promotion is a code predicate | `promote_strategy()`; owner-only; the policy is a versioned `gate_policy` row |
| **I-8** | `WAIT` / `NO_TRADE` / `STALE` / `ENGINE_UNAVAILABLE` are success states | UI + API contract; all four render as a normal card |
| **I-9** | RLS is tested | 27 behaviour tests across anon / trusted / owner / service_role |
| **I-10** | Daily backup, monthly restore rehearsal | `tools/backup.py` |
| **I-11** | AI writes only to the sidecar tables | DB grants; `ai_runs` is owner-only |
| **I-12** | AI output is schema-validated; invalid is dropped and counted | `validate_json()`; never coerced |
| **I-13** | Every AI proposal increments `n_trials` | `strategy_proposals.trial_id`, created at proposal time |
| **I-14** | The news pipeline is itself gated | AI Feature Gate: news features must beat no-news features OOS and cost-adjusted |
| **I-15** | AI's ranking is scored against reality | AI Ranking Score; failure demotes AI to explanation-only, automatically |
| **I-16** | No look-ahead | Point-in-time news join; `published_at ≤ bar_close_time`, else the backtest fails |
| **I-17** | No isolated parameter peaks | Neighbourhood-stability gate: the candidate's neighbours must also pass |
| **I-18** | AI-authored code is sandboxed | AST gate → smoke test → locked container → the normal gate |
| **I-19** | Free tiers get public market news only | `AISidecar` refuses `data_class != 'public'` on a free tier, before the request |
| **I-20** | Every AI path has a deterministic fallback | `NullProvider`; the app keeps pricing, signalling and ticketing |
| **I-21** | Rules fire alerts; AI writes the text | `fire_alert()` takes `rule_id`; `alerts.ai_summary` is advisory only |
| **I-22** | "I don't know" is a first-class answer | Ticket, news panel and chat all carry a confidence/uncertainty state |
| **I-23** | Untrusted text may not give orders | `detect_prompt_injection` + `sanitize_untrusted` + `wrap_untrusted`; the live sidecar has no tool access, so a fenced headline can only ever be data |
| **I-24** | An agent task cannot outspend its budget | `Budget` (tokens, cost, wall clock, calls); the sidecar refuses the call **before** the request leaves the machine |

---

## 3. The decision path

What happens when the user asks "what should I do with BTC?"

```
User opens the app
  │
  ├─ Supabase Realtime pushes a 5s bundle of quotes
  │
  └─ User selects BTC/USDT
       │
       └─ GET /api/ticket?instrument=BTCUSDT&timeframe=1h
            │
            ├─ Supabase (user session) → RLS
            │    resolve the symbol → instrument id
            │
            └─ RPC get_ticket(user, instrument, timeframe)
                 │
                 ├─ 1. ENGINE       heartbeat stale? → ENGINE_UNAVAILABLE
                 ├─ 2. PROFILE      no profile?     → NO_MATCH
                 ├─ 3. UNIVERSE     not in universe? → NO_MATCH
                 ├─ 4. FRESHNESS    quote too old?  → STALE
                 ├─ 5. LIMITS       daily tickets / concurrent positions → WAIT
                 ├─ 6. CHAMPION     no promoted champion for this asset class
                 │                    and timeframe? → NO_MATCH
                 ├─ 7. SIGNAL       no active signal? → WAIT
                 ├─ 8. SIZING       size = risk_budget / stop_distance
                 │                    capped by position %, gross %, concurrent
                 │                    below the R:R floor? → WAIT
                 │                    stop too far?    → WAIT
                 ├─ 9. NEWS         catalyst within 24h → warning (never a size change)
                 │
                 └─ 10. TICKET      { side, entry, stop, targets, size, R:R,
                                     metrics, n_trials, data_as_of, provenance,
                                     odds_band, warnings }
```

The LLM does not appear anywhere in that chain. It narrates the result and
explains it; it cannot change a number in it.

---

## 4. The AI boundary

### Where AI IS used

| Capability | What the model does | What stays deterministic |
|---|---|---|
| `news_extract` | Structured extraction: event type, impact subject, time horizon, confidence, implicit entities | Entity resolution, SEC 8-K item codes, the point-in-time join |
| `news_sentiment` | Nothing — a **local FinBERT** does this, for free and in private | Everything |
| `narrate` | One paragraph explaining a ticket | Every number in the ticket |
| `explain` | Answering "why is there no trade on AAPL?" | The reason string |
| `propose_mutation` | Parameter mutations of an existing strategy | Trial counting, the gate |
| `propose_code` | Writing strategy code (Tier B, sandboxed) | AST gate, sandbox, the normal gate |
| `rank_strategies` | A ranking of the candidate set | The ranking is *scored* against realised outcomes and demoted if it loses |
| `alert_summary` | The wording of an alert | Which alerts fire |
| `chat` | Parsing a question, calling read-only tools | Every number; there is no write path |

### Why structure beats sentiment

The evidence is specific (see `MASTER_SPEC.md` for the full table):

* FinBERT sentiment alone: F1 **0.576**
* LLM structured features alone: F1 **0.450**
* **Both together: 0.600** (p < 0.0001)
* Stripping sentiment out of the LLM set still gives **0.594** — the structural
  dimensions carry the information; sentiment adds only +0.006 on top.

So the news pipeline is **deterministic-first**: entity and event typing from
a dictionary and SEC item codes, then local FinBERT for sentiment, and the LLM
only for the *residue* — implicit entities and the things a rule cannot know.

### The three rules the code enforces

```python
# 1. Logged. Every call.
ai_runs(capability, provider, model_id, prompt_hash, seed, temperature,
        input_hash, output_json, valid, invalid_reason, tokens, cost, data_class)

# 2. Validated. Invalid output is dropped and counted, never coerced.
if not result.valid:
    return result          # data is None; the caller falls back

# 3. Grounded. A number absent from the input is stripped or nulled.
cleaned, stripped = grounded_numbers(text, input_payload)
```

### Two more rules the sidecar now enforces

```python
# 4. Fenced. Untrusted text is data, never instructions.
verdict = detect_prompt_injection(headline)
prompt = wrap_untrusted(verdict.sanitized, label="news")

# 5. Bounded. A task stops when it runs out of allowance.
if self.budget.breach() is not None:
    return ValidationResult(valid=False, reason="budget exhausted")
```

The live sidecar has **no tool access at all**. A model in this system cannot
fetch a URL, call a broker, or write a row. It receives a prepared evidence
pack and returns a validated object. Everything that moves money is a
deterministic function of stored data.

The provenance of these patterns, and the licence reasoning behind them, is
in [`PROVENANCE.md`](PROVENANCE.md).

---

## 5. Data model

Twenty-seven tables. Three access tiers.

| Tier | Tables |
|---|---|
| **Shared read** (any authenticated user) | `instruments`, `latest_quotes`, `bars`, `signals`, `strategies`, `strategy_versions`, `backtests`, `performance`, `champions`, `holdout_ledger`, `news`, `news_features`, `catalysts`, `event_stats`, `positioning`, `strategy_proposals`, `ai_rankings`, `model_registry`, `prompt_registry`, `gate_policy` |
| **User-owned** | `user_profiles`, `tickets`, `journal`, `alerts`, `conversations`, `messages` |
| **Owner-only** | `audit_log`, `jobs`, `worker_heartbeat`, `ai_runs`, and all writes to the strategy library |
| **Service role** (worker only) | Writes to `latest_quotes`, `bars`, `signals`, `news`, `news_features`, `positioning`, `worker_heartbeat`, `ai_runs`, `alerts`, `jobs` |

### The two functions that hold authority

| Function | Authority | Gate |
|---|---|---|
| `get_ticket(user, instrument, timeframe)` | Decides whether a ticket may be issued, and computes it | Freshness · session · news risk · sizing · caps · R:R floor |
| `promote_strategy(version_id, note)` | Decides whether a strategy earns capital | min trades · DSR · PBO · Sharpe · Calmar · max DD · neighbourhood stability · holdout reads · AI feature gain |

Both are `SECURITY DEFINER`, both are granted only to `authenticated` or
`service_role`, and neither reads the AI sidecar for its decision.

---

## 6. The free-tier budget

| Resource | Limit | How we stay inside it |
|---|---|---|
| Supabase DB | 500 MB | Retention policy; target < 300 MB |
| Supabase egress | 5 GB/month | Cached bars; the app reads our own data |
| Supabase Realtime | 2M messages/month | **One 5-second bundle** for all 12 symbols ≈ 518k/month |
| Supabase Storage | 1 GB | Compressed daily dumps only |
| Supabase inactivity pause | 1 week | Daily heartbeat + daily Vercel cron write |
| Vercel cron | 1/day/job | Daily housekeeping only; the live loop does not use it |
| Vercel function | 300 s | API routes are milliseconds |
| Alpaca API | 200/min, 30 WS symbols | 6 US symbols; sweeps run on cached bars |
| Binance WS | 1,024 streams, 300 conn/5 min | 3 symbols; reconnect with jittered backoff |
| Groq free | 30 RPM, 1,000/day | Quota monitor warns at 80%; sidecar refuses when exhausted |
| Gemini free | unpublished | Conservative 1,500/day assumption; public news only |
| **Total** | **$0–10/month** | |

---

## 7. Build status

| Phase | Scope | Status |
|---|---|---|
| **0** | Schema, RLS, ticket/gate functions, worker, web app, AI sidecar, ops tooling | ✅ **Complete** |
| **1** | Alpaca + OANDA connectors, 12 symbols, retention, data-quality checks, local FinBERT, economic calendar | 🟡 **In progress** — connectors for all three venues, history backfill and the economic calendar are built and tested; local FinBERT remains |
| **2** | Signal engine, Decision Center, Time Machine odds band, journal, Telegram, chat | ⬜ |
| **3** | vectorbt + Optuna, promotion gate, AI Feature Gate, AI Ranking Score, shadow mode, weekly report | ⬜ |
| **4** | Two-stage news pipeline, event-study library, catalyst cards, positioning, PWA, hardening | ⬜ |
| **5** | Alpaca paper execution, idempotency, reconciliation, autonomy ladder | ⬜ Deferred — not in v1 |

### Verification status

| Suite | Count | What it proves |
|---|---|---|
| `python tools/arch_ratchet.py` | 6 rules | The web tier never imports the worker; nothing can place an order; the AI sidecar never names a decision table; the service-role key stays in its allowed paths; no AGPL source is in first-party code; vendored code is attributed. A self-test proves every rule fires on a deliberate breach |
| `node tools/apply_migrations.mjs` | 6 migrations, 12 invariants | The schema applies to real Postgres and every structural invariant holds |
| `node supabase/tests/rls_test.mjs` | 27 tests | The custody boundary: anon sees nothing, a trusted user cannot read the owner's journal, only the owner promotes |
| `pytest services/live-worker/tests` | 101 tests | Bars close exactly once; signal keys are stable; the worker refuses forbidden writes; the AI sidecar grounds numbers and refuses private data on a free tier; all three venues parse and label their data quality correctly; the budget stops a task; untrusted text cannot give orders; duplicate headlines collapse into one cluster; the economic calendar reaches the right instruments and the right clock |
| `pytest services/research-worker/tests` | 7 tests | **S18 reproduces: +28.91% CAGR, Sharpe 1.79, −18.45% maxDD** (published: +28.41%, 1.77, −18.10%) and the engine agrees with an independent implementation to 0.000000 |

---

## 8. File map

```
apps/web/                     Next.js 16 app
  src/app/page.tsx            Command Center
  src/app/api/ticket/         the only route that produces advice
  src/components/             DecisionCenter · LiveWatchlist · EngineBadge
  src/lib/types.ts            the ticket contract

services/live-worker/         the live engine (GCP)
  worker/main.py              the loop: streams, bars, heartbeat, broadcast, jobs
  worker/bars.py              bar builder + signal idempotency key
  worker/connectors/          base (the Trade contract) · binance · alpaca · oanda
  worker/ai/                  sidecar · providers · quota · budget · guardrails
  worker/calendar/            the economic calendar (Finnhub) and the catalyst planner
  worker/news/                cluster (one story, one cluster)
  worker/supabase_client.py   guarded writes

services/research-worker/     the research engine (your PC)
  research/backtest.py        mark-to-market backtester with a denominator

packages/strategy-core/       shared by BOTH engines
  strategy_core/interface.py  the fixed contract AI code must satisfy
  strategy_core/engine.py     indicators + context
  strategy_core/strategies.py rotation · trend · breakout · mean reversion
  strategy_core/indicators.py EMA · SMA · RSI · ATR · ADX · Donchian

packages/vendor/              third-party code, with ATTRIBUTION.md

supabase/migrations/          6 migrations
supabase/tests/rls_test.mjs   the custody boundary

tools/                        apply_migrations · backup · lint_sql · arch_ratchet
```

---

## 9. What is deliberately NOT built

* **No order placement.** Not in the web tier, not in the worker, not in any
  function. v1 is ticket-only.
* **No tick-level signals.** 1-minute closed bars and level triggers only.
* **No LLM-generated strategy code running live.** It must pass the gate.
* **No auto-execution.** Not even on paper.
* **No US intraday on free data.** IEX is partial; those are swing signals.
* **No guaranteed forex champion.** Forex is the weakest leg and may
  legitimately produce zero.
* **No native mobile app.** A PWA.

---

## 10. Standing caveats

* This is engineering, not financial advice. Trading carries loss risk.
* The S18 figures are a **backtest on historical data**, not a promise.
  The published source was reproduced within data-revision drift.
* The 2M Realtime messages and 500 MB DB are hard limits. Exceeding either
  degrades the app; the quota dashboard exists to catch that early.
* Free AI tiers change without notice. The quota monitor warns at 80% and
  the deterministic fallback keeps the app working when a tier runs out.
