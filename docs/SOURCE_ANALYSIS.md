# Source Analysis — Seven Trading-Education Sources, Independently Verified

**Date:** 2026-11-11
**Method:** full transcripts via agent-reach → yt-dlp (auto-captions, deduplicated, timestamped) · 150+ viewer comments · external verification of every named tool · **four independent empirical replications** against market data.

---

## 1. The Sources

### English — AI/agentic trading courses

| # | Source | Channel | Date | Views | Duration | Type |
|---|---|---|---|---|---|---|
| **E1** | "How To Actually Build an AI Trading Bot With Claude (3 Hour Course)" | AI Pathways | 2026-11-07 | 59,106 | 3h07m | Methodology + harness |
| **E2** | "How To Build A Self-Improving AI Trading Agent (Insanely Cool)" | Lewis Jackson Investing | 2026-05-22 | 549,731 | 18m | Concept + one-shot prompt |
| **E3** | "MASTER AI Trading Bots in 61 Minutes (Claude + TradingView)" | Mind Math Money | 2026-11-07 | 56,678 | 61m | Beginner build, Pine Script |
| **E4** | "Full AI Trading Course 2026: Master Agentic Trading (Beginner to Pro)" | Alex Carter | 2026-04-26 | 36,990 | 84m | 26-module full stack |

### Arabic — EA Trading (crypto education + signal/bot funnel)

| # | Playlist | Videos | Content | Captions obtained |
|---|---|---|---|---|
| **A1** | `PL49ZWxxRtW26RVSVAOudzWXP2SCei-2MT` | **211** | Market commentary + price calls, 2018→2026 | 20 sampled (Arabic + English auto) |
| **A2** | `PL49ZWxxRtW26ISsdeys31Na7ToWRpmWwG` | 2 | Overlap with A1 (one live, one unavailable) | — |
| **A3** | `PL49ZWxxRtW24SWDDbP2ZWd_S7rf0NJIQV` | **15** | The actual course: TA, S/R, time analysis, patterns, scalping | **15/15** |

**Channel business model (from descriptions, verified on every video):**
- `t.me/EATrading1/7784` — free crypto signals
- `t.me/EATrading1/7835` — free trade copies
- `t.me/EAWorldBots` — **free copy-trading bots**
- `t.me/EAWorldForex/367` — forex signals
- `t.me/+qa78WcQaNts4MTk0` — funded accounts
- Affiliate links: **Pionex** ($1,000 deposit bonus), **WEEX** (up to $30,000 bonus), **OKX**, **Vantage** (forex)

> Note: **WEEX** is the *same venue* that Alex Carter (E4) promotes as "our primary crypto broker… a $30,000 deposit bonus… no KYC." Two independent audiences, same affiliate deal.

---

## 2. Empirical Verification — What We Actually Tested

Four replications. Scripts in `research/`, outputs reproducible.

### 2.1 Mind Math Money's Turtle 55/20 — ⚠️ the headline risk number is wrong

**Claim (E3, 47:53–48:08):** +729% profit, 45% win rate, profit factor 7.6, **max drawdown 3.29%**.

**Our reproduction** — BTC-USD daily, Coinbase, 2015-07-20 → 2026-11-11, 4,133 bars, 1% risk, 10bps fee + 5bps slippage:

| Metric | Their claim | Ours | Note |
|---|---|---|---|
| Total return | +729% | **+174.6%** (over 11.1y, CAGR 9.48%) | Different horizon; theirs unknown |
| Win rate | 45% | **44.1%** | ✅ matches |
| Profit factor | 7.6 | **6.15** | close |
| **Max drawdown** | **3.29%** | **−18.91%** (mark-to-market) | ❌ **6× understated** |
| Max drawdown, closed-trade-only | — | **−3.01%** | ✅ **matches their figure** |

**The trap, precisely:** TradingView's strategy-tester Max Drawdown is computed on **closed-trade equity only**. The presenter *knows this* — at [48:08] he says *"This is not the max from the highest to lowest point. This is only of closed trades"* — and then presents **3.29%** as the course's headline risk number. On mark-to-market equity the identical trades drew down **18.91%**.

**And the strategy dies out-of-sample:**
- Full sample: Sharpe 0.76, 34 trades
- **OOS 2021+ (params fixed at 55/20): +8.6% over 5.7 years, CAGR 1.46%, Sharpe 0.30**
- OOS with IS-selected params: Sharpe 0.60
- **Concentration:** top-3 trades = **69.3%** of total P&L; top-1 trade = **43.5%**
- Buy & hold BTC over the same window: **+26,200%** (Sharpe 1.08)

