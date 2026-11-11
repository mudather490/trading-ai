"""
Two follow-up empirical checks:

(1) Metric quirk: the course claims "max drawdown 3.29%" for Turtle 55/20.
    TradingView's strategy-tester Max Drawdown is measured on CLOSED-trade
    equity only (the presenter says so at 48:08). On a mark-to-market
    equity curve including open positions, the same strategy shows a very
    different number. Quantify the gap.

(2) AI Pathways "ALPHA" core claim:
    "If QQQ is above its 200-day average, hold QQQ. If it's not, hold gold."
    Claims: 24.8%/yr with 11.2% max DD on the locked 2025+ test;
            full account ~28.7%/yr with 13% max DD; $100k -> $221k 2025->2026.
    We test the CORE rule alone (this is testable without their code):
      - training window 2022-2024
      - locked test 2025 -> present
      - vs buy-and-hold QQQ, buy-and-hold GLD, and 60/40
Data: Yahoo Finance chart API (no key).
"""
import json, math, urllib.request, io, time
import os
import pandas as pd, numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_core.txt")
lines = []
def p(s=""):
    lines.append(str(s)); print(s)

def yahoo(sym, start="2015-01-01", end="2026-11-12"):
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    p2 = int(pd.Timestamp(end, tz="UTC").timestamp())
    u = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         f"?period1={p1}&period2={p2}&interval=1d&events=div%2Csplit")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    r = d["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
    df = pd.DataFrame({"date": pd.to_datetime(ts, unit="s", utc=True).tz_localize(None),
                       "open": q["open"], "high": q["high"], "low": q["low"],
                       "close": q["close"], "adj": adj})
    return df.dropna(subset=["close"]).reset_index(drop=True)

def st2(eq, label, bench=None):
    r = eq.pct_change().dropna()
    if len(r) < 30:
        p(f"  {label}: insufficient ({len(r)})"); return {}
    yrs = (eq.index[-1]-eq.index[0]).days/365.25
    total = eq.iloc[-1]/eq.iloc[0]-1
    cagr = (eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd = (eq/eq.cummax()-1).min()
    vol = r.std()*math.sqrt(365)
    sr = r.mean()*365/vol
    beta = alpha = None
    if bench is not None:
        b = bench.reindex(eq.index).ffill()
        br = b.pct_change().dropna()
        j = pd.concat([r, br], axis=1).dropna()
        beta = j.cov().iloc[0,1]/j.iloc[:,1].var()
        alpha = (r.mean()*365) - beta*(br.mean()*365)
    out = {"label":label,"total":total*100,"cagr":cagr*100,"dd":dd*100,"sharpe":sr,
           "beta":beta,"alpha":alpha,"yrs":yrs,"final":eq.iloc[-1]}
    p(f"  {label}")
    p(f"     total={out['total']:+.1f}%  CAGR={out['cagr']:+.2f}%  maxDD={out['dd']:.2f}%  Sharpe={out['sharpe']:.2f}  yrs={out['yrs']:.1f}")
    if beta is not None:
        p(f"     beta vs benchmark={beta:.2f}  alpha(ann)={alpha*100:+.2f}%")
    return out

# ---------------------------------------------------------------- (1)
p("="*78)
p("(1) TURTLE 55/20 : 'closed-trade maxDD' vs 'mark-to-market maxDD'")
p("="*78)
exec(open(r"C:\Users\MudaX\AppData\Local\Temp\opencode\yt\bt.py").read().split("p(\"=\"*78)")[0])

try:
    df = fetch_candles()
    cash, trades, eq, d = backtest(df, fee_bps=10, slip_bps=5)
    p(f"  bars {df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()}  n={len(df)}")
    p(f"  Mark-to-market equity-curve max drawdown : {(eq/eq.cummax()-1).min()*100:.2f}%")
    # closed-trade equity only: cash realised at each exit, ignoring open position marks
    realized = []
    c = 1.0
    closed = [t for t in trades if "ret" in t]
    for t in closed:
        c += (t["exit"]-t["entry"])*t["size"]
        realized.append(c)
    rs = pd.Series(realized)
    p(f"  Closed-trade-only equity max drawdown    : {(rs/rs.cummax()-1).min()*100:.2f}%   <-- TradingView-style metric")
    p(f"  Claim in course                          : 3.29% maxDD, +729% profit, 45% winrate, PF 7.6")
    p(f"  This reproduction (1% risk, 15bps rt)     : +{(eq.iloc[-1]-1)*100:.1f}%, "
      f"m2m maxDD {(eq/eq.cummax()-1).min()*100:.2f}%, "
      f"winrate {sum(1 for t in closed if t['ret']>0)/len(closed)*100:.1f}%, "
      f"PF {sum(max(t['ret'],0)*t['size']*t['entry'] for t in closed)/max(sum(abs(min(t['ret'],0))*t['size']*t['entry'] for t in closed),1e-12):.2f}")
except Exception as e:
    p(f"  (1) failed: {e}")

# ---------------------------------------------------------------- (2)
p("")
p("="*78)
p("(2) AI PATHWAYS CORE: 'QQQ above 200d -> hold QQQ; else hold gold'")
p("   Claim: 24.8%/yr, 11.2% maxDD on locked 2025+ test; full acct ~28.7%/yr, 13% DD")
p("="*78)
try:
    qqq = yahoo("QQQ", "2021-06-01", "2026-11-12")
    gld = yahoo("GLD", "2021-06-01", "2026-11-12")
    m = pd.DataFrame({"qqq": qqq.set_index("date")["adj"], "gld": gld.set_index("date")["adj"]}).ffill().dropna()
    m["sma200"] = m["qqq"].rolling(200).mean()
    m = m.dropna()
    p(f"  data {m.index[0].date()} -> {m.index[-1].date()}  n={len(m)}")

    def core_eq(sub):
        sig = (sub["qqq"] > sub["sma200"]).astype(int)          # 1 = hold QQQ
        ret = sub["qqq"].pct_change().fillna(0)*sig + sub["gld"].pct_change().fillna(0)*(1-sig)
        return (1+ret).cumprod()

    for label, s, e in [("TRAIN 2022-2024","2022-01-01","2024-12-31"),
                        ("LOCK 2025+","2025-01-01","2026-11-12"),
                        ("FULL 2022+","2022-01-01","2026-11-12")]:
        sub = m[(m.index>=s)&(m.index<=e)]
        if len(sub) < 200: continue
        p("")
        p(f"  --- {label} ---")
        eq_core = core_eq(sub)
        st2(eq_core, f"{label} CORE (QQQ/GLD 200d rotation)", bench=sub["qqq"])
        st2(sub["qqq"]/sub["qqq"].iloc[0], f"{label} BUY&HOLD QQQ", bench=sub["qqq"])
        st2(sub["gld"]/sub["gld"].iloc[0], f"{label} BUY&HOLD GLD", bench=sub["qqq"])
        b = (0.6*sub["qqq"]+0.4*sub["gld"])
        st2(b/b.iloc[0], f"{label} 60/40 QQQ+GLD", bench=sub["qqq"])

    # sensitivity: 150 / 200 / 250-day  (the "neighbours" test)
    p("")
    p("  --- NEIGHBOUR SENSITIVITY on LOCK 2025+ (does the edge survive a parameter nudge?) ---")
    sub_all = m[(m.index>="2025-01-01")]
    for n in (150, 175, 200, 225, 250):
        mm = m.copy()
        mm["s"] = mm["qqq"].rolling(n).mean()
        sub = mm[(mm.index>="2025-01-01")].dropna()
        sig = (sub["qqq"]>sub["s"]).astype(int)
        ret = sub["qqq"].pct_change().fillna(0)*sig + sub["gld"].pct_change().fillna(0)*(1-sig)
        eq = (1+ret).cumprod()
        dd = (eq/eq.cummax()-1).min()*100
        r = eq.pct_change().dropna()
        sr = r.mean()*365/(r.std()*math.sqrt(365))
        p(f"     SMA{n}: total={(eq.iloc[-1]-1)*100:+.1f}%  maxDD={dd:.2f}%  Sharpe={sr:.2f}")
except Exception as e:
    p(f"  (2) failed: {e}")

io.open(OUT,"w",encoding="utf-8").write("\n".join(lines))
p("")
p(f"[written to {OUT}]")
