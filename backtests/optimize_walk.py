"""
OPTIMIZADOR WALK-FORWARD
========================
Prueba combinaciones de parámetros en walk-forward real para cada bot.
Busca el set que maximiza retorno total con drawdown controlado.
"""
import sys, json, math, time, itertools, gc, copy
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BINANCE_FAPI = "https://fapi.binance.com"
COMMISSION = 0.0005
SPREAD = 0.0002
INITIAL_EQUITY = 100.0
WARMUP = 400
CACHE_DIR = Path(__file__).parent / "cache_15m"

# ================================================================
# FETCH + CACHE
# ================================================================
def fetch_klines(symbol, interval, limit=1500, start_time=None):
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time:
        params["startTime"] = int(start_time.timestamp() * 1000)
    for _ in range(5):
        try:
            r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines", params=params, timeout=30)
            if r.status_code == 200: break
        except: pass
        time.sleep(1)
    else:
        return pd.DataFrame()
    rows = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
    return df

def fetch_cached(symbol, interval, start_dt, end_dt):
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}_{interval}.npy"
    # Try to load cache
    df = None
    if cache_file.exists():
        try:
            arr = np.load(cache_file, allow_pickle=True)
            df = pd.DataFrame(arr.tolist(), columns=["open_time","open","high","low","close","volume"])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            for c in ["open","high","low","close","volume"]: df[c] = pd.to_numeric(df[c])
            # Check if we have enough data
            if len(df) > 15000:
                return df
        except: pass
    # Fetch
    all_bars = []; start = start_dt; max_iter = 60
    while start < end_dt and len(all_bars) < max_iter:
        df_batch = fetch_klines(symbol, interval, 1500, start_time=start)
        if df_batch.empty: break
        all_bars.append(df_batch)
        start = df_batch["open_time"].iloc[-1] + timedelta(minutes=1)
    if not all_bars: return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    full = full[full["open_time"] < end_dt].reset_index(drop=True)
    # Save cache
    arr = np.array(full[["open_time","open","high","low","close","volume"]].values, dtype=object)
    np.save(cache_file, arr)
    return full

# ================================================================
# INDICADORES
# ================================================================
def precompute_all(df):
    """Precalcula ATR(14) y VWAPs para varios períodos."""
    h, l, c, v = df["high"].values, df["low"].values, df["close"].values, df["volume"].values
    tp = (h + l + c) / 3.0
    n = len(df)
    # ATR(14)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = np.full(n, np.nan)
    atr[13] = tr[:14].mean()
    for i in range(14, n): atr[i] = (atr[i-1] * 13 + tr[i]) / 14
    # VWAPs
    vp = tp * v
    csvp = np.cumsum(vp); csv = np.cumsum(v)
    vwaps = {}
    for period in [8, 10, 12, 14, 15, 16, 18, 20, 25, 50, 100, 200]:
        vw = np.full(n, np.nan)
        vw[period-1:] = (csvp[period-1:] - np.concatenate([[0], csvp[:-period]])) / \
                        (csv[period-1:] - np.concatenate([[0], csv[:-period]]))
        vwaps[period] = vw
    # Dev %
    devs = {p: (c - vw) / vw * 100.0 for p, vw in vwaps.items()}
    return atr, vwaps, devs

# ================================================================
# RÉGIMEN (BTC, precalculado)
# ================================================================
def regime_precalc(df1h):
    df = df1h.copy().sort_values("open_time").reset_index(drop=True)
    n = len(df)
    regs = np.full(n, "LATERAL", dtype=object)
    if n < 25: return regs
    df["day"] = df["open_time"].dt.floor("D")
    daily = df.groupby("day").agg(close_d=("close","last")).reset_index()
    daily["ema20"] = daily["close_d"].ewm(span=20, adjust=False).mean()
    daily["rising"] = daily["ema20"].diff() > 0
    daily["falling"] = daily["ema20"].diff() < 0
    df = df.merge(daily[["day","ema20","rising","falling"]], on="day", how="left")
    df["h4"] = df["open_time"].dt.floor("4h")
    h4 = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4["day"] = h4["h4"].dt.floor("D")
    h4 = h4.merge(daily[["day","ema20","rising","falling"]], on="day", how="left")
    h4["raw"] = "LATERAL"
    h4.loc[(h4["close_4h"] > h4["ema20"]) & h4["rising"], "raw"] = "ALCISTA"
    h4.loc[(h4["close_4h"] < h4["ema20"]) & h4["falling"], "raw"] = "BAJISTA"
    h4.loc[h4["ema20"].isna(), "raw"] = "LATERAL"
    raw = h4["raw"].values
    smooth = np.full(len(raw), "LATERAL", dtype=object)
    cur = "LATERAL"; pending = None; cnt = 0
    for i, v in enumerate(raw):
        if v == cur: pending = None; cnt = 0; smooth[i] = cur
        else:
            if v == pending: cnt += 1
            else: pending = v; cnt = 1
            if cnt >= 2: cur = pending; pending = None; cnt = 0
            smooth[i] = cur
    h4["final"] = smooth
    df = df.merge(h4[["h4","final"]], on="h4", how="left")
    return df["final"].values

