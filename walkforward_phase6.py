"""
FASE 6: ML controla leverage 0-20x + SL mínimo contra spread
=============================================================
- Walk-forward real desde enero
- ML aprende y asigna leverage (confianza → 0x a 20x)
- SL mínimo = 3× (spread + comisión) para que trades no mueran por ruido
- Parámetros base sólidos de Fase 3
"""
import sys, json, math, time, random
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
SL_MIN_PCT = 0.003   # 0.3% mínimo de distancia SL
CACHE_DIR = Path(__file__).parent / "cache_15m"

BOTS = {
    'BNB': {'vn': 8,  'dt': 1.25, 'sl': 1.5, 'tp': 2.0,  'rp': 0.015, 'ms': 0.01,  'tp_': 50, 'sym': 'BNBUSDT', 'tf': 'vwap'},
    'BTC': {'vn': 20, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.0112, 'ms': 0.001, 'tp_': 50, 'sym': 'BTCUSDT', 'tf': 'vwap'},
    'ETH': {'vn': 10, 'dt': 1.25, 'sl': 1.0, 'tp': 2.5,  'rp': 0.0128, 'ms': 0.001, 'tp_': 50, 'sym': 'ETHUSDT', 'tf': 'vwap'},
    'SOL': {'vn': 10, 'dt': 1.25, 'sl': 1.5, 'tp': 2.5,  'rp': 0.015,  'ms': 0.01,  'tp_': 50, 'sym': 'SOLUSDT', 'tf': 'vwap'},
    'XRP': {'vn': 10, 'dt': 1.0,  'sl': 1.2, 'tp': 2.5,  'rp': 0.015,  'ms': 0.1,   'tp_': 50, 'sym': 'XRPUSDT', 'tf': 'vwap'},
}

# ================================================================
# FETCH + CACHE
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

def fetch_cached(symbol, interval, start_dt, end_dt):
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{symbol}_{interval}.npy"
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
    tp = (h + l + c) / 3.0
    n = len(df)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = np.full(n, np.nan)
    atr[13] = tr[:14].mean()
    for i in range(14, n): atr[i] = (atr[i-1] * 13 + tr[i]) / 14
    vp = tp * v
    csvp = np.cumsum(vp); csv = np.cumsum(v)
    vwaps = {}
    for period in [8, 10, 12, 14, 15, 16, 18, 20, 25, 50, 100, 200]:
        vw = np.full(n, np.nan)
        vw[period-1:] = (csvp[period-1:] - np.concatenate([[0], csvp[:-period]])) / \
                        (csv[period-1:] - np.concatenate([[0], csv[:-period]]))
        vwaps[period] = vw
    devs = {p: (c - vw) / vw * 100.0 for p, vw in vwaps.items()}
    return atr, vwaps, devs

# ================================================================
# ML LEVERAGE ENGINE
# ================================================================
class MLLeverage:
    """ML que aprende de trades pasados y asigna leverage 0-20x.
    
    Para cada feature, binned win rate. En inferencia, calcula
    confianza promedio y la mapea a leverage.
    """
    def __init__(self, name, min_trades=15):
        self.name = name
        self.min_trades = min_trades
        self.log = []       # [{features}, pnl]
        self.rules = {}     # feature -> [(threshold, win_rate)]
        self.trained = False

    def add(self, features, pnl):
        self.log.append((dict(features), pnl))
        if len(self.log) >= self.min_trades and len(self.log) % 10 == 0:
            self.train()

    def train(self):
        if len(self.log) < 15: return
        wins = [f for f, p in self.log if p > 0]
        losses = [f for f, p in self.log if p <= 0]
        if len(wins) < 5 or len(losses) < 5: return
        self.rules = {}
        for key in wins[0]:
            wv = np.array([f[key] for f in wins])
            lv = np.array([f[key] for f in losses])
            allv = np.concatenate([wv, lv])
            if len(allv) < 10: continue
            # 5 bins por percentiles
            bins = [10, 30, 50, 70, 90]
            prev = -np.inf
            rules = []
            for bp in bins:
                thresh = float(np.percentile(allv, bp))
                w_in = int(np.sum((wv >= prev) & (wv < thresh)))
                l_in = int(np.sum((lv >= prev) & (lv < thresh)))
                total = w_in + l_in
                wr = w_in / total if total > 0 else 0.5
                rules.append((thresh, wr, total))
                prev = thresh
            # Tail
            w_in = int(np.sum(wv >= prev))
            l_in = int(np.sum(lv >= prev))
            total = w_in + l_in
            wr = w_in / total if total > 0 else 0.5
            rules.append((np.inf, wr, total))
            self.rules[key] = rules
        self.trained = True

    def get_leverage(self, features):
        """0-20x basado en confianza ML."""
        if not self.trained or len(self.log) < 20:
            return 1  # default 1x mientras aprende
        wrs = []
        for key, rules in self.rules.items():
            if key not in features: continue
            v = features[key]
            for thresh, wr, total in rules:
                if v < thresh:
                    if total >= 3:  # mínimo 3 trades en el bucket
                        wrs.append(wr)
                    break
        if not wrs:
            return 1
        avg_wr = float(np.mean(wrs))
        # Mapeo: WR → leverage
        if avg_wr < 0.40: return 0          # no trade
        if avg_wr < 0.50: return 1          # mínimo
        if avg_wr < 0.55: return 3
        if avg_wr < 0.60: return 5
        if avg_wr < 0.65: return 8
        if avg_wr < 0.70: return 12
        if avg_wr < 0.75: return 15
        if avg_wr < 0.80: return 18
        return 20

