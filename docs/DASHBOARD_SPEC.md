# AI Trading Agent — Dashboard Specification

**Status:** Ready for build
**Version:** 1.0
**Date:** 2026-11-11
**Companion to:** [`MASTER_SPEC.md`](MASTER_SPEC.md)

---

## 1. Purpose & Non-Goals

The dashboard is the **operator's** view of a system whose failures are irreversible. It exists so that a single person can answer, in under 60 seconds, three questions:

1. **Is it safe right now?** — mode, kill switch, breaches, reconciliation state.
2. **What is it doing and why?** — positions, orders, and the decision record behind each.
3. **Is it working?** — P&L, drawdown, live-vs-backtest divergence.

**Non-goals:**
- ❌ It is **not** a control panel for a chatty agent. It is not a place to ask a chatbot questions.
- ❌ It is **not** a place to place orders by clicking. Approval happens on the **promotion** surface only; execution is never a UI click.
- ❌ It is **not** a public dashboard. No anonymous access, no monetized public tier, no client-side API credentials.
- ❌ It does **not** show a "confidence score" or any uncalibrated model output as a headline risk figure.

> *Rejection recorded:* Alex Carter's Live Ops Dashboard is explicitly recommended for **Netlify** (public URL) and **Walltier** monetisation ($29/mo). A dashboard showing live equity forces API credentials into client-side JavaScript, shipped to every visitor. **We reject that deployment model.**

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Browser  (Next.js App Router, React, Tailwind, shadcn/ui)   │
│  ── SSE subscription (EventSource) for live panels           │
│  ── REST for queries / pagination / exports                  │
└───────────────┬──────────────────────────────┬───────────────┘
                │ SSE (text/event-stream)      │ HTTPS/JSON
                ▼                              ▼
┌──────────────────────────────────────┐  ┌────────────────────────────┐
│  dashboard-api  (read-only service)  │  │  control-api (separate,    │
│  ── reads ONLY from a read replica   │  │  authenticated + audited)  │
│  ── no broker credentials            │  │  ── kill switch            │
│  ── no write path to trading         │  │  ── mode change            │
└───────────────┬──────────────────────┘  │  ── promotion approve      │
                │                         └──────────────┬─────────────┘
                ▼                                        ▼