# ================================================================
# ML FILTER
# ================================================================
class MLWalker:
    def __init__(self, retrain=25):
        self.retrain = retrain
        self.log = []; self.n = 0; self.rules = {}; self.trained = False
    def add(self, f, pnl):
        self.log.append((dict(f), pnl)); self.n += 1
        if self.n >= self.retrain and self.n % self.retrain == 0:
            self.train()
    def train(self):
        if len(self.log) < 20: return
        w = [f for f, p in self.log if p > 0]
        l = [f for f, p in self.log if p <= 0]
        if len(w) < 5 or len(l) < 5: return
        self.rules = {}
        for k in w[0]:
            wv = np.array([f[k] for f in w])
            lv = np.array([f[k] for f in l])
            self.rules[k] = {"w_q1": float(np.percentile(wv, 20)), "w_q3": float(np.percentile(wv, 80)),
                             "l_q1": float(np.percentile(lv, 20)), "l_q3": float(np.percentile(lv, 80))}
        self.trained = True
    def ok(self, f):
        if not self.trained or self.n < 30: return True
        score = max_score = 0
        for k, r in self.rules.items():
            if k not in f: continue
            v = f[k]; max_score += 1
            if r["w_q1"] <= v <= r["w_q3"]: score += 1
            elif v < r["l_q1"] or v > r["l_q3"]: score += 0.5
        return score / max_score >= 0.50 if max_score > 0 else True

# ================================================================
# WALK-FORWARD (parametrizada)
# ================================================================
def walk_bot(df, atr, vwaps, devs, config, regime_arr=None):
    """Ejecuta walk-forward para un bot con parámetros específicos.
    Devuelve (trades, final_equity).
    """
    eq = INITIAL_EQUITY
    trades = []
    pos = None
    ml = MLWalker(retrain=25)

    ca = df["close"].values; ot = df["open_time"].values
    n = len(df)

    vwap_n = config.get("vn", 15); dev_thr = config.get("dt", 1.0); sl_mult = config.get("sl", 1.5)
    tp_mult = config.get("tp", 1.5); risk_pct = config["rp"]; min_size = config["ms"]
    tf = config.get("tf", "vwap"); tp_ = config.get("tp_", 50)
    regime_params = config.get("rp_map", None)

    sig_count = 0; ent_count = 0; ml_veto = 0; size_fail = 0

    for i in range(WARMUP, n):
        c = float(ca[i])
        t = pd.Timestamp(ot[i])

        # Exit
        if pos is not None:
            exited = False
            ep = None; reason = None
            if pos["dir"] == 1:
                if c <= pos["sl"]:
                    ep = pos["sl"] * (1 - SPREAD)
                    pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                    exited = True; reason = "sl"
                elif c >= pos["tp"]:
                    ep = pos["tp"] * (1 - SPREAD)
                    pnl = (ep - pos["entry"]) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                    exited = True; reason = "tp"
            else:
                if c >= pos["sl"]:
                    ep = pos["sl"] * (1 + SPREAD)
                    pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                    exited = True; reason = "sl"
                elif c <= pos["tp"]:
                    ep = pos["tp"] * (1 + SPREAD)
                    pnl = (pos["entry"] - ep) * pos["size"] - (pos["entry"] + ep) * pos["size"] * COMMISSION
                    exited = True; reason = "tp"
            if exited:
                eq += pnl
                ml.add({"dev": pos["dev"], "atr": pos["atr"], "hour": t.hour, "dir": pos["dir"]}, pnl)
                trades.append({"dir":pos["dir"],"pnl":pnl,"reason":reason,
                               "entry":pos["entry"],"exit":ep,"size":pos["size"]})
                pos = None
                continue
            # Position open but no exit — next iteration will check again

        # Signal
        b_bias = 0; b_vn = vwap_n; b_dt = dev_thr; b_sl = sl_mult; b_tp = tp_mult
        if tf == "regime" and regime_arr is not None:
            ri = min(i // 4, len(regime_arr)-1)
            rg = str(regime_arr[ri])
            rp = regime_params.get(rg, {}) if regime_params else {}
            b_bias = rp.get("bias", 0)
            b_vn = rp.get("vn", vwap_n)
            b_dt = rp.get("dt", dev_thr)
            b_sl = rp.get("sl", sl_mult)
            b_tp = rp.get("tp", tp_mult)
        elif tf == "vwap":
            if tp_ in vwaps:
                vs = vwaps[tp_]
                if not np.isnan(vs[i]) and i >= 5:
                    slope = vs[i] - vs[i-5]
                    if c > vs[i] and slope > 0: b_bias = 1
                    elif c < vs[i] and slope < 0: b_bias = -1

        if b_vn not in devs: continue
        da = devs[b_vn]; dv = float(da[i]); dvp = float(da[i-1]) if i > 0 else dv
        a = float(atr[i])
        if not math.isfinite(a) or a <= 0 or not math.isfinite(dv): continue

        long_ok = (b_bias >= 0) and dvp >= -b_dt and dv < -b_dt
        short_ok = (b_bias <= 0) and dvp <= b_dt and dv > b_dt

        sig = None
        if long_ok:
            sig = {"dir": 1, "entry": c, "stop": c - b_sl * a, "tp": c + b_tp * a, "dev": dv, "atr": a}
        elif short_ok:
            sig = {"dir": -1, "entry": c, "stop": c + b_sl * a, "tp": c - b_tp * a, "dev": dv, "atr": a}
        if sig is None: continue
        sig_count += 1
        if not ml.ok({"dev": sig["dev"], "atr": sig["atr"], "hour": t.hour, "dir": sig["dir"]}):
            ml_veto += 1
            continue
        risk_amt = risk_pct * eq
        price_range = abs(sig["entry"] - sig["stop"])
        if price_range <= 0: continue
        size = risk_amt / price_range
        if size < min_size:
            size_fail += 1
            continue
        entry_fill = sig["entry"] * (1 + SPREAD) if sig["dir"] == 1 else sig["entry"] * (1 - SPREAD)
        entry_comm = entry_fill * size * COMMISSION
        eq -= entry_comm
        ent_count += 1
        pos = {"dir": sig["dir"], "entry": entry_fill, "size": size,
               "sl": sig["stop"], "tp": sig["tp"], "dev": sig["dev"], "atr": sig["atr"]}

    if len(trades) == 0 and abs(eq - INITIAL_EQUITY) > 1.0:
        print(f'    DEBUG: signals={sig_count} entries={ent_count} ml_veto={ml_veto} '
              f'size_fail={size_fail} trades={len(trades)} eq={eq:.2f}', flush=True)
    if len(trades) > 0:
        print(f'    OK: {len(trades)}t sig={sig_count} ent={ent_count} veto={ml_veto} szfail={size_fail}', flush=True)
    return trades, eq

# ================================================================
# OPTIMIZADOR
# ================================================================
def optimize_bot(name, symbol, df, atr, vwaps, devs, df1h=None, regime_arr=None):
    """Busca la mejor combinación de parámetros para un bot."""
    results = []

    # Default config base
    base = {"rp": 0.01, "ms": 0.001, "tf": "vwap", "tp_": 50}

    if name == "BTC":
        base["tf"] = "regime"
        base["rp"] = 0.0075; base["ms"] = 0.001; base["vn"] = 15; base["dt"] = 1.0; base["sl"] = 1.5; base["tp"] = 1.5
        base["rp_map"] = {
            "ALCISTA": {"bias": 1, "vn": 15, "dt": 0.75, "sl": 1.2, "tp": 1.2},
            "BAJISTA": {"bias": -1, "vn": 20, "dt": 1.0, "sl": 2.0, "tp": 1.5},
            "LATERAL": {"bias": 0, "vn": 15, "dt": 0.75, "sl": 1.0, "tp": 1.5},
        }
        return optimize_btc(base, df, atr, vwaps, devs, regime_arr)

    # Risk % specific per bot
    rp_map = {"ETH": 0.0085, "SOL": 0.01, "XRP": 0.01, "BNB": 0.01}
    ms_map = {"ETH": 0.001, "SOL": 0.01, "XRP": 0.1, "BNB": 0.01}
    base["rp"] = rp_map.get(name, 0.01)
    base["ms"] = ms_map.get(name, 0.001)

    # Grid search for sl_mult, tp_mult first
    best_combo = None; best_ret = -999
    combos = []
    for vn in [8, 10, 12, 15]:          # VWAP period
        for dt in [0.75, 1.0, 1.25]:     # Deviation threshold
            for sl in [1.0, 1.2, 1.5, 2.0]:  # SL multiplier
                for tp in [1.2, 1.5, 2.0, 2.5]:  # TP multiplier
                    combos.append((vn, dt, sl, tp))

    print(f"    Testing {len(combos)} combinations...", flush=True)
    for idx, (vn, dt, sl, tp) in enumerate(combos):
        if idx % 20 == 0:
            print(f"    {idx}/{len(combos)}...", end=" ", flush=True)
        cfg = dict(base, vn=vn, dt=dt, sl=sl, tp=tp)
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg, regime_arr)
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        wt = sum(1 for t in trades if t["pnl"] > 0) if trades else 0
        wr = wt / len(trades) * 100 if trades else 0
        dd = 0
        if trades:
            eq = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades:
                eq += t["pnl"]
                if eq > peak: peak = eq
                dd = min(dd, (eq - peak) / peak * 100)
        results.append({"vn": vn, "dt": dt, "sl": sl, "tp": tp,
                        "trades": len(trades), "wr": round(wr, 1),
                        "ret": round(ret, 2), "dd": round(dd, 2), "pnl": round(final_eq - INITIAL_EQUITY, 2)})
        if ret > best_ret and dd >= -15:
            best_ret = ret; best_combo = (vn, dt, sl, tp)

    if best_combo is None:
        best_combo = (15, 1.0, 1.5, 1.5)
    print(f"\n    Best: vn={best_combo[0]}, dt={best_combo[1]}, sl={best_combo[2]}, tp={best_combo[3]} → ret={best_ret:.1f}%", flush=True)
    return ("vwap", *best_combo), results

