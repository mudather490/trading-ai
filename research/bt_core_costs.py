"""Rigor pass: transaction costs + rotation count + 'is it just gold?' for the QQQ/GLD 200d core."""
import json, math, urllib.request, io
import os
import pandas as pd, numpy as np

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_core_costs.txt")
lines = []
def p(s=""):
    lines.append(str(s)); print(s)

def yahoo(sym, start="2021-06-01", end="2026-11-12"):
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp()); p2 = int(pd.Timestamp(end, tz="UTC").timestamp())
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose", q["close"])
    return pd.DataFrame({"date": pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_localize(None),
                         "close": q["close"], "adj": adj}).dropna(subset=["close"])

qqq = yahoo("QQQ"); gld = yahoo("GLD")
m = pd.DataFrame({"qqq": qqq.set_index("date")["adj"], "gld": gld.set_index("date")["adj"]}).ffill().dropna()
m["sma200"] = m["qqq"].rolling(200).mean(); m = m.dropna()

def run(sub, cost_bps=5.0):
    """cost_bps charged ONE-WAY on each day a switch happens (not every day)."""
    sig = (sub["qqq"] > sub["sma200"]).astype(int)
    switches = int((sig.diff().abs() == 1).sum())
    ret = sub["qqq"].pct_change().fillna(0)*sig + sub["gld"].pct_change().fillna(0)*(1-sig)
    switch_days = (sig.diff().abs() == 1).astype(float)      # 1.0 on the day of a switch
    net = ret - switch_days * (cost_bps/10000.0)            # one full round-trip charge per switch
    return (1+net).cumprod(), switches, switch_days

for label, s, e in [("TRAIN 2022-2024","2022-01-01","2024-12-31"),
                    ("LOCK 2025+","2025-01-01","2026-11-12"),
                    ("FULL 2022+","2022-01-01","2026-11-12")]:
    sub = m[(m.index>=s)&(m.index<=e)]
    p("")
    p(f"--- {label} ---")
    for cb, tag in [(0.0,"0 bps (as claimed)"), (5.0,"5 bps/side"), (10.0,"10 bps/side"), (20.0,"20 bps/side")]:
        eq, sw, to = run(sub, cb)
        dd = (eq/eq.cummax()-1).min()*100
        r = eq.pct_change().dropna()
        sr = r.mean()*365/(r.std()*math.sqrt(365))
        yrs = (eq.index[-1]-eq.index[0]).days/365.25
        cagr = (eq.iloc[-1])**(1/yrs)-1
        p(f"   cost {tag:18s} CAGR={cagr*100:+6.2f}%  total={(eq.iloc[-1]-1)*100:+7.1f}%  maxDD={dd:6.2f}%  Sharpe={sr:.2f}  switches={sw}")

p("")
p("--- DECOMPOSITION: how much of the 'core edge' is just being long gold? (FULL 2022+) ---")
sub = m[(m.index>="2022-01-01")]
eq_core,_,_ = run(sub, 0)
def met(x):
    r = x.pct_change().dropna(); yrs=(x.index[-1]-x.index[0]).days/365.25
    return (x.iloc[-1]-1)*100, ((x.iloc[-1])**(1/yrs)-1)*100, (x/x.cummax()-1).min()*100, r.mean()*365/(r.std()*math.sqrt(365))
g = sub["gld"]/sub["gld"].iloc[0]; q = sub["qqq"]/sub["qqq"].iloc[0]
for nm, x in [("CORE rotation", eq_core), ("B&H GLD", g), ("B&H QQQ", q)]:
    t,c,d,s = met(x); p(f"   {nm:16s} total={t:+8.1f}%  CAGR={c:+6.2f}%  maxDD={d:6.2f}%  Sharpe={s:.2f}")
days_in_gold = float((sub["qqq"]<=sub["sma200"]).mean())
p(f"   fraction of days holding GOLD: {days_in_gold*100:.1f}%   (rotation beta to QQQ was ~0.54)")
p(f"   => capital-weighted gold contribution approx {days_in_gold*100:.0f}% of the book")

p("")
p("--- STRESS: what if 2025+ had NOT been a gold bull market? (regime dependence) ---")
for yr in [2022,2023,2024,2025,2026]:
    s2 = m[m.index.year==yr]
    if len(s2)<150: continue
    eq,_,_ = run(s2, 5)
    g2 = s2["gld"]/s2["gld"].iloc[0]; q2 = s2["qqq"]/s2["qqq"].iloc[0]
    p(f"   {yr}: CORE={(eq.iloc[-1]-1)*100:+7.1f}%   B&H GLD={(g2.iloc[-1]-1)*100:+7.1f}%   B&H QQQ={(q2.iloc[-1]-1)*100:+7.1f}%")

io.open(OUT,"w",encoding="utf-8").write("\n".join(lines))
p("")
p(f"[written to {OUT}]")