# ================================================================
# WALK-FORWARD CON LEVERAGE
# ================================================================
def walk_lev(df, atr, vwaps, devs, config):
    """Walk-forward con ML leverage + SL mínimo contra spread."""
    eq = INITIAL_EQUITY
    trades = []
    pos = None
    ml = MLLeverage(config.get('name', 'bot'))

    ca = df["close"].values; ot = df["open_time"].values
    n = len(df)

    vwap_n = config["vn"]; dev_thr = config["dt"]; sl_mult = config["sl"]
    tp_mult = config["tp"]; risk_pct = config["rp"]; min_size = config["ms"]
    tf = config["tf"]; tp_ = config["tp_"]
    min_sl_pct = config.get("min_sl_pct", SL_MIN_PCT)

    for i in range(WARMUP, n):
        c = float(ca[i])
        t = pd.Timestamp(ot[i])

        # --- EXIT ---
        if pos is not None:
            exited = False; reason = None
            if pos["dir"] == 1:
                if c <= pos["sl"]:
                    exit_p = pos["sl"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"] - (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    exited = True; reason = "sl"
                elif c >= pos["tp"]:
                    exit_p = pos["tp"] * (1 - SPREAD)
                    pnl = (exit_p - pos["entry"]) * pos["size"] - (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    exited = True; reason = "tp"
            else:
                if c >= pos["sl"]:
                    exit_p = pos["sl"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"] - (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    exited = True; reason = "sl"
                elif c <= pos["tp"]:
                    exit_p = pos["tp"] * (1 + SPREAD)
                    pnl = (pos["entry"] - exit_p) * pos["size"] - (pos["entry"] + exit_p) * pos["size"] * COMMISSION
                    exited = True; reason = "tp"
            if exited:
                eq += pnl
                ml.add({"dev": pos["dev"], "atr": pos["atr"], "hour": t.hour, "dir": pos["dir"]}, pnl)
                trades.append({"dir":pos["dir"],"pnl":pnl,"reason":reason,"lev":pos.get("lev",1),
                               "entry":float(pos["entry"]),"exit":float(exit_p),"size":float(pos["size"])})
                pos = None
                continue

        # --- SIGNAL ---
        b_bias = 0; b_vn = vwap_n; b_dt = dev_thr; b_sl = sl_mult; b_tp = tp_mult
        if tf == "vwap" and tp_ in vwaps:
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
        if not long_ok and not short_ok: continue

        sig_dir = 1 if long_ok else -1
        raw_sl = c - b_sl * a if long_ok else c + b_sl * a
        raw_tp = c + b_tp * a if long_ok else c - b_tp * a

        # --- ML LEVERAGE ---
        feat = {"dev": dv, "atr": a, "hour": t.hour, "dir": sig_dir}
        lev = ml.get_leverage(feat)
        if lev == 0: continue  # ML veta

        # --- SL MÍNIMO CONTRA SPREAD ---
        min_sl_dist = c * min_sl_pct
        sl_dist = abs(c - raw_sl)
        if sl_dist < min_sl_dist:
            sl_dist = min_sl_dist
            # Ajustar SL manteniendo R:R original
            raw_sl = c - min_sl_dist if long_ok else c + min_sl_dist
        tp_dist = sl_dist * (b_tp / b_sl)  # mantener R:R original
        raw_tp = c + tp_dist if long_ok else c - tp_dist

        # --- POSITION SIZE con leverage ---
        price_range = abs(c - raw_sl)
        if price_range <= 0: continue
        base_risk = risk_pct * eq
        size = (base_risk * lev) / price_range
        if size < min_size: continue

        entry_fill = c * (1 + SPREAD) if long_ok else c * (1 - SPREAD)
        entry_comm = entry_fill * size * COMMISSION
        eq -= entry_comm

        pos = {"dir": sig_dir, "entry": entry_fill, "size": size,
               "sl": raw_sl, "tp": raw_tp, "dev": dv, "atr": a, "lev": lev}

    return trades, eq

# ================================================================
# RUN
# ================================================================
def run(min_sl_pct=SL_MIN_PCT):
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    print('=' * 72)
    print(f'  FASE 6: ML LEVERAGE 0-20x + SL mínimo {min_sl_pct*100:.1f}%')
    print('=' * 72)

    portfolio_trades = []
    for name in sorted(BOTS):
        cfg = dict(BOTS[name], name=name, min_sl_pct=min_sl_pct)
        sym = cfg['sym']
        print(f'\n{name} ({sym})')
        print(f'  Params: vn={cfg["vn"]} dt={cfg["dt"]} sl={cfg["sl"]} tp={cfg["tp"]} rp={cfg["rp"]} tp_={cfg["tp_"]}')

        df = fetch_cached(sym, '15m', start_date, end_date)
        atr, vwaps, devs = precompute_all(df)
        t0 = time.time()
        trades, final_eq = walk_lev(df, atr, vwaps, devs, cfg)
        el = time.time() - t0

        pnl = sum(t['pnl'] for t in trades)
        wins = sum(1 for t in trades if t['pnl'] > 0)
        wr = wins / len(trades) * 100 if trades else 0
        ret = (final_eq / INITIAL_EQUITY - 1) * 100
        dd = 0
        if trades:
            eq_c = INITIAL_EQUITY; peak = INITIAL_EQUITY
            for t in trades: eq_c += t['pnl']; peak = max(peak, eq_c); dd = min(dd, (eq_c-peak)/peak*100)
        avg_lev = float(np.mean([t.get("lev", 1) for t in trades])) if trades else 0

        print(f'  {len(trades)}t | PnL ${pnl:+.2f} | Eq ${final_eq:.2f} ({ret:+.2f}%) | '
              f'WR {wr:.1f}% | DD {dd:.1f}% | AvgLev {avg_lev:.1f}x | {el:.0f}s')
        for t in trades:
            t['bot'] = name
            portfolio_trades.append(t)

    if portfolio_trades:
        tp = sum(t['pnl'] for t in portfolio_trades)
        tw = sum(1 for t in portfolio_trades if t['pnl'] > 0)
        nt = len(portfolio_trades)
        pe = INITIAL_EQUITY * len(BOTS) + tp
        pr = (pe / (INITIAL_EQUITY * len(BOTS)) - 1) * 100
        avg_lev_all = float(np.mean([t.get("lev", 1) for t in portfolio_trades]))
        print(f'\n{"=" * 72}')
        print(f'  PORTFOLIO FASE 6: {nt}t | PnL ${tp:+.2f} | Eq ${pe:.2f} ({pr:+.2f}%) | '
              f'WR {tw/nt*100:.1f}% | AvgLev {avg_lev_all:.1f}x')
        print(f'{"=" * 72}')
        Path('walkforward_phase6_results.json').write_text(json.dumps({
            'config': {k: {kk: vv for kk, vv in v.items() if kk != 'sym'} for k, v in BOTS.items()},
            'min_sl_pct': min_sl_pct,
            'portfolio': {'trades': nt, 'pnl': round(tp, 2), 'equity': round(pe, 2),
                          'return': round(pr, 2), 'avg_leverage': round(avg_lev_all, 1)},
        }, indent=2))
        print(f'\nResultados guardados en walkforward_phase6_results.json')


if __name__ == "__main__":
    # Probar con diferentes SL mínimos
    for pct in [0.003, 0.005, 0.008]:
        print(f"\n{'#' * 72}")
        print(f'#  SL mínimo = {pct*100:.1f}%')
        print(f"{'#' * 72}")
        run(min_sl_pct=pct)