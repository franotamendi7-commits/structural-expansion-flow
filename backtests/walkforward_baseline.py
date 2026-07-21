"""
WALK-FORWARD BASELINE (sin ML) — Enero 2026 → Hoy
Misma estrategia VWAP, mismos parámetros, sin RF filter.
"""
import sys, json, math, time
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from multi_bot import BINANCE_FAPI

COMMISSION = 0.0005; SPREAD = 0.0002
INITIAL_EQUITY = 100.0; WARMUP = 500

BOTS = {
    "BTC": {"symbol": "BTCUSDT", "vn": 15, "dt": 0.75, "sl": 1.2, "tp": 1.5, "rp": 0.0075, "ms": 0.001},
    "ETH": {"symbol": "ETHUSDT", "vn": 10, "dt": 1.0,  "sl": 1.5, "tp": 1.5, "rp": 0.0085, "ms": 0.001},
    "SOL": {"symbol": "SOLUSDT", "vn": 12, "dt": 1.2,  "sl": 1.2, "tp": 1.5, "rp": 0.01,   "ms": 0.01},
    "XRP": {"symbol": "XRPUSDT", "vn": 10, "dt": 1.0,  "sl": 1.2, "tp": 1.5, "rp": 0.01,   "ms": 0.1},
    "BNB": {"symbol": "BNBUSDT", "vn": 10, "dt": 1.0,  "sl": 1.2, "tp": 1.2, "rp": 0.01,   "ms": 0.01},
}

