import json, math, urllib.request, io, time
import os
import pandas as pd, numpy as np
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_predictions.txt")
lines=[]
def p(s=""):
    lines.append(str(s)); print(s)
def yahoo(sym,start="2018-01-01",end="2026-11-12"):
    p1=int(pd.Timestamp(start,tz="UTC").timestamp()); p2=int(pd.Timestamp(end,tz="UTC").timestamp())
    u=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d"
    req=urllib.request.Request(u,headers={"User-Agent":"Mozilla/5.0"})
    try: r=json.loads(urllib.request.urlopen(req,timeout=30).read())["chart"]["result"][0]
    except Exception as e: return None
    q=r["indicators"]["quote"][0]
    adj=r["indicators"].get("adjclose",[{}])[0].get("adjclose",q["close"])
    df=pd.DataFrame({"date":pd.to_datetime(r["timestamp"],unit="s",utc=True).tz_localize(None),
                     "close":q["close"],"adj":adj}).dropna(subset=["close"])
    return df.set_index("date")["adj"]
assets={}
for sym,lab in [("BTC-USD","BTC"),("GC=F","GOLD"),("SI=F","SILVER"),("^GSPC","SPX")]:
    s=yahoo(sym)
    if s is not None and len(s): assets[lab]=s
    time.sleep(0.5)
def px(a,d):
    s=assets.get(a)
    if s is None: return None
    s=s[s.index<=pd.Timestamp(d)]
    return float(s.iloc[-1]) if len(s) else None
def win(a,d0,d1):
    s=assets.get(a)
    if s is None: return None
    m=s[(s.index>=pd.Timestamp(d0))&(s.index<=pd.Timestamp(d1))]
    if len(m)==0: return None
    return float(m.iloc[0]),float(m.max()),float(m.min()),float(m.iloc[-1])
CLAIMS=[
 ("C1a","2023-10-24","BTC",+1,"2023-12-31","'2023 bull season, break above 69,000'"),
 ("C1b","2023-10-24","BTC",+1,"2024-12-31","'2023 bull season' (1y)"),
 ("C2a","2023-10-24","BTC",-1,"2024-12-31","'35,000 is the FINAL target' (ceiling)"),
 ("C3","2023-06-17","BTC",+1,"2023-12-31","'expect continuation of the rise'"),
 ("C4","2023-12-06","BTC",+1,"2024-12-31","'best crypto profit opportunities 2024'"),
 ("C5a","2023-11-09","BTC",+1,"2024-12-31","'big explosion to $100,000'"),
 ("C5b","2023-11-09","BTC",+1,"2025-12-31","'to $100,000' (2y)"),
 ("C6a","2023-11-03","BTC",-1,"2024-12-31","'upcoming collapse even worse' (1y)"),
 ("C6b","2023-11-03","BTC",-1,"2025-12-31","'upcoming collapse' (2y)"),
 ("C7","2023-10-28","BTC",+1,"2024-12-31","'the explosion is coming'"),
 ("C8","2024-01-04","BTC",+1,"2024-12-31","'excellent profit opportunity'"),
 ("C9a","2025-10-17","BTC",-1,"2025-12-31","'Bitcoin crashes due to platform manipulation'"),
 ("C9b","2025-10-17","BTC",-1,"2026-11-11","crash (13m)"),
 ("C10a","2025-06-06","BTC",-1,"2025-12-31","'Bitcoin in a catastrophe'"),
 ("C10b","2025-06-06","BTC",-1,"2026-11-11","catastrophe (17m)"),
 ("C11a","2025-12-28","BTC",-1,"2026-06-30","2026 H1 = manipulation and decline"),
 ("C11b","2025-12-28","BTC",-1,"2026-11-11","2026 = decline, bottom end Q4 2026"),
 ("C11c","2025-12-28","GOLD",-1,"2026-11-11","'gold, silver, US market ALL fall in 2026'"),
 ("C11d","2025-12-28","SPX",-1,"2026-11-11","'US market falls in 2026'"),
 ("C12","2026-01-31","BTC",-1,"2026-11-11","2026 forecasts (bearish)"),
 ("C12g","2026-01-31","GOLD",-1,"2026-11-11","gold forecast"),
 ("C13a","2026-11-07","BTC",-1,"2026-11-11","'historic crash coming soon'"),
 ("C13b","2026-11-07","SPX",-1,"2026-11-11","global markets crash"),
 ("C14","2026-10-30","BTC",-1,"2026-11-11","'crisis following Binance hack'"),
 ("C15","2026-07-04","BTC",+1,"2026-11-11","'start of a new rally'"),
]
def wilson(k,n,z=1.96):
    if n==0: return (0,0)
    ph=k/n
    c=(ph+z*z/(2*n))/(1+z*z/n)
    h=z/(1+z*z/n)*math.sqrt(ph*(1-ph)/n + z*z/(4*n*n))
    return (100*max(0,c-h), 100*min(1,c+h))