The presenter does honestly disclose the buy-and-hold loss (*"we get absolutely obliterated by Bitcoin"*, [49:35]) — to his credit. But the 3.29% drawdown figure stands unchallenged as the course's safety claim.

### 2.2 AI Pathways' QQQ/gold rotation — ✅ reproduces

**Claim (E1, 27:41–28:02):** core alone on the locked 2025+ test = **24.8%/yr, 11.2% max drawdown**; full account ≈ **28.7%/yr, 13% max DD**; $146k → $221k.

**Rule, as spoken:** *"If QQQ is above its 200-day average, hold QQQ. If it's not, then hold the gold."*

**Our reproduction** (Yahoo adj-close, QQQ + GLD):

| Window | Their claim | Ours | Note |
|---|---|---|---|
| LOCK 2025+ | 24.8%/yr, 11.2% DD | **+29.63% CAGR, −12.25% maxDD, Sharpe 1.69** | ✅ within tolerance |
| FULL 2022+ | ~28.7–29%/yr | **+28.41% CAGR, Sharpe 1.77, −18.10% maxDD** | ✅ matches |
| B&H QQQ (same window) | — | +17.46% CAGR, −29.56% DD | rotation wins on risk |
| B&H GLD (same window) | — | +16.84% CAGR | rotation wins on return |

**Neighbour robustness (his best test, and it passes):**
| SMA | Total | MaxDD | Sharpe |
|---|---|---|---|
| 150 | +59.7% | −20.8% | 1.59 |
| 175 | +55.6% | −17.9% | 1.53 |
| **200** | **+61.8%** | **−12.3%** | **1.69** |
| 225 | +70.3% | −11.9% | 1.85 |
| 250 | +74.0% | −11.2% | 1.96 |

**Cost sensitivity:** negligible — 16 switches in 4.7 years; at 20bps/side CAGR only falls 28.41% → 27.54%.

**The caveat he doesn't stress:** the rotation holds **gold 23.8% of days** and has **beta 0.54** vs QQQ. Its +24.1%/yr "alpha" vs QQQ is largely *gold's* diversification (+21.0%/yr alpha for B&H GLD). And it is regime-dependent year to year:

| Year | Rotation | B&H GLD | B&H QQQ |
|---|---|---|---|
| 2022 | −2.3% | −5.2% | −18.3% |
| 2023 | +59.6% | +11.8% | +55.9% |
| 2024 | +27.7% | +27.0% | +27.7% |
| 2025 | +41.4% | +61.5% | +21.0% |
| 2026 | +14.3% | −7.3% | +10.4% |

**Verdict:** This is the **only** verified headline across all seven sources. Adopt it as our **reference strategy** — the one that proves our engine works — while keeping the gold benchmark permanently visible.

### 2.3 EA Trading's market calls — ❌ worse than a naive baseline

**Method:** 25 dated, directional claims extracted from the videos, each scored as HIT if price moved ≥ threshold in the predicted direction within the stated horizon. Compared against a **naive-continuation baseline**: "predict whichever way the market actually went" — i.e. what a trend-follower who merely looked at the chart would have said.

| Threshold | EA Trading | Naive baseline | **Excess** |
|---|---|---|---|
| ±10% | **72.0%** (18/25) | 88.0% (22/25) | **−16.0 pp** |
| ±25% | 56.0% (14/25) | 68.0% (17/25) | **−12.0 pp** |
| ±50% | 28.0% (7/25) | 40.0% (10/25) | **−12.0 pp** |

**84–88% of the calls were directionally identical to simply following the tape.** The channel's directional skill is *negative* against the simplest possible benchmark. Wilson 95% CI on the raw rate: [52%, 86%] — overlapping a coin flip.

**The 2026 macro call, scored** (video dated 2025-12-28: *"manipulation and decline… bottom in late Q4 2026… BTC from 125,000 → 58,000 → 35,000–40,000… gold, silver AND the US market all fall"*):

| Asset | Call | Actual (2025-12-28 → 2026-11-11) | Result |
|---|---|---|---|
| BTC | down | 87,836 → 73,551 (−16.3%), low **58,559** | ✅ HIT. Touched the 58,000 band. **Never reached 35,000–40,000.** |
| GOLD | down | 4,344 → 4,056 (−6.6%) | ✅ HIT (low 3,899) |
| SILVER | down | 70 → 53 (−24.2%) | ✅ HIT |
| **SPX** | **down** | **6,906 → 7,521 (+8.9%)** | ❌ **MISS** |

And from 2023-10-24 (*"35,000 is the **final** target"*) — BTC hit **106,141**, exceeding that "final" ceiling by **213%**.