def optimize_btc(base, df, atr, vwaps, devs, regime_arr):
    """Optimización específica para BTC: prueba diferentes params por régimen."""
    results = []

    # Primero: probar sin régimen (trend_filter=vwap como los demás)
    print(f"    Testing without regime (simple VWAP)...", flush=True)
    best_no_reg = None; best_ret = -999
    for vn in [8, 10, 12, 15, 20]:
        for dt in [0.5, 0.75, 1.0, 1.25]:
            for sl in [1.0, 1.2, 1.5, 2.0]:
                for tp in [1.2, 1.5, 2.0, 2.5]:
                    cfg = {"rp": 0.0075, "ms": 0.001, "vn": vn, "dt": dt, "sl": sl, "tp": tp,
                           "tf": "vwap", "tp_": 50}
                    trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
                    ret = (final_eq / INITIAL_EQUITY - 1) * 100
                    wt = sum(1 for t in trades if t["pnl"] > 0) if trades else 0
                    wr = wt / len(trades) * 100 if trades else 0
                    if ret > best_ret and ret > -15:
                        best_ret = ret; best_no_reg = (vn, dt, sl, tp)
                    results.append({"type":"no_reg","vn":vn,"dt":dt,"sl":sl,"tp":tp,
                                    "trades":len(trades),"wr":round(wr,1),"ret":round(ret,2)})
    print(f"    Best (no regime): vn={best_no_reg[0]}, dt={best_no_reg[1]}, sl={best_no_reg[2]}, tp={best_no_reg[3]} → ret={best_ret:.1f}%", flush=True)

    # Segundo: probar con régimen ajustando params
    print(f"    Testing with regime (ALCISTA params)...", flush=True)
    best_reg = None; best_reg_ret = -999
    rp_map_template = base["rp_map"]
    for al_vn in [10, 15, 20]:
        for al_dt in [0.5, 0.75]:
            for al_sl in [1.0, 1.2]:
                for al_tp in [1.2, 1.5]:
                    rp_map_copy = {k: dict(v) for k, v in rp_map_template.items()}
                    rp_map_copy["ALCISTA"] = {"bias": 1, "vn": al_vn, "dt": al_dt, "sl": al_sl, "tp": al_tp}
                    cfg = dict(base, tf="regime", rp_map=rp_map_copy)
                    trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg, regime_arr)
                    ret = (final_eq / INITIAL_EQUITY - 1) * 100
                    if ret > best_reg_ret and ret > -15:
                        best_reg_ret = ret; best_reg = (al_vn, al_dt, al_sl, al_tp)
                    results.append({"type":"regime_al","al_vn":al_vn,"al_dt":al_dt,"al_sl":al_sl,"al_tp":al_tp,
                                    "trades":len(trades),"ret":round(ret,2)})
    print(f"    Best (regime): vn={best_reg[0]}, dt={best_reg[1]}, sl={best_reg[2]}, tp={best_reg[3]} → ret={best_reg_ret:.1f}%", flush=True)

    # Elegir la mejor entre las dos
    if best_no_reg is None and best_reg is None:
        return ("none", 15, 1.0, 1.5, 1.5), results
    if best_reg is None or best_ret >= best_reg_ret:
        return ("vwap", *best_no_reg), results
    else:
        return ("regime", *best_reg), results


