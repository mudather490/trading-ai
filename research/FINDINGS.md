# FINDINGS — Reproducible Empirical Results

**Date:** 2026-11-11
**Every number below is reproducible** by running the scripts in this directory against public market data.

Scripts: `bt_turtle.py` (Turtle 55/20) · `bt_core_qqq_gld.py` (AI Pathways core) · `ea_score_predictions.py` (EA Trading calls) · `ea_sr_rule.py` (EA Trading level rule)

---

## F-1 · The TradingView max-drawdown trap — ❌ CRITICAL

**Source claim (Mind Math Money, 47:53–48:08):** Turtle breakout 55/20 on BTC daily → **+729% profit, 45% win rate, profit factor 7.6, max drawdown 3.29%**.

**Our reproduction** (`bt_turtle.py`, Coinbase BTC-USD daily, 2015-07-20 → 2026-11-11, 4,133 bars, 1% risk, 10bps fee + 5bps slippage):

| Metric | Their claim | Ours |
|---|---|---|
| Win rate | 45% | **44.1%** ✅ |
| Profit factor | 7.6 | **6.15** ≈ |
| Total return | +729% | +174.6% over 11.1y (CAGR 9.48%) |
| **Max drawdown** | **3.29%** | **−18.91%** mark-to-market |
| Max drawdown, closed-trade-only | — | **−3.01%** ← *matches their figure* |

### The trap
TradingView's strategy-tester Max Drawdown is computed on **closed-trade equity only**. The presenter *states this on camera* — *"This is not the max from the highest to lowest point. This is only of closed trades"* [48:08] — and then presents **3.29%** as the course's headline risk number.

**On mark-to-market equity the identical trades drew down 18.91% — a 6× understatement of the real risk.**

> **Rule adopted (INV-5):** mark-to-market is the only reporting basis. Closed-trade drawdown may be shown but never headlined. A lint rule rejects any risk summary sourced from a closed-trade figure.

### And the strategy dies out-of-sample
| Window | Result |
|---|---|
| Full sample | Sharpe 0.76, 34 trades |
| **OOS 2021+ (params fixed 55/20)** | **+8.6% over 5.7y, CAGR 1.46%, Sharpe 0.30** |
| OOS with IS-selected params | Sharpe 0.60 |
| Buy & hold BTC, same window | **+26,200%**, Sharpe 1.08 |

**Concentration:** top-3 trades = **69.3%** of total P&L; top-1 trade = **43.5%**.

**Verdict:** reject as a live candidate. It is a 34-trade sample with three trades carrying the result, beaten by holding.

---

## F-2 · AI Pathways' core rotation — ✅ REPRODUCES

**Source claim (AI Pathways, 27:41–28:02):** *"If QQQ is above its 200-day average, hold QQQ. If it's not, then hold the gold."* → core alone 24.8%/yr with 11.2% max DD on the locked 2025+ test; full account ≈28.7%/yr, 13% DD.

**Our reproduction** (`bt_core_qqq_gld.py`, Yahoo adj-close, QQQ + GLD):

| Window | Their claim | Ours |
|---|---|---|
| LOCK 2025+ | 24.8%/yr, 11.2% DD | **+29.63% CAGR, −12.25% maxDD, Sharpe 1.69** ✅ |
| FULL 2022+ | ~28.7–29%/yr | **+28.41% CAGR, −18.10% maxDD, Sharpe 1.77** ✅ |
| B&H QQQ (2022+) | — | +17.46% CAGR, −29.56% DD |
| B&H GLD (2022+) | — | +16.84% CAGR, −28.26% DD |

**Parameter-neighbour robustness (his best test — it passes):**
| SMA | Total | MaxDD | Sharpe |
|---|---|---|---|
| 150 | +59.7% | −20.8% | 1.59 |
| 175 | +55.6% | −17.9% | 1.53 |
| **200** | **+61.8%** | **−12.3%** | **1.69** |
| 225 | +70.3% | −11.9% | 1.85 |
| 250 | +74.0% | −11.2% | 1.96 |

**Cost sensitivity:** negligible — 16 switches in 4.7 years; at 20bps/side CAGR falls only 28.41% → 27.54%.

### The caveat the source underplays
- The rotation holds **gold 23.8% of days**; **beta vs QQQ = 0.54**.
- Its +24.1%/yr alpha vs QQQ is largely *gold's* diversification (B&H GLD alone: +21.0%/yr alpha).
- **Regime-dependent year to year:**