### 2.4 EA Trading's "confirmed level" rule — under evaluation

The single most valuable extract from the Arabic course is the **level-identification protocol**: a level is real only if price *rejected* from it **at least twice**, each time sharply; draw the **line**, not the zone, at the **candle body**. The course supplies *no* stop, no size, no target, and no cost model.

We encoded it with engineer-supplied risk (≥2 prior rejections, ATR stop, R-multiple target, 1% risk) and tested: min-rejection sensitivity, the Ichimoku video's "fresh trade only" filter, cost sensitivity, and a 2017–2021 → 2022+ out-of-sample split against buy-and-hold.

→ **Results in `research/FINDINGS.md`.**

---

## 3. Per-Source Teardown

### 3.1 AI Pathways (E1) — best methodology, no live proof

**Framework:** **ALPHA** = Architecture · Logic · Proof · Harness · Adapt.

**What is genuinely excellent:**
- **The rules trade, Claude reviews.** *"the rules primarily do the trading… We would only add AI as a layer on top, and it can only touch the core, not the sleeves."* This is the correct inversion — the one thing E4 gets wrong.
- **Backtester validation triad** with *pre-specified* acceptance bands: planted edge (must land 0.6–1.2% on a +1% planted edge), pure noise (must find nothing beyond cost), look-ahead guard (30 random cut-off dates; a signal that changes is rejected). **We adopt this and add a golden-master diff.**
- **Neighbours test** — 150/200/250-day variants must all work. *We verified the core passes this.*
- **Two-file rules architecture** — one rules file read by both the backtester and the live bot, so research and execution cannot drift.
- **Reconcile-before-trade**; data-freshness gate ("never trades on old prices"); content-addressed order IDs; lockfile for mutual exclusion.
- **Risk limits that can only subtract** — *"none of them ever change a rule. So, they can only make the bot do less."*
- **Kill lines pre-committed** — sleeve = 2× worst tested drop; core = 1.5×; core changes are human-only.
- **Trade-frequency drift detection** — flag if live trades <½× or >2× the backtested rate.
- **Repick routine with a truncation proof** — run as of a past quarter-end twice (full data vs truncated) and require identical lineups. A clean, cheap guard against look-ahead in a *meta*-procedure.

**What is weak:**
- **The lock test is consumed repeatedly.** He states the principle correctly (*"if you run the lock test and you don't like the result, you change a setting, and then you run it on that same exact set of data again, the lock test has turned into more training data"*) — then runs it as a filter in prompts 3, 4, 5, 5B, 6 and 7, and admits the final account run is *"more of a check than a fresh test."*
- **Survivorship documented, not fixed** — *"this line here just makes sure that the prompt writes it into the report."* The screener still backtests today's constituents.
- **No live evidence** — *"the bot hasn't made its first real run yet."* The reconciliation instrument has zero observations.
- **`.env` + "Claude will never read that key"** is a *policy statement to an LLM running in bypass-permissions mode*, which is also instructed to read Fed statements and scraped web pages. One prompt injection away from the keys.
- **Stops placed the morning after the fill** — the position is unprotected between the 9:30 fill and the bot's morning run.
- **3%/5% daily limits are market-level** — a routine 1-in-15 day 3% SPY drop trips "no new sleeve trades," so in a crisis you are blocked from rebalancing into weakness while core exposure is untouched.
- **The AI layer was never run.** *"Not running it here because it makes a separate call to Claude for every single statement twice."* And model drift invalidates it anyway: *"new models come out all the time… the answers could be very, very different from one model to the next."*
- Model names as spoken ("Fable 5.1", "Opus 5.5", "Astra") match no shipping SKU.

### 3.2 Lewis Jackson (E2) — most views, least evidence

**Four bars for a trading agent:** accurate data · reliable (24/7) · well-defined goal with explicit success *and failure* · self-improving (scientific method: one variable at a time, baseline → improve).

**Good:** the scientific-method framing is the correct discipline against overfitting. The review-only-first-cycle and human-approves-flip-to-live gates are right.

**Weak:**
- **"Self-improving" = the agent rewrites its strategy from its own trade ledger, scored by a scorer whose weights the agent also owns.** Circular. The goal *"target return over 30 days is 4.7 — that is 47% by the way"* is aspirational and creates pressure toward risk-taking and curve-fitting.
- No backtesting discipline, no walk-forward, no costs, no slippage, no benchmark.
- **The audience saw it.** Top comments: *"classic overfitting made fancy"* · *"This will end up in overfitting"* · *"a lot of hype and absolutely no results to prove that anything he is showing works at all"* · *"This doesn't work. Period."* And the creator's own reply: *"a few weeks of live trades can't tell you if something really makes money… treat the videos as build concepts and thought experiments."*
- 549k views, 427 comments, and the free prompt is behind a Skool classroom that was on maintenance for weeks — *"can't find the prompt"* appears repeatedly.