def run():
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    print("=" * 68)
    print("  OPTIMIZADOR WALK-FORWARD: búsqueda de parámetros")
    print("=" * 68)

    BOTS_CONFIG = {
        "ETH": {"symbol": "ETHUSDT", "df1h": False},
        "SOL": {"symbol": "SOLUSDT", "df1h": False},
        "XRP": {"symbol": "XRPUSDT", "df1h": False},
        "BNB": {"symbol": "BNBUSDT", "df1h": False},
        "BTC": {"symbol": "BTCUSDT", "df1h": True},
    }

    best_params = {}
    all_results = {}

    for name, cfg in sorted(BOTS_CONFIG.items()):
        sym = cfg["symbol"]
        print(f"\n📡 {name} ({sym})")
        print(f"  Fetching 15m...", end=" ", flush=True)
        df = fetch_cached(sym, "15m", start_date, end_date)
        if df.empty or len(df) < 800:
            print(f"SKIP")
            continue
        print(f"{len(df)} bars")
        print(f"  Precomputing...", end=" ", flush=True)
        atr, vwaps, devs = precompute_all(df)
        print(f"OK")

        regime_arr = None
        if cfg["df1h"]:
            print(f"  Fetching 1h...", end=" ", flush=True)
            df1h = fetch_cached(sym, "1h", start_date, end_date)
            if len(df1h) >= 25:
                regime_arr = regime_precalc(df1h)
                print(f"OK ({len(df1h)} bars)")
            else:
                print(f"too short")
        else:
            df1h = None

        print(f"  Optimizing...", flush=True)
        t0 = time.time()
        best, results = optimize_bot(name, sym, df, atr, vwaps, devs, df1h, regime_arr)
        elapsed = time.time() - t0
        best_params[name] = {"best": best, "n_results": len(results), "time": round(elapsed, 1)}
        all_results[name] = results

    # Summary
    print(f"\n{'='*68}")
    print(f"  BEST PARAMETERS PER BOT")
    print(f"{'='*68}")
    for name, bp in sorted(best_params.items()):
        b = bp["best"]
        print(f"  {name}: type={b[0]}, vn={b[1]}, dt={b[2]}, sl={b[3]}, tp={b[4]} ({bp['n_results']} combos in {bp['time']}s)")

    # Save
    out = Path(__file__).parent / "optimize_results.json"
    # Flatten
    save = {}
    for name, bp in best_params.items():
        b = bp["best"]
        save[name] = {
            "type": str(b[0]),
            "vwap_n": int(b[1]),
            "dev_thr": float(b[2]),
            "sl_mult": float(b[3]),
            "tp_mult": float(b[4]),
            "search_time_s": bp["time"],
        }
    out.write_text(json.dumps(save, indent=2))
    print(f"\nSaved to {out}")

def run_final():
    """Ejecuta walk-forward con los mejores parámetros encontrados."""
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    BEST = {
        'BNB': {'vn': 8,  'dt': 1.25, 'sl': 1.5, 'tp': 2.0,  'rp': 0.01, 'ms': 0.01,  'tf': 'vwap', 'tp_': 50, 'sym': 'BNBUSDT'},
        'BTC': {'vn': 20, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.0075, 'ms': 0.001, 'tf': 'vwap', 'tp_': 50, 'sym': 'BTCUSDT'},
        'ETH': {'vn': 10, 'dt': 1.25, 'sl': 1.0, 'tp': 2.5,  'rp': 0.0085, 'ms': 0.001, 'tf': 'vwap', 'tp_': 50, 'sym': 'ETHUSDT'},
        'SOL': {'vn': 10, 'dt': 1.25, 'sl': 1.5, 'tp': 2.5,  'rp': 0.01, 'ms': 0.01,   'tf': 'vwap', 'tp_': 50, 'sym': 'SOLUSDT'},
        'XRP': {'vn': 10, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.01, 'ms': 0.1,    'tf': 'vwap', 'tp_': 50, 'sym': 'XRPUSDT'},
    }

    print('=' * 68)
    print('  WALK-FORWARD FINAL (params optimizados)')
    print('=' * 68)

    portfolio_trades = []
    for name in sorted(BEST):
        cfg = BEST[name]
        sym = cfg['sym']
        print(f'\n{name} ({sym})')
        print(f'  vn={cfg["vn"]} dt={cfg["dt"]} sl={cfg["sl"]} tp={cfg["tp"]} rp={cfg["rp"]}')
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        t0 = time.time()
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
        el = time.time() - t0
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades:
                eq_c += t['pnl']
                if eq_c > peak: peak = eq_c
                dd = min(dd, (eq_c - peak) / peak * 100)
        print(f'  {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | '
              f'WR {wr:.1f}% | DD {dd:.1f}% | {el:.0f}s')
        for t in trades:
            t['bot'] = name
            portfolio_trades.append(t)

    total_pnl = sum(t['pnl'] for t in portfolio_trades)
    total_wins = sum(1 for t in portfolio_trades if t['pnl'] > 0)
    n_t = len(portfolio_trades)
    portfolio_eq = INITIAL_EQUITY * 5 + total_pnl
    portfolio_ret = (portfolio_eq / (INITIAL_EQUITY * 5) - 1) * 100
    print(f'\n{"=" * 68}')
    print(f'  PORTFOLIO: {n_t}t | PnL ${total_pnl:+.2f} | Eq ${portfolio_eq:.2f} '
          f'({portfolio_ret:+.2f}%) | WR {total_wins/n_t*100:.1f}%' if n_t else '  No trades')
    print(f'{"=" * 68}')

    Path('walkforward_final_results.json').write_text(json.dumps({
        'params': BEST,
        'portfolio': {'trades': n_t, 'pnl': round(total_pnl, 2),
                      'equity': round(portfolio_eq, 2), 'return': round(portfolio_ret, 2)},
    }, indent=2))