def fetch_range(symbol, interval, start_dt, end_dt):
    all_bars = []; max_iter = 50; n = 0
    start = start_dt
    while start < end_dt and n < max_iter:
        n += 1
        params = {"symbol": symbol, "interval": interval, "limit": 1500}
        params["startTime"] = int(start.timestamp() * 1000)
        for _ in range(3):
            try:
                r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines", params=params, timeout=30)
                if r.status_code == 200: break
            except: pass
        else: break
        rows = r.json()
        if not rows: break
        cols = ["open_time","open","high","low","close","volume",
                "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
        df = pd.DataFrame(rows, columns=cols)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
        all_bars.append(df)
        start = df["open_time"].iloc[-1] + timedelta(minutes=1)
        time.sleep(0.3)
    if not all_bars: return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    full = full[full["open_time"] < end_dt].reset_index(drop=True)
    return full

def precompute(df15):
    h, l, c, v = df15["high"].values, df15["low"].values, df15["close"].values, df15["volume"].values
    tp = (h + l + c) / 3.0; n = len(df15)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = np.full(n, np.nan); atr[13] = tr[:14].mean()
    for i in range(14, n): atr[i] = (atr[i-1] * 13 + tr[i]) / 14
    vwaps_p = [8, 10, 12, 15, 20, 50, 200]
    vwaps = {}
    for p in vwaps_p:
        vw = np.full(n, np.nan)
        if p <= n:
            vp = tp * v; cs = np.cumsum(vp); cv = np.cumsum(v)
            vw[p-1:] = (cs[p-1:] - np.concatenate([[0], cs[:-p]])) / (cv[p-1:] - np.concatenate([[0], cv[:-p]]))
        vwaps[p] = vw
    devs = {}
    for p, vw in vwaps.items():
        devs[p] = (c - vw) / vw * 100.0
    return {"atr14": atr, "vwaps": vwaps, "devs": devs, "c": c}

def run():
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)
    print("="*72)
    print("  BASELINE (sin ML): misma estrategia VWAP, ene→hoy")
    print("="*72)
    portfolio_trades = []
    for bname, cfg in sorted(BOTS.items()):
        sym = cfg["symbol"]
        print(f"\n  {bname} ({sym})")
        print(f"  Fetching...", end=" ", flush=True)
        df15 = fetch_range(sym, "15m", start_date, end_date)
        if df15.empty or len(df15) < 1000: print("SKIP"); continue
        print(f"{len(df15)} bars")
        ind = precompute(df15)
        vn=cfg["vn"]; dt=cfg["dt"]; sl_m=cfg["sl"]; tp_m=cfg["tp"]; rp=cfg["rp"]; ms=cfg["ms"]
        da=ind["devs"][vn]; aa=ind["atr14"]; ca=ind["c"]
        eq=INITIAL_EQUITY; trades=[]; pos=None
        for i in range(WARMUP, len(df15)):
            c=float(ca[i])
            if pos:
                ex=False
                if pos["dir"]==1:
                    if c<=pos["sl"]: ep=pos["sl"]*(1-SPREAD); pnl=(ep-pos["entry"])*pos["size"]-(pos["entry"]+ep)*pos["size"]*COMMISSION; ex=True
                    elif c>=pos["tp"]: ep=pos["tp"]*(1-SPREAD); pnl=(ep-pos["entry"])*pos["size"]-(pos["entry"]+ep)*pos["size"]*COMMISSION; ex=True
                else:
                    if c>=pos["sl"]: ep=pos["sl"]*(1+SPREAD); pnl=(pos["entry"]-ep)*pos["size"]-(pos["entry"]+ep)*pos["size"]*COMMISSION; ex=True
                    elif c<=pos["tp"]: ep=pos["tp"]*(1+SPREAD); pnl=(pos["entry"]-ep)*pos["size"]-(pos["entry"]+ep)*pos["size"]*COMMISSION; ex=True
                if ex:
                    eq+=pnl; trades.append({"dir":pos["dir"],"pnl":pnl}); pos=None; continue
            dv=float(da[i]) if not np.isnan(da[i]) else None; dvp=float(da[i-1]) if i>0 and not np.isnan(da[i-1]) else dv
            a=float(aa[i]) if not np.isnan(aa[i]) else 0
            if dv is None or a<=0: continue
            lo = dvp >= -dt and dv < -dt
            so = dvp <= dt and dv > dt
            if not lo and not so: continue
            sd = 1 if lo else -1
            pr = a * sl_m
            if pr <= 0: continue
            sz = (rp * eq) / pr
            if sz < ms: continue
            ef = c * (1 + SPREAD) if sd == 1 else c * (1 - SPREAD)
            ec = ef * sz * COMMISSION
            eq -= ec
            pos = {"dir": sd, "entry": ef, "size": sz,
                   "sl": c - sl_m * a if sd == 1 else c + sl_m * a,
                   "tp": c + tp_m * a if sd == 1 else c - tp_m * a}
        if not trades: print(f"  ⏭ 0t"); continue
        pt=sum(t["pnl"] for t in trades); w=sum(1 for t in trades if t["pnl"]>0)
        wr=w/len(trades)*100; ret=(eq/INITIAL_EQUITY-1)*100
        print(f"  ✅ {len(trades)}t | PnL ${pt:+.2f} | Eq ${eq:.2f} ({ret:+.2f}%) | WR {wr:.1f}%")
        for t in trades: t["bot"]=bname; portfolio_trades.append(t)

    if portfolio_trades:
        tp=sum(t["pnl"] for t in portfolio_trades); tw=sum(1 for t in portfolio_trades if t["pnl"]>0)
        pf=INITIAL_EQUITY*len(BOTS)+tp; pr=(pf/(INITIAL_EQUITY*len(BOTS))-1)*100
        print(f"\n{'='*72}")
        print(f"  BASELINE PORTFOLIO: {len(portfolio_trades)}t | PnL ${tp:+.2f} | Eq ${pf:.2f} ({pr:+.2f}%) | WR {tw/len(portfolio_trades)*100:.1f}%")
        print(f"{'='*72}")
        Path("baseline_results.json").write_text(json.dumps({
            "portfolio":{"trades":len(portfolio_trades),"pnl":round(tp,2),"equity":round(pf,2),"return_pct":round(pr,2),"wr":round(tw/len(portfolio_trades)*100,1)}},indent=2))

if __name__ == "__main__":
    run()