### 3.3 Mind Math Money (E3) — best skepticism, worst metric

**Genuinely good:** *"An AI trading bot can't predict the future."* Semi-automated by design. The 4 oversight rules, including *"never give Claude your broker login."* The honest admission that buy-and-hold Bitcoin obliterates the strategy. Win rate correctly demoted below profit factor. Commissions and slippage correctly required. Checking 3 trades by hand before trusting.

**Weak:**
- **The 3.29% max-drawdown figure** (see §2.1).
- **No out-of-sample, no walk-forward, no multiple-testing correction.** TradingView's built-in backtest is the entire evidence base.
- **The strategy is 34 trades over 11 years with 69% of P&L in three trades** — statistically indistinguishable from luck.
- **TradingView MCP security:** the unofficial `tradesdontlie/tradingview-mcp` (7,149 stars, verified) drives TradingView Desktop over **Chrome DevTools Protocol on an unauthenticated `localhost:9222`**. Any local process — including any npm postinstall script or IDE extension — can drive the app. *"Local is a network boundary, not a process boundary."*
- He also contradicts himself on the AI's role: *"the AI's are better than 99.99% of coders"* while warning they hallucinate.
- Affiliate-heavy: TradingView $15 + 30-day trial, pushed ~8 times in 61 minutes; the paid PDF guide is the actual product.

### 3.4 Alex Carter (E4) — widest stack, worst risk hygiene at the money path

**Genuinely good:**
- **The three-agent separation of duties** — scanner (*"physically incapable of placing an order"*), guardian (*"only has two words in its vocabulary: approve or reject"*), executor (listens only to the guardian). **We adopt this, with the guardian in deterministic code.**
- **Schema-validated inter-agent contract** — *"if the data doesn't fit the shape, the machine doesn't move."*
- **The honesty filter** — *"fewer than 100 trades, it's just noise"*; always include commission and slippage; verify no look-ahead/repaint.
- Paper-first with `live=false` default, unit-test gate, 48h soak, 3% daily kill switch, Telegram heartbeat, key scoping (read + trade + IP whitelist, never withdrawal), VPS hardening (dedicated user, key-only SSH, no root, UFW, systemd restart).

**Weak — and these are hard-stops:**
- **No performance shown for anything he built.** The contract *"Real money, every tool"* and *"Everything I show you today, I have built and run myself"* is never cashed out — no win rate, Sharpe, drawdown, or equity curve for any of his own systems.
- **The backtested artifact ≠ the deployed artifact.** The honesty filter is applied to Pine strategies; the deployed system is an LLM agent. Nothing he builds as an agent is ever backtested.
- **The MCP parameter search is a false-positive factory** — *"systematically run dozens of variations… rank them by profit factor and hand you the top five winning variants."* Selecting the max of N random parameter sets on one in-sample period. The ≥100-trade rule does not address it.
- **The guardian runs on the cheapest model** (Haiku 4.5) while **the LLM sizes positions in the hot path** (*"Claude remains the brain that decides exactly how to size the position"*).
- **The kill switch, caps and tests live only in his custom Python bot** — not in Agent PNL, Senpi, the webhook pipeline, or the Telegram agent.
- **The Flask webhook has no authentication, no HMAC, no idempotency.** A public ngrok URL; anyone who learns it can post JSON and trigger a leveraged order. TradingView can re-fire alerts → double positions.
- **Polymarket EVM private key in plaintext `.env`,** scp'd to a VPS and injected into Railway env vars — while the video lectures on plugin malware and RCE.
- **125× leverage on a no-KYC venue with a $30,000 deposit bonus,** no liquidation/funding-rate mechanics, no margin-call modeling.
- **The dashboard is recommended for Netlify (public) and monetisation via Walltier** — a live-equity page would push API credentials into client-side JS.
- Affiliate disclosure arrives **82 minutes in**, *after* every recommendation.
- Scale claims unsourced: *"AI-powered bots now account for roughly 70 to 80% of all crypto volume."*

### 3.5 EA Trading (A1/A2/A3) — 211 calls, one real course, zero proof

**A1 (211 videos, 2018→2026):** the format is uniform — hook → chart screen-share → support/resistance drawn → *"here's the target"* → news overlay → CTA to Telegram. Market calls scored in §2.3.

**A3 (15 videos) — the real content.** The 142-min masterclass is structured well: *psychology → trading basics → technical analysis → the 4 keys (timeframe, candles, Fibonacci, RSI) → market psychology*.

