# Vendored Third-Party Code

This directory contains code copied from external open-source projects. Each vendored
component keeps its own upstream `LICENSE` file, unmodified.

---

## `day-trader-skill`

| Field | Value |
|---|---|
| **Upstream** | https://github.com/Jakeschincariol/day-trader-skill |
| **Author** | Jake Schincariol (https://opusjake.ai) |
| **License** | MIT (see `day-trader-skill/LICENSE`) |
| **Vendored commit** | `3d0f4ef6986f4984b7c6bd46f7ff4f87744c3abd` |
| **Upstream date** | 2026-10-20T09:17:50-05:00 |
| **Retrieved** | 2026-11-11 |

### What we take, and why

| Upstream module | Our use | Changes made by us |
|---|---|---|
| `trader-timemachine/timemachine.py` | Bootstrap path simulation → ticket confidence ("odds band") | **Rewritten** to read bars from our own Supabase bar store instead of Yahoo; verdict thresholds replaced with a statistical test against break-even with a bootstrap CI |
| `trader-news/news.py` | SEC EDGAR Atom feed + 8-K item-code → event taxonomy | **Adapted**: extracted into `services/live-worker/news/sec_edgar.py`; entity resolution and event typing kept deterministic; LLM handles only the residual |
| `trader-analyst/facts.py` | SEC XBRL companyfacts → fundamentals | **Adapted**: extracted into `services/research-worker/fundamentals/` |
| `trader-budget/`, `trader-risk/` | Risk config vocabulary + refusal semantics | **Schema adopted only.** The maths is reimplemented in SQL (`supabase/functions/`) so there is exactly one implementation of position sizing |
| `tests/` + fixtures | Test discipline: real-data fixtures, offline, deterministic | Kept as-is, run as our first CI gate |

### What we deliberately do NOT use

| Upstream module | Reason |
|---|---|
| `trader-broker/` | LLM-mediated order placement with no idempotency key and no reconciliation. Replaced by our own executor design (Phase 5). |
| `trader-scanner/scan.py` universe scan (~5,900 symbols) | Our universe is 12 symbols. The *setup logic* (opening-range breakout) is reused as a strategy template. |
| Yahoo Finance as the data spine | Unofficial endpoint, rate-limits, personal-use licence. Our spine is Alpaca + Binance + OANDA; Yahoo is research-backfill only. |
| The Claude Code skill/plugin harness | It is an LLM-driven CLI loop. We have a deployed app (web + worker + database). We vendor **algorithms**, not the agent harness. |

### MIT Notice

```
Copyright (c) 2026 Jake Schincariol

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Updating the vendored copy

```bash
cd packages/vendor/day-trader-skill
git clone --depth 1 https://github.com/Jakeschincariol/day-trader-skill.git /tmp/dts
# diff /tmp/dts against this directory, then update the commit SHA above.
python -m unittest discover -s tests -v   # must be green before merging
```
