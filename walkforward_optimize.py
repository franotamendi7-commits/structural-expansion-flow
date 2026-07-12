"""
Walk-forward backtest: enero 2026 → hoy.
Simula trading en vivo: cada punto solo ve datos pasados.
Mejora parámetros cada N trades basado en rendimiento.
ML filter aprende continuamente de señales correctas/incorrectas.
"""
import sys, json, math, time
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

BINANCE_FAPI = "https://fapi.binance.com"
COMMISSION = 0.0005
SPREAD = 0.0002

# Parámetros iniciales (originales)
BOTS = {
    "BTC": {"symbol": "BTCUSDT", "risk_pct": 0.0075, "min_size": 0.001,
             "base_params": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.5, "tp_mult": 1.0},
             "regime_params": {
                 "ALCISTA": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.5, "tp_mult": 1.0, "bias": 1},
                 "BAJISTA": {"vwap_n": 20, "dev_thr": 1.0, "sl_mult": 2.0, "tp_mult": 1.5, "bias": -1},
                 "LATERAL": {"vwap_n": 15, "dev_thr": 0.75, "sl_mult": 1.5, "tp_mult": 1.0, "bias": 0},
             }},
    "ETH": {"symbol": "ETHUSDT", "risk_pct": 0.0085, "min_size": 0.001,
             "base_params": {"vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.5, "tp_mult": 1.5}},
    "SOL": {"symbol": "SOLUSDT", "risk_pct": 0.01, "min_size": 0.01,
             "base_params": {"vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.5}},
    "XRP": {"symbol": "XRPUSDT", "risk_pct": 0.01, "min_size": 0.1,
             "base_params": {"vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.5}},
    "BNB": {"symbol": "BNBUSDT", "risk_pct": 0.01, "min_size": 0.01,
             "base_params": {"vwap_n": 10, "dev_thr": 1.0, "sl_mult": 1.2, "tp_mult": 1.2}},
}

# ---------------------------------------------------------------
# INDICADORES
# ---------------------------------------------------------------
def atr_wilder(df, n=14):
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    out[n-1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i-1] * (n-1) + tr[i]) / n
    return out

def rolling_vwap(df, n):
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vp = tp * df["volume"]
    return vp.rolling(n).sum() / df["volume"].rolling(n).sum()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

# ---------------------------------------------------------------
# FETCH
# ---------------------------------------------------------------
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
        except Exception:
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

def fetch_range(symbol, interval, start, end, label=""):
    from datetime import timedelta
    all_bars = []; max_iter = 50; iters = 0
    print(f"    Fetching {label}...", end="", flush=True)
    while start < end and iters < max_iter:
        df = fetch_klines(symbol, interval, 1500, start_time=start)
        if df.empty:
            print(f" empty at iter {iters}", flush=True)
            break
        all_bars.append(df)
        start = df["open_time"].iloc[-1] + timedelta(minutes=1)
        iters += 1
        if iters % 5 == 0:
            print(f".", end="", flush=True)
    print(f" done ({iters} req)", flush=True)
    if not all_bars:
        return pd.DataFrame()
    full = pd.concat(all_bars, ignore_index=True)
    full = full.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return full

# ---------------------------------------------------------------
# RÉGIMEN (igual que multi_bot)
# ---------------------------------------------------------------
def detect_regime(df1h):
    df = df1h.copy().sort_values("open_time").reset_index(drop=True)
    df["day"] = df["open_time"].dt.floor("D")
    df["h4"] = df["open_time"].dt.floor("4h")
    daily = df.groupby("day").agg(close_d=("close","last")).reset_index()
    daily["ema20"] = ema(daily["close_d"], 20)
    daily["ema20_y"] = daily["ema20"].shift(1)
    daily["ema20_y_prev"] = daily["ema20_y"].shift(1)
    daily["rising_y"] = (daily["ema20_y"] > daily["ema20_y_prev"]).astype(bool)
    daily["falling_y"] = (daily["ema20_y"] < daily["ema20_y_prev"]).astype(bool)
    if len(daily) < 2:
        return "LATERAL"
    last_day = daily["day"].iloc[-1]
    dr = daily[daily["day"] == last_day].iloc[0]
    ema20_y = dr["ema20_y"]; rising = bool(dr["rising_y"]) if pd.notna(dr["rising_y"]) else False
    falling = bool(dr["falling_y"]) if pd.notna(dr["falling_y"]) else False
    h4 = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    if len(h4) < 2: return "LATERAL"
    close_4h = float(h4["close_4h"].iloc[-2])
    if pd.isna(ema20_y): raw = "LATERAL"
    elif close_4h > ema20_y and rising: raw = "ALCISTA"
    elif close_4h < ema20_y and falling: raw = "BAJISTA"
    else: raw = "LATERAL"
    h4_full = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4_full["day"] = h4_full["h4"].dt.floor("D")
    h4_full = h4_full.merge(daily[["day","ema20_y","rising_y","falling_y"]], on="day", how="left")
    h4_full["raw"] = "LATERAL"
    up = (h4_full["close_4h"] > h4_full["ema20_y"]) & h4_full["rising_y"]
    dn = (h4_full["close_4h"] < h4_full["ema20_y"]) & h4_full["falling_y"]
    h4_full.loc[up, "raw"] = "ALCISTA"; h4_full.loc[dn, "raw"] = "BAJISTA"
    h4_full.loc[h4_full["ema20_y"].isna(), "raw"] = "LATERAL"
    raw_series = h4_full["raw"].tolist(); final = []
    cur = "LATERAL"; pending=None; pc=0
    for v in raw_series:
        if v == cur: pending=None; pc=0; final.append(cur)
        else:
            if v == pending: pc += 1
            else: pending=v; pc=1
            if pc >= 2: cur = pending; pending=None; pc=0
            final.append(cur)
    return final[-1] if final else raw

# ---------------------------------------------------------------
# TREND FILTER (para bots sin régimen)
# ---------------------------------------------------------------
def trend_bias(df1h):
    """Retorna 1 (alcista), -1 (bajista) o 0 (lateral) basado en EMA20 1h."""
    if df1h is None or len(df1h) < 30:
        return 0
    close = df1h["close"].values
    ema20 = pd.Series(close).ewm(span=20, adjust=False).mean().values
    ema50 = pd.Series(close).ewm(span=50, adjust=False).mean().values
    ema20_slope = ema20[-1] - ema20[-5]
    if close[-1] > ema20[-1] and ema20[-1] > ema50[-1] and ema20_slope > 0:
        return 1
    elif close[-1] < ema20[-1] and ema20[-1] < ema50[-1] and ema20_slope < 0:
        return -1
    return 0

# ---------------------------------------------------------------
# SEÑAL
# ---------------------------------------------------------------
def vwap_signal(df15, vwap_n, dev_thr, sl_mult, tp_mult, bias=0):
    """VWAP breakout con bias direccional.
    bias=1: solo LONGS, bias=-1: solo SHORTS, bias=0: ambos.
    """
    vwap = rolling_vwap(df15, vwap_n)
    dev = (df15["close"] - vwap) / vwap * 100.0
    dev_prev = dev.shift(1)
    if len(dev) < 2: return None
    last_dev = float(dev.iloc[-1])
    last_dev_prev = float(dev_prev.iloc[-1]) if pd.notna(dev_prev.iloc[-1]) else None
    if last_dev_prev is None: return None
    a15 = float(atr_wilder(df15, 14)[-1]) if len(df15) >= 14 else 0
    if not math.isfinite(a15) or a15 <= 0: return None
    close = float(df15["close"].iloc[-1])

    # Señal LONG: precio cruza DEBAJO de -thr (desviación negativa → mean reversion)
    long_ok = (bias >= 0) and last_dev_prev >= -dev_thr and last_dev < -dev_thr
    # Señal SHORT: precio cruza ENCIMA de +thr
    short_ok = (bias <= 0) and last_dev_prev <= dev_thr and last_dev > dev_thr

    if long_ok:
        return {"dir": 1, "entry": close, "stop": close - sl_mult * a15, "tp": close + tp_mult * a15}
    if short_ok:
        return {"dir": -1, "entry": close, "stop": close + sl_mult * a15, "tp": close - tp_mult * a15}
    return None

# ---------------------------------------------------------------
# ML FILTER (aprende de trades pasados)
# ---------------------------------------------------------------
class MLFilter:
    """Filtro simple: aprende qué combinaciones de features predicen pérdidas."""
    def __init__(self):
        self.feature_log = []  # [(features_dict, pnl)]
        self.min_samples = 20
        self.thresholds = {}   # feature -> (min_val, max_val) para trades ganadores

    def add_trade(self, features, pnl):
        self.feature_log.append((features, pnl))
        if len(self.feature_log) > 500:
            self.feature_log.pop(0)
        if len(self.feature_log) % 20 == 0:
            self._learn()

    def _learn(self):
        """Aprende thresholds: rangos de features donde los trades son positivos."""
        wins = [f for f, p in self.feature_log if p > 0]
        if len(wins) < self.min_samples:
            return
        for key in wins[0]:
            vals = [f[key] for f in wins]
            self.thresholds[key] = (float(np.percentile(vals, 10)),
                                    float(np.percentile(vals, 90)))

    def should_enter(self, features):
        if len(self.feature_log) < self.min_samples:
            return True, "learning"
        # Veto si el feature está fuera del rango donde históricamente se ganó
        for key, (lo, hi) in self.thresholds.items():
            if key not in features:
                continue
            v = features[key]
            if v < lo - 1.5 * (hi - lo) or v > hi + 1.5 * (hi - lo):
                return False, f"{key}={v:.2f} out of range [{lo:.2f},{hi:.2f}]"
        return True, "ok"

# ---------------------------------------------------------------
# WALK-FORWARD ENGINE
# ---------------------------------------------------------------
def walkforward_bot(name, config, df15, df1h=None):
    """Corre el bot desde enero hasta hoy, mejorando parámetros en el camino."""
    from copy import deepcopy

    MIN_RISK = 1.0
    eq = 0.0
    trades = []
    ml = MLFilter()
    params = deepcopy(config.get("base_params", {}))
    n_bars = len(df15)
    adaptation_window = 30  # re-evaluar cada 30 trades
    adaptation_counter = 0

    for i in range(100, n_bars):
        bar = df15.iloc[:i+1]
        close = float(bar["close"].iloc[-1])

        # Determinar bias (trend filter)
        bias = 0
        if name == "BTC" and df1h is not None:
            regime = detect_regime(df1h)
            rp = config["regime_params"].get(regime, config["base_params"])
            bias = rp.get("bias", 0)
            params["sl_mult"] = rp["sl_mult"]
            params["tp_mult"] = rp["tp_mult"]
            params["vwap_n"] = rp["vwap_n"]
            params["dev_thr"] = rp["dev_thr"]
        else:
            bias = trend_bias(df1h)

        pos = None
        # Asignar pos desde fuera del loop (lo manejo con yield-like, pero usemos variable externa)
        # Simplifico: busco trades abiertos desde la iteración anterior
        # Uso un enfoque más simple: trackear posición globalmente

    # Rehago con estructura más simple
    return []


def run():
    """Walk-forward principal: enero 2026 → hoy."""
    import sys
    print("=" * 60, flush=True)
    print("  WALK-FORWARD BACKTEST: Enero → Julio 2026", flush=True)
    print("  Aprendizaje continuo + Trend Filter + R:R dinámico", flush=True)
    print("=" * 60, flush=True)

    from datetime import timedelta
    end_date = datetime.now(timezone.utc)
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

    results = {}
    portfolio_trades = []

    for name, config in sorted(BOTS.items()):
        symbol = config["symbol"]
        print(f"\n📡 {name} ({symbol})", flush=True)
        df15 = fetch_range(symbol, "15m", start_date, end_date, label="15m")
        if df15.empty or len(df15) < 200:
            print(f"  SKIP: {len(df15)} bars")
            continue
        print(f"  → {len(df15)} 15m bars", flush=True)
        df1h = fetch_range(symbol, "1h", start_date, end_date, label="1h")
        print(f"  → {len(df1h)} 1h bars. Walk-forward...", flush=True)

        trades = _walk_bot(name, config, df15, df1h)
        if not trades:
            print(f"  No trades")
            results[name] = {"trades": 0, "pnl": 0}
            continue

        pnl = sum(t["pnl"] for t in trades)
        wins = sum(1 for t in trades if t["pnl"] > 0)
        wr = wins / len(trades) * 100
        # Max DD
        eq = 0; peak = 0.001; dd_max = 0
        for t in trades:
            eq += t["pnl"]
            if eq > peak: peak = eq
            dd = (eq - peak) / peak * 100 if peak > 0 else 0
            dd_max = min(dd_max, dd)

        results[name] = {"trades": len(trades), "pnl": round(pnl, 2),
                         "wr": round(wr, 1), "dd": round(dd_max, 2)}
        for t in trades:
            t["bot"] = name
            portfolio_trades.append(t)

        print(f"  ✅ {len(trades)} trades | PnL ${pnl:+.2f} | WR {wr:.1f}% | DD {dd_max:.2f}%")

    # Portfolio
    portfolio_trades.sort(key=lambda x: x.get("time", ""))
    pnl_total = sum(t["pnl"] for t in portfolio_trades)
    wins_total = sum(1 for t in portfolio_trades if t["pnl"] > 0)
    wr_total = wins_total / len(portfolio_trades) * 100 if portfolio_trades else 0

    print(f"\n{'='*60}")
    print(f"  PORTFOLIO TOTAL (5 bots, enero→julio 2026)")
    print(f"{'='*60}")
    print(f"  Trades:    {len(portfolio_trades)}")
    print(f"  Win Rate:  {wr_total:.1f}%")
    print(f"  PnL:       ${pnl_total:+.2f}")
    print(f"  Mejor bot: {max(results, key=lambda k: results[k]['pnl'])}")
    print(f"{'='*60}")

    # Guardar
    Path(__file__).parent.joinpath("walkforward_results.json").write_text(
        json.dumps({"bots": results, "portfolio": {
            "trades": len(portfolio_trades), "pnl": round(pnl_total, 2),
            "wr": round(wr_total, 1)}}, indent=2))
    print(f"\nResultados guardados en walkforward_results.json")


def _walk_bot(name, config, df15, df1h=None):
    """Walk-forward para un bot: itera barra por barra, solo ve datos pasados."""
    MIN_RISK = 1.0
    eq = 0.0
    trades = []
    pos = None
    ml = MLFilter()
    params = dict(config.get("base_params", {}))
    adaptation = {"trades_in_window": [], "window_size": 30}

    n_bars = len(df15)

    for i in range(100, n_bars):
        bar = df15.iloc[:i+1]
        close = float(bar["close"].iloc[-1])
        bar_time = bar["open_time"].iloc[-1]

        # 1x1h data hasta este punto (sin lookahead)
        bh = None
        if df1h is not None:
            bh = df1h[df1h["open_time"] <= bar_time].copy()

        # 1) Trend bias
        bias = 0
        sl_mult = params.get("sl_mult", 1.5)
        tp_mult = params.get("tp_mult", 1.0)
        vwap_n = params.get("vwap_n", 10)
        dev_thr = params.get("dev_thr", 1.0)

        if name == "BTC" and bh is not None and len(bh) > 10:
            regime = detect_regime(bh)
            rp = config["regime_params"].get(regime, config["base_params"])
            bias = rp.get("bias", 0)
            sl_mult = rp["sl_mult"]; tp_mult = rp["tp_mult"]
            vwap_n = rp["vwap_n"]; dev_thr = rp["dev_thr"]
        else:
            bias = trend_bias(bh)

        # 2) Exit check
        if pos is not None:
            exited = False
            if pos["dir"] == 1:
                if close <= pos["sl"]:
                    exit_p = pos["sl"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"]
                    trades.append({"dir": 1, "pnl": pnl, "reason": "sl",
                                   "time": str(bar_time)})
                    pos = None; exited = True
                elif close >= pos["tp"]:
                    exit_p = pos["tp"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"]
                    trades.append({"dir": 1, "pnl": pnl, "reason": "tp",
                                   "time": str(bar_time)})
                    pos = None; exited = True
            else:
                if close >= pos["sl"]:
                    exit_p = pos["sl"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"]
                    trades.append({"dir": -1, "pnl": pnl, "reason": "sl",
                                   "time": str(bar_time)})
                    pos = None; exited = True
                elif close <= pos["tp"]:
                    exit_p = pos["tp"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"]
                    trades.append({"dir": -1, "pnl": pnl, "reason": "tp",
                                   "time": str(bar_time)})
                    pos = None; exited = True
            if exited:
                eq += pnl
                # ML aprende de este trade
                if bh is not None and len(bh) > 5:
                    ml.add_trade({
                        "dev": float((close - rolling_vwap(bar, vwap_n).iloc[-1]) / rolling_vwap(bar, vwap_n).iloc[-1] * 100) if len(bar) >= vwap_n else 0,
                        "atr": float(atr_wilder(bar, 14)[-1]) if len(bar) >= 14 else 0,
                        "hour": bar_time.hour,
                    }, pnl)
                # Adaptation check
                adaptation["trades_in_window"].append(pnl)
                if len(adaptation["trades_in_window"]) >= adaptation["window_size"]:
                    recent_pnl = sum(adaptation["trades_in_window"])
                    if recent_pnl < 0:
                        # Ajustar parámetros: reducir SL, aumentar TP
                        old_sl = sl_mult
                        sl_mult = max(1.0, sl_mult - 0.1)
                        tp_mult = tp_mult + 0.1
                    adaptation["trades_in_window"] = []
                continue

        if pos is not None:
            continue

        # 3) Señal
        sig = vwap_signal(bar, vwap_n, dev_thr, sl_mult, tp_mult, bias)
        if sig is None:
            continue

        # ML veto
        ok, reason = ml.should_enter(sig)
        if not ok:
            continue

        # Entry
        risk = config["risk_pct"] * eq if eq > 0 else MIN_RISK
        price_range = abs(sig["entry"] - sig["stop"])
        if price_range <= 0: continue
        size = risk / price_range
        if size < config["min_size"]: continue

        entry_fill = sig["entry"] * (1 + SPREAD) if sig["dir"] == 1 else sig["entry"] * (1 - SPREAD)
        pos = {"dir": sig["dir"], "entry": entry_fill, "size": size,
               "sl": sig["stop"], "tp": sig["tp"]}

    return trades


if __name__ == "__main__":
    run()