def run_search5():
    """Fase 5: sobre los mejores params de Fase 4, probar riesgo extremo + R:R."""
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    # Mejores params de Fase 4 (dd-10)
    PHASE4_BEST = {
        'BNB': {'vn': 15, 'dt': 2.0, 'sl': 0.75, 'tp': 4.0, 'tp_': 30, 'rp': 0.0225, 'ms': 0.01},
        'BTC': {'vn': 20, 'dt': 1.25, 'sl': 1.0, 'tp': 2.0, 'tp_': 30, 'rp': 0.0224, 'ms': 0.001},
        'ETH': {'vn': 20, 'dt': 2.0, 'sl': 0.75, 'tp': 3.0, 'tp_': 200, 'rp': 0.032, 'ms': 0.001},
        'SOL': {'vn': 8, 'dt': 1.25, 'sl': 0.5, 'tp': 2.5, 'tp_': 100, 'rp': 0.0338, 'ms': 0.01},
        'XRP': {'vn': 20, 'dt': 2.0, 'sl': 1.0, 'tp': 3.0, 'tp_': 30, 'rp': 0.03, 'ms': 0.1},
    }
    SYMBOLS = {'BNB':'BNBUSDT','BTC':'BTCUSDT','ETH':'ETHUSDT','SOL':'SOLUSDT','XRP':'XRPUSDT'}

    print('=' * 68)
    print('  FASE 5: riesgo extremo + high R:R')
    print('=' * 68)

    # Para cada bot, probar variaciones de sl/tp + risk multipliers altos
    best_per_bot = {}
    for name in sorted(PHASE4_BEST):
        cfg = dict(PHASE4_BEST[name])
        sym = SYMBOLS[name]
        print(f'\n{name} ({sym})')
        print(f'  Base: vn={cfg["vn"]} dt={cfg["dt"]} sl={cfg["sl"]} tp={cfg["tp"]} tp_={cfg["tp_"]} rp={cfg["rp"]}')

        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)

        best = None; best_ret = -999

        # Variar SL, TP y risk multiplier
        sl_values = [0.3, 0.5, 0.75, 1.0, 1.2]
        tp_values = [2.0, 2.5, 3.0, 4.0, 5.0, 6.0]
        risk_mults = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]

        total = len(sl_values) * len(tp_values) * len(risk_mults)
        idx = 0
        for sl in sl_values:
            for tp in tp_values:
                for rm in risk_mults:
                    idx += 1
                    test_cfg = dict(cfg)
                    test_cfg['sl'] = sl
                    test_cfg['tp'] = tp
                    test_cfg['rp'] = round(cfg['rp'] * rm, 4)
                    trades, final_eq = walk_bot(df, atr, vwaps, devs, test_cfg)
                    ret = (final_eq / INITIAL_EQUITY - 1) * 100
                    dd = 0
                    if trades:
                        eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
                        for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
                    wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100 if trades else 0
                    if ret > best_ret and dd >= -20 and len(trades) >= 5:
                        best_ret = ret; best = (sl, tp, rm, test_cfg['rp'], len(trades), round(ret,2), round(dd,2), round(wr,1))
                    if idx % int(max(1, total/4)) == 0:
                        print(f'  {idx}/{total}...', end=' ', flush=True)
        print()
        if best is None:
            print(f'  No valid combination found (all DD > -20%)')
            best_per_bot[name] = {'params': dict(cfg), 'result': {'trades': 0, 'ret': 0, 'dd': 0, 'wr': 0}}
        else:
            print(f'  Best: sl={best[0]} tp={best[1]} rm={best[2]}x rp={best[3]}'
                  f' → {best[4]}t ret={best[5]}% dd={best[6]}% wr={best[7]}%')
            best_per_bot[name] = {'params': {**cfg, 'sl': best[0], 'tp': best[1], 'rp': best[3]},
                                  'result': {'trades': best[4], 'ret': best[5], 'dd': best[6], 'wr': best[7]}}

    # Portfolio final
    print(f'\n{"=" * 68}')
    print(f'  RUN FINAL FASE 5')
    print(f'{"=" * 68}')
    final_cfg = {}
    for name in sorted(best_per_bot):
        b = best_per_bot[name]
        if b['result']['trades'] == 0:
            print(f'  {name}: SKIPPED (no valid params)')
            continue
        final_cfg[name] = dict(b['params'], sym=SYMBOLS[name], tf='vwap')
        r = b['result']
        print(f'  {name}: vn={final_cfg[name]["vn"]} dt={final_cfg[name]["dt"]} '
              f'sl={final_cfg[name]["sl"]} tp={final_cfg[name]["tp"]} '
              f'tp_={final_cfg[name]["tp_"]} rp={final_cfg[name]["rp"]} '
              f'→ {r["trades"]}t ret={r["ret"]}% dd={r["dd"]}% wr={r["wr"]}%')

    portfolio_trades = []
    for name in sorted(final_cfg):
        cfg = final_cfg[name]
        df = fetch_cached(cfg['sym'], '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
        print(f'  → {name}: {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | '
              f'WR {wr:.1f}% | DD {dd:.1f}%')
        for t in trades: t['bot'] = name; portfolio_trades.append(t)

    if portfolio_trades:
        tp = sum(t['pnl'] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t['pnl'] > 0)
        nt = len(portfolio_trades)
        pe = INITIAL_EQUITY * len(final_cfg) + tp
        pr = (pe / (INITIAL_EQUITY * len(final_cfg)) - 1) * 100
        print(f'\n{"=" * 68}')
        print(f'  PORTFOLIO FASE 5: {nt}t | PnL ${tp:+.2f} | Eq ${pe:.2f} ({pr:+.2f}%) | WR {tw/nt*100:.1f}%')
        print(f'{"=" * 68}')
        Path('walkforward_phase5_results.json').write_text(json.dumps({
            'final_cfg': {k: {kk: vv for kk, vv in v.items() if kk != 'sym'} for k, v in final_cfg.items()},
            'portfolio': {'trades': nt, 'pnl': round(tp,2), 'equity': round(pe,2), 'return': round(pr,2)},
        }, indent=2))

def run_search4():
    """Fase 4: búsqueda aleatoria amplia — más parámetros, más combinaciones."""
    import random
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    random.seed(426)

    SYMBOLS = {'BNB':'BNBUSDT','BTC':'BTCUSDT','ETH':'ETHUSDT','SOL':'SOLUSDT','XRP':'XRPUSDT'}
    # Risk & min_size base
    BASE_RP = {'BNB': 0.015, 'BTC': 0.0112, 'ETH': 0.0128, 'SOL': 0.015, 'XRP': 0.015}
    BASE_MS = {'BNB': 0.01, 'BTC': 0.001, 'ETH': 0.001, 'SOL': 0.01, 'XRP': 0.1}

    N_SAMPLES = 400

    print('=' * 68)
    print('  FASE 4: BÚSQUEDA ALEATORIA AMPLIA (%d samples/bot)' % N_SAMPLES)
    print('=' * 68)

    best_all = {}
    for name in sorted(SYMBOLS):
        sym = SYMBOLS[name]
        print(f'\n📡 {name} — buscando...', flush=True)
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)

        results = []
        for s in range(N_SAMPLES):
            # Muestreo aleatorio de parámetros
            vn = random.choice([5, 6, 7, 8, 10, 12, 15, 20])
            dt = random.choice([0.3, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0])
            sl = random.choice([0.5, 0.75, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0])
            tp = random.choice([1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0])
            tp_ = random.choice([10, 20, 30, 50, 100, 200])
            rp_mult = random.choice([0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5])
            rp = round(BASE_RP[name] * rp_mult, 4)

            cfg = {'vn': vn, 'dt': dt, 'sl': sl, 'tp': tp, 'tp_': tp_,
                   'rp': rp, 'ms': BASE_MS[name], 'tf': 'vwap'}

            trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)

            ret = (final_eq / INITIAL_EQUITY - 1) * 100
            dd = 0
            if trades:
                eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
                for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c)
                dd = min(dd, (eq_c-peak)/peak*100) if peak > 0 else 0

            wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100 if trades else 0
            results.append({'vn': vn, 'dt': dt, 'sl': sl, 'tp': tp, 'tp_': tp_,
                           'rp': rp, 'trades': len(trades), 'ret': round(ret, 2),
                           'dd': round(dd, 2), 'wr': round(wr, 1)})

            if (s + 1) % 50 == 0:
                print(f'  {s+1}/{N_SAMPLES}...', end=' ', flush=True)

        # Mejores por rango de DD
        best_all[name] = {}
        for max_dd_label, max_dd in [('dd-10', -10), ('dd-15', -15), ('dd-20', -20), ('dd-all', -99)]:
            candidates = [r for r in results if r['dd'] >= max_dd and r['trades'] >= 5]
            if not candidates:
                continue
            best = max(candidates, key=lambda r: r['ret'])
            best_all[name][max_dd_label] = best

        print(f'\n  Results:', flush=True)
        for label, b in sorted(best_all[name].items()):
            print(f'    {label}: vn={b["vn"]} dt={b["dt"]} sl={b["sl"]} tp={b["tp"]} '
                  f'tp_={b["tp_"]} rp={b["rp"]} → {b["trades"]}t ret={b["ret"]}% '
                  f'dd={b["dd"]}% wr={b["wr"]}%', flush=True)

    # Portfolio: evaluar la mejor combinación global
    print(f'\n{"=" * 68}')
    print(f'  PORTFOLIO — BEST CONFIG')
    print(f'{"=" * 68}')

    # Elegir configuración dd-15 para cada bot (buen balance)
    final_cfg = {}
    for name in sorted(SYMBOLS):
        b = best_all[name].get('dd-15', best_all[name].get('dd-all'))
        if not b:
            print(f'  {name}: NO BEST FOUND')
            continue
        final_cfg[name] = {'vn': b['vn'], 'dt': b['dt'], 'sl': b['sl'], 'tp': b['tp'],
                          'tp_': b['tp_'], 'rp': b['rp'], 'ms': BASE_MS[name], 'tf': 'vwap',
                          'sym': SYMBOLS[name]}
        print(f'  {name}: vn={b["vn"]} dt={b["dt"]} sl={b["sl"]} tp={b["tp"]} '
              f'tp_={b["tp_"]} rp={b["rp"]} → ret={b["ret"]}% dd={b["dd"]}%')

    print(f'\n  Running portfolio...', flush=True)
    portfolio_trades = []
    for name in sorted(final_cfg):
        cfg = final_cfg[name]
        df = fetch_cached(cfg['sym'], '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
        print(f'  {name}: {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | '
              f'WR {wr:.1f}% | DD {dd:.1f}%')
        for t in trades: t['bot'] = name; portfolio_trades.append(t)

    if portfolio_trades:
        tp = sum(t['pnl'] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t['pnl'] > 0)
        nt = len(portfolio_trades)
        pe = INITIAL_EQUITY * len(final_cfg) + tp
        pr = (pe / (INITIAL_EQUITY * len(final_cfg)) - 1) * 100
        print(f'\n{"=" * 68}')
        print(f'  PORTFOLIO FASE 4: {nt}t | PnL ${tp:+.2f} | Eq ${pe:.2f} ({pr:+.2f}%) | WR {tw/nt*100:.1f}%')
        print(f'{"=" * 68}')
        Path('walkforward_phase4_results.json').write_text(json.dumps({
            'final_cfg': {k: {kk: vv for kk, vv in v.items() if kk != 'sym'} for k, v in final_cfg.items()},
            'portfolio': {'trades': nt, 'pnl': round(tp,2), 'equity': round(pe,2), 'return': round(pr,2)},
        }, indent=2))

def run_optimize3():
    """Fase 3: risk_pct más agresivo para bots estrella + ajustes finos."""
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    # Base = mejores params de fase 2
    BASE = {
        'BNB': {'vn': 8,  'dt': 1.25, 'sl': 1.5, 'tp': 2.0,  'rp': 0.015, 'ms': 0.01,  'tp_': 50, 'sym': 'BNBUSDT', 'tf': 'vwap'},
        'BTC': {'vn': 20, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.0112, 'ms': 0.001, 'tp_': 50, 'sym': 'BTCUSDT', 'tf': 'vwap'},
        'ETH': {'vn': 10, 'dt': 1.25, 'sl': 1.0, 'tp': 2.5,  'rp': 0.0128, 'ms': 0.001, 'tp_': 50, 'sym': 'ETHUSDT', 'tf': 'vwap'},
        'SOL': {'vn': 10, 'dt': 1.25, 'sl': 1.5, 'tp': 2.5,  'rp': 0.015,  'ms': 0.01,  'tp_': 200, 'sym': 'SOLUSDT', 'tf': 'vwap'},
        'XRP': {'vn': 10, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.015,  'ms': 0.1,   'tp_': 50, 'sym': 'XRPUSDT', 'tf': 'vwap'},
    }

    print('=' * 68)
    print('  OPTIMIZE FASE 3: risk_pct agresivo + per-bot tuning')
    print('=' * 68)

    # Para cada bot: probar risk_pct desde 1.0x hasta 2.5x
    best_per_bot = {}
    for name in sorted(BASE):
        cfg = dict(BASE[name])
        sym = cfg['sym']
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)

        base_rp = cfg['rp']
        print(f'\n{name} (base_rp={base_rp})')

        multipliers = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]
        best = None; best_ret = -999
        for m in multipliers:
            cfg['rp'] = round(base_rp * m, 4)
            trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
            ret = (final_eq / INITIAL_EQUITY - 1) * 100
            dd = 0
            if trades:
                eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
                for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
            wr = sum(1 for t in trades if t['pnl'] > 0) / len(trades) * 100 if trades else 0
            status = 'OK' if dd >= -18 else 'HIGH DD'
            print(f'    {m:.2f}x rp={cfg["rp"]}: {len(trades)}t ret={ret:+.2f}% DD={dd:.1f}% WR={wr:.1f}% {status}')
            if ret > best_ret and dd >= -18:
                best_ret = ret; best = (m, cfg['rp'], len(trades), round(ret,2), round(dd,2))

        best_per_bot[name] = {'mult': best[0], 'rp': best[1], 'trades_n': best[2],
                               'ret': best[3], 'dd': best[4]}

    print(f'\n{"=" * 68}')
    print(f'  BEST PER BOT — FASE 3')
    print(f'{"=" * 68}')
    final_cfg = {}
    for name in sorted(BASE):
        cfg = dict(BASE[name])
        b = best_per_bot[name]
        cfg['rp'] = b['rp']
        final_cfg[name] = cfg
        print(f'  {name}: rp={cfg["rp"]} → {b["trades_n"]}t ret={b["ret"]}% DD={b["dd"]}%')

    print(f'\n{"=" * 68}')
    print(f'  RUN FINAL FASE 3')
    print(f'{"=" * 68}')
    portfolio_trades = []
    for name in sorted(final_cfg):
        cfg = final_cfg[name]
        sym = cfg['sym']
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
        print(f'  {name}: {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | WR {wr:.1f}% | DD {dd:.1f}%')
        for t in trades: t['bot'] = name; portfolio_trades.append(t)

    if portfolio_trades:
        tp = sum(t['pnl'] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t['pnl'] > 0)
        nt = len(portfolio_trades)
        pe = INITIAL_EQUITY * 5 + tp
        pr = (pe / (INITIAL_EQUITY * 5) - 1) * 100
        print(f'\n{"=" * 68}')
        print(f'  PORTFOLIO FASE 3: {nt}t | PnL ${tp:+.2f} | Eq ${pe:.2f} ({pr:+.2f}%) | WR {tw/nt*100:.1f}%')
        print(f'{"=" * 68}')
        Path('walkforward_phase3_results.json').write_text(json.dumps({
            'final_cfg': {k: {kk: vv for kk, vv in v.items() if kk != 'sym'} for k, v in final_cfg.items()},
            'portfolio': {'trades': nt, 'pnl': round(tp, 2), 'equity': round(pe, 2), 'return': round(pr, 2)},
        }, indent=2))