| Year | Rotation | B&H GLD | B&H QQQ |
|---|---|---|---|
| 2022 | −2.3% | −5.2% | −18.3% |
| 2023 | +59.6% | +11.8% | +55.9% |
| 2024 | +27.7% | +27.0% | +27.7% |
| 2025 | +41.4% | +61.5% | +21.0% |
| 2026 | +14.3% | −7.3% | +10.4% |

**Verdict:** this is the **only verified headline across all seven sources**. Adopt as **reference strategy S18** — the strategy that proves our engine is correct — while keeping the gold and SPY benchmark lines permanently on the dashboard. Never present it as "found an edge"; present it as "reproduced a benchmark, and here is what it actually depends on."

---

## F-3 · EA Trading's market calls are worse than guessing — ❌

**Method** (`ea_score_predictions.py`): 25 dated, directional claims extracted from the videos. A claim is a HIT if price moved ≥ threshold in the predicted direction within the stated horizon. Baseline = **naive continuation**: predict whichever way the market actually went — i.e. what someone who merely looked at the chart would have said.

| Threshold | EA Trading | Naive baseline | **Excess** |
|---|---|---|---|
| ±10% | **72.0%** (18/25) | **88.0%** (22/25) | **−16.0 pp** |
| ±25% | 56.0% (14/25) | 68.0% (17/25) | **−12.0 pp** |
| ±50% | 28.0% (7/25) | 40.0% (10/25) | **−12.0 pp** |

- **84–88% of calls were directionally identical to just following the tape.**
- Wilson 95% CI on the raw 72% rate: **[52%, 86%]** — overlapping a coin flip.
- Buy & hold return averaged over the same windows: **+51.6%**.

### The 2026 macro call (video 2025-12-28)
> *"manipulation and decline… the bottom forms in the last quarter of 2026… BTC from 125,000 → 58,000 → 35,000–40,000… gold, silver AND the US market all fall"*

| Asset | Call | Actual 2025-12-28 → 2026-11-11 | Result |
|---|---|---|---|
| BTC | down | 87,836 → 73,551 (−16.3%), low **58,559** | ✅ HIT. Reached the 58,000 band; **never reached 35,000–40,000** |
| GOLD | down | 4,344 → 4,056 (−6.6%) | ✅ HIT |
| SILVER | down | 70 → 53 (−24.2%) | ✅ HIT |
| **SPX** | **down** | **6,906 → 7,521 (+8.9%)** | ❌ **MISS** |

### And the "final target"
From 2023-10-24 — *"35,000 is the **final** target"* — BTC reached **106,141**, exceeding that ceiling by **213%**.

**Verdict:** reject PL1 (211 videos) as a strategy source. Its directional skill is *negative* against the simplest benchmark.

---

## F-4 · EA Trading's "confirmed level" rule — the idea is real, the strategy is not

This is the single most valuable extract from the Arabic course:

> *"A level is real only if price **reacted** from it — dropped from it forcefully."* [39:42]
> *"I know it's a support zone because I wait for price to bounce from it and rise, and then it comes back to it a second time."* [37:32]
> *"The best thing to draw it is at the **candle body**… now you're a professional, you draw a **line**, not a zone."* [44:26, 47:55]

The course supplies **no** stop, no size, no target, no cost model. We encoded it with engineered risk (≥N prior sharp rejections, ATR stop, R-multiple target, 1% risk) and tested (`ea_sr_rule.py`, BTC-USD daily 2016→2026, 3,968 bars).

### Test 1 — the rule with engineered risk
```
S/R confirmed-level system (min_rej=2, stop 1.5ATR, target 2R, 1% risk, 15bps round trip)
   total=+26.5%   CAGR=+2.22%   maxDD=-8.78%   Sharpe=0.68
   trades=70      winrate=72.9%  PF=1.75
```

### Test 2 — the level-identification rule DOES add value
| Prior sharp rejections required | Total | Sharpe | Trades |
|---|---|---|---|
| 0 (any level) | +4.7% | **0.25** | 20 |
| **1** | +29.0% | **0.74** | 71 |
| **2** | +26.5% | **0.68** | 70 |
| 3 | +21.6% | 0.57 | 70 |

**Requiring ≥1 prior rejection roughly triples the Sharpe (0.25 → 0.74).** The course's central identification insight is empirically real. This is the one substantive thing all seven sources got right.

### Test 3 — "fresh trade only" filter: no measurable effect
`fresh_only=True` → +26.5%, Sharpe 0.68 · `fresh_only=False` → +26.6%, Sharpe 0.68. **The Ichimoku video's filter adds nothing measurable.**

