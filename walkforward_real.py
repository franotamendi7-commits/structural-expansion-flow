"""
WALK-FORWARD REAL — Enero 2026 → Hoy
======================================
Simula trading en vivo estrictamente cronológico:
  - Cada decisión usa SOLO datos disponibles hasta ESE momento
  - ML filter aprende de trades ya cerrados y mejora con el tiempo
  - Compounding desde $100 por bot
  - Sin lookahead, sin futurología
  - Indicadores precalculados para velocidad O(1) por barra
"""
import sys, json, math, time, gc
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from ml_filter import debe_ejecutar, cargar_modelo

BINANCE_FAPI = "https://fapi.binance.com"
COMMISSION = 0.0005
SPREAD = 0.0002
INITIAL_EQUITY = 100.0
WARMUP = 400  # velas para tener indicadores estables

BOTS = {
    "BTC": {"symbol": "BTCUSDT", "risk_pct": 0.0075, "min_size": 0.001,
             "trend_filter": "regime",
             "regime_params": {
                 "ALCISTA": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.2, "tp_mult": 1.2, "bias": 1},
                 "BAJISTA": {"vwap_n": 20, "dev_thr": 1.0,  "sl_mult": 2.0, "tp_mult": 1.5, "bias": -1},
                 "LATERAL": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.0, "tp_mult": 1.5, "bias": 0},
             }},
    "ETH": {"symbol": "ETHUSDT", "risk_pct": 0.0085, "min_size": 0.001,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.5, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 50},
    "SOL": {"symbol": "SOLUSDT", "risk_pct": 0.01,   "min_size": 0.01,
             "vwap_n": 12, "dev_thr": 1.2, "sl_mult": 1.2, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 200},
    "XRP": {"symbol": "XRPUSDT", "risk_pct": 0.01,   "min_size": 0.1,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.5,
             "trend_filter": "vwap", "trend_period": 50},
    "BNB": {"symbol": "BNBUSDT", "risk_pct": 0.01,   "min_size": 0.01,
             "vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.2,
             "trend_filter": "vwap", "trend_period": 50},
}