**The one genuinely valuable rule — the level-identification protocol:**
- A level is real only if price **reacted** from it — *"when price goes there it drops from it forcefully"* [39:42].
- A level is **confirmed** only after a **second** and ideally **third** reaction [37:32, 42:14].
- Draw a **line**, not a zone, at the **candle body** [44:26, 47:55].
- **This is the best idea in any of the seven sources** and it is stated with unusual precision. It is also the *only* part with a deterministic encoding.

**Other encodable rules:** trendline needs ≥2 touches, **third touch = entry**; the neckline of a double top/bottom *is* the S/R line; measured-move targets (double top/bottom, H&S from head-to-neckline, cup from cup-depth); RSI 30/70 as *confirmation* of an S/R level, never as an originator; EMA50/200 golden/death cross on daily; Fibonacci 0.618 as "the golden level."

**The fatal gaps:**
- **142 minutes, and the ONLY mention of a stop loss is [26:08]** — *"you must have something that says 'stop.'"* No placement, no sizing, no R:R, no drawdown limit, no daily loss cap, no leverage, no liquidation, no slippage, no spread, no funding rate, no tax. **The only quantitative cost figure in the entire course is a 0.08% fee**, used for platform selection.
- **Zero backtests, zero win rates, zero profit factors, zero sample sizes.** Every demonstration is a hand-picked chart where the pattern worked. **Zero failed examples of any of the nine patterns.**
- **The unfalsifiability hedge:** *"There is no such thing as the best analysis science… it doesn't exist."* Combined with *"try freely, I speak from my experience"* — any outcome can be explained away. A course that cannot be wrong cannot be tested.
- **The false-breakout explanation is promised and never delivered.** `اختراق كاذب` ("false breakout") appears **exactly once** in 2,697 lines. This is the single most important concept for avoiding the most common beginner error, and it is dropped.
- **Internal contradictions:** candles taught as primary for 20 minutes then *"your main reliance is not on Japanese candles"*; the engulfing pattern is defined then un-defined (*"the correct pattern requires the immediately preceding candle… but from my experience, no, it doesn't matter"*); doji is a reversal candle *"but sometimes it comes as continuation"*; the flag is taught then disowned (*"not that important, honestly"*); the trendline is *"not what I trade"* yet the third touch *is* the entry.
- **7 verbatim promotional interstitials** for signals/bots, in the same video that promises *"you won't need to rely on anyone at all."*
- **One anecdote as the entire track record:** *"when I predicted Bitcoin would fall from the $105,000 top and it did fall to $70,000"* — single, unverifiable, survivorship-selected, and partly attributed to news.
- **The 99-min scalping course contradicts its own headline numbers:** *"+$48,000 in 12 trading days"* [11:30] and *"+5,500 in 12 days"* [12:27] on the same $100,000 account. It sells funded accounts with the challenge **pre-passed for a fee** — *and he is the sole judge of whether it passed*. Its Fibonacci target ladder (*"T1 = 0.50, T2 = 0.38, T3 = 0.236"* while long from 0.618) is **geometrically incoherent as instructed**.

**What survives:** the level-identification protocol, the measured-move target formulas, RSI-as-confirmation, the "fresh trade only" filter, and the money-management envelope (*"define the number you are willing to lose before you enter"*).

---

---

## 3.6 The 17-module corpus, quantified (EA Trading)

All 17 Arabic transcripts were read in full (1,008,977 views · 31,982 likes · 6,121 comments · channel 536k subs). **ASR caveat: numeric layers are frequently corrupted** — `"من 80000 ل 600000"` is 80,000→60,000; `"من 30 ل 400 الف"` is 35→40 thousand; the angle-indicator name at [11:05] is unrecognisable. **Every level must be re-derived from video frames before any backtest.**

### Two formats, one skeleton

- **Format A — market commentary (14 videos):** hook → subscribe/bell ask (11/17) → chart with drawn levels → *"here's the target"* → news overlay → Telegram CTA → soft close.
- **Format B — course episode (3 videos):** OKX walkthrough + *"الحلقه السابعه من كورس احتراف التداول"* [00:00:09] / *"الحلقه السادسة"* [00:00:07].

**The pivot:** in 2021 the channel published **205k-view *method* videos**; in 2023–2026 it publishes **7k–26k-view *prediction* videos**. The audience prefers method; the business model monetises prediction.

### The quantification that settles it

