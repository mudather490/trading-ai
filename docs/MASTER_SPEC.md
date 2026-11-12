# AI Trading Agent — Master Development Specification

> **Note on version.** This document (v1.0) remains the authoritative record
> of the **source research** and the **eight original invariants**. Its
> *deployment topology* described a single-machine agent and has been
> **superseded** by the three-tier design in
> [`ARCHITECTURE.md`](ARCHITECTURE.md) (v1.1), for the reasons set out
> there. The invariant mapping from v1.0 to v1.1 is in
> `ARCHITECTURE.md` §0.
>
> Nothing in the research findings below changed. The reproduction table, the
> drawdown artefact, the naive-baseline result, and the S18 verification are
> all still exactly as measured.

**Status:** Ready for build
**Version:** 1.0 (research record; topology superseded — see above)
**Date:** 2026-11-11
**Workspace:** `E:\trading-ai`
**Derived from:** 7 YouTube sources (4 English AI-trading courses + 3 Arabic EA Trading playlists) + independent empirical verification + published 2026 deployment literature.

---

## 0. Executive Summary

We analyzed **seven** trading-education sources and **independently reproduced their testable claims** against market data. The result is decisive:

| Source | Testable claim | Our reproduction | Verdict |
|---|---|---|---|
| **Mind Math Money** — Turtle 55/20 on BTC daily | +729% profit, **3.29% max drawdown**, 45% win rate, PF 7.6 | +174.6% over 11.1y (CAGR 9.5%), win rate 44.1%, PF 6.15, **max drawdown −18.91%** | ⚠️ **Max drawdown understated 6×.** The 3.29% figure is TradingView's *closed-trade-only* metric. On mark-to-market equity the same trades draw down 18.91%. |
| **AI Pathways** — QQQ/gold 200-day rotation | 24.8%/yr core (11.2% DD); ~28.7%/yr full account | **+29.63% CAGR, −12.25% DD, Sharpe 1.69** on the locked 2025+ window; **+28.41% CAGR** full 2022+ | ✅ **Reproduces.** The only verified headline across all seven sources. Parameter-neighbour test passes (SMA 150→250 all profitable). |
| **Lewis Jackson** — self-improving Hermes agent | "self-improving, learns from mistakes" | No P&L published; own commenters: *"classic overfitting made fancy"*, *"a lot of hype and absolutely no results"* | ❌ Unfalsifiable. Its "learning" is a self-scored ledger. |
| **Alex Carter** — full agentic stack | "real money, every tool" | **No performance shown for anything he built.** LLM in position-sizing hot path; unauthenticated ngrok webhook; EVM private key in plaintext `.env` copied to a VPS and to Railway. | ❌ **Hard-stop** risks at the money path. |
| **EA Trading** — 211 videos of market calls | Directional calls | **72% hit rate — but the naive "follow the tape" baseline scores 88%**. Excess: **−16pp**. | ❌ **Worse than guessing.** |
| **EA Trading** — 142-min masterclass | "10 years of experience" | **Zero** backtests, win rates, or drawdowns. One anecdote. 7 signal/bot promos. | ❌ No evidence. |
| **EA Trading** — 99-min scalping course | "+$48,000 in 12 days" | Same video also says "+$5,500 in 12 days". No statement, dates, or curve. | ❌ Self-contradictory. |

**Core architectural conclusion — the inversion that makes us better than all seven:**

> **None of these sources puts the LLM in the right place.** They either (a) let an LLM size positions in the hot path, (b) let an agent grade its own homework, or (c) teach entry rules with no stop, no size, and no cost model.
>
> Our system inverts it: **the LLM never touches the execution path.** Not "prompted not to trade" — the order-placement and position-size parameters **do not exist** in the agent's tool schema. The LLM proposes; deterministic code disposes; the human rules on judgment, not on clicks.

---

## 1. Architecture — The Eight Invariants

These are not guidelines. Each is enforced in code the agent cannot write to, and each has a test.

### INV-1 — Capability removal over capability restriction
The agent's tool schema contains **no** order-placement tool, **no** position-size parameter, and **no** trading-mode parameter. It cannot place an order because there is no such tool — not because a prompt forbids it.

> *Source:* Iron Mind (2026-08) — "the agent cannot pass a position size — the parameter does not exist in the tool signature."