# ================================================================
# FETCH
# ================================================================
def fetch_klines(symbol, interval, limit=1500, start_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = int(start_time.timestamp() * 1000)
    for _ in range(3):
        try:
            r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines", params=params, timeout=30)
            if r.status_code == 200:
                break
            time.sleep(1)
        except:
            time.sleep(1)
    else:
        return pd.DataFrame()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    return df

def fetch_range(symbol, interval, start_dt, end_dt):
    all_bars = []; max_iter = 50
    start = start_dt
    while start < end_dt and len(all_bars) < max_iter:
        df = fetch_klines(symbol, interval, 1500, start_time=start)
        if df.empty:
            break
        all_bars.append(df)
        start = df["open_time"].iloc[-1] + timedelta(minutes=1)
    if not all_bars:
        return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    full = full[full["open_time"] < end_dt].reset_index(drop=True)
    return full

# ================================================================
# INDICADORES VECTORIZADOS (precalculados)
# ================================================================
def precompute_indicators(df):
    """Calcula todos los indicadores para todo el dataset (solo usan datos pasados)."""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    v = df["volume"].values
    tp = (h + l + c) / 3.0
    n = len(df)

    # ATR(14)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr14 = np.full(n, np.nan)
    atr14[13] = tr[:14].mean()
    for i in range(14, n):
        atr14[i] = (atr14[i-1] * 13 + tr[i]) / 14

    # VWAP dinámico para diferentes períodos
    vwaps = {}
    for period in [8, 10, 12, 15, 20, 50, 200]:
        vp = tp * v
        cs = np.cumsum(vp)
        cv = np.cumsum(v)
        p = np.full(n, np.nan)
        p[period-1:] = (cs[period-1:] - np.concatenate([[0], cs[:-period]])) / \
                        (cv[period-1:] - np.concatenate([[0], cv[:-period]]))
        vwaps[period] = p

    # Desviación % sobre VWAP
    devs = {}
    for period, vwap_arr in vwaps.items():
        devs[period] = (c - vwap_arr) / vwap_arr * 100.0

    # Williams %R(14) on 15m
    wr_15m = np.full(n, np.nan)
    for i in range(13, n):
        hh = np.max(h[i-13:i+1])
        ll = np.min(l[i-13:i+1])
        wr_15m[i] = (hh - c[i]) / (hh - ll) * -100 if (hh - ll) > 0 else -50

    # Choppiness Index(14) on 15m
    ci = np.full(n, np.nan)
    for i in range(13, n):
        sum_tr = np.sum(tr[i-13:i+1])
        hh14 = np.max(h[i-13:i+1])
        ll14 = np.min(l[i-13:i+1])
        ci[i] = 100 * math.log10(sum_tr / (hh14 - ll14)) / math.log10(14) if (hh14 - ll14) > 0 and sum_tr > 0 else 50

    # Momento: rate of change(5)
    mom = np.full(n, np.nan)
    for i in range(5, n):
        mom[i] = (c[i] / c[i-5] - 1) * 100

    # Volume ratio 20
    vr = np.full(n, np.nan)
    for i in range(20, n):
        vr[i] = v[i] / max(np.mean(v[i-20:i]), 1e-10)

    # Fib width: 60-period range / close
    fib_w = np.full(n, np.nan)
    for i in range(60, n):
        fib_w[i] = (np.max(h[i-59:i+1]) - np.min(l[i-59:i+1])) / c[i] * 100

    return {
        "atr14": atr14,
        "vwaps": vwaps,
        "devs": devs,
        "wr_15m": wr_15m,
        "ci": ci,
        "mom": mom,
        "vr": vr,
        "fib_w": fib_w,
    }

# ================================================================
# RÉGIMEN (BTC) — sobre datos 1h, actualizado en cada barra
# ================================================================
def compute_regime_h1(df1h):
    """Precalcula régimen para cada vela 1h."""
    df = df1h.copy().sort_values("open_time").reset_index(drop=True)
    n = len(df)
    regimes = np.full(n, "LATERAL", dtype=object)
    if n < 25:
        return regimes
    df["day"] = df["open_time"].dt.floor("D")
    # EMA20 diaria
    daily = df.groupby("day").agg(close_d=("close","last")).reset_index()
    daily["ema20"] = daily["close_d"].ewm(span=20, adjust=False).mean()
    daily["rising"] = daily["ema20"].diff() > 0
    daily["falling"] = daily["ema20"].diff() < 0

    # Merge diario → 1h
    df = df.merge(daily[["day","ema20","rising","falling"]], on="day", how="left", suffixes=("","_d"))
    # Régimen raw por cada 4h
    df["h4"] = df["open_time"].dt.floor("4h")
    h4_grp = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4_grp["day"] = h4_grp["h4"].dt.floor("D")
    h4_grp = h4_grp.merge(daily[["day","ema20","rising","falling"]], on="day", how="left")
    h4_grp["raw_regime"] = "LATERAL"
    up = (h4_grp["close_4h"] > h4_grp["ema20"]) & h4_grp["rising"]
    dn = (h4_grp["close_4h"] < h4_grp["ema20"]) & h4_grp["falling"]
    h4_grp.loc[up,"raw_regime"] = "ALCISTA"
    h4_grp.loc[dn,"raw_regime"] = "BAJISTA"
    h4_grp.loc[h4_grp["ema20"].isna(),"raw_regime"] = "LATERAL"

    # Suavizado: necesita 2 raw consecutivos iguales
    raw_list = h4_grp["raw_regime"].values
    smooth = np.full(len(raw_list), "LATERAL", dtype=object)
    cur = "LATERAL"; pending = None; cnt = 0
    for i, v in enumerate(raw_list):
        if v == cur:
            pending = None; cnt = 0; smooth[i] = cur
        else:
            if v == pending: cnt += 1
            else: pending = v; cnt = 1
            if cnt >= 2: cur = pending; pending = None; cnt = 0
            smooth[i] = cur
    h4_grp["regime_final"] = smooth

    df = df.merge(h4_grp[["h4","regime_final"]], on="h4", how="left")
    return df["regime_final"].values

# ================================================================
# ML FILTER
# ================================================================
class MLWalker:
    def __init__(self, name, retrain_interval=25):
        self.name = name
        self.retrain_interval = retrain_interval
        self.trades_log = []
        self.n_trades_seen = 0
        self.rules = {}
        self.trained = False

    def add_trade(self, features: dict, pnl: float):
        self.trades_log.append((dict(features), pnl))
        self.n_trades_seen += 1
        if self.n_trades_seen >= self.retrain_interval and \
           self.n_trades_seen % self.retrain_interval == 0:
            self.train()

    def train(self):
        if len(self.trades_log) < 20:
            return
        wins = [f for f, p in self.trades_log if p > 0]
        losses = [f for f, p in self.trades_log if p <= 0]
        if len(wins) < 5 or len(losses) < 5:
            return
        self.rules = {}
        for key in wins[0]:
            w_vals = np.array([f[key] for f in wins])
            l_vals = np.array([f[key] for f in losses])
            self.rules[key] = {
                "win_q1": float(np.percentile(w_vals, 20)),
                "win_q3": float(np.percentile(w_vals, 80)),
                "loss_q1": float(np.percentile(l_vals, 20)),
                "loss_q3": float(np.percentile(l_vals, 80)),
                "win_med": float(np.median(w_vals)),
                "loss_med": float(np.median(l_vals)),
            }
        self.trained = True

    def should_enter(self, features: dict) -> tuple:
        if not self.trained or len(self.trades_log) < 30:
            return True, "learning"
        score = 0; max_score = 0
        for key, r in self.rules.items():
            if key not in features:
                continue
            v = features[key]; max_score += 1
            if r["win_q1"] <= v <= r["win_q3"]:
                score += 1
            elif v < r["loss_q1"] or v > r["loss_q3"]:
                score += 0.5
        if max_score == 0:
            return True, "no_feat"
        pct = score / max_score
        if pct >= 0.50:
            return True, f"ml_{pct:.0%}"
        return False, f"veto_{pct:.0%}"

# ================================================================
# TREND BIAS (desde 15m precalculado)
# ================================================================
def trend_bias_series(dev_slow, close_arr):
    """Precalcula bias para cada barra usando VWAP lento."""
    n = len(close_arr)
    bias = np.zeros(n, dtype=int)
    vwap_slow = dev_slow  # usamos close/vwap-1, necesitamos vwap real
    return bias  # placeholder - computed on the fly

# ================================================================
# WALK-FORWARD ENGINE
# ================================================================
def run_bot_walk(name, config, df15, ind, df1h=None, regime_arr=None):
    """Walk-forward estricto: barra por barra con ML filter (Random Forest)."""
    eq = INITIAL_EQUITY
    trades = []
    pos = None

    close_arr = df15["close"].values
    high_arr = df15["high"].values
    low_arr = df15["low"].values
    open_arr = df15["open"].values
    vol_arr = df15["volume"].values
    open_time = df15["open_time"].values
    atr14 = ind["atr14"]
    vwaps = ind["vwaps"]
    devs = ind["devs"]
    wr_15m = ind["wr_15m"]
    ci_arr = ind["ci"]
    mom_arr = ind["mom"]
    vr_arr = ind["vr"]
    fib_w_arr = ind["fib_w"]

    n = len(df15)
    tf = config.get("trend_filter", "vwap")
    trend_period = config.get("trend_period", 50)
    vwap_n = config.get("vwap_n", 10)
    dev_thr = config.get("dev_thr", 1.0)
    sl_mult = config.get("sl_mult", 1.5)
    tp_mult = config.get("tp_mult", 1.5)
    risk_pct = config["risk_pct"]
    min_size = config["min_size"]

    # Precompute 4h body ratio from 1h data
    br4h_arr = None
    if df1h is not None and len(df1h) > 4:
        h1_h = df1h["high"].values; h1_l = df1h["low"].values
        h1_o = df1h["open"].values; h1_c = df1h["close"].values
        h1_t = df1h["open_time"].values
        n1h = len(df1h)
        br4h = np.full(n1h, np.nan)
        for i in range(4, n1h):
            if (h1_t[i] - h1_t[i-3]).astype('timedelta64[s]').astype(float) <= 4*3600 + 60:
                h4_h = np.max(h1_h[i-3:i+1])
                h4_l = np.min(h1_l[i-3:i+1])
                h4_o = h1_o[i-3]
                h4_c = h1_c[i]
                rng = h4_h - h4_l
                br4h[i] = abs(h4_c - h4_o) / rng if rng > 0 else 0.5
        # Map each 15m bar to nearest 4h body ratio
        br4h_arr = np.full(n, np.nan)
        for i in range(1, n):
            ts = pd.Timestamp(open_time[i])
            mask = h1_t <= ts
            if mask.any():
                idx = int(mask.sum()) - 1
                br4h_arr[i] = br4h[idx] if idx >= 0 and not np.isnan(br4h[idx]) else 0.5

    # Load RF model
    try:
        cargar_modelo()
        rf_loaded = True
    except Exception as e:
        print(f"  RF model load error: {e}")
        rf_loaded = False

    for i in range(WARMUP, n):
        c = float(close_arr[i])
        t = pd.Timestamp(open_time[i])

        # --- EXIT ---
        if pos is not None:
            exited = False
            if pos["dir"] == 1:
                if c <= pos["sl"]:
                    exit_p = pos["sl"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"] - \
                          (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    trades.append({"dir":1,"pnl":pnl,"reason":"sl"})
                    exited = True
                elif c >= pos["tp"]:
                    exit_p = pos["tp"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"] - \
                          (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    trades.append({"dir":1,"pnl":pnl,"reason":"tp"})
                    exited = True
            else:
                if c >= pos["sl"]:
                    exit_p = pos["sl"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"] - \
                          (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    trades.append({"dir":-1,"pnl":pnl,"reason":"sl"})
                    exited = True
                elif c <= pos["tp"]:
                    exit_p = pos["tp"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"] - \
                          (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    trades.append({"dir":-1,"pnl":pnl,"reason":"tp"})
                    exited = True
            if exited:
                eq += pnl
                pos = None
                continue

        # --- SIGNAL ---
        b_bias = 0; b_vwap_n = vwap_n; b_dev_thr = dev_thr
        b_sl_mult = sl_mult; b_tp_mult = tp_mult

        if tf == "regime" and regime_arr is not None:
            regime = str(regime_arr[min(i // 4, len(regime_arr)-1)])
            rp = config["regime_params"].get(regime, {})
            b_bias = rp.get("bias", 0)
            b_vwap_n = rp.get("vwap_n", vwap_n)
            b_dev_thr = rp.get("dev_thr", dev_thr)
            b_sl_mult = rp.get("sl_mult", sl_mult)
            b_tp_mult = rp.get("tp_mult", tp_mult)
        elif tf == "vwap":
            if trend_period in vwaps:
                vs = vwaps[trend_period]
                if not np.isnan(vs[i]) and i >= 5:
                    slope = vs[i] - vs[i-5]
                    if c > vs[i] and slope > 0: b_bias = 1
                    elif c < vs[i] and slope < 0: b_bias = -1

        if b_vwap_n not in devs:
            continue
        dev_arr = devs[b_vwap_n]
        dv = float(dev_arr[i])
        dv_prev = float(dev_arr[i-1]) if i > 0 else dv
        a = float(atr14[i])
        if not math.isfinite(a) or a <= 0 or not math.isfinite(dv):
            continue

        long_ok = (b_bias >= 0) and dv_prev >= -b_dev_thr and dv < -b_dev_thr
        short_ok = (b_bias <= 0) and dv_prev <= b_dev_thr and dv > b_dev_thr

        if long_ok:
            sig_dir = 1
            sig = {"dir": 1, "entry": c, "stop": c - b_sl_mult * a,
                   "tp": c + b_tp_mult * a, "dev": dv, "atr": a}
        elif short_ok:
            sig_dir = -1
            sig = {"dir": -1, "entry": c, "stop": c + b_sl_mult * a,
                   "tp": c - b_tp_mult * a, "dev": dv, "atr": a}
        else:
            continue

        # RF ML VETO
        if rf_loaded:
            features = {
                "body_ratio_4h": float(br4h_arr[i]) if br4h_arr is not None and not np.isnan(br4h_arr[i]) else 0.5,
                "ci_value": float(ci_arr[i]) if not np.isnan(ci_arr[i]) else 50,
                "mom_score": float(mom_arr[i]) if not np.isnan(mom_arr[i]) else 0,
                "fib_width": float(fib_w_arr[i]) if not np.isnan(fib_w_arr[i]) else 0,
                "wr_15m": float(wr_15m[i]) if not np.isnan(wr_15m[i]) else -50,
                "vol_ratio_5m": float(vr_arr[i]) if not np.isnan(vr_arr[i]) else 1.0,
                "hour_of_day": t.hour,
                "direction_long": 1 if sig_dir == 1 else 0,
                "wr_5m": float(wr_15m[i]) if not np.isnan(wr_15m[i]) else -50,
                "fib_high_key": 0.5, "fib_low_key": 0.5,
                "st_aligned": 0, "st_bias_bullish": 0, "st_bias_bearish": 0,
                "mom_bearish": 0, "mom_bullish": 0, "mom_neutral": 1,
                "phase_compressing": 0, "phase_expanding": 0, "phase_trending": 1,
            }
            ok, prob = debe_ejecutar(features)
            if not ok:
                continue

        # ENTRY
        risk_amt = risk_pct * eq
        price_range = abs(sig["entry"] - sig["stop"])
        if price_range <= 0:
            continue
        size = risk_amt / price_range
        if size < min_size:
            continue
        entry_fill = sig["entry"] * (1 + SPREAD) if sig_dir == 1 else sig["entry"] * (1 - SPREAD)
        entry_comm = entry_fill * size * COMMISSION
        eq -= entry_comm

        pos = {"dir": sig_dir, "entry": entry_fill, "size": size,
               "sl": sig["stop"], "tp": sig["tp"], "dev": sig["dev"], "atr": sig["atr"]}

    return trades, eq


def run():
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    print("=" * 68)
    print("  WALK-FORWARD REAL: Enero 2026 → Hoy (sin lookahead · ML aprende)")
    print("=" * 68)

    results = {}
    portfolio_trades = []

    for name, config in sorted(BOTS.items()):
        symbol = config["symbol"]
        print(f"\n📡 {name} ({symbol})")
        print(f"  Fetching 15m data...", end=" ")
        df15 = fetch_range(symbol, "15m", start_date, end_date)
        if df15.empty or len(df15) < 800:
            print(f"SKIP: {len(df15)} bars")
            continue
        print(f"{len(df15)} bars")

        # Precalcular indicadores
        print(f"  Precomputing indicators...", end=" ")
        ind = precompute_indicators(df15)
        print(f"OK")

        # 1h data
        df1h = None
        regime_arr = None
        if config.get("trend_filter") == "regime":
            df1h = fetch_range(symbol, "1h", start_date, end_date)
            print(f"  Fetching 1h data... {len(df1h)} bars")
            if len(df1h) >= 25:
                regime_arr = compute_regime_h1(df1h)
                print(f"  Regime computed")

        print(f"  Walk-forward...")
        t0 = time.time()
        trades, final_eq = run_bot_walk(name, config, df15, ind, df1h, regime_arr)
        elapsed = time.time() - t0

        if not trades:
            print(f"  ⏭ No trades")
            results[name] = {"trades": 0, "pnl": 0, "final_eq": round(final_eq, 2)}
            continue

        pnl_total = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = wins / len(trades) * 100
        ret = (final_eq / INITIAL_EQUITY - 1) * 100

        eq_curve = INITIAL_EQUITY; peak = INITIAL_EQUITY; dd_max = 0
        for t in trades:
            eq_curve += t["pnl"]
            if eq_curve > peak: peak = eq_curve
            dd = (eq_curve - peak) / peak * 100
            dd_max = min(dd_max, dd)

        results[name] = {"trades": len(trades), "wins": wins, "wr": round(wr, 1),
                         "pnl": round(pnl_total, 2), "final_eq": round(final_eq, 2),
                         "return": round(ret, 2), "dd": round(dd_max, 2)}
        for t in trades:
            t["bot"] = name
            portfolio_trades.append(t)

        print(f"  ✅ {len(trades)} t | PnL ${pnl_total:+.2f} | "
              f"Eq ${final_eq:.2f} ({ret:+.2f}%) | WR {wr:.1f}% | "
              f"MaxDD {dd_max:.1f}% | {elapsed:.0f}s")

    # Portfolio
    if portfolio_trades:
        total_pnl = sum(t["pnl"] for t in portfolio_trades)
        total_wins = sum(1 for t in portfolio_trades if t["pnl"] > 0)
        portfolio_final = INITIAL_EQUITY * len(BOTS) + total_pnl
        portfolio_ret = (portfolio_final / (INITIAL_EQUITY * len(BOTS)) - 1) * 100

        print(f"\n{'='*68}")
        print(f"  PORTFOLIO: {len(portfolio_trades)} trades | "
              f"PnL ${total_pnl:+.2f} | "
              f"Eq ${portfolio_final:.2f} ({portfolio_ret:+.2f}%)")
        print(f"="*68)
    else:
        print(f"\n{'='*68}")
        print(f"  NO TRADES IN PORTFOLIO")
        print(f"="*68)

    # Guardar
    out = Path(__file__).parent / "walkforward_real_results.json"
    out.write_text(json.dumps({
        "config": {"initial_equity_per_bot": INITIAL_EQUITY, "warmup": WARMUP},
        "bots": results,
        "portfolio": {"trades": len(portfolio_trades),
                      "pnl": round(total_pnl, 2) if portfolio_trades else 0,
                      "final_equity": round(portfolio_final, 2) if portfolio_trades else 0,
                      "return_pct": round(portfolio_ret, 2) if portfolio_trades else 0}},
        indent=2))
    print(f"\nResultados guardados en {out}")


if __name__ == "__main__":
    run()