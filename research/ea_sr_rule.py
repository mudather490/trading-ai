import json, math, urllib.request, io, time
import os
import pandas as pd, numpy as np
OUT=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out_sr_rule.txt")
lines=[]
def p(s=""):
    lines.append(str(s)); print(s)
def coinbase(product="BTC-USD", start="2016-01-01", end="2026-11-11"):
    rows=[]; cur=start
    while cur < end:
        s=pd.Timestamp(cur,tz="UTC"); e=min(s+pd.Timedelta(days=290), pd.Timestamp(end,tz="UTC"))
        u=("https://api.exchange.coinbase.com/products/%s/candles?granularity=86400&start=%s&end=%s"
           % (product, s.strftime('%Y-%m-%d'), e.strftime('%Y-%m-%d')))
        req=urllib.request.Request(u,headers={"User-Agent":"research/1.0","Accept":"application/json"})
        try: d=json.loads(urllib.request.urlopen(req,timeout=30).read())
        except Exception as ex: p("  fetch err %s"%ex); break
        if not d: break
        rows+=d
        last=max(r[0] for r in d)
        nxt=(pd.Timestamp(last,unit="s",tz="UTC")+pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt<=cur: break
        cur=nxt; time.sleep(0.3)
        if len(rows)>20000: break
    df=pd.DataFrame(rows,columns=["ts","low","high","open","close","volume"])
    df=df.drop_duplicates("ts").sort_values("ts")
    df["date"]=pd.to_datetime(df["ts"],unit="s",utc=True).dt.tz_localize(None)
    return df[["date","open","high","low","close"]].reset_index(drop=True)

def prep(d, W=180):
    """Vectorized precompute: ATR, level, touch, rejection, rejection-count."""
    d=d.copy()
    pc=d["close"].shift(1)
    tr=pd.concat([d["high"]-d["low"],(d["high"]-pc).abs(),(d["low"]-pc).abs()],axis=1).max(axis=1)
    d["atr"]=tr.rolling(14).mean()
    d["fwd_max5"]=d["high"].rolling(5).max().shift(-5)
    d["fwd_min5"]=d["low"].rolling(5).min().shift(-5)
    d["Lsup"]=d["low"].rolling(W).min().shift(1)      # level known BEFORE the bar
    d["Lres"]=d["high"].rolling(W).max().shift(1)
    return d
def backtest(d, min_rej=2, reject_atr=0.8, touch_atr=1.0, stop_atr=1.5, target_r=2.0,
             risk_pct=0.01, fee_bps=10, slip_bps=5, fresh_only=True, W=180):
    one_way=(fee_bps+slip_bps)/10000.0
    low=d["low"].values; high=d["high"].values; close=d["close"].values
    atr=d["atr"].values; fmax=d["fwd_max5"].values; fmin=d["fwd_min5"].values
    Lsup=d["Lsup"].values; Lres=d["Lres"].values
    n=len(d)
    # vectorized rejection detection relative to the level known at that bar
    with np.errstate(invalid="ignore"):
        touch_s = np.abs(low-Lsup)<=touch_atr*atr
        touch_r = np.abs(high-Lres)<=touch_atr*atr
        rej_s = touch_s & (fmax >= Lsup+reject_atr*atr)
        rej_r = touch_r & (fmin <= Lres-reject_atr*atr)
    rej_s=pd.Series(rej_s).fillna(False).values
    rej_r=pd.Series(rej_r).fillna(False).values
    cs=pd.Series(rej_s).rolling(W).sum().shift(1).fillna(0).values   # rejections before bar i
    cr=pd.Series(rej_r).rolling(W).sum().shift(1).fillna(0).values
    cash=1.0; units=0.0; equity=[]; trades=[]; seen={}
    for i in range(60,n):
        a_i=atr[i]
        if not np.isfinite(a_i) or a_i<=0: continue
        px=close[i]
        if units==0:
            # long: confirmed support, price touches it and closes above
            if cs[i]>=min_rej and np.isfinite(Lsup[i]):
                L=Lsup[i]
                if low[i]<=L+touch_atr*a_i and px>L:
                    key=("s",round(L,1))
                    if not(fresh_only and key in seen):
                        seen[key]=i
                        dist=stop_atr*a_i; entry=px; stop=L-dist
                        sz=(cash*risk_pct)/max(abs(entry-stop),1e-9); cost=sz*entry*one_way
                        if sz*entry+cost>cash: sz=cash/(entry*(1+one_way)); cost=sz*entry*one_way
                        cash-=(sz*entry+cost); units=sz
                        trades.append({"entry_date":d["date"].iloc[i],"entry":entry,"stop":stop,
                                       "size":sz,"kind":"support","rej":int(cs[i]),"level":L,
                                       "risk":entry-stop})
            elif cr[i]>=min_rej and np.isfinite(Lres[i]):
                L=Lres[i]
                if high[i]>=L-touch_atr*a_i and px<L:
                    key=("r",round(L,1))
                    if not(fresh_only and key in seen):
                        seen[key]=i
                        dist=stop_atr*a_i; entry=px; stop=L+dist
                        sz=(cash*risk_pct)/max(abs(entry-stop),1e-9); cost=sz*entry*one_way
                        if sz*entry+cost>cash: sz=cash/(entry*(1+one_way)); cost=sz*entry*one_way
                        cash-=(sz*entry+cost); units=sz
                        trades.append({"entry_date":d["date"].iloc[i],"entry":entry,"stop":stop,
                                       "size":sz,"kind":"resistance","rej":int(cr[i]),"level":L,
                                       "risk":stop-entry})
        else:
            t0=trades[-1]
            risk=abs(t0["entry"]-t0["stop"])
            tgt=t0["entry"]+target_r*risk if t0["kind"]=="support" else t0["entry"]-target_r*risk
            ex=None; reason=None
            if t0["kind"]=="support":
                if low[i]<=t0["stop"]: ex,reason=t0["stop"],"stop"
                elif high[i]>=tgt: ex,reason=tgt,"target"
            else:
                if high[i]>=t0["stop"]: ex,reason=t0["stop"],"stop"
                elif low[i]<=tgt: ex,reason=tgt,"target"
            if ex is not None:
                t=trades[-1]; cost=units*ex*one_way; cash+=units*ex-cost
                t.update({"exit_date":d["date"].iloc[i],"exit":ex,"reason":reason,
                          "ret":(ex*(1-one_way))/(t["entry"]*(1+one_way))-1})
                units=0.0
        equity.append((d["date"].iloc[i], cash+units*px))
    if units>0 and trades:
        px=close[-1]; t=trades[-1]; cash+=units*px*(1-one_way)
        t.update({"exit_date":d["date"].iloc[-1],"exit":px,"reason":"eom",
                  "ret":(px*(1-one_way))/(t["entry"]*(1+one_way))-1})
    eq=pd.Series([v for _,v in equity], index=[dd for dd,_ in equity])
    return cash, trades, eq
def stats(eq,trades,label):
    if len(eq)<100: p("  %s: too short"%label); return {}
    r=eq.pct_change().dropna(); yrs=(eq.index[-1]-eq.index[0]).days/365.25
    dd=(eq/eq.cummax()-1).min()*100; vol=r.std()*math.sqrt(365); sr=r.mean()*365/vol if vol>0 else 0
    closed=[t for t in trades if "ret" in t]; wins=[t for t in closed if t["ret"]>0]
    gp=sum(t["ret"]*t["size"]*t["entry"] for t in wins)
    gl=abs(sum(t["ret"]*t["size"]*t["entry"] for t in closed if t["ret"]<=0))
    pf=gp/gl if gl>0 else float("inf"); wr=len(wins)/len(closed)*100 if closed else 0
    exp_=sum(t["ret"]*t["size"] for t in closed)/len(closed) if closed else 0
    p("  %s"%label)
    p("     total=%+.1f%%  CAGR=%+.2f%%  maxDD=%.2f%%  Sharpe=%.2f  trades=%d  winrate=%.1f%%  PF=%.2f"
      % ((eq.iloc[-1]-1)*100, ((eq.iloc[-1])**(1/yrs)-1)*100, dd, sr, len(closed), wr, pf))
    return {"total":(eq.iloc[-1]-1)*100,"cagr":((eq.iloc[-1])**(1/yrs)-1)*100,"dd":dd,"sharpe":sr,
            "n":len(closed),"wr":wr,"pf":pf,"eq":eq}
p("="*94); p("TESTING EA TRADING'S EXTRACTABLE RULE: 'CONFIRMED LEVEL' SUPPORT/RESISTANCE SYSTEM"); p("="*94)
raw=coinbase(); df=prep(raw)
p("  BTC-USD daily: %s -> %s  n=%d"%(df['date'].iloc[0].date(), df['date'].iloc[-1].date(), len(df)))
p("")
p("="*94); p("TEST 1 - course rule + engineer risk (min_rej=2, stop 1.5ATR, target 2R, 1% risk, 15bps rt)"); p("="*94)
c_,t_,e_=backtest(df); s1=stats(e_,t_,"S/R confirmed-level system")
p("")
p("="*94); p("TEST 2 - does the LEVEL-IDENTIFICATION rule matter? (min_rej 0/1/2/3)"); p("="*94)
for mr in (0,1,2,3):
    c_,tt,ee=backtest(df, min_rej=mr); stats(ee,tt,"min_rej=%d"%mr)
p("")
p("="*94); p("TEST 3 - 'fresh trade only' filter (Ichimoku video rule) ON vs OFF"); p("="*94)
for fo in (True,False):
    c_,tt,ee=backtest(df, fresh_only=fo); stats(ee,tt,"fresh_only=%s"%fo)
p("")
p("="*94); p("TEST 4 - cost sensitivity"); p("="*94)
for fee,slip in [(0,0),(5,2),(10,5),(20,10),(40,20)]:
    c_,tt,ee=backtest(df, fee_bps=fee, slip_bps=slip); stats(ee,tt,"fee=%dbps slip=%dbps"%(fee,slip))
p("")
p("="*94); p("TEST 5 - BASELINE: buy & hold BTC, same window"); p("="*94)
bh=df["close"]/df["close"].iloc[0]; r=bh.pct_change().dropna()
yrs=(df['date'].iloc[-1]-df['date'].iloc[0]).days/365.25
bh_sr=r.mean()*365/(r.std()*math.sqrt(365))
p("  BUY & HOLD BTC: total=%+.1f%%  CAGR=%+.2f%%  maxDD=%.2f%%  Sharpe=%.2f"
  % ((bh.iloc[-1]-1)*100, (bh.iloc[-1])**(1/yrs)*100-100, (bh/bh.cummax()-1).min()*100, bh_sr))
p("")
p("="*94); p("TEST 6 - OUT-OF-SAMPLE: select on 2017-2021, apply to 2022+ (single holdout)"); p("="*94)
tr=prep(raw[raw["date"]<="2021-12-31"].reset_index(drop=True))
te=prep(raw[raw["date"]>"2021-12-31"].reset_index(drop=True))
best=None; bs=-9
for mr in (0,1,2,3):
    for stop in (1.0,1.5,2.0,3.0):
        for trg in (1.0,1.5,2.0,3.0):
            c_,tt,ee=backtest(tr, min_rej=mr, stop_atr=stop, target_r=trg)
            if len(ee)<100: continue
            rr=ee.pct_change().dropna(); sr=rr.mean()*365/(rr.std()*math.sqrt(365))
            if sr>bs: bs=sr; best=(mr,stop,trg)
mr,stop,trg=best
p("  selected on 2017-2021 (Sharpe %.2f): min_rej=%d, stop=%.1fATR, target=%.1fR"%(bs,mr,stop,trg))
c_,tt,ee=backtest(te, min_rej=mr, stop_atr=stop, target_r=trg); stats(ee,tt,"OOS 2022+ IS-selected params")
c_,tt,ee=backtest(te, min_rej=2, stop_atr=1.5, target_r=2.0); stats(ee,tt,"OOS 2022+ default params")
bh2=te["close"]/te["close"].iloc[0]; r2=bh2.pct_change().dropna()
yrs2=(te['date'].iloc[-1]-te['date'].iloc[0]).days/365.25
p("  OOS BUY & HOLD BTC: total=%+.1f%%  CAGR=%+.2f%%  maxDD=%.2f%%  Sharpe=%.2f"
  % ((bh2.iloc[-1]-1)*100, (bh2.iloc[-1])**(1/yrs2)*100-100, (bh2/bh2.cummax()-1).min()*100, r2.mean()*365/(r2.std()*math.sqrt(365))))
p("")
p("="*94); p("VERDICT"); p("="*94)
if s1:
    p("  S/R system Sharpe %.2f on %d trades vs buy&hold Sharpe %.2f"%(s1["sharpe"],s1["n"],bh_sr))
    p("  -> %s"%("PROMISING but unproven" if (s1["sharpe"]>0.3 and s1["n"]>=30) else "NOT DEMONSTRATED"))
io.open(OUT,"w",encoding="utf-8").write("\n".join(lines))
p(""); p("[written to %s]"%OUT)