| Metric | Count |
|---|---|
| Makes a specific forward numeric price target | **14/17** |
| Presents a chart annotation as evidence | 15/17 |
| Gives ANY entry/stop/size parameter | 11/17 — and **0/17** give a numeric stop for a market trade |
| Gives numeric position sizing | 5/17 (as a 50/50 split rule, not a risk budget) |
| Gives an **R:R multiple** | **0/17** (named twice, quantified never) |
| Gives a concrete performance figure | 8/17 — all screenshots |
| **States a WIN RATE as a number** | **0/17** |
| **States a DRAWDOWN / max-DD / risk-of-ruin figure** | **0/17** |
| **States sample size, expectancy, or any risk-adjusted metric** | **0/17** |
| **Mentions trading COSTS at all** | **1/17** — and only to sell the referral code |
| Presents a broker-verified / audited track record | **0/17** |
| `t.me` links in description | **17/17** (71 links total) |
| Exchange/broker referral link | 15/17 (OKX 15/17, Vantage 8/17, Pionex 5/17, Toobit 4/17, **WEEX 3/17**) |
| "Bonus" language | 10/17 |
| **Any disclaimer at all** | 9/17 — and the 8 with **none** include every 2023–24 video carrying a bonus link |

> **0/17 win rate · 0/17 drawdown · 0/17 R:R · 0/17 numeric stop · 0/17 audited statement — while 8/17 publish trade-level percentage returns.** That is the signature of reporting winners as screenshots and never publishing a denominator. A trader with an edge and a trader without one can produce identical screenshots.

### Six dated internal contradictions

| # | Contradiction | Evidence |
|---|---|---|
| **1** | **$35,000 is both "final target — exit the market" and "the level I must reclaim to re-enter."** | `Z1CXfqGZyeY` (2023-10-28) [00:02:54]–[00:04:58]: *"الهدف الاخير… 35000… في حال ان وصل له انا هخرج من السوق"* → `p0OjQGl1LUE` (2023-11-09) [00:05:12]: *"لو رجع السعر فوق المقاومه بتاعه 35150… هندخل السوق مره اخرى"*. **12 days apart. A level cannot be both.** |
| **2** | **The 2026 bottom call was abandoned mid-year** | Dec 2025: *"القاع في نهايات الربع الاخير من 2026"*, zone $35–40k → Jan 2026: same target → **Jul 2026**: *"بداية انطلاق جديد"* at $86,000 → Nov 2026: *"انهيار تاريخي… هدف 64000"*. **$35–40k never printed in 2026; the "new launch" lasted ~4 months.** |
| **3** | **"$60,000 will very likely not break" → broken in 3 months** | `42_Geqn383Y` (2026-04-03) [00:01:51]–[00:02:39]: *"مستبعد جداً جداً للأمانة يكسر المنطقة"* → Jul 2026: BTC traded through $58–64k |
| **4** | **Title promises $1,000,000; body never says it** | `vcSCfO6801I` — title *"البيتكوين الي المليون"*; body says $35,000 then $100,000 |
| **5** | **"Buy the dip" vs "run from the dip" — the same event, opposite actions** | `owFAEa3jhKM` [00:05:27]: in a −30% tape, selling is amateur and **buying** is professional → `vUcvYQj1e_E` [00:06:15]: on a fake break, *"بنسي من السوق"* (**exit**) → `rASC1NalVbw` [00:12:41]: **buy** the fake break at a Fib level. **This is the single biggest obstacle to encoding anything from this corpus.** |
| **6** | **Leverage advice vs. leverage sold** | `vsqbB672zxk` [00:13:53]: *"ما انصحكش… بالرافعه الماليه العاليه"* + isolated-only [00:12:35] → but the copy product runs 1×/3×/10× perps and funded accounts are inherently high-leverage evaluations |

### The ethical core

- **The channel sells a service to pass a third party's risk evaluation** — `vUcvYQj1e_E`… actually `_zJwDbd82AU` [00:05:33]–[00:05:44]: *"تقدر تتحصل عليها لما بتشتري حساب ممول 300000 وبتاخد خدمه اجتياز الاختبار، لان انا بقدم خدمه اجتياز الاختبار في الحسابات المموله فبتاخد المؤشر مجانا."* A $300k purchase bundles a test-passing service.
- **The channel sells copy trading while warning against copy trading** — `vsqbB672zxk` [00:07:27]: *"بوتات التداول دي انا مش مؤمن بيها… الناس اللي جوه مجهوله الهواء… ما انصحش"* — in the same channel that sells `EAWorldBots` in 6/17 descriptions and a copy system in 11/17.
- **Minimum copy-trading capital stated 20× apart** — $25,000 (`p0OjQGl1LUE` [00:12:49]) vs **$500,000** (`owFAEa3jhKM` [00:03:41]).
- **The referral code is pitched as a profit improvement** — `vsqbB672zxk` [00:02:15]: *"الموضوع ده بيفرق معاك بشكل كبير جداً في الارباح… في الربح بدل مثلا ما كنت هتكسب 50% لا ممكن تكسب 53%"* — inside the corpus's most-viewed video (383,912 views), whose single highest-intent action is typing his code at signup.
- **An explicit guarantee:** *"فانا **ضامن** ان هيحصل ارتداد"* (`H7y1RHkJ3CM` [00:08:06]).
- **Free-claims without denominator:** *"ما عندناش اي خساير خالص"* [00:03:52]; *"توقعت لك **جميع** قمم وقعان البيتكوين بالكامل… من ست سنين"* (`vUcvYQj1e_E` [00:12:11]).