def run_optimize2():
    """Fase 2: optimiza trend_period + risk_pct con los mejores vn/dt/sl/tp de fase 1."""
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    PHASE1_BEST = {
        'BNB': {'vn': 8,  'dt': 1.25, 'sl': 1.5, 'tp': 2.0,  'rp': 0.01, 'ms': 0.01,  'sym': 'BNBUSDT'},
        'BTC': {'vn': 20, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.0075, 'ms': 0.001, 'sym': 'BTCUSDT'},
        'ETH': {'vn': 10, 'dt': 1.25, 'sl': 1.0, 'tp': 2.5,  'rp': 0.0085, 'ms': 0.001, 'sym': 'ETHUSDT'},
        'SOL': {'vn': 10, 'dt': 1.25, 'sl': 1.5, 'tp': 2.5,  'rp': 0.01, 'ms': 0.01,   'sym': 'SOLUSDT'},
        'XRP': {'vn': 10, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.01, 'ms': 0.1,    'sym': 'XRPUSDT'},
    }

    print('=' * 68)
    print('  OPTIMIZE FASE 2: trend_period + risk_pct')
    print('=' * 68)

    optimized = {}
    for name in sorted(PHASE1_BEST):
        cfg = PHASE1_BEST[name]
        sym = cfg['sym']
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)

        print(f'\n{name} ({sym}) — vn={cfg["vn"]} dt={cfg["dt"]} sl={cfg["sl"]} tp={cfg["tp"]}')

        best = None; best_ret = -999
        trend_periods = [20, 50, 100, 200]
        # risk multipliers: 0.5x, 0.75x, 1.0x, 1.25x, 1.5x of base (up to DD control)
        risk_mults = [0.75, 1.0, 1.25, 1.5]

        count = len(trend_periods) * len(risk_mults)
        idx = 0
        for tp_ in trend_periods:
            for rm in risk_mults:
                idx += 1
                test_cfg = dict(cfg, tf='vwap', tp_=tp_)
                test_cfg['rp'] = round(cfg['rp'] * rm, 4)
                trades, final_eq = walk_bot(df, atr, vwaps, devs, test_cfg)
                ret = (final_eq / INITIAL_EQUITY - 1) * 100
                # DD
                dd = 0
                if trades:
                    eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
                    for t in trades:
                        eq_c += t['pnl']
                        if eq_c > peak: peak = eq_c
                        dd = min(dd, (eq_c - peak) / peak * 100)
                if ret > best_ret and dd >= -15 and ret > -20:
                    best_ret = ret; best = (tp_, rm, round(test_cfg['rp'], 4), len(trades), round(ret, 2), round(dd, 2))
                if idx % int(max(1, count/4)) == 0:
                    print(f'    {idx}/{count}...', end=' ', flush=True)
        print(f'\n    Best: tp_={best[0]} risk_mult={best[1]} rp={best[2]} '
              f'→ {best[3]}t ret={best[4]}% DD={best[5]}%')
        optimized[name] = {
            'phase1': {'vn': cfg['vn'], 'dt': cfg['dt'], 'sl': cfg['sl'], 'tp': cfg['tp']},
            'phase2': {'tp_': best[0], 'risk_mult': best[1], 'rp': best[2]},
            'result': {'trades': best[3], 'ret': best[4], 'dd': best[5]},
        }

    print(f'\n{"=" * 68}')
    print(f'  FASE 2 COMPLETA')
    print(f'{"=" * 68}')
    for name, data in sorted(optimized.items()):
        p1 = data['phase1']; p2 = data['phase2']; r = data['result']
        print(f'  {name}: vn={p1["vn"]} dt={p1["dt"]} sl={p1["sl"]} tp={p1["tp"]} '
              f'tp_={p2["tp_"]} rp={p2["rp"]} → {r["trades"]}t ret={r["ret"]}% DD={r["dd"]}%')
    Path('optimize_phase2_results.json').write_text(json.dumps(optimized, indent=2))

    # Run final with phase2 best params
    print(f'\n{"=" * 68}')
    print(f'  RUN FINAL CON FASE 2')
    print(f'{"=" * 68}')
    portfolio_trades = []
    for name in sorted(optimized):
        d = optimized[name]
        cfg = dict(PHASE1_BEST[name])
        cfg['tp_'] = d['phase2']['tp_']
        cfg['rp'] = d['phase2']['rp']
        cfg['tf'] = 'vwap'
        sym = cfg['sym']
        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        trades, final_eq = walk_bot(df, atr, vwaps, devs, cfg)
        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
        print(f'  {name}: {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | '
              f'WR {wr:.1f}% | DD {dd:.1f}%')
        for t in trades: t['bot'] = name; portfolio_trades.append(t)

    if portfolio_trades:
        tp = sum(t['pnl'] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t['pnl'] > 0)
        nt = len(portfolio_trades)
        pe = INITIAL_EQUITY * 5 + tp
        pr = (pe / (INITIAL_EQUITY * 5) - 1) * 100
        print(f'\n{"=" * 68}')
        print(f'  PORTFOLIO FASE 2: {nt}t | PnL ${tp:+.2f} | Eq ${pe:.2f} ({pr:+.2f}%) | WR {tw/nt*100:.1f}%')
        print(f'{"=" * 68}')

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == 'final':
        run_final()
    elif len(sys.argv) > 1 and sys.argv[1] == 'opt2':
        run_optimize2()
    elif len(sys.argv) > 1 and sys.argv[1] == 'opt3':
        run_optimize3()
    elif len(sys.argv) > 1 and sys.argv[1] == 'search4':
        run_search4()
    elif len(sys.argv) > 1 and sys.argv[1] == 'search5':
        run_search5()
    else:
        run()