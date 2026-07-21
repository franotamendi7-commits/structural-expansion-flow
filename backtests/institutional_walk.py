"""
OUT-OF-SAMPLE WALK-FORWARD INSTITUCIONAL v2
=========================================
- Multi-timeframe confluence (15m + 1h VWAP) como sizing modifier
- Volatility-adjusted position sizing
- Rolling walk-forward mensual
- Monte Carlo: 1000 simulaciones
- Costos realistas: maker 0.02%, taker 0.04%, spread 0.03%, slippage 0.10%
"""
import sys, json, math, time, random
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BINANCE_FAPI = "https://fapi.binance.com"

COMMISSION_MAKER = 0.0002
COMMISSION_TAKER = 0.0004
SPREAD = 0.0003
SLIPPAGE_SL = 0.0010
SLIPPAGE_TP = 0.0005

INITIAL_EQUITY = 500.0
WARMUP = 400
CACHE_DIR = Path(__file__).parent / "cache_15m"
CACHE_DIR_1H = Path(__file__).parent / "cache_1h"

random.seed(2026)
np.random.seed(2026)

SYMBOLS = ['BNBUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']

# ================================================================
# FETCH
# ================================================================
def fetch_klines(symbol, interval, limit=1500, start_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time: params["startTime"] = int(start_time.timestamp() * 1000)
    for _ in range(5):
        try:
            r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines", params=params, timeout=30)
            if r.status_code == 200: break
        except: pass
        time.sleep(1)
    else: return pd.DataFrame()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
    return df

def fetch_cached(symbol, interval, start_dt, end_dt, cache_dir):
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{symbol}_{interval}.npy"
    if cache_file.exists():
        try:
            arr = np.load(cache_file, allow_pickle=True)
            df = pd.DataFrame(arr.tolist(), columns=["open_time","open","high","low","close","volume"])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
            if len(df) > 15000: return df
        except: pass
    all_bars = []; start = start_dt; max_iter = 60
    while start < end_dt and len(all_bars) < max_iter:
        b = fetch_klines(symbol, interval, 1500, start_time=start)
        if b.empty: break
        all_bars.append(b)
        start = b["open_time"].iloc[-1] + timedelta(minutes=1)
    if not all_bars: return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    full = full[full["open_time"] < end_dt].reset_index(drop=True)
    arr = np.array(full[["open_time","open","high","low","close","volume"]].values, dtype=object)
    np.save(cache_file, arr)
    return full

# ================================================================
# INDICADORES
# ================================================================
def precompute_all(df):
    h, l, c, v = df["high"].values, df["low"].values, df["close"].values, df["volume"].values
    tp = (h + l + c) / 3.0; n = len(df)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = np.full(n, np.nan); atr[13] = tr[:14].mean()
    for i in range(14, n): atr[i] = (atr[i-1] * 13 + tr[i]) / 14
    vp = tp * v; csvp = np.cumsum(vp); csv = np.cumsum(v)
    vwaps = {}; devs = {}
    for period in [5,6,7,8,10,12,14,15,16,18,20,25,30,50,100,200]:
        vw = np.full(n, np.nan)
        if period <= n:
            vw[period-1:] = (csvp[period-1:] - np.concatenate([[0], csvp[:-period]])) / \
                            (csv[period-1:] - np.concatenate([[0], csv[:-period]]))
        vwaps[period] = vw
        devs[period] = (c - vw) / vw * 100.0
    return atr, vwaps, devs

# ================================================================
# 1H VWAP
# ================================================================
def precompute_1h_vwap(df_1h, period=20):
    h, l, c, v = df_1h["high"].values, df_1h["low"].values, df_1h["close"].values, df_1h["volume"].values
    tp = (h + l + c) / 3.0; n = len(df_1h)
    vp = tp * v; csvp = np.cumsum(vp); csv = np.cumsum(v)
    vwap = np.full(n, np.nan)
    if period <= n:
        vwap[period-1:] = (csvp[period-1:] - np.concatenate([[0], csvp[:-period]])) / \
                          (csv[period-1:] - np.concatenate([[0], csv[:-period]]))
    return vwap

def get_1h_bias_at(df_1h, vwap_1h, timestamp_15m):
    """1=bullish, -1=bearish, 0=neutral"""
    ts = pd.Timestamp(timestamp_15m).tz_localize('UTC') if pd.Timestamp(timestamp_15m).tzinfo is None else pd.Timestamp(timestamp_15m)
    mask = df_1h["open_time"] <= ts
    if not mask.any(): return 0
    idx = int(mask.values.sum()) - 1
    if idx < 0 or idx >= len(df_1h): return 0
    c1h = float(df_1h["close"].iloc[idx])
    vw1h = float(vwap_1h[idx])
    if not np.isfinite(vw1h) or pd.isna(vw1h) or vw1h <= 0: return 0
    return 1 if c1h > vw1h else (-1 if c1h < vw1h else 0)

# ================================================================
# WALK BOT (único, usado en train y test)
# ================================================================
def walk_bot(df, atr, vwaps, devs, params, df_1h=None, vwap_1h=None, start_idx=0):
    """Un solo walk_bot para train y test con multi-TF + vol-sizing."""
    eq = 100.0
    trades = []
    pos = None
    ca = df["close"].values; n = len(df)
    times = df["open_time"].values
    vn = params["vn"]; dt = params["dt"]; sl = params["sl"]
    tp = params["tp"]; rp = params["rp"]; ms = params["ms"]
    tp_ = params.get("tp_", 50)

    start = max(WARMUP, start_idx)
    for i in range(start, n):
        c = float(ca[i])

        if pos is not None:
            exited = False
            if pos["dir"] == 1:
                if c <= pos["sl"]:
                    ep = pos["sl"] * (1 - SPREAD - SLIPPAGE_SL)
                    pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION_TAKER
                    exited = True
                elif c >= pos["tp"]:
                    ep = pos["tp"] * (1 - SPREAD - SLIPPAGE_TP)
                    pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION_MAKER
                    exited = True
            else:
                if c >= pos["sl"]:
                    ep = pos["sl"] * (1 + SPREAD + SLIPPAGE_SL)
                    pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION_TAKER
                    exited = True
                elif c <= pos["tp"]:
                    ep = pos["tp"] * (1 + SPREAD + SLIPPAGE_TP)
                    pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION_MAKER
                    exited = True
            if exited:
                eq += pnl
                trades.append({"dir": pos["dir"], "pnl": pnl, "entry": float(pos["entry"]), "exit": float(ep)})
                pos = None
                continue

        if pos is not None: continue

        b_bias = 0
        if tp_ in vwaps:
            vs = vwaps[tp_]
            if not np.isnan(vs[i]) and i >= 5:
                slope = vs[i] - vs[i-5]
                if c > vs[i] and slope > 0: b_bias = 1
                elif c < vs[i] and slope < 0: b_bias = -1

        if vn not in devs: continue
        da = devs[vn]
        dv = float(da[i]); dvp = float(da[i-1]) if i > 0 else dv
        a = float(atr[i])
        if not math.isfinite(a) or a <= 0 or not math.isfinite(dv): continue

        long_ok = (b_bias >= 0) and dvp >= -dt and dv < -dt
        short_ok = (b_bias <= 0) and dvp <= dt and dv > dt
        if not long_ok and not short_ok: continue

        sig_dir = 1 if long_ok else -1
        raw_sl = c - sl * a if long_ok else c + sl * a
        raw_tp = c + tp * a if long_ok else c - tp * a

        price_range = abs(c - raw_sl)
        if price_range <= 0: continue

        # ---- Sizing modifiers ----
        size_mult = 1.0

        # 1h multi-timeframe modifier
        if df_1h is not None and vwap_1h is not None:
            h1_bias = get_1h_bias_at(df_1h, vwap_1h, times[i])
            if h1_bias == sig_dir:
                size_mult *= 1.5
            elif h1_bias == -sig_dir:
                size_mult *= 0.67

        # Volatility modifier
        if i >= 100:
            recent = atr[max(0, i-100):i+1] / ca[max(0, i-100):i+1] * 100
            p66 = np.percentile(recent, 66)
            p33 = np.percentile(recent, 33)
            atr_pct = a / c * 100
            if atr_pct > p66:
                size_mult *= 1.3
            elif atr_pct < p33:
                size_mult *= 0.75

        adjusted_rp = rp * size_mult
        adjusted_rp = min(adjusted_rp, 0.035)
        size = (adjusted_rp * eq) / price_range
        if size < ms: continue

        entry_fill = c * (1 + SPREAD) if long_ok else c * (1 - SPREAD)
        entry_comm = entry_fill * size * COMMISSION_MAKER
        eq -= entry_comm
        pos = {"dir": sig_dir, "entry": entry_fill, "size": size, "sl": raw_sl, "tp": raw_tp}

    return trades, eq

# ================================================================
# OPTIMIZADOR
# ================================================================
def find_params(sym, df_train, atr, vwaps, devs, df_1h, vwap_1h, n_samples=250):
    candidates = []
    for s in range(n_samples):
        params = {
            "vn": random.choice([6, 8, 10, 12, 15, 20]),
            "dt": random.choice([0.5, 0.75, 1.0, 1.25, 1.5, 2.0]),
            "sl": random.choice([0.75, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]),
            "tp": random.choice([1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]),
            "tp_": random.choice([20, 30, 50, 100, 200]),
            "rp": random.choice([0.008, 0.01, 0.012, 0.015, 0.018, 0.02, 0.025]),
            "ms": 0.001 if sym != "XRPUSDT" else 0.1,
        }
        if sym in ("BNBUSDT", "SOLUSDT"): params["ms"] = 0.01

        trades, final_eq = walk_bot(df_train, atr, vwaps, devs, params, df_1h, vwap_1h)
        if len(trades) < 30: continue

        pnls = [t["pnl"] for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / len(pnls)
        ret = (final_eq / 100.0 - 1.0) * 100
        sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(len(pnls))) if np.std(pnls) > 0.01 else 0
        dd = 0
        eq_c = 100.0; peak = 100.0
        for t in trades: eq_c += t["pnl"]; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)

        score = sharpe * math.sqrt(len(pnls)) * (1 + dd/20) if dd > -20 else -999
        candidates.append((score, params, ret, wr, dd, len(trades), sharpe))

    candidates.sort(key=lambda x: -x[0])
    return candidates[0] if candidates else None

