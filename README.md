# AI Trading Agent

**A research-first, risk-bounded AI trading agent** — built by analysing seven trading-education sources, independently reproducing their testable claims against market data, and rejecting everything that did not survive.

**Status:** Specification complete. Ready for Phase 1 build.
**Date:** 2026-11-11

---

## Why this project exists

Seven YouTube courses were analysed in full (4 English AI-trading courses, 3 Arabic EA Trading playlists totalling 226 videos). Every headline claim that could be tested was **independently reproduced against market data**. The results:

| Finding | Evidence |
|---|---|
| A course's **headline drawdown figure understated real drawdown 6×** | Turtle 55/20: claimed 3.29%, actual mark-to-market **−18.91%** |
| Exactly **one** headline across all seven sources reproduces | AI Pathways' QQQ/gold rotation: claimed 24.8%/yr, reproduced **+29.63% CAGR, Sharpe 1.69** |
| The most-viewed course's directional calls are **worse than a naive baseline** | EA Trading: 72% hit rate vs **88%** for "just follow the tape" → **−16pp** |
| The best *idea* in the new Arabic course is real but its *strategy* is not | "Confirmed level" rule: Sharpe 0.25 → **0.74** in-sample, → **−0.04** out-of-sample |
| Two courses ship materially misleading numbers | Mind Math Money (drawdown metric), EA Trading (+$48,000 vs +$5,500 in the same video) |

**The conclusion that shapes this build:** none of these sources puts the LLM in the right place. Some let it size positions in the hot path, some let it grade its own homework, and most teach entry rules with no stop, no size, and no cost model.

> **This system inverts it: the LLM never touches the execution path.**
> Not "prompted not to trade" — the order-placement and position-size parameters **do not exist** in the agent's tool schema.

---

## Documents

| Document | What it contains |
|---|---|
| **[`docs/MASTER_SPEC.md`](docs/MASTER_SPEC.md)** | **Start here.** Architecture, the 8 invariants, component specs, the autonomy ladder, the 7-phase build plan, risk register, success metrics, and 19 explicitly-rejected patterns with reasons. |
| **[`docs/DASHBOARD_SPEC.md`](docs/DASHBOARD_SPEC.md)** | Complete dashboard spec: 9 panels, SQL schema, alert taxonomy (P0/P1/P2), exact metric definitions, stack, build order, and 8 rejected anti-patterns. |
| **[`docs/SOURCE_ANALYSIS.md`](docs/SOURCE_ANALYSIS.md)** | Full teardown of all 7 sources with quotes, timestamps, empirical replications, and what we take vs. reject. |
| **[`research/FINDINGS.md`](research/FINDINGS.md)** | The four reproduced traps with reproducible numbers. |
| `research/*.py` | The verification scripts. Run them to reproduce every number. |

---

## The 8 Invariants

These are enforced in code the agent cannot write to. Each has a test.

1. **Capability removal** — no order tool, no size parameter, no mode parameter in the agent's schema.
2. **Separate decisioning from execution** — the executor is a non-LLM process with its own credentials and its own network policy.
3. **Promotion gates in code** — Python assertions, not prompts. Failure = *"nothing happened"*, never *"something subtly different happened."*
4. **One holdout, consumed once** — `N_trials` logged; Deflated Sharpe and PBO reported beside raw Sharpe.
5. **Mark-to-market only** — closed-trade drawdown may be shown, never headlined.
6. **Reconciliation is continuous** — broker ≡ internal ≡ expected, every cycle. Mismatch ⇒ freeze.
7. **Risk limits can only subtract** — there is no code path that opens an unprotected position.
8. **Self-revision may tighten, never promote** — it can never promote itself to autonomous mode or deactivate monitoring.

---

## Repository layout (target)

```
trading-ai/
├── README.md
├── docs/
│   ├── MASTER_SPEC.md          # architecture + build plan
│   ├── DASHBOARD_SPEC.md       # UI requirements
│   └── SOURCE_ANALYSIS.md      # evidence from all 7 sources
├── research/                   # verification scripts + FINDINGS.md
│   ├── FINDINGS.md
│   ├── bt_turtle.py
│   ├── bt_core_qqq_gld.py
│   ├── ea_score_predictions.py
│   ├── ea_sr_rule.py
│   └── out_*.txt
├── engine/                     # Phase 1 — reference backtest engine
│   ├── data/                   # adapters, quality, point-in-time universe
│   ├── engine/                 # simulator, fills, metrics
│   └── research/               # protocol, multiplicity, robustness, reports
├── strategies/                 # S1..S20 candidate specs
├── risk/                       # deterministic limits (read-only to the agent)
├── agent/                      # Hermes perimeter — 6 tools, no order tools
├── broker/                     # executor (non-LLM) — owns credentials
├── dashboard/                  # Next.js + SSE ops UI
└── broker-executor/            # (Phase 2) separate process
```

---

## Build phases

| Phase | Deliverable | Gate |
|---|---|---|
| **0** | Evidence base — 7 sources, 4 replications, specs | ✅ **complete** |
| **1** | Reference engine + reference strategy S18 | Engine rejects a known-false strategy; S18 reproduces +28.4% CAGR / Sharpe 1.77 |
| **2** | Deterministic risk + executor (no LLM) | Agent process provably cannot open a broker connection |
| **3** | Agent perimeter + promotion gates | Agent asked to place a trade **cannot**; it proposes instead |
| **4** | Dashboard v1 (read-only) | Kill-switch drill works from the UI; audit written |
| **5** | Paper trading + reconciliation | Signal agreement ≥99% for ≥40 days; divergence report published |
| **6** | Assisted live | Zero unauthorized orders |
| **7** | Bounded autonomous *(conditional)* | Requires human re-approval. Never self-reachable |

---

## Environment notes

- **Hermes Agent is already installed** at `%LOCALAPPDATA%\hermes\hermes-agent` — Nous Research's self-improving agent, with cron, a Telegram gateway, and a `finance` skill bundle (polymarket, stocks_client, sec-filings). This is the runtime for Phase 3. It is also the **safer** of the two agent runtimes the source courses push (no 360k-star plugin ecosystem, no ClawHub supply-chain surface).
- **Node v22.23.1**, **Python 3.13** with pandas 3.0.5 / numpy 2.4.3, **ffmpeg 8.1.2**, `gh` CLI, `mcporter` (Exa search), `yt-dlp` (installed for this analysis).
- No `agent-reach` CLI on PATH; `yt-dlp` was installed via pip to satisfy it.

---

## Before Phase 2 begins, answer these

1. **Capital and jurisdiction** → determines venue (regulated broker vs crypto exchange) and whether crypto perps are in scope.
2. **Target market and timeframe** → S18 is daily-scale; the Arabic scalping content is 15-minute-scale and needs a different cost model.
3. **Live trading at all?** → recommendation: Phases 1–6 only.
4. **Which reference strategy first?** → S18 (verified) or S1 (most novel)?
5. **Dashboard hosting** → recommendation: local/VPS with IP lock, never public.
6. **Single-user or multi-tenant?** → materially changes auth and audit design.

---

## A note on scope

This is engineering, not financial advice. Trading carries loss risk; no claim in this repository is a return promise. The seven source courses make many such promises, and the empirical work here exists partly to demonstrate how few of them survive contact with data.

The most useful thing we took from all of them is one sentence from the 142-minute masterclass, and it is not about trading:

> *"You must have something that says 'stop'… otherwise don't step outside your rules."* — [26:08]

That single sentence — the only risk rule in 142 minutes — is exactly what all seven sources under-deliver, and exactly what this system is built around.
