"""
Empirical check of the Mind Math Money course claim:
  "Turtle Breakout 55/20", long-only, daily BTC
  - entry: daily close > highest high of previous 55 days
  - stop: 2 * ATR(20) below signal-day close
  - exit: daily close < lowest low of previous 20 days (trailing)
  - position size: risk 1% of account / stop distance
  - claims: +729% profit, 45% win rate, profit factor 7.6, max DD 3.29%

We test: (a) reproduce with costs, (b) buy-and-hold comparison,
(c) walk-forward out-of-sample, (d) deflated Sharpe for multiple testing.
Data: Coinbase public candles API (BTC-USD daily, no key).
"""
import json, math, urllib.request, sys, io, time
import os
import pandas as pd, numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_turtle.txt")
lines = []
def p(s=""):
    lines.append(str(s)); print(s)

def fetch_candles(product="BTC-USD", gran=86400, start="2015-01-01", end="2026-11-11"):
    """Coinbase public candles API. Max 300 candles per request -> chunk by ~290 days."""
    rows, cursor = [], start
    while cursor < end:
        s = pd.Timestamp(cursor, tz="UTC")
        e = min(s + pd.Timedelta(days=290), pd.Timestamp(end, tz="UTC"))
        u = (f"https://api.exchange.coinbase.com/products/{product}/candles"
             f"?granularity={gran}&start={s.strftime('%Y-%m-%d')}&end={e.strftime('%Y-%m-%d')}")
        req = urllib.request.Request(u, headers={"User-Agent": "research/1.0", "Accept": "application/json"})
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as ex:
            p(f"fetch error {ex} at {cursor}"); break
        if not d:
            break
        rows += d
        last_ts = max(r[0] for r in d)
        nxt = (pd.Timestamp(last_ts, unit="s", tz="UTC") + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt <= cursor:
            break
        cursor = nxt
        time.sleep(0.4)
        if len(rows) > 20000:
            break
    df = pd.DataFrame(rows, columns=["ts","low","high","open","close","volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_localize(None)
    return df[["date","open","high","low","close","volume"]].reset_index(drop=True)

def atr(df, n=20):
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-prev_close).abs(),
                    (df["low"]-prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def backtest(df, fee_bps=10, slip_bps=5, risk_pct=0.01, atr_mult=2.0,
             entry_n=55, exit_n=20, start_idx=0, end_idx=None, warmup=60):
    """Long-only turtle. Costs applied on entry+exit (notional)."""
    d = df.copy()
    d["atr"] = atr(d, 20)
    d["hh"] = d["high"].shift(1).rolling(entry_n).max()
    d["ll"] = d["low"].shift(1).rolling(exit_n).min()
    end_idx = len(d) if end_idx is None else end_idx
    d = d.iloc[:end_idx]

    cash, units, stop, equity_curve, trades = 1.0, 0.0, None, [], []
    entry_px = None
    one_way = (fee_bps + slip_bps) / 10000.0

    for i in range(max(start_idx, warmup), len(d)):
        row = d.iloc[i]
        px, a = row["close"], row["atr"]
        if np.isnan(row["hh"]) or np.isnan(a):
            equity_curve.append((row["date"], cash + units*px)); continue
        if units == 0:
            if px > row["hh"]:
                entry_px = px
                stop = px - atr_mult*a
                dist = max(px - stop, 1e-9)
                risk_amt = cash * risk_pct
                size = risk_amt / dist
                cost = size * px * one_way
                if size * px + cost > cash:      # no leverage allowed
                    size = cash / (px * (1 + one_way))
                    cost = size * px * one_way
                cash -= (size*px + cost); units = size
                trades.append({"entry_date": row["date"], "entry": px, "stop": stop, "size": size})
        else:
            exit_px, reason = None, None
            if px < stop:
                exit_px, reason = stop, "stop"          # assume stop fills at stop price
            elif px < row["ll"]:
                exit_px, reason = px, "signal"
            if exit_px is not None:
                t = trades[-1]
                cost = units * exit_px * one_way
                cash += units*exit_px - cost
                t.update({"exit_date": row["date"], "exit": exit_px, "reason": reason,
                          "ret": (exit_px*(1-one_way))/(t["entry"]*(1+one_way)) - 1})
                units = 0.0; stop = None
        equity_curve.append((row["date"], cash + units*px))

    if units > 0:   # mark to market at end
        px = d.iloc[-1]["close"]
        t = trades[-1]
        t.update({"exit_date": d.iloc[-1]["date"], "exit": px, "reason": "eom",
                  "ret": (px*(1-one_way))/(t["entry"]*(1+one_way)) - 1})
        cash += units*px*(1-one_way); units = 0.0
    eq = pd.Series([v for _, v in equity_curve], index=[d_ for d_, v in equity_curve])
    return cash, trades, eq, d

def stats(cash, trades, eq, label):
    r = eq.pct_change().dropna()
    if len(r) < 30:
        p(f"{label}: insufficient data ({len(r)} pts)"); return {}
    years = (eq.index[-1]-eq.index[0]).days/365.25
    total = eq.iloc[-1]/eq.iloc[0] - 1
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/years) - 1 if years > 0 else 0
    dd = (eq/eq.cummax() - 1).min()
    vol = r.std()*math.sqrt(365)
    sharpe = (r.mean()*365)/vol if vol > 0 else 0
    closed = [t for t in trades if "ret" in t]
    wins = [t for t in closed if t["ret"] > 0]
    losses = [t for t in closed if t["ret"] <= 0]
    gp = sum(t["ret"]*t["size"]*t["entry"] for t in wins)
    gl = abs(sum(t["ret"]*t["size"]*t["entry"] for t in losses))
    pf = gp/gl if gl > 0 else float("inf")
    wr = len(wins)/len(closed) if closed else 0
    out = {"label": label, "final": eq.iloc[-1], "total_pct": total*100, "cagr_pct": cagr*100,
           "max_dd_pct": dd*100, "sharpe": sharpe, "trades": len(closed),
           "win_rate_pct": wr*100, "profit_factor": pf,
           "years": years, "n_returns": len(r)}
    p(f"  {label}: final={out['final']:.3f}  total={out['total_pct']:+.1f}%  CAGR={out['cagr_pct']:+.2f}%")
    p(f"     maxDD={out['max_dd_pct']:.2f}%  Sharpe={out['sharpe']:.2f}  trades={out['trades']}"
      f"  winrate={out['win_rate_pct']:.1f}%  PF={out['profit_factor']:.2f}  yrs={years:.1f}")
    return out

def deflated_sharpe(observed_sr, n_trials, n_obs, skew=0, kurt=3):
    """Bailey & Lopez de Prado style DSR approximation (PSR-based)."""
    if n_obs <= 1: return 0.0
    e = [0.0]
    for k in range(1, n_trials):
        e.append(e[-1] + (1.0/k) * math.log(n_trials/k))   # expected max SR under null
    e_max = e[-1]
    denom = math.sqrt(1 - skew*observed_sr + (kurt-1)/4*observed_sr**2)
    sr0 = e_max / math.sqrt(n_obs) if n_obs > 0 else 0
    z = (observed_sr - sr0) * math.sqrt(n_obs - 1) / denom
    return 0.5 * (1 + math.erf(z/math.sqrt(2)))

p("="*78)
p("EMPIRICAL BACKTEST: 'Turtle Breakout 55/20' long-only daily BTC")
p("Claim under test (Mind Math Money course): +729%, win rate 45%, PF 7.6, maxDD 3.29%")
p("="*78)

try:
    df = fetch_candles()
except Exception as e:
    p(f"FATAL fetch: {e}")
    io.open(OUT,"w",encoding="utf-8").write("\n".join(lines)); sys.exit(0)

p(f"Loaded {len(df)} daily candles: {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()}")
p(f"BTC close range: {df['close'].min():.0f} .. {df['close'].max():.0f}")

# --- A. full-sample, with costs
cash, trades, eq, d = backtest(df, fee_bps=10, slip_bps=5)
sA = stats(cash, trades, eq, "A) FULL SAMPLE (1% risk, 10bps fee + 5bps slip)")

# --- A2. frictionless (to see if the video's 729% is a frictionless artifact)
cash0, trades0, eq0, _ = backtest(df, fee_bps=0, slip_bps=0)
sA0 = stats(cash0, trades0, eq0, "A2) FULL SAMPLE frictionless (0 cost)")

# --- B. buy and hold
bh = df["close"]/df["close"].iloc[0]
p("")
r_bh = bh.pct_change().dropna()
yrs = (df['date'].iloc[-1]-df['date'].iloc[0]).days/365.25
dd_bh = (bh/bh.cummax()-1).min()
vol_bh = r_bh.std()*math.sqrt(365)
p(f"  B) BUY & HOLD BTC: total={ (bh.iloc[-1]-1)*100:+.1f}%  CAGR={((bh.iloc[-1])**(1/yrs)-1)*100:+.2f}%")
p(f"     maxDD={dd_bh*100:.2f}%  Sharpe={(r_bh.mean()*365)/vol_bh:.2f}")

# --- C. cost sensitivity
p("")
for fee, slip in [(0,0),(5,2),(10,5),(20,10),(40,20)]:
    c,t,e,_ = backtest(df, fee_bps=fee, slip_bps=slip)
    s = stats(c,t,e,f"C) costs fee={fee}bps slip={slip}bps")

# --- D. walk-forward: IS 2015-2020 -> OOS 2020+, and IS->OOS split variants
p("")
splits = [("2015-01-01","2020-12-31","2021-01-01","2026-11-11"),
          ("2015-01-01","2022-12-31","2023-01-01","2026-11-11"),
          ("2016-01-01","2021-12-31","2022-01-01","2026-11-11")]
for is_s, is_e, oos_s, oos_e in splits:
    m_is = (df["date"]>=is_s)&(df["date"]<=is_e)
    m_oos= (df["date"]>=oos_s)&(df["date"]<=oos_e)
    df_is, df_oos = df[m_is].reset_index(drop=True), df[m_oos].reset_index(drop=True)
    if len(df_is) < 200 or len(df_oos) < 200:
        p(f"  D) {is_s}..{is_e} split skipped (insufficient bars)"); continue
    # In-sample: parameter chosen ON IS (grid), then locked, evaluated OOS
    grid = [(en, ex) for en in (20,40,55) for ex in (10,20,30)]
    best, best_s = None, -9e9
    for en, ex in grid:
        c,t,e,_ = backtest(df_is, entry_n=en, exit_n=ex)
        if len(e) < 30: continue
        sr = (e.pct_change().dropna().mean()*365)/(e.pct_change().dropna().std()*math.sqrt(365))
        if sr > best_s: best_s, best = sr, (en,ex)
    en, ex = best
    c,t,e,_ = backtest(df_oos, entry_n=en, exit_n=ex)
    s_oos = stats(c,t,e,f"D) OOS {oos_s[:7]}.. (params from IS: entry={en},exit={ex})")
    # baseline params (55/20) evaluated OOS
    c,t,e,_ = backtest(df_oos, entry_n=55, exit_n=20)
    stats(c,t,e,f"D) OOS {oos_s[:7]}.. (fixed 55/20)")

# --- E. multiple testing: how many variants would produce a Sharpe this good by luck?
p("")
n_trials = 3*3
sr_obs = sA["sharpe"] if sA else 0
dsr = deflated_sharpe(sr_obs, n_trials, sA["n_returns"] if sA else 100)
p(f"  E) Observed Sharpe (full sample, with costs) = {sr_obs:.2f}")
p(f"     Trials for the 3x3 param grid = {n_trials}; n_returns = {sA.get('n_returns',0)}")
p(f"     Deflated Sharpe probability (approx) = {dsr*100:.1f}%  [P(true edge) estimate]")
p("     Note: course-style 'dozens of variants ranked by profit factor' => far higher trial count.")

# --- F. how much of the return came from the best trade(s)?
closed = [t for t in trades if "ret" in t]
if closed:
    pnl = [ (t["exit"]-t["entry"])*t["size"] for t in closed ]
    tot = sum(pnl)
    pnl_sorted = sorted(pnl, reverse=True)
    top3 = sum(pnl_sorted[:3])
    p("")
    p(f"  F) {len(closed)} closed trades; total gross PnL = {tot:.3f} (starting 1.0)")
    p(f"     Top-3 trades contributed {top3:.3f} = {100*top3/tot:.1f}% of total PnL")
    p(f"     Top-1 trade contributed {pnl_sorted[0]:.3f} = {100*pnl_sorted[0]/tot:.1f}% of total PnL")
    p(f"     Number of losing trades: {sum(1 for x in pnl if x<=0)}")

# --- G. volatility regime check: did it work in the bull, fail in the chop?
p("")
closed_sorted = sorted([t for t in closed], key=lambda t: t["entry_date"])
if closed_sorted:
    for lo, hi in [(2015,2018),(2018,2020),(2020,2022),(2022,2024),(2024,2026)]:
        sub = [t for t in closed_sorted if lo <= t["entry_date"].year <= hi]
        if not sub: continue
        pn = [(t["exit"]-t["entry"])*t["size"] for t in sub]
        p(f"  G) {lo}-{hi}: n={len(sub):3d}  grossPnL={sum(pn):+.4f}  wins={sum(1 for x in pn if x>0)}")

io.open(OUT,"w",encoding="utf-8").write("\n".join(lines))
p("")
p(f"[written to {OUT}]")