# ================================================================
# ROLLING WALK-FORWARD
# ================================================================
def rolling_forward():
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime(2026, 7, 11, tzinfo=timezone.utc)

    test_months = [
        ("ENE-MAR→ABR", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 4, 1, tzinfo=timezone.utc),
                         datetime(2026, 4, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc)),
        ("ENE-ABR→MAY", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 5, 1, tzinfo=timezone.utc),
                         datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)),
        ("ENE-MAY→JUN", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc),
                         datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc)),
        ("ENE-JUN→JUL", datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 7, 1, tzinfo=timezone.utc),
                         datetime(2026, 7, 1, tzinfo=timezone.utc), datetime(2026, 7, 11, tzinfo=timezone.utc)),
    ]

    print("=" * 72)
    print("  OUT-OF-SAMPLE ROLLING WALK-FORWARD v2")
    print("  Mejoras: multi-timeframe (1h sizing) + volatility-adjusted sizing")
    print("  Cada mes: train on past data - test on NEXT month (100% out-of-sample)")
    print("=" * 72)

    all_data = {}
    for sym in SYMBOLS:
        print(f"\n  {sym}: fetching 15m...", end=" ", flush=True)
        df = fetch_cached(sym, "15m", start_date, end_date, CACHE_DIR)
        atr, vwaps, devs = precompute_all(df)
        print(f"{len(df)} bars | 1h...", end=" ", flush=True)
        df_1h = fetch_cached(sym, "1h", start_date, end_date, CACHE_DIR_1H)
        vwap_1h = precompute_1h_vwap(df_1h) if len(df_1h) > 0 else None
        print(f"{len(df_1h)} bars")
        all_data[sym] = (df, atr, vwaps, devs, df_1h, vwap_1h)

    all_oot_trades = []
    rolling_params = {}

    for label, train_start, train_end, test_start, test_end in test_months:
        print(f"\n{'=' * 72}")
        print(f"  ROLLING: Train {label}")
        print(f"{'=' * 72}")

        period_trades = []
        for sym in SYMBOLS:
            df, atr, vwaps, devs, df_1h, vwap_1h = all_data[sym]

            train_mask = (df["open_time"] >= train_start) & (df["open_time"] < train_end)
            train_indices = df[train_mask].index
            if len(train_indices) == 0:
                print(f"  {sym}: no training data")
                continue
            train_start_idx = train_indices[0]
            df_train = df.iloc[:train_indices[-1]+1].reset_index(drop=True)
            atr_t, vwaps_t, devs_t = precompute_all(df_train)

            print(f"  {sym}: training on {len(train_indices)} bars...", end=" ", flush=True)
            result = find_params(sym, df_train, atr_t, vwaps_t, devs_t, df_1h, vwap_1h, n_samples=250)
            if result is None:
                print(f"NO VALID PARAMS")
                continue
            score, params, ret, wr, dd, nt, sharpe = result
            print(f"nt={nt} ret={ret:.1f}% wr={wr*100:.0f}% sharpe={sharpe:.2f} score={score:.1f}")
            rolling_params[f"{sym}_{label}"] = {"params": params, "train_ret": ret, "train_wr": round(wr*100,1)}

            test_mask = (df["open_time"] >= test_start) & (df["open_time"] < test_end)
            test_indices = df[test_mask].index
            if len(test_indices) == 0:
                print(f"  {sym}: no test data")
                continue
            test_start_idx = test_indices[0]

            trades_test, _ = walk_bot(df, atr, vwaps, devs, params, df_1h, vwap_1h, start_idx=test_start_idx)
            if trades_test:
                pnl = sum(t["pnl"] for t in trades_test)
                wins = sum(1 for t in trades_test if t["pnl"] > 0)
                wr_t = wins / len(trades_test) * 100
                print(f"  → TEST: {len(trades_test)}t PnL=${pnl:+.2f} WR={wr_t:.1f}%")
                for t in trades_test:
                    t["sym"] = sym; t["period"] = label
                    period_trades.append(t)
                    all_oot_trades.append(t)
            else:
                print(f"  → TEST: 0 trades")

        if period_trades:
            tp = sum(t["pnl"] for t in period_trades)
            print(f"  PERIOD PnL: ${tp:+.2f}")
        else:
            print(f"  PERIOD: no trades")

    # ================================================================
    # FINAL REPORT
    # ================================================================
    print(f"\n{'=' * 72}")
    print(f"  RESULTADOS 100% OUT-OF-SAMPLE")
    print(f"{'=' * 72}")

    if not all_oot_trades:
        print("  NO TRADES")
        return

    total_pnl = sum(t["pnl"] for t in all_oot_trades)
    wins = sum(1 for t in all_oot_trades if t["pnl"] > 0)
    nt = len(all_oot_trades)
    wr = wins / nt * 100
    portfolio_eq = INITIAL_EQUITY + total_pnl
    portfolio_ret = (portfolio_eq / INITIAL_EQUITY - 1) * 100

    eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY; dd_max = 0
    for t in all_oot_trades:
        eq_c += t["pnl"]; peak = max(peak, eq_c); dd_max = min(dd_max, (eq_c-peak)/peak*100)

    pnls = [t["pnl"] for t in all_oot_trades]
    sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(len(pnls))) if np.std(pnls) > 0.01 else 0

    print(f"\n  Trades totales:       {nt}")
    print(f"  Win Rate:            {wr:.1f}%")
    print(f"  PnL total:           ${total_pnl:+.2f}")
    print(f"  Equity final:        ${portfolio_eq:.2f}")
    print(f"  Retorno:             {portfolio_ret:+.2f}%")
    print(f"  Max Drawdown:        {dd_max:.1f}%")
    print(f"  Sharpe (anualizado): {sharpe:.2f}")

    print(f"\n  POR BOT:")
    for sym in SYMBOLS:
        bt = [t for t in all_oot_trades if t["sym"] == sym]
        if not bt: continue
        bp = sum(t["pnl"] for t in bt)
        bw = sum(1 for t in bt if t["pnl"] > 0)
        print(f"    {sym}: {len(bt)}t | PnL ${bp:+.2f} | WR {bw/len(bt)*100:.1f}%")

    print(f"\n  MONTE CARLO (1000 simulaciones):")
    mc_returns = []
    for m in range(1000):
        sample = np.random.choice(pnls, size=len(pnls), replace=True)
        mc_returns.append(float(np.sum(sample) / INITIAL_EQUITY * 100))
    mc_returns = np.array(mc_returns)
    print(f"    Media:             {mc_returns.mean():+.2f}%")
    print(f"    Mediana:           {np.median(mc_returns):+.2f}%")
    print(f"    P5 (peor 5%):      {np.percentile(mc_returns, 5):+.2f}%")
    print(f"    P95 (mejor 5%):    {np.percentile(mc_returns, 95):+.2f}%")
    print(f"    P(positivo):       {np.mean(mc_returns > 0)*100:.1f}%")

    if np.percentile(mc_returns, 5) > 0:
        print(f"\n  ✅ EDGE ESTADÍSTICO CONFIRMADO: P5 > 0")
    else:
        print(f"\n  ❌ SIN EDGE: P5 < 0")

    annualized = (portfolio_eq / INITIAL_EQUITY) ** (12 / 4) - 1
    print(f"\n  PROYECCIÓN ANUAL: {annualized*100:+.1f}%")

    print(f"\n  CONCLUSIÓN:")
    if portfolio_ret > 0 and np.percentile(mc_returns, 5) > 0:
        print(f"  ✅ ESTRATEGIA MEJORADA: edge confirmado, retorno positivo OOS")
    elif portfolio_ret > 0:
        print(f"  ⚠️  RENTABLE pero sin edge estadístico")
    else:
        print(f"  ❌ NO RENTABLE out-of-sample")

    Path("institutional_results.json").write_text(json.dumps({
        "out_of_sample": {
            "trades": nt, "pnl": round(total_pnl, 2), "equity": round(portfolio_eq, 2),
            "return_pct": round(portfolio_ret, 2), "dd_pct": round(dd_max, 2),
            "wr": round(wr, 1), "sharpe": round(sharpe, 2),
            "annualized": round(annualized * 100, 1),
        },
        "monte_carlo": {
            "mean": round(float(mc_returns.mean()), 2),
            "median": round(float(np.median(mc_returns)), 2),
            "p5": round(float(np.percentile(mc_returns, 5)), 2),
            "p95": round(float(np.percentile(mc_returns, 95)), 2),
            "prob_positive": round(float(np.mean(mc_returns > 0) * 100), 1),
        },
    }, indent=2))

if __name__ == "__main__":
    rolling_forward()