### INV-2 — Separate decisioning from execution
Two processes with different identities:
- **Agent process** — read-only broker view, market data, research tools, `propose_change`. Network egress to broker **blocked**.
- **Executor process** — non-LLM, owns credentials, owns order lifecycle, owns risk enforcement.

> *Source:* APEX Standard runtime safety guide — "The model proposes. The runtime enforces."

### INV-3 — Promotion gates in code, not in prompts
Every agent-initiated change passes Python assertions in `risk/` (read-only to the agent). Failure returns the proposal to the queue — **"nothing happened," never "something subtly different happened."**

Minimum sample size, min hold period, max 2 deploys/week, 14-day cooling period, byte-identical base check, monotonic version, frozen authorization/status fields.

> *Sources:* John Costa (2026-03) — "Minimum sample sizes are enforced at the tool level, not the prompt level… They're Python assertions that throw errors. The prompt is not the safety layer." AllPath (AI4F 2026) — "The failure mode is 'nothing happened,' never 'something subtly different happened.'"

### INV-4 — One holdout, consumed once
The out-of-sample window is read **exactly once**, by the final candidate only. Every intermediate exploration is training. `N_trials` is logged; Deflated Sharpe and PBO are reported alongside raw Sharpe.

> *Source:* Bailey & López de Prado — "as few as three independent trials suffice to produce an investment strategy that is likely false."

### INV-5 — Mark-to-market is the only reporting basis
Closed-trade-only drawdown may be *shown* but is **never** the headline risk number. A lint rule rejects any risk summary sourced from a closed-trade drawdown.

> *Source:* our reproduction — the identical Turtle strategy shows −3.01% (closed-trade) vs **−18.91%** (mark-to-market).

### INV-6 — Reconciliation is a continuously-verified invariant
Broker state ≡ internal state ≡ expected positions, every cycle, not just at start. Mismatch ⇒ halt and alert. `UNKNOWN_PENDING_RECON > 0` ⇒ `trading_mode = FROZEN`.

> *Source:* AI Pathways (best idea in that video) + APEX invariants.

### INV-7 — Risk limits can only subtract
Risk rules may never *increase* exposure or alter a strategy. A limit breach reduces, halts, or freezes. There is no code path that opens an unprotected position.

> *Sources:* AI Pathways — "none of them ever change a rule. So, they can only make the bot do less." AllPath — closing positions is never blocked by value caps.

### INV-8 — Self-revision may tighten, never promote
An agent-initiated revision may move a stop closer, add a risk rule, or remove a rule. It may **never**: promote itself to autonomous mode, deactivate monitoring, change authorization scope, or grant itself a larger mandate.

> *Source:* AllPath revision integrity gate (iii) — "a self-revision can tighten a stop, but it can never promote itself to autonomous trading or silently de-activate monitoring."

---

## 2. Component Specifications

### 2.1 `research/` — Reference engine & research protocol

**Purpose:** produce falsifiable answers. Not a trading bot — the engine that proves whether anything deserves to be traded.

| Module | Responsibility | Key requirement |
|---|---|---|
| `data/adapters.py` | Point-in-time OHLCV from Yahoo / Coinbase / venue APIs | No survivorship: point-in-time index membership, not today's constituents |
| `data/quality.py` | Freshness, gaps, corporate actions, split adjustment | Refuse to trade if any symbol's last bar is not from the current session |
| `engine/backtest.py` | Event-driven simulator, mark-to-market equity | **Signals at close → fill at next open.** Shared signal code with the live bot |
| `engine/fills.py` | Per-asset fee + spread + auction-participation model | Fill is a *distribution*, not a point |
| `engine/metrics.py` | CAGR, Sharpe, Sortino, max DD (M2M), profit factor, expectancy, exposure | DD must be mark-to-market |
| `research/protocol.py` | Train/holdout split, purged walk-forward, embargo | Holdout consumed once |
| `research/multiplicity.py` | `N_trials` ledger, Deflated Sharpe, PBO | Report DSR/PBO next to raw Sharpe |
| `research/robustness.py` | Parameter-neighbour surface, bootstrap CIs, Monte Carlo | Neighbours must all clear |
| `research/reports.py` | Reproducible run reports with a content hash | Same inputs ⇒ same output hash |

**Mandatory validation of the engine itself (before any strategy is trusted):**