### Test 4 — cost sensitivity
| Fee | Slippage | Total | Sharpe | PF |
|---|---|---|---|---|
| 0bps | 0bps | +29.2% | 0.74 | 1.84 |
| 10bps | 5bps | +26.5% | 0.68 | 1.75 |
| 40bps | 20bps | +18.6% | 0.50 | 1.52 |

Costs erode it but do not kill it — at daily-bar frequency, which is the only frequency this rule was testable at.

### Test 5 — the baseline
```
BUY & HOLD BTC, same window: total=+16,801%  CAGR=+60.37%  maxDD=-83.80%  Sharpe=1.04
```
The S/R system earns **+26.5%** against **+16,801%**. It is beaten by a factor of ~600.

### Test 6 — out-of-sample: the edge does not survive
| | Result |
|---|---|
| Selected on 2017–2021 (Sharpe 1.81): min_rej=1, stop 3.0ATR, target 1.5R | — |
| **OOS 2022+ with IS-selected params** | **+2.8% total, CAGR 0.58%, Sharpe 0.26**, 15 trades |
| **OOS 2022+ with default params** | **−0.9% total, CAGR −0.19%, Sharpe −0.04**, 25 trades |
| OOS buy & hold BTC | +54.3%, Sharpe 0.43 |

**The in-sample Sharpe of 0.74–0.68 collapses to 0.26 (best case) and −0.04 (default) out of sample.**

### Verdict on F-4
> **The idea is real; the strategy is not.**
>
> The course's level-identification protocol is genuinely valuable and empirically validated as an *input-quality filter* (Sharpe 0.25 → 0.74). But a rule that only wins 26.5% in a decade, loses money out-of-sample, and is beaten 600× by holding is **not a live candidate**.
>
> **Action:** adopt S1 as a **feature/filter inside the reference engine** — use "confirmed level" to improve *entry quality* within a strategy that has a benchmark-relative edge — rather than as a standalone trading system. And record that the source course's own presentation (a full trading system) does not survive testing.

---

## F-5 · The six dated contradictions in EA Trading's own corpus

The corpus is self-contradicting on a **verifiable timeline**. This matters more than any single call: it shows that even when a call is right, there is no stable rule behind it.

| Date | Video | Claim |
|---|---|---|
| 2023-10-24 | `vcSCfO6801I` | $35,000 is the **final target** — exit the market there |
| 2023-10-28 | `Z1CXfqGZyeY` | *"الهدف الاخير… 35000… في حال ان وصل له انا هخرج من السوق"* |
| 2023-11-03 | `vUcvYQj1e_E` | 35,150 is now the **resistance** whose break was faked — *"على وشك الانهيار"* |
| 2023-11-09 | `p0OjQGl1LUE` | *"لو رجع السعر فوق المقاومه بتاعه 35150 هتبقى مؤشر ايجابي… وهندخل السوق مره اخرى"* |

**The same level, 16 days apart: "sell here, the cycle ends" → "reclaim this to re-enter."** A level cannot be both.

And the 2026 arc:
| Date | Claim |
|---|---|
| 2025-12-28 | *"القاع في نهايات الربع الاخير من 2026"* — bottom zone $35–40k |
| 2026-01-31 | *"2026 هي اعاده تشكيل القاع من جديد"* — same $35–40k |
| **2026-07-04** | **BTC $86,000 — *"بداية انطلاق جديد"* (new bull launch)** |
| 2026-11-07 | *"انهيار تاريخي… هدف 64000"* |

**$35–40k never printed in 2026. The "new launch" call lasted about four months.**

### The impossible pair — and it blocks encoding

| Source | Event | Action |
|---|---|---|
| `owFAEa3jhKM` [00:05:27] | price crossing a level in a downtrend | **BUY** — *"selling is amateur, buying is professional"* |
| `vUcvYQj1e_E` [00:06:15] | fake break of a resistance | **EXIT** — *"بنسي من السوق"* |
| `rASC1NalVbw` [00:12:41] | fake break of a Fib level | **BUY** — demonstrated twice, +12% each |