┌────────────────────────────────────────────────────────────────┐
│  Postgres (primary)  ──write──▶  TimescaleDB / read replica     │
│  ── orders, trades, signals, decisions, audit, metrics          │
│  ── Redis: live state cache, idempotency keys, rate limits     │
└────────────────────────────────────────────────────────────────┘
```

### 2.1 Hard constraints

| Constraint | Rule |
|---|---|
| **Credential isolation** | The dashboard service has **no** broker API key, ever. It reads a read replica only. |
| **Write separation** | Mutating controls live in `control-api`, separate binary, separate auth, every action audited with actor identity. |
| **Transport** | SSE for server→client push (unidirectional is all we need). REST for queries. No WebSocket gateway — it adds stateful infrastructure for no benefit here. |
| **Freshness** | SSE `id:` field carries a monotonic event sequence; browser `Last-Event-ID` replays missed events on reconnect. `X-Accel-Buffering: no` on every SSE route. |
| **Freshness SLO** | p95 ≤ 2 s from event write to browser paint. |
| **Auth** | Single-user to start: Tailscale/WireGuard or Caddy + IP allowlist + SSO. Never a public route. |
| **Time** | All timestamps UTC end-to-end; the browser renders local as a secondary label. Clock-skew target ≤ 250 ms. |

> *Sources:* Agent Sentinel console structure (approvals/replay/ledger/kill-switch), APEX observability taxonomy, SSE-vs-WebSocket trade study (2026), Next.js streaming guidance.

---

## 3. Panels

### Panel 1 — Command (landing page)

The one screen that must be readable at a glance and correct under stress.

**Top strip (always visible, sticky):**
```
[ MODE: PAPER ]  [ KILL SWITCH: ARMED ]  [ RECON: CLEAN 00:00:14 ago ]
[ EQUITY $10,412.55 ]  [ DAY P&L +$38.20 ]  [ DRAWDOWN -2.1% / -15% limit ]
[ OPEN RISK 3.4% / 6% cap ]  [ SSE ● LIVE 0.4s ]  [ LAST RUN 19:02:11Z ]
```

**Rules:**
- Mode badge is **colour-coded by severity**, not decoration: grey `OBSERVE`, blue `PAPER`, amber `ASSIST`, orange `AUTONOMOUS_LIMITED`, red `AUTONOMOUS_FULL`, **black `FROZEN`**, **pulsing red `KILL SWITCH`**.
- `FROZEN` and `KILL SWITCH` override all other panels with a full-width banner stating *why* and *who can clear it*.
- Drawdown shows **both** current and limit, with the limit visible. A limit that is not displayed is not a limit.
- Day P&L is always **mark-to-market**, never closed-trade-only. A closed-trade figure may appear in the research panel, labelled as such.

**Panel body:**
- Equity curve (mark-to-market) with drawdown shaded underneath.
- Limit headroom bars: risk per trade, gross exposure, correlation clusters, daily loss breaker, max DD breaker.
- Open positions summary + count by direction.
- Venue lock indicator: `PAPER ENV LOCKED` (green) — the executor is hard-coded to paper.

### Panel 2 — Positions

Table of open positions:

| Column | Source | Notes |
|---|---|---|
| Instrument | position | |
| Side / Qty | position | |
| Avg entry | fill | |
| Mark | quote | Live |
| Unrealized P&L | mark − entry | Live, M2M |
| Stop (broker-held) | order | **Green if verified present at broker; red if missing** |
| Target | strategy | |
| R multiple (current) | derived | |
| Sleeve / strategy | signal | |
| Opened at | fill | UTC |
| Age | derived | |
| Time in trade vs. planned max | strategy | Drift detector |

**Critical behaviour:** if any position lacks a broker-held stop, the row turns red **and** the panel raises a P0 alert. This is the single most important check in the whole UI — a stop that lives only in our process is not a stop.

### Panel 3 — Orders

**Funnel:** `INTENT → SUBMITTED → ACKED → PARTIAL → FILLED` with `CANCELED / REJECTED / EXPIRED / UNKNOWN` branches.

Display:
- Counts per state, last 24h and current session.
- **UNKNOWN** count is highlighted — `UNKNOWN_PENDING_RECON > 0` during a trading window is a P0.
- Submit latency percentiles: p50 / p95 / p99.
- First-broker-status timeout count (> 60 s).
- Duplicate idempotency suppression count (proves INV-2 is working).
- Fill slippage vs. assumption, per order and aggregated.
- Filterable list of every order with `client_order_id`, `order_intent_id`, `trace_id`.

### Panel 4 — Reconciliation

- **Gap count** (must be 0).
- Drift: broker positions − internal positions, per instrument, with age.
- Last clean reconciliation timestamp, plus a timeline strip of the last 30 days.
- Exception list with the pre-declared gap categories from the reference design: *fills / timing / data / rounding / limits / fuzzy rules* — each with a count and trend.
- A `FROZEN` banner when gaps > 0, with the auto-created reconciliation run.

### Panel 5 — Risk

- Every limit from `risk/limits.py` rendered as: name, configured value, current usage, headroom, and **a link to the code line that enforces it**.
- Breaker state: daily loss, max drawdown, kill-switch file, venue lock.
- Correlation-group exposure with cluster membership.
- Borrowing/margin usage if enabled (off by default).

### Panel 6 — Promotions *(the human-in-the-loop surface)*

The agent's proposals arrive here. This is the **only** place a human clicks anything.

Per proposal:
```
PROPOSAL #482  ·  Strategy: S1 confirmed-level S/R  ·  Submitted 2026-11-09 14:22Z
  Rationale (agent)        : "RSI(2)<10 twice above 200d over last 40 bars; PF 1.9 on 60 trades"
  Gate results:
    ✓ min_sample_size     60 trades (≥30 required)
    ✓ min_hold_period     21 days live (≥14 required)
    ✓ deploy_rate         1 deploy in 30d (≤2 allowed)
    ✓ cooling_period      18 days since last change (≥14 required)
    ✓ base_unchanged      file hash matches proposal base
    ✓ monotonic_version   v7 → v8
    ✓ frozen_fields       authorization + status unchanged
    ✗ holdout_validation  DSR = -0.11 (not > 0)
  VERDICT: REJECTED — nothing happened. No files changed.
  [ Reject ]  [ Request more evidence ]  [ Open in research panel ]