1. **Planted edge** — synthetic prices with a known +1%/trade edge must be detected (pre-registered acceptance band).
2. **Pure noise** — random prices must yield no edge beyond cost.
3. **Look-ahead guard** — a cheating signal (buys tomorrow's high) must be *rejected*.
4. **Golden-master diff** — the engine must match an independent second implementation on a fixed fixture set.

> *Source:* AI Pathways' planted-edge/noise/look-ahead triad — the single best piece of methodology in all seven sources. We add the golden-master diff, which they lack.

### 2.2 `strategies/` — Encodable candidates

Every candidate from the source courses, with an honest status. **"Unusable — parameters missing"** is a valid and important status.

| ID | Candidate | Source | Status | Missing / notes |
|---|---|---|---|---|
| S1 | **Confirmed-level S/R** — level is real only after ≥2 sharp rejections; buy support / sell resistance; draw the line not the zone | EA Trading 142-min | **Encodable — highest value** | "Sharp" needs numeric definition; stop/size/target must be engineered |
| S2 | Trendline third-touch continuation | EA Trading 142-min | Encodable | Swing + touch-tolerance definitions missing |
| S3/S4 | Double bottom / double top measured move | EA Trading 142-min | Encodable | Target formula explicit; low-equality tolerance missing; implied R:R ≈ 1 — weak |
| S5 | Head & Shoulders measured move | EA Trading 142-min | Encodable | Shoulder symmetry tolerance missing |
| S6 | Cup & Handle (measure the cup) | EA Trading 142-min | Encodable | Pattern detection heavy |
| S7/S8 | Falling / Rising wedge breakout | EA Trading 142-min | Encodable | Convergence metric missing |
| S9 | Triangle breakout | EA Trading 142-min | Encodable | Contraction definition missing |
| S10 | Flag / pennant continuation | EA Trading 142-min | Encodable — *disowned by its own author* | — |
| S11 | RSI(14) 30/70 **confirming** a confirmed S/R level | EA Trading 142-min | Encodable | RSI never originates a trade (author's own rule) |
| S12 | RSI divergence | EA Trading 142-min | Encodable | Pivot definition + trigger missing |
| S13 | EMA50/200 Golden/Death Cross | EA Trading 142-min | Encodable — *use as regime filter only* | Weak standalone expectancy |
| S14 | Fibonacci 0.618 continuation | EA Trading 142-min + 99-min | Encodable | Swing selection dominates results; **99-min version's target ladder is geometrically incoherent as taught** |
| S15 | Market-cycle regime filter | EA Trading 142-min | Encodable | Use S2/S13 as the cycle proxy |
| S16 | News/sentiment contrarian overlay | EA Trading 142-min | **Unusable as taught** | No source, no classifier, no threshold. Build independently or drop |
| **S17a** | **Three-scenario breakout confirmation** — both BTC + alts break and hold → long · BTC alone → wait 2–3 days · both break then both fall back → exit all | EA Trading (99-min corpus) | **Encodable — highest-value new rule** | Only complete decision tree in the corpus with an observable trigger and a defined wait period. Needs: alt-basket definition, per-coin levels, close convention, window (2 vs 3 days) |
| **S17b** | **Dual-asset confirmation with time window** — BTC >64k AND ETH >1.8k within 2 days; BTC alone ⇒ don't buy | EA Trading (99-min corpus) | Encodable | Levels stated; entry fill + stop must be supplied. Exit when BTC loses 81,000 |
| **S17c** | **Chart-precedes-news filter** — price structure must already imply the setup; news confirms, never triggers entry | EA Trading (99-min corpus) | Encodable — *meta-rule* | If adopted, it invalidates most of the corpus's own videos. That is why it is valuable |
| **S17d** | **Fib false-break reversal** — close below a Fib support then reclaim it within N candles → long | EA Trading (99-min corpus) | Encodable ⚠️ **CONFLICTS with S17a** | S17a says *exit* on a fake break; S17d says *buy* it. **Must be reconciled before coding.** Candidate disambiguator: S17d applies only at Fib levels on a higher timeframe than S17a's resistance |
| **S17e** | **Gann-style price-angle cycle projection** — 0°→360°; shape from first pullback angle (180°/120°/90°); spacing 1/10/100/1000/10000 | EA Trading (2021 course) | Encodable ⚠️ *requires frame re-derivation* | The only fully numeric derivation in the corpus. Anchor-selection and spacing-multiplier rules are missing; indicator name is ASR-corrupted |
| **S17f** | **BTC-dominance gate** — 54–55% → 58% peak → roll-over opens the alt season | EA Trading (99-min corpus) | Encodable | The corpus's only fully specified regime rule. Needs `CRYPTOCAP:BTC.D` |
| **S17g** | **Season-high as next-season-low**; **season duration ≈ 1 year** | EA Trading (99-min corpus) | Encodable — *weak priors only* | "Season" definition missing. Use as a prior, never as a trigger |
| **S17h** | **Isolated margin only, never cross** | EA Trading (OKX walkthrough) | Encodable — *risk mechanics, not a strategy* | 3× on $100 → $300 notional, ~$91 margin. Contradicts the channel's own copy product |
| **S18** | QQQ/gold 200-day rotation (**core**) | AI Pathways | **Verified — our reference strategy** | Reproduces; but 23.8% of days in gold ⇒ regime-dependent |
| S19 | Turtle breakout 55/20 | Mind Math Money | **Rejected as a live candidate** | OOS Sharpe 0.30; top-3 trades = 69% of PnL; beaten by buy-and-hold 100× |
| S20 | Dip-buy sleeve (RSI(2)<10 above 200-day) | AI Pathways | Encodable | Requires the 15-trade / PF≥1.3 funnel gates |

**Promotion bar for any strategy to reach paper trading:**
```
PASS if:  OOS Sharpe >= 0.5 across >= 3 walk-forward folds
      AND deflated Sharpe > 0  (N_trials logged)
      AND max drawdown <= 1.5x expected, mark-to-market
      AND >= 5 parameter neighbours also clear
      AND beats buy-and-hold AND beats cash on a risk-matched basis
      AND cost stress at 2x costs still positive
```

### 2.3 `risk/` — Deterministic risk engine

Implemented in a directory the agent has **read-only** access to. All limits are Python constants.

| Limit | Default | Notes |
|---|---|---|
| Risk per trade | 1% of equity | Sized from stop distance, never from the LLM |
| Max concurrent positions | 5 | — |
| Max gross exposure | 100% of equity | No leverage by default |
| Correlation-group cap | 30% per cluster | Prevents 5 "different" positions being 1 concentrated bet |
| Daily loss breaker | −3% | Freeze new entries, exits continue |
| Max drawdown breaker | −15% from peak | Freeze everything, require human resume |
| Mandatory broker-held stop | 100% of positions | Verified by reconciliation, not assumed |
| Kill switch | File-based | While it exists, every write refuses. No deploy required |
| Venue hard-lock | Paper environment only in code | Requesting live raises **before** any network call |

**Audit trail** — every decision appends an immutable record:
```
{ts_utc, trace_id, agent_id, order_intent_id, instrument_id, event_id,
 service, severity, action, params, policy_version, rule_set, decision,
 reason_code, human_decision, outcome}
```
Lineage is preserved end-to-end: `raw_event_id → trade_event_id → signal_id → order_intent_id`.

### 2.4 `agent/` — LLM perimeter (Hermes)

Runs on the **existing local Hermes install** (`%LOCALAPPDATA%\hermes\hermes-agent`) — the *safer* of the two runtimes these courses push (no 360k-star plugin ecosystem, no ClawHub supply-chain surface).

**Allowed tools (the complete list):**
| Tool | Access | Notes |
|---|---|---|
| `read_market_data` | read | Symbol, timeframe, range |
| `read_account_state` | read | Positions, equity, open orders — **read-only broker view** |
| `read_research_log` | read | Own prior runs |
| `run_backtest` | compute | Via the reference engine only. No live path reachable |
| `propose_change` | write-to-quarantine | A proposal, not an action. Goes to the promotion gate |
| `write_report` | write | Markdown findings |

**Not present (removed, not disabled):** `place_order`, `cancel_order`, `set_position_size`, `set_trading_mode`, `read_credentials`, `read_secrets`, `shell_to_broker`.

**Learning scope — honestly bounded:**

| Learning type | Allowed | Gate |
|---|---|---|
| **Workflow learning** (how to run a backtest, which data source is broken) | ✅ Yes | Hermes skill/memory curation. Low risk. |
| **Parameter learning** (thresholds, sizes, timings) | ⚠️ Conditional | Must pass INV-3 promotion gates. Minimum sample size, cooling period, holdout validation |
| **Policy learning** (rewriting the decision procedure) | ⚠️ Conditional | Shadow-experiment path first. Must beat fixed policy out-of-sample |
| **Risk-limit learning** | ❌ Never | Risk constants are not editable by the agent, ever |
| **Self-promotion** | ❌ Never | Frozen fields (INV-8) |

> *Sources:* Hermes' learning loop is a *review-fork + curator + `skill_manage`* mechanism — real, but it curates **skills and memory**, not validated policy. EvolveTrade (2026) shows policy-text self-evolution beats fixed policy in only **3 of 6** model-regime pairs — self-evolution can also lose.

### 2.5 `broker/` — Executor (non-LLM)

- Owns the exchange/broker credentials. Nothing else does.
- Order lifecycle state machine: `INTENT → SUBMITTED → ACKED → PARTIAL → FILLED | CANCELED | REJECTED | EXPIRED | UNKNOWN`.
- Idempotency: unique `client_order_id` per intent (UUID). Retries reuse the same ID — the broker returns the original response rather than duplicating.
- Session windows enforced in code (never by the agent).
- `UNKNOWN_PENDING_RECON` ⇒ `FROZEN`.
- All timestamps UTC; clock-skew bounded ≤ 250 ms.

**Latency SLOs (adopted from the trading-platform reference design):**
```
signal → risk decision      p95 ≤ 40 ms
risk decision → intent      p95 ≤ 30 ms
intent → broker submit ack  p95 ≤ 50 ms
fill → position update      p95 ≤ 1 s
dashboard SSE freshness      p95 ≤ 2 s
```

### 2.6 `dashboard/` — Operations UI

Full specification in **[`docs/DASHBOARD_SPEC.md`](DASHBOARD_SPEC.md)**. Summary of the nine panels:

| # | Panel | Purpose |
|---|---|---|
| 1 | **Command** | Trading mode ladder, kill switch, P&L headline, drawdown, exposure vs limits |
| 2 | **Positions** | Live positions, entry/stop/target, unrealized, broker-held stop status |
| 3 | **Orders** | Order lifecycle funnel, rejects, cancels, unknowns, latency percentiles |
| 4 | **Reconciliation** | Gap count, drift, last clean timestamp, FROZEN banner |
| 5 | **Strategies** | Per-strategy equity, kill-line distance, frequency drift, shadow experiments |
| 6 | **Promotions** | Agent proposals, gate-by-gate results, human decision, audit link |
| 7 | **Agent Runs** | Run history, step timeline, replay, determinism check |
| 8 | **Risk** | All limits with live headroom, breaker state, venue lock |
| 9 | **Research** | Backtest results, DSR/PBO, holdout usage, neighbour surface |

Stack: **Next.js (App Router) + SSE + Postgres (TimescaleDB) + Redis + D3/lightweight-charts + Tailwind + shadcn/ui**, deployed as a separate service from the executor. Backend exposes a read-only API over a read replica; the dashboard can **never** write to the trading path. Mutating controls (kill switch, mode change) go through a separate authenticated, audited endpoint.

> *Sources:* Agent Sentinel console (approvals/replay/ledger/kill-switch structure), Algorithmic Trading Dashboard reference, APEX observability taxonomy, SSE-vs-WebSocket analysis (SSE wins: server→client only, proxy-friendly, auto-reconnect).

---

## 3. The Autonomy Ladder

Each rung must pass its gate before promotion. **Promotion is never self-granted.**

| Rung | Name | Agent can | Gate to reach |
|---|---|---|---|
| 0 | `OBSERVE` | Read everything, propose nothing | — |
| 1 | `PAPER` | Propose + run backtests | Engine validated (planted/noise/look-ahead/golden-master all pass) |
| 2 | `ASSIST` | Propose live trades for approval | ≥60 paper days; signal agreement ≥99%; divergence report published |
| 3 | `AUTONOMOUS_LIMITED` | Execute within hard caps | ≥40 live-assisted days; zero unauthorized orders; kill-switch drill passed |
| 4 | `AUTONOMOUS_FULL` | Execute within mandate | Requires human re-approval. Never self-reachable (INV-8) |

Human involvement sits on **judgment** (promotion, rule change, mode change) — never on **mechanical clicks** in the execution path.

At rung 2, an approval has a **TTL** and a **market-drift re-check at execution time**: if the human takes 45 s and the price moved >0.5% in that window, the trade voids.

---

## 4. Execution Blueprint

### Phase 0 — Evidence ✅ COMPLETE
Transcripts (7 sources, 4,000+ timestamped lines), 150+ viewer comments, external tool verification, **four independent empirical replications**, course teardowns, this spec.

### Phase 1 — Reference engine + reference strategy
| # | Deliverable | Owner | Prereq | Done when |
|---|---|---|---|---|
| 1.1 | Data adapters (Yahoo/Coinbase/venue), point-in-time universe | agent | Phase 0 | No survivorship in index membership; splits/dividends verified |
| 1.2 | Event-driven engine, M2M equity, fill distribution | agent | 1.1 | Golden-master diff vs independent implementation passes |
| 1.3 | Research protocol: purged walk-forward, holdout ledger, DSR/PBO | agent | 1.2 | A known-false strategy is **rejected** by the protocol |
| 1.4 | Port S18 (QQQ/gold rotation) | agent | 1.3 | Reproduces **+28.4% CAGR / Sharpe 1.77 / −18.1% maxDD** on 2022+ within tolerance |
| 1.5 | Port S1 (confirmed-level S/R) with engineered risk | agent | 1.3 | Result published with DSR, PBO, neighbour surface, OOS split |
| 1.6 | **Reconcile S17a vs S17d** (exit-on-fake-break vs buy-on-fake-break) | agent | 1.3 | Disambiguating rule proposed and **empirically tested**, not assumed |
| 1.7 | Port S17a (three-scenario breakout confirmation) | agent | 1.6 | Encoded with explicit close convention, window, and supplied risk params; result published with denominator |
| 1.8 | `FINDINGS.md` — the five reproduced traps | agent | 1.4–1.7 | Closed-trade-DD trap, Turtle-OOS collapse, negative-base-rate calls, in-sample collapse, and denominator-free reporting all documented + lint rules active |

### Phase 2 — Deterministic risk & execution core (no LLM)
| # | Deliverable | Done when |
|---|---|---|
| 2.1 | Risk engine (all INV-7 limits as code constants) | Fault-injection suite passes |
| 2.2 | Order lifecycle + reconciliation service | 0 gaps across 1,000 simulated cycles |
| 2.3 | Executor as separate non-LLM process | Automated test proves the agent process **cannot** open a broker connection |
| 2.4 | Secrets via OS keychain / secret manager | Automated scan: zero secrets in agent-readable paths; rotation rehearsed |
| 2.5 | Immutable audit ledger | "Why did it do that?" answerable for any of 1,000 synthetic decisions |

### Phase 3 — Agent perimeter + promotion gates
| # | Deliverable | Done when |
|---|---|---|
| 3.1 | Hermes skill set (6 allowed tools only) | Negative test: agent asked explicitly to place a trade **cannot**; it proposes instead |
| 3.2 | Promotion gate service (Python assertions in `risk/`) | A proposal failing any gate returns *"nothing happened"*, zero side effects |
| 3.3 | Shadow-experiment path | Agent can accumulate evidence without deploying |
| 3.4 | Bounded learning loop | Before/after task-success measured on a held-out task set; zero P&L-driven rule changes below min sample |

### Phase 4 — Dashboard v1 (read-only)
| # | Deliverable | Done when |
|---|---|---|
| 4.1 | Backend read API + SSE stream | SSE freshness p95 ≤ 2 s |
| 4.2 | Panels 1–5 (Command, Positions, Orders, Reconciliation, Risk) | Kill-switch drill executed from the UI, audit record written |
| 4.3 | Panels 6–9 (Promotions, Agent Runs, Strategies, Research) | Every proposal visible with gate-by-gate outcome and human decision |
| 4.4 | Alert router (P0/P1/P2) | P0 (broker disconnect, recon gap, max-loss breach) pages immediately |

### Phase 5 — Paper trading + reconciliation *(the gate on everything)*
| # | Deliverable | Done when |
|---|---|---|
| 5.1 | ≥60 paper-trading days, daily reconciliation | Signal agreement ≥99% for ≥40 trading days |
| 5.2 | Realized-vs-assumed slippage attribution | ≥95% of days inside the assumed band |
| 5.3 | **Publish the divergence report** | Report exists and is reviewed |
| 5.4 | Fault-injection rehearsal | Broker down, stale data, partial fill, duplicate webhook, clock drift, kill-switch → every scenario yields a defined safe state |

### Phase 6 — Assisted live
Veto channel with TTL + drift re-check · small capital · weekly 10-minute review · Telegram heartbeat · zero unauthorized orders.

### Phase 7 — Bounded autonomous *(conditional)*
Only if 5 and 6 pass **and** are published. Hard-coded caps, monthly kill-switch drill, re-approval required for every escalation.

---

## 5. Success Metrics

**Strategy gate (Phase 1)**
- Deflated Sharpe > 0 with `N_trials` logged · PBO < 20%
- OOS Sharpe ≥ 0.5 across ≥3 purged walk-forward folds
- Max DD ≤ 1.5× expected, **mark-to-market**
- ≥5 parameter neighbours also clear
- Beats buy-and-hold on a risk-matched basis
- **Every published performance figure carries its denominator**: trade count, period, win rate, max drawdown, expectancy, and realized cost. A screenshot is not evidence — 0/17 videos in the Arabic corpus published a win rate or a drawdown while 8/17 published percentage returns, which is exactly the reporting pattern that makes an edge indistinguishable from no edge.

**System gate (Phase 5–6)**
- Live-vs-backtest signal agreement ≥ 99%
- Realized slippage inside assumed band ≥ 95% of days
- Reconciliation gap count = 0 · 100% of positions carry a broker-held stop
- Zero unauthorized orders · kill switch verified monthly

**Agent gate (Phase 3)**
- Agent **cannot** place an order (automated negative test passes)
- 100% of proposals carry an audit record with a gate outcome
- **Zero rule changes from below-minimum-sample evidence**
- Every "self-improvement" claim backed by before/after task-success on a held-out task set — never asserted

**Operational**
- Time-to-first-validated-signal ≤ 7 days
- Weekly human time ≤ 10 minutes

---

## 6. Risk Register

| # | Risk | L / I | Mitigation | Residual |
|---|---|---|---|---|
| R1 | Overfitting via search | High / High | INV-4, DSR/PBO, neighbour surface, one holdout | Med — irreducible |
| R2 | Drawdown metric artifact | High / High | INV-5 + lint rule | Low |
| R3 | LLM credential compromise | Med / Critical | INV-1/2, keychain, read-only agent tools, no broker egress | Low–Med |
| R4 | Unauthenticated ingress → unforced order | Med / Critical | No public HTTP ingress; HMAC + nonce + idempotency if any exists | Low |
| R5 | Agent grades its own homework | High / High | Grader external + versioned + human-promoted | Med |
| R6 | Regime dependence mistaken for edge | Med / High | Permanent benchmark lines; regime-conditional reporting | Med |
| R7 | Market-level limits misfire in crises | Med / Med | Strategy-relative bands + separate hard portfolio breaker | Low |
| R8 | First-bar unprotected | Med / High | Protective/OCO order submitted with entry; reconcile asserts within N sec | Low |
| R9 | Venue/counterparty risk (no-KYC perps, 125×) | Med / Critical | **Excluded by decision.** Regulated venue, IP whitelist, no withdrawals | Low |
| R10 | Naive costs/fills | High / Med | Per-asset fee+spread model, fill distribution, slippage attribution | Med |
| R11 | Live-vs-backtest divergence unmeasured | High / High | INV-6, continuous reconciliation, published report | Med until Phase 5 |
| R12 | Self-improvement = memory loop, not policy loop | High / Med | Scoped learning types, shadow experiments, gates | Med |
| R13 | Dashboard becomes a credential-exposure surface | Low / High | Dashboard reads a **read replica**; no API keys client-side; no public deployment; IP/Caddy lock | Low |
| R14 | Platform supply-chain (plugins, MCP servers) | Med / Med | No third-party MCP in the trading path; pin versions; read-only allowlist | Low |

---

## 7. Explicitly Rejected

Rejected from the source material, with reasons — recorded so they don't creep back:

| Rejected | Source | Why |
|---|---|---|
| TradingView→ngrok→Flask→LLM→exchange webhook | Alex Carter M16 | No auth, no HMAC, no idempotency, LLM in the hot path, laptop-dependent |
| LLM in position-sizing loop | Alex Carter M17 | Non-deterministic sizing; must be deterministic code |
| EVM private key in plaintext `.env`, copied to VPS + Railway | Alex Carter M18 | Single largest unmitigated risk across all seven sources |
| Live trade-enabled API keys to a third-party SaaS | Alex Carter M19 | Custody risk, undeclared |
| "Rank dozens of variants by profit factor" | Alex Carter M11 | Multiple-comparisons false-positive factory |
| Auto-optimize parameters on the live chart | Alex Carter M11 | Same |
| Guardian approve/reject on the *cheapest* model | Alex Carter M23 | Cheapest model on the safety-critical path |
| 125× leverage on a no-KYC venue with deposit bonus | Alex Carter M6 | Counterparty risk unpriced |
| `bypass permissions` for a live-credentialed bot | AI Pathways | Unrestricted shell next to trading keys |
| "Claude will never read `.env`" as a control | AI Pathways | A policy statement to an LLM, not a technical control |
| Using the lock test as a repeated filter | AI Pathways | Holdout consumed repeatedly ⇒ it is training data |
| Point predictions as a strategy | EA Trading (211 vids) | −16pp vs a naive baseline |
| Signal/copy-bot funnel with undisclosed performance | EA Trading | No disclosure of strategy, risk, or track record |
| Contests whose prizes are the presenter's own products | EA Trading 99-min | Attention monetisation presented as education |
| "There is no best analysis science" | EA Trading 142-min | Makes the system unfalsifiable by construction |
| **A copy-trading service sold to the same audience the channel warns about copy trading** | EA Trading (17 videos) | `vsqbB672zxk` [00:07:27] *"ما انصحش"* vs. `EAWorldBots` in 6/17 descriptions and a copy system in 11/17 |
| **A funded-account *test-passing* service bundled with the purchase** | EA Trading `_zJwDbd82AU` [00:05:33] | A product whose purpose is helping a third party pass a risk evaluation |
| **"I guarantee a rebound will happen"** | EA Trading `H7y1RHkJ3CM` [00:08:06] | An explicit guarantee on a market outcome |
| **"We had zero losses"** / "I called every top and bottom for 6 years" | EA Trading | Unfalsifiable without a timestamped call log — and none is ever published |
| **A referral code pitched as a profit improvement** ("50% → 53%") | EA Trading `vsqbB672zxk` [00:02:15] | Unsourced; in the corpus's most-viewed video |
| **"Time analysis" taught in an episode titled "Mastery of Time Analysis"** | EA Trading `7J56h-gT7z0` [00:17:12] | The promised methodology is deferred to a later episode — 206k views |
| **"Bitcoin to a million dollars" — a title for a video that never says a million** | EA Trading `vcSCfO6801I` | Title/body mismatch; expectation-setting by headline |

---

## 8. Open Questions — Answer Before Phase 2

These change the build and **must be confirmed**:

1. **Capital and jurisdiction** — determines venue (regulated broker vs crypto exchange) and whether crypto perps are in scope at all.
2. **Target market & timeframe** — S18 (daily ETF rotation) and S1 (level reaction) are daily-scale; the EA Trading scalping content is 15-minute-scale and needs a different cost model.
3. **Live trading at all?** — recommendation: Phases 1–6 only (paper + small assisted capital).
4. **Which reference strategy first?** — S18 (verified, reproducible) or S1 (most novel, from the new source)?
5. **Dashboard hosting** — local-only vs VPS. Recommendation: **local/VPS with IP lock**, never public.
6. **Single-user or multi-tenant?** — the auth and audit design differ materially.

---

## 9. Document Map

| File | Contents |
|---|---|
| `docs/MASTER_SPEC.md` | This document |
| `docs/SOURCE_ANALYSIS.md` | Full teardown of all 7 sources with evidence, quotes, timestamps |
| `docs/DASHBOARD_SPEC.md` | Complete dashboard requirements: panels, schemas, alerts, stack |
| `research/` | The verification scripts and their reproducible outputs |
| `research/FINDINGS.md` | The four reproduced traps, with the code that reproduces them |