**The same visual event maps to opposite actions with no reconciliation.** A deterministic system cannot encode all three. The candidate disambiguator (from the corpus's own structure): R4 (buy the Fib false-break) applies only at **Fib levels on a higher timeframe** than the resistance in R1 (exit on the range false-break).

### The metric that settles it
Across all 17 Arabic transcripts:

| Metric | Count |
|---|---|
| **Win rate stated as a number** | **0/17** |
| **Drawdown / max-DD / risk-of-ruin stated** | **0/17** |
| **R:R multiple stated** | **0/17** |
| **Numeric stop-loss for a recommended trade** | **0/17** |
| Risk-per-trade as % of account | **0/17** |
| Any cost mentioned at all | 1/17 — and only to sell the referral code |
| Broker-verified / audited track record | **0/17** |
| Performance figure given (always a screenshot) | 8/17 |

**Reporting winners without a denominator is indistinguishable from having no edge.** Two of the source channels do exactly this.

---

## F-6 · The four traps, and what each one costs you

| # | Trap | Cost | Rule that prevents it |
|---|---|---|---|
| **F-1** | Closed-trade-only drawdown | Understates real drawdown **6×** (3.29% vs 18.91%) | INV-5 — mark-to-market only; lint rule on risk summaries |
| **F-2** | A holdout consumed as a filter | Turns OOS into training; every "verified" edge becomes untestable | INV-4 — one holdout, consumed once, `N_trials` logged, DSR/PBO reported |
| **F-3** | Judging a forecaster without a base rate | 72% hit rate reads as skill when the naive baseline is 88% | Always publish the naive baseline next to any hit rate |
| **F-4** | In-sample edge never re-tested | Sharpe 0.68 in-sample → −0.04 out-of-sample | Purged walk-forward + single holdout; promotion bar requires OOS Sharpe ≥ 0.5 across ≥3 folds |
| **F-5** | Winners reported without a denominator | 0/17 win rates, 0/17 drawdowns — while 8/17 publish % returns | Every performance claim must publish its denominator, its period, and its drawdown. Screenshots are not evidence |

---

## F-7 · What the evidence says we should build

1. **The reference strategy is S18 (QQQ/gold rotation), not any of the "signal" strategies.** It is the only one that reproduces.
2. **S1 (confirmed level) becomes a filter, not a system.** Its identification rule triples Sharpe — use that where it helps, not as a standalone.
3. **The first new candidate to build is S17a — the three-scenario breakout confirmation.** It is the only complete decision tree in the Arabic corpus, with an observable trigger and a defined wait period. It also arrives with its own disambiguator problem (S17a vs S17d) that must be resolved *before* code is written.
4. **Never accept a drawdown figure without knowing its basis.** Ask: closed-trade or mark-to-market? Realized or expected?
5. **Never accept a hit rate without the naive baseline.** If the baseline matches, there is no signal.
6. **Never accept a performance claim without its denominator.** 0/17 win rates across the Arabic corpus — every screenshot is a single cherry-picked sample.
7. **Every claim in the course material is a hypothesis.** None of the seven sources ships a verified edge, and two of them (Mind Math Money, EA Trading) ship numbers that are materially misleading or self-contradicting on a verifiable timeline.

---

## F-8 · Recommendations for the two best new candidates

### S17a — Three-scenario breakout confirmation (build first)

```
INPUT   R_BTC  = key BTC resistance level
        R_ALT  = per-coin resistance of the alt basket
        W      = confirmation window (2–3 days)
        CLOSE  = daily close (must be specified: UTC? venue?)

S1  both break AND both hold            -> LONG
S2  BTC breaks, alts do not             -> NO ENTRY; wait W days; then re-evaluate
S3  both break, both close back below   -> FAKE BREAKOUT: EXIT ALL, no new entry
```
**Must be supplied by us:** alt-basket definition · per-coin resistance levels · close convention · window length · entry fill · stop · target · position size.
**Why first:** it is the only rule in the corpus that is a *procedure* rather than a *description*.

### S17d — Fib false-break (blocked until reconciled)

```
IF price closes BELOW a Fib support level L (0.23 / 0.50 / 0.618)
   AND price closes back ABOVE L within N candles
THEN LONG at L ; STOP just below L
```
**Blocked by:** S17a Scenario 3 says exit on a fake break; S17d says buy it.
**Resolution required before coding:** scope S17d to Fib levels on a higher timeframe than S17a's range resistance, and verify that scope empirically before adopting it.

### S17e — Gann-style angle cycle (reconstruct from frames before use)

```
anchor   = swing extreme (angle 0)
cycle end= the 360° level
shape from the FIRST pullback angle:
   180° -> binary    (secondary pullbacks at 45°, 90°, 135°)
   120° -> ternary   (secondary pullback at 60° only)
    90° -> quaternary(secondary pullback at 45°)
spacing  = 1, 10, 100, 1000, 10000
```
**Blocked by:** ASR corruption — the indicator name and every price must be re-read off the video frames. The spacing-multiplier arithmetic is shown by only one worked example and is not derivable from the text alone.
**Note:** the author himself disclaims it — *"القاعده دي ما تعتمدش عليها بشكل ثابت هي مجرد سلوك"* [00:01:09]. If we build it, we are testing his claim, not accepting it.