### Credit where due — the calls that were right, and the reason

- `vcSCfO6801I` (2023-10-24): $35,000 final target — BTC printed ~$35k within a week.
- `Z1CXfqGZyeY` (2023-10-28): break of $35,000 opens the next cycle — BTC broke $35k on 3 Nov and ran through 2024.
- `5WVWTEWc0lI` (2023-12-06): $100–105k within 2024 — BTC printed $100k on 5 Dec 2024, $105k on 17 Dec 2024. Plus the 3rd-candle alt-rotation call.
- `owFAEa3jhKM` (2024-01-04): $38,000 holds (BTC bottomed ~$38.5k on 20 Jan 2024); $60,000 (Mar 2024) and $105,000 (Dec 2024) both hit.
- `fe0N_nIjZvg` (2026-01-31): gold −16%, silver −40% — called days ahead, with the correct mechanism (paper-vs-physical, forced deleveraging).

**The problem is not that the calls are wrong.** It is that the same channel makes a "$35–40k 2026 bottom" call it abandons four months later, titles a video "Bitcoin to a million dollars" and never says a million, and **never once quantifies a success with a denominator.** A track record containing only successes because failures are unpublished is not a track record.

### The strongest encodable rule in the corpus — R1, three-scenario breakout confirmation

From `vUcvYQj1e_E` [00:03:38]–[00:06:27], corroborated by `MxYoaNPHJI4`, `_zJwDbd82AU`, `dSd9FIdmOE8`:

```
INPUT  R_BTC = key BTC resistance;  R_ALT = per-coin resistance of the alt basket
       W     = confirmation window (2–3 days)

SCENARIO 1  both break AND both hold          -> LONG
SCENARIO 2  BTC breaks, alts do not           -> NO ENTRY; wait W days
SCENARIO 3  both break, both close back below -> FAKE BREAKOUT: EXIT ALL, no new entry
```

**This is the only complete decision tree in the corpus with an observable trigger and a defined wait period.** It is the one item from PL3 worth building first.

### The conflict that blocks encoding

**R4 vs R1 must be reconciled before any code.** R4 (Fib false-break) says *buy* the fake break at a Fib level; R1 says *exit* on a fake break at a range resistance. Both are in the same corpus. A deterministic system cannot contain both without a disambiguator — candidate: R4 applies only at Fib levels on a **higher** timeframe than R1's resistance.

### Genuinely valuable, non-obvious content worth stealing

| # | Item | Source |
|---|---|---|
| 1 | **R1 three-scenario breakout confirmation** | `vUcvYQj1e_E` |
| 2 | **Dual-asset confirmation with a time window** — BTC >64k AND ETH >1.8k within 2 days; BTC alone ⇒ don't buy | `MxYoaNPHJI4` [00:06:41] |
| 3 | **"The chart precedes the news"** — price structure must already imply the setup; news confirms and is used for exit, never entry | `dSd9FIdmOE8` [00:02:38] |
| 4 | **Negative news = buying opportunity** (liquidity event framing) | `5WVWTEWc0lI`, `HfRiLr5RqJ8` |
| 5 | **Paper-gold vs. physical divergence + forced deleveraging** — a fast parabolic move in a non-reserve-backed asset is a liquidity event, not a trend | `fe0N_nIjZvg` [00:04:16]–[00:08:26] |
| 6 | **Severity/base-rate refutation** of the quantum-wallet scare (if it could break wallets it would break Visa first) | `42_Geqn383Y` [00:05:37] |
| 7 | **Isolated margin only, never cross; understand the leverage cost** — 3× on $100 → $300 notional, ~$91 margin | `vsqbB672zxk` [00:11:58]–[00:13:56] |
| 8 | **Custody hygiene** — don't hold the full balance on one exchange; reduce exposure into stress | `VHeN7UtOhyg` [00:06:11] |
| 9 | **"Capital doesn't renew; opportunities do"** | `vUcvYQj1e_E` [00:11:05] |
| 10 | **Gann-style price-angle cycle projection** (0°→360°; shape from the first pullback angle: 180°/120°/90°) — the only fully numeric derivation in the corpus | `7J56h-gT7z0` |
| 11 | **Sequential coin rotation** (ride one at a time, in order of expected move) — but the selection filter is paywalled | `owFAEa3jhKM` [00:13:02] |
| 12 | **Season-duration ≈ 1 year** as a weak prior | `5WVWTEWc0lI`, `vcSCfO6801I` |
| 13 | **BTC-dominance gate (54–55% → 58% peak → roll-over)** for alt rotation — the corpus's only fully specified regime rule | `Z1CXfqGZyeY` [00:07:29] |
| 14 | **Season-high as next-season-low** | `fe0N_nIjZvg` [00:18:20] |

