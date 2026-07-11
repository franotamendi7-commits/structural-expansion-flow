"""
WALK-FORWARD RF FINAL — Enero 2026 → Hoy
=========================================
Vela por vela · ML Filter real (Random Forest de ml_model.pkl)
Todas las 20 features computadas como en producción.
"""
import sys, json, math, time, gc
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from ml_filter import debe_ejecutar, cargar_modelo
from multi_bot import BINANCE_FAPI

COMMISSION = 0.0005
SPREAD = 0.0002
INITIAL_EQUITY = 100.0
WARMUP = 500

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
    return {"atr14": atr, "vwaps": vwaps, "devs": devs, "tr": tr, "h": h, "l": l, "c": c, "v": v}

def run():
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    cargar_modelo()
    from multi_bot import compute_ml_features
    print("="*72)
    print("  WALK-FORWARD RF: vela por vela · ML real · ene→hoy")
    print("="*72)

    portfolio_trades = []

    for bname, cfg in sorted(BOTS.items()):
        sym = cfg["symbol"]
        print(f"\n  {bname} ({sym})")
        print(f"  Fetching data...", end=" ", flush=True)
        df15 = fetch_range(sym, "15m", start_date, end_date)
        if df15.empty or len(df15) < 1000: print("SKIP"); continue
        n15 = len(df15)
        df5 = fetch_range(sym, "5m", start_date, end_date)
        if df5.empty: df5 = df15.copy()
        df1h = fetch_range(sym, "1h", start_date, end_date)
        if df1h.empty: df1h = None
        print(f"{n15} bars 15m")

        ind = precompute(df15)
        vn = cfg["vn"]; dt = cfg["dt"]; sl_m = cfg["sl"]
        tp_m = cfg["tp"]; rp = cfg["rp"]; ms = cfg["ms"]
        dev_arr = ind["devs"][vn]; atr_arr = ind["atr14"]
        ca = ind["c"]; ot = df15["open_time"].values

        eq = INITIAL_EQUITY; trades = []; pos = None

        for i in range(WARMUP, n15):
            c = float(ca[i]); row = df15.iloc[i]
            t = row["open_time"]
            if pd.isna(c): continue

            # EXIT
            if pos is not None:
                exited = False; exit_reason = ""
                if pos["dir"] == 1:
                    if c <= pos["sl"]:
                        ep = pos["sl"] * (1 - SPREAD)
                        pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                        exited = True; exit_reason = "sl"
                    elif c >= pos["tp"]:
                        ep = pos["tp"] * (1 - SPREAD)
                        pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                        exited = True; exit_reason = "tp"
                else:
                    if c >= pos["sl"]:
                        ep = pos["sl"] * (1 + SPREAD)
                        pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                        exited = True; exit_reason = "sl"
                    elif c <= pos["tp"]:
                        ep = pos["tp"] * (1 + SPREAD)
                        pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                        exited = True; exit_reason = "tp"
                if exited:
                    eq += pnl
                    trades.append({"dir": pos["dir"], "pnl": pnl, "reason": exit_reason,
                                   "entry": float(pos["entry"]), "exit": float(ep)})
                    pos = None
                    continue

            # SIGNAL: VWAP deviation breakout
            dv = float(dev_arr[i]) if not np.isnan(dev_arr[i]) else None
            dvp = float(dev_arr[i-1]) if i > 0 and not np.isnan(dev_arr[i-1]) else dv
            a = float(atr_arr[i]) if not np.isnan(atr_arr[i]) else 0
            if dv is None or a <= 0: continue

            long_ok = dvp >= -dt and dv < -dt
            short_ok = dvp <= dt and dv > dt
            if not long_ok and not short_ok: continue
            sig_dir = 1 if long_ok else -1

            # ML FILTER (solo features realmente disponibles en esta barra)
            df15_i = df15.iloc[max(0,i-60):i+1].reset_index(drop=True)
            df5_i = df5[df5["open_time"] <= t].iloc[-60:].reset_index(drop=True) if len(df5) > 0 else df15_i
            df1h_i = df1h[df1h["open_time"] <= t].iloc[-60:].reset_index(drop=True) if df1h is not None else None
            try:
                feats = compute_ml_features(sym, df15_i, df5_i, df1h_i, sig_dir)
                feats["hour_of_day"] = t.hour
                ok, prob = debe_ejecutar(feats)
                if not ok: continue
            except Exception as e:
                continue

            # ENTRY
            price_range = a * sl_m
            if price_range <= 0: continue
            size = (rp * eq) / price_range
            if size < ms: continue
            entry_fill = c * (1 + SPREAD) if sig_dir == 1 else c * (1 - SPREAD)
            entry_comm = entry_fill * size * COMMISSION
            eq -= entry_comm
            pos = {"dir": sig_dir, "entry": entry_fill, "size": size,
                   "sl": c - sl_m * a if sig_dir == 1 else c + sl_m * a,
                   "tp": c + tp_m * a if sig_dir == 1 else c - tp_m * a}

        # Results per bot
        if not trades:
            print(f"  ⏭ 0 trades (RF vetó todo)")
            continue
        pnl_t = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = wins/len(trades)*100
        ret = (eq/INITIAL_EQUITY-1)*100
        eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY; dd_m = 0
        for t in trades:
            eq_c += t["pnl"]; peak = max(peak, eq_c)
            dd_m = min(dd_m, (eq_c-peak)/peak*100)
        print(f"  ✅ {len(trades)}t | PnL ${pnl_t:+.2f} | Eq ${eq:.2f} ({ret:+.2f}%) | "
              f"WR {wr:.1f}% | DD {dd_m:.1f}%")
        for t in trades: t["bot"] = bname; portfolio_trades.append(t)

    # PORTFOLIO
    print(f"\n{'='*72}")
    if portfolio_trades:
        tp = sum(t["pnl"] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t["pnl"] > 0)
        pf = INITIAL_EQUITY * len(BOTS) + tp
        pr = (pf / (INITIAL_EQUITY * len(BOTS)) - 1) * 100
        eq_c = INITIAL_EQUITY * len(BOTS); peak = eq_c; dd_m = 0
        for t in portfolio_trades:
            eq_c += t["pnl"]; peak = max(peak, eq_c)
            dd_m = min(dd_m, (eq_c-peak)/peak*100)
        pnls = np.array([t["pnl"] for t in portfolio_trades])
        sharpe = float(np.mean(pnls)/np.std(pnls)*np.sqrt(len(pnls))) if np.std(pnls) > 0.01 else 0
        print(f"  PORTFOLIO ({len(portfolio_trades)} trades)")
        print(f"  PnL:      ${tp:+.2f}")
        print(f"  Equity:   ${pf:.2f} ({pr:+.2f}%)")
        print(f"  Win Rate: {tw/len(portfolio_trades)*100:.1f}%")
        print(f"  Max DD:   {dd_m:.1f}%")
        print(f"  Sharpe:   {sharpe:.2f}")
        print(f"{'='*72}")

        # Monte Carlo
        mc = []
        for _ in range(1000):
            s = np.random.choice(pnls, size=len(pnls), replace=True)
            mc.append(float(np.sum(s)/(INITIAL_EQUITY*len(BOTS))*100))
        mc = np.array(mc)
        print(f"\n  MONTE CARLO (1000 sims)")
        print(f"  Media:     {mc.mean():+.2f}%")
        print(f"  Mediana:   {np.median(mc):+.2f}%")
        print(f"  P5:        {np.percentile(mc,5):+.2f}%")
        print(f"  P95:       {np.percentile(mc,95):+.2f}%")
        print(f"  P(pos):    {np.mean(mc>0)*100:.1f}%")
        print(f"\n  {'✅ EDGE CONFIRMADO' if np.percentile(mc,5) > 0 else '❌ SIN EDGE'}")
        print(f"{'='*72}")

        Path("walkforward_rf_results.json").write_text(json.dumps({
            "portfolio": {
                "trades": len(portfolio_trades), "pnl": round(tp,2),
                "equity": round(pf,2), "return_pct": round(pr,2),
                "wr": round(tw/len(portfolio_trades)*100,1), "dd": round(dd_m,2),
                "sharpe": round(sharpe,2),
            },
            "monte_carlo": {
                "mean": round(float(mc.mean()),2), "median": round(float(np.median(mc)),2),
                "p5": round(float(np.percentile(mc,5)),2),
                "p95": round(float(np.percentile(mc,95)),2),
                "prob_positive": round(float(np.mean(mc>0)*100),1),
            }
        }, indent=2))
        print(f"\n  Resultados guardados en walkforward_rf_results.json")
    else:
        print(f"  NO TRADES — RF vetó todo el portfolio")
        print(f"{'='*72}")

if __name__ == "__main__":
    run()