```
- A rejected proposal shows **zero** side effects, visibly.
- The "Request more evidence" action creates a shadow experiment.
- Every decision writes an audit record with actor identity.

### Panel 7 — Agent Runs

- Run list: id, agent, trigger, mode, start/end, outcome, tokens, cost, artifacts.
- Run detail: step-by-step timeline with tool calls, inputs, outputs, and timings.
- **Replay** — re-run a past run against the same inputs in a sandbox (zero cost) and diff the outcome. This is how non-determinism is detected.
- **Determinism analysis** — flag runs where the same input produced materially different outputs. A non-deterministic component in a decision path is a defect, not a curiosity.

### Panel 8 — Strategies

Per live strategy:
- Equity curve (its own book), P&L, trade count, win rate, expectancy, profit factor, max DD (M2M).
- **Distance to kill line** — the pre-committed removal threshold. A visual proximity bar.
- **Frequency drift** — live trade rate vs. backtested rate (course: flag if <½× or >2×).
- Shadow experiments currently running, with their evidence.
- Gate results for each candidate, and why it is not live.

### Panel 9 — Research

- Backtest results with **DSR, PBO, `N_trials`, and the neighbour surface** visible — not hidden in a footnote.
- **Holdout usage counter**: how many times the holdout has been consumed. It must read `1`. If it reads > 1, the protocol is broken and this panel says so loudly.
- Comparison table: strategy × {CAGR, Sharpe, DSR, maxDD M2M, trades, neighbours, cost stress} vs buy-and-hold and cash.
- Published divergence report (Phase 5.3) with a link.

---

## 4. Data Model (core tables)

```sql
-- Every order attempt, immutable
CREATE TABLE order_intents (
  order_intent_id   UUID PRIMARY KEY,
  idempotency_key   TEXT UNIQUE NOT NULL,
  client_order_id   TEXT UNIQUE NOT NULL,
  signal_id         UUID REFERENCES signals,
  instrument        TEXT NOT NULL,
  side              TEXT NOT NULL,
  quantity          NUMERIC NOT NULL,
  order_type        TEXT NOT NULL,
  limit_price       NUMERIC,
  stop_price        NUMERIC,
  trading_mode      TEXT NOT NULL,          -- observe|paper|assist|autonomous_*
  policy_version    TEXT NOT NULL,
  rule_set          TEXT NOT NULL,
  risk_snapshot     JSONB NOT NULL,         -- limits in force at decision time
  decision          TEXT NOT NULL,          -- approved|rejected|auto
  reason_code       TEXT,
  human_decision_by TEXT,
  created_at        TIMESTAMPTZ NOT NULL,
  trace_id          UUID NOT NULL
);

CREATE TABLE fills (
  exec_id         TEXT PRIMARY KEY,          -- dedupe key
  order_intent_id UUID NOT NULL REFERENCES order_intents,
  instrument      TEXT NOT NULL,
  qty             NUMERIC NOT NULL,
  price           NUMERIC NOT NULL,
  fee             NUMERIC NOT NULL,
  slippage_bps    NUMERIC NOT NULL,
  ts              TIMESTAMPTZ NOT NULL
);

CREATE TABLE decisions (                     -- append-only audit
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  trace_id      UUID,
  agent_id      TEXT,
  order_intent_id UUID,
  instrument    TEXT,
  event_id      TEXT,
  service       TEXT NOT NULL,
  severity      TEXT NOT NULL,              -- info|warn|error|critical
  action        TEXT NOT NULL,
  params        JSONB,
  policy_version TEXT,
  rule_set      TEXT,
  decision      TEXT,
  reason_code   TEXT,
  actor         TEXT,                       -- human identity for control actions
  outcome       TEXT
);

CREATE TABLE reconciliation_runs (
  id            BIGSERIAL PRIMARY KEY,
  started_at    TIMESTAMPTZ NOT NULL,
  finished_at   TIMESTAMPTZ,
  broker_count  INT NOT NULL,
  internal_count INT NOT NULL,
  gaps          INT NOT NULL,
  gap_detail    JSONB,
  status        TEXT NOT NULL               -- clean|dirty|running
);