### Two title/content mismatches worth noting

- **"Mastery of Time Analysis" does not contain time analysis.** It teaches price-angle geometry and explicitly defers the temporal part: *"الحلقه اللي جايه هنتكلم عن الزمني"* [00:17:12]. The promised methodology is **not delivered** — and it is one of the two highest-view videos in the corpus (206,353 views).
- **"Bitcoin to a million dollars" never mentions a million dollars.**

---

## 4. Cross-Cutting Failures Common to All Seven

| # | Shared failure | Evidence |
|---|---|---|
| 1 | **The LLM owns numbers it must not own** — position sizing, risk limits, or its own grading | E2 (scorer weights), E4 (LLM sizing; guardian on cheapest model) |
| 2 | **No correction for the number of things tried** | E4 (dozens ranked by PF); E1 (44 ideas, 1,600 filters); E3 (single backtest) |
| 3 | **No live-vs-backtest reconciliation with data** | E1 builds the instrument and has zero observations |
| 4 | **"The LLM built the code" conflated with "the strategy works"** | E1 says it best: *"Claude writes all the code for you… what decides whether a trading system works is how you set it up and how hard you test the rules."* |
| 5 | **Costs, fills and market impact are absent or naive** | All seven; only E1 models 0.05%/side |
| 6 | **Security posture breaks exactly where money moves** | E4 (webhook, private key); E1 (`.env` + bypass permissions) |
| 7 | **Every source upsells** | Free artifact = the video; prompts/settings behind paywall; affiliate venues (WEEX appears in both E4 and EA Trading) |
| 8 | **No one publishes a P&L for a system they built** | Sole exception: E1's backtest numbers — which we independently reproduced |

---

## 5. What We Take

| From | What | Where it lands |
|---|---|---|
| **E1 (AI Pathways)** | ALPHA discipline; backtester validation triad; neighbours test; two-file rules architecture; reconcile-before-trade; risk-limits-that-only-subtract; pre-committed kill lines; frequency-drift detection | `research/`, `risk/`, Phase 1 |
| **E1** | The verified QQQ/gold rotation | **Reference strategy S18** |
| **E3 (Mind Math Money)** | "AI can't predict"; human-in-the-loop by default; never give the model broker credentials; win rate is not the metric; compare to buy-and-hold | `risk/`, `agent/` |
| **E4 (Alex Carter)** | Three-agent separation of duties; schema-validated signal contract; honesty filter; paper-first + `live=false` + 48h soak; key scoping; VPS hardening | `agent/`, `broker/` |
| **E4** | The *dashboard panel list* — expanded and hardened | `dashboard/` |
| **E2 (Lewis Jackson)** | Scientific method: one variable at a time, baseline→improve; explicit success *and failure* definition; review-only first cycle | `research/protocol.py`, promotion gates |
| **A3 (EA Trading)** | Level-identification protocol; measured-move target formulas; RSI-as-confirmation; "fresh trade only"; money-management envelope; psychology and revenge-trading discipline | `strategies/S1–S17`, `risk/` |
| **A1/A2 (EA Trading)** | *Nothing.* Scored at −16pp vs a naive baseline | — |

---

## 6. Rejected, With Reasons

See [`MASTER_SPEC.md` §7](MASTER_SPEC.md) for the full table (19 items). The five that matter most:

1. **TradingView→ngrok→Flask→LLM→exchange** — no auth, no idempotency, LLM in the hot path.
2. **LLM in position-sizing** — sizing must be deterministic code.
3. **EVM private key in plaintext `.env` copied to a VPS and a PaaS** — the largest unmitigated risk across all seven.
4. **`bypass permissions` beside live credentials** — an unrestricted shell next to trading keys.
5. **Point predictions as a strategy** — EA Trading's A1 playlist, −16pp vs a naive baseline.
