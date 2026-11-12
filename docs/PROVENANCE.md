# Provenance

This file records every idea this project has taken from a third-party
source, where it came from, and what was done about the licence.

The rule is simple: **no third-party source code is copied into this
repository.** Where an idea was useful, it was re-implemented from scratch,
in this project's own style, with its own tests and its own naming.

---

## FinceptTerminal

| | |
|---|---|
| Source | `https://github.com/Fincept-Corporation/FinceptTerminal` |
| Licence | **AGPL-3.0** (`LICENSE`, verified 2026-11-12) |
| Nature | C++/Qt desktop terminal: market data connectors, broker adapters, a QuantConnect-style backtest engine, a generic agent framework |
| Why we looked | It is the most visible open-source "professional trading terminal" agent, and the agent harness is the part worth studying |

### The licence problem

AGPL-3.0 says: if you run this code as a network service, you must publish
your source code. Copying any part of FinceptTerminal into this app would
therefore oblige us to publish the whole command center.

**We do not copy Fincept code.** We read the public repository to understand
the shape of its agent harness and re-implemented the useful parts from
scratch. If a future decision ever requires their code, the only two clean
options are: open-source this entire project under AGPL, or buy a commercial
licence from Fincept. There is no third option.

### What was taken as an idea, and what was built

| Fincept idea (file in their repo) | What we built | Where | Status |
|---|---|---|---|
| `agentic/budget.py` — four independent ceilings (tokens, cost, wall clock, steps) that stop a task instead of overspending | `Budget` dataclass with the same four ceilings, our own API, no provider pricing table | `services/live-worker/worker/ai/budget.py` | Done, tested |
| `agentic/runner.py` — the agent loop refuses to continue once a budget breaks | The sidecar checks the budget **before** the request leaves the machine and returns a failed, logged result | `services/live-worker/worker/ai/sidecar.py` | Done, tested |
| `modules/guardrails_module.py` — prompt-injection patterns over untrusted input | `detect_prompt_injection`, `sanitize_untrusted`, `wrap_untrusted`; our own pattern list, our own fencing | `services/live-worker/worker/ai/guardrails.py` | Done, tested |
| `modules/guardrails_module.py` — trading-compliance checks on model output | `find_outcome_promises`: the app advises, it never guarantees | `services/live-worker/worker/ai/guardrails.py` | Done, tested |
| `services/news/NewsClusterService.cpp` — headline clustering so one story from five outlets is one event | `cluster_headlines` using word-overlap similarity, deterministic, no model | `services/live-worker/worker/news/cluster.py` | Done, tested |
| `.github/scripts/arch_ratchet.py` — architecture rules that fail CI | Planned (Sprint D) | — | Pending |

### What was deliberately NOT taken

| Fincept component | Why not |
|---|---|
| The C++/Qt terminal, dashboard, node editor, voice, i18n, packaging | A desktop app is not this project. It is a Vercel web app plus a worker. |
| 250+ data-connector scripts | We need three venues and a handful of public sources. A connector library is a maintenance liability, not an edge. |
| `trading/brokers/*` — 20+ broker adapters with order placement | This app is ticket-only. No broker write credentials exist, by design. |
| `strategies/fincept_engine/*` — a QuantConnect-compatible engine | Our engine is validated against S18 and an independent implementation. Replacing it would invalidate the golden master. |
| `ai_quant_lab/*` — Qlib feature store, meta-learning, RL | Heavy dependencies for a 12-symbol universe. vectorbt and Optuna are the right size. |
| `vision_quant/*` — a chart-image model | Needs a GPU and labelled data we do not have. |
| `agents/deepagents/*` — a free-form tool-calling agent loop | A live agent with tool access is an attack surface, and it cannot be grounded. The live sidecar gets a richer evidence pack instead of tools. |
| `output_models.py` — `TradeSignal` and friends | Weaker than our own ticket contract: no evidence ids, no invalidation, and `BUY`/`SELL` semantics that would fight our `WAIT`/`NO_TRADE` success states. |
| Their LLM provider catalogue and hosted research desk | Our two free providers are enough. Their paid "Enterprise" and "Quantcept" tiers are not in the AGPL repository at all. |

### Third-party data that is not their code

* **Economic calendars.** A weekly economic-calendar feed (e.g. the
  ForexFactory JSON export) is *data*, not Fincept code, and carries its own
  terms of use. For personal use a free feed is fine; a commercial product
  needs a licensed calendar. Not yet integrated — see the Phase 1 backlog.

### Rule for future work

1. Read the idea; write the code yourself.
2. Record it in this file: source, commit, date, what you built.
3. Never copy code, comments, names, constants or file structure.
4. Run a licence scan in CI so an accidental import is caught by the build and
   not by a lawyer.