CREATE TABLE proposals (                    -- agent-initiated changes
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL,
  agent_id      TEXT NOT NULL,
  target        TEXT NOT NULL,              -- strategy id
  change_hash   TEXT NOT NULL,
  base_hash     TEXT NOT NULL,
  rationale     TEXT NOT NULL,
  gates         JSONB NOT NULL,             -- per-gate pass/fail + evidence
  verdict       TEXT NOT NULL,              -- approved|rejected|needs_evidence
  decided_by    TEXT,
  decided_at    TIMESTAMPTZ,
  shadow_experiment_id UUID
);

CREATE TABLE agent_runs (
  run_id        UUID PRIMARY KEY,
  agent_id      TEXT NOT NULL,
  trigger       TEXT NOT NULL,
  mode          TEXT NOT NULL,
  started_at    TIMESTAMPTZ NOT NULL,
  ended_at      TIMESTAMPTZ,
  status        TEXT NOT NULL,
  steps         JSONB NOT NULL,             -- step timeline
  artifacts     JSONB,
  tokens        INT,
  cost_usd      NUMERIC
);
```

**Lineage is preserved end-to-end:** `raw_event_id → trade_event_id → signal_id → order_intent_id`.

---

## 5. Alert Taxonomy

| Priority | Condition | Action |
|---|---|---|
| **P0** | `UNKNOWN_PENDING_RECON > 0` during trading window | Page immediately. Freeze mode. |
| **P0** | Broker connection lost | Page. Freeze. Notify. |
| **P0** | Position without broker-held stop | Page. **Flatten or halt new entries immediately.** |
| **P0** | Max drawdown breaker breached | Page. Full freeze. Human resume required. |
| **P0** | Kill switch engaged | Page. Everything halts. |
| **P0** | Unexplored order outside a known intent | Page. Halt. This is the "unforced order" detector. |
| **P1** | Reconciliation mismatch above threshold | Urgent. Investigate before next session. |
| **P1** | Order submit latency p95 > 50 ms sustained | Degradation watch. |
| **P1** | First-broker-status timeout count rising | Degradation watch. |
| **P1** | Policy/gate evaluation failures rising | Investigate. |
| **P1** | Data freshness breach (stale bar) | Halt new entries. Exits continue. |
| **P2** | Retry counts rising | Watch. |
| **P2** | Dashboard SSE freshness > 2 s | Watch (UI only, not a trading risk). |
| **P2** | Agent token spend above budget | Watch. |

Alerts route to Telegram (the user already runs a Hermes Telegram gateway) and optionally email. **P0 uses push, not pull.** A dashboard that requires you to look at it is not an alerting system.

---

## 6. Metric Definitions (must be exact)

| Metric | Definition | Trap avoided |
|---|---|---|
| **Max drawdown** | Peak-to-trough on the **mark-to-market** equity curve, including open positions | Never the closed-trade-only figure. That variant understated real drawdown **6×** in our Turtle reproduction. |
| **Sharpe** | `mean(daily returns) × 365 / (std(daily returns) × √365)` | Annualization stated explicitly; crypto trades 365 days |
| **Profit factor** | Gross profit ÷ gross loss, **in currency**, not in return % | Return-% PF is scale-dependent and misleading across positions |
| **Expectancy** | `win_rate × avg_win − (1 − win_rate) × avg_loss`, in currency per trade | The only number that determines viability |
| **Win rate** | Closed trades with net P&L > 0 ÷ total closed trades | Must be reported *with* expectancy; 45% win rate can be excellent or fatal |
| **Exposure** | Time-in-market ÷ total time, and gross notional ÷ equity | A strategy that trades 2% of the time cannot be compared to buy-and-hold on return alone |
| **Realized slippage** | `(fill price − decision reference price) / reference × bps`, signed | Must be measured against the *assumption*, not just reported absolutely |
| **Signal agreement** | Live signal == backtest signal on the same bar, ÷ total signals | The single best live-vs-backtest proxy |
| **Deflated Sharpe** | P(true edge) after accounting for `N_trials`, sample length, skew, kurtosis | Report beside raw Sharpe, always |
| **PBO** | Probability of backtest overfitting | Same |

**Forbidden metrics:** "confidence score" (uncalibrated model self-assessment), "profit factor" without trade count, "win rate" without expectancy, "AI confidence" as a risk input. None of these may gate capital allocation.

---

## 7. Implementation Stack

| Layer | Choice | Rationale |
|---|---|---|
| Frontend | **Next.js (App Router) + React + TypeScript** | SSR for static panels; route handlers for the read API |
| Styling | **Tailwind + shadcn/ui** | Fast, accessible primitives; no bespoke component library to maintain |
| Live transport | **SSE** (Next.js route handler, `text/event-stream`) | Unidirectional is sufficient; proxy-friendly; native auto-reconnect with `Last-Event-ID`; no stateful WS tier needed |
| Charts | **lightweight-charts** (finance) + D3 for the neighbour surface | Small, fast, good at candlesticks |
| Tables | **TanStack Table** | Sorting/filtering/virtualisation for order and fill tables |
| Backend | **FastAPI** (Python) — mirrors the research engine's language | Shared types with `research/` and `risk/`; no impedance mismatch |
| Primary DB | **PostgreSQL** | Orders, decisions, audit; transactional integrity on intent creation |
| Time-series | **TimescaleDB** (or plain Postgres partitioning) | Equity curves, metrics, latency percentiles |
| Cache | **Redis** | Live state, idempotency keys, rate limiting, SSE fan-out |
| Auth | **Tailscale or WireGuard** + Caddy IP-allowlist; SSO later | No public exposure, ever |
| Deployment | **Docker Compose** on the same VPS as the executor (or a separate small VPS) | Simple, inspectable; dashboard must be able to run without the executor |
| Telemetry | **OpenTelemetry** spans + JSON logs with `trace_id`, `agent_id`, `order_intent_id`, `instrument_id`, `event_id`, `service`, `severity` | Correlate broker callbacks to originating intent |

> *Reuse note:* the user already has **Hermes Agent** installed locally (`%LOCALAPPDATA%\hermes\hermes-agent`) with a Vite/React/TS web workspace, `lucide-react`, `tailwindcss`, `motion`, and `react-router` already in `node_modules`. The Hermes UI package can be **referenced** for component patterns and the design system rather than rebuilt from zero — but the trading dashboard must remain a **separate service** so it can never inherit the agent's process identity or credentials.

---

## 8. Build Order

| Sprint | Deliverable | Done when |
|---|---|---|
| **D1** | Backend read API + SSE + Postgres schema | SSE freshness p95 ≤ 2 s; schema migrations applied; read-only enforced |
| **D2** | Panel 1 (Command) + Panel 4 (Reconciliation) | Kill-switch drill from the UI; FROZEN banner verified |
| **D3** | Panel 2 (Positions) + Panel 3 (Orders) | Missing-stop row turns red; UNKNOWN count highlighted |
| **D4** | Panel 5 (Risk) + Panel 8 (Strategies) | Every limit links to its enforcing code line |
| **D5** | Panel 6 (Promotions) | Rejected proposal visibly changes nothing; audit record written |
| **D6** | Panel 7 (Agent Runs) + Panel 9 (Research) | Replay diff works; holdout counter visible |
| **D7** | Alert router (P0/P1/P2) → Telegram | P0 pages within 60 s of condition |
| **D8** | Fault-injection UI test | Every alert fires under its simulated condition |

**Non-negotiable review gate before D2 merges:** a credential scan proving no broker key, no exchange secret, and no `.env` content is reachable from any dashboard code path or client bundle.

---

## 9. Anti-Patterns — Explicitly Rejected

| Anti-pattern | Source it came from | Why rejected |
|---|---|---|
| Dashboard on a public Netlify URL | Alex Carter M26 | Forces API credentials into client JS shipped to every visitor |
| Monetised public dashboard via Walltier | Alex Carter M26 | Makes credential exposure commercially attractive |
| Vanilla-JS HTML parsed from log files | Alex Carter M26 | Fragile, unqueryable, no audit trail |
| "Profitability" headline from a leaderboard screenshot | Alex Carter M19 / EA Trading | Unverifiable; survivorship-selected |
| Heartbeat-only monitoring (no gap detection) | Alex Carter M25 | Silent silence is not a detection mechanism |
| Showing only closed-trade P&L | Mind Math Money | Hides unrealized loss; understates drawdown 6× |
| Chatbot chat window as the primary interface | Alex Carter M25 | Encourages asking rather than verifying |
| Model "confidence" as a size input | Alex Carter M13/M18 | Uncalibrated; not a probability |