def evaluate(thr):
    rows=[]
    for cid,date,a,d,hend,note in CLAIMS:
        p0=px(a,date); w=win(a,date,hend)
        if p0 is None or w is None: continue
        f,hi,lo,last=w
        hit=(hi>p0*(1+thr)) if d>0 else (lo<p0*(1-thr))
        # baseline A: naive continuation (always predict the direction price went)
        trend_up = (last>=f)
        naive_hit = (hi>p0*(1+thr)) if trend_up else (lo<p0*(1-thr))
        # baseline B: random coin (expected 50%)
        # baseline C: buy&hold return in window
        bh = (last/p0-1)*100
        rows.append(dict(cid=cid,date=date,a=a,d=d,hit=hit,naive=naive_hit,bh=bh,
                         move=(hi/p0-1)*100 if d>0 else (lo/p0-1)*100, end=last, p0=p0))
    return rows
p("="*92); p("EA TRADING PREDICTION SCORECARD vs NAIVE BASELINES"); p("="*92)
p("")
p("Method: each claim = (video date, asset, direction, horizon). 'HIT' = price moved >=thr%")
p("in the predicted direction within the stated horizon. Baseline A = 'naive continuation':")
p("always predict the direction the market actually went (i.e. what a trend-follower who")
p("just looked at the chart would have said). Baseline C = buy & hold return over the window.")
p("")
for thr in (0.10, 0.25, 0.50):
    rows=evaluate(thr)
    k=sum(1 for r in rows if r["hit"]); n=len(rows)
    nk=sum(1 for r in rows if r["naive"])
    agree=sum(1 for r in rows if r["hit"]==r["naive"])
    bh=sum(r["bh"] for r in rows)/len(rows)
    lo,hi=wilson(k,n)
    klo,khi=wilson(nk,n)
    p(f"--- threshold +-{thr*100:.0f}%   (n={n} claims) ---")
    p(f"   EA Trading hit rate           : {100*k/n:5.1f}%  ({k}/{n})   95% CI [{lo:.0f}%, {hi:.0f}%]")
    p(f"   Naive-continuation baseline    : {100*nk/n:5.1f}%  ({nk}/{n})   95% CI [{klo:.0f}%, {khi:.0f}%]")
    p(f"   -> EXCESS over naive baseline : {100*(k-nk)/n:+5.1f} pp")
    p(f"   Agreement with naive baseline  : {100*agree/n:5.1f}% of calls were directionally identical to just following the tape")
    p(f"   Avg buy&hold return over same windows: {bh:+.1f}%")
    p("")
p("-"*92)
p("PER-CLAIM DETAIL (threshold +10%)")
p("-"*92)
rows=evaluate(0.10)
p(f"{'id':6s} {'date':11s} {'asset':5s} {'call':5s} {'result':6s} {'naive':6s} {'B&H':>8s}  note")
for r in rows:
    p(f"{r['cid']:6s} {r['date']:11s} {r['a']:5s} {'UP' if r['d']>0 else 'DOWN':5s} "
      f"{'HIT' if r['hit'] else 'MISS':6s} {'HIT' if r['naive'] else 'MISS':6s} {r['bh']:+7.1f}%  {r['cid']}")
p("")
p("-"*92)
p("SKEW CHECK: how much of the scorecard came from the single crypto bull run (Oct 2023 - Mar 2024)?")
p("-"*92)
bull=[r for r in rows if r["date"]<="2024-12-31"]
post=[r for r in rows if r["date"]>"2024-12-31"]
for nm,g in (("2023-2024 (bull run era)",bull),("2025-2026 (later)",post)):
    if not g: continue
    k=sum(1 for r in g if r["hit"]); nk=sum(1 for r in g if r["naive"])
    p(f"  {nm:26s} n={n:2d}  EA {100*k/len(g):5.1f}%  naive {100*nk/len(g):5.1f}%  excess {100*(k-nk)/len(g):+5.1f}pp")
p("")
p("-"*92)
p("DIRECTION-ONLY CALLS (explicit 'crash' / 'explosion' framing, excluding hedged ones)")
p("-"*92)
explicit=[("2023-11-03","BTC","collapse"),("2023-11-09","BTC","explosion to 100k"),
          ("2023-10-28","BTC","explosion coming"),("2025-10-17","BTC","crash"),
          ("2025-06-06","BTC","catastrophe"),("2026-11-07","BTC","historic crash"),
          ("2026-10-30","BTC","crisis"),("2026-07-04","BTC","new rally"),
          ("2025-12-28","BTC","2026 decline")]
k=n=0
for date,a,lab in explicit:
    m=[r for r in rows if r["date"]==date and r["a"]==a]
    if not m: continue
    r=m[0]; n+=1; k+= 1 if r["hit"] else 0
    p(f"  {date}  {lab:22s} -> {'HIT ' if r['hit'] else 'MISS'} (naive would be {'HIT ' if r['naive'] else 'MISS'})")
p(f"  explicit calls: {k}/{n} = {100*k/n:.0f}%")
io.open(OUT,"w",encoding="utf-8").write("\n".join(lines))
p(""); p(f"[written to {OUT}]")
