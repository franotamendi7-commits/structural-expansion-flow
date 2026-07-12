"""
OPTIMIZACIÓN COMPLETA — RF sizing + XGBoost + Feature Engineering + Walk-Forward
Integra A+B+C+D del AGENTS.md:
  A: RF como sizing (no gate) — proba ajusta tamaño
  B: Walk-forward Q1→Q2
  C: XGBoost + RF
  D: Feature engineering (volatilidad, correlación, regime)
  E: Todo junto
"""
import json, time, pickle, warnings
import numpy as np
import pandas as pd
import requests
from scipy.stats import linregress
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False
    print("  [!] XGBoost no instalado — solo RF")

BINANCE_FAPI = "https://testnet.binancefuture.com"
COMMISSION = 0.0005; SPREAD = 0.0002; SLIPPAGE_ATR_MULT = 0.1
CAP = 100.0; RISK = 0.01
SYM = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
CFG = {
    'BTCUSDT': {'sm': 2.8, 'ct': 74, 'fd': 30, 'tp': 1.5},
    'ETHUSDT': {'sm': 2.6, 'ct': 72, 'fd': 30, 'tp': 1.8},
    'SOLUSDT': {'sm': 2.3, 'ct': 70, 'fd': 90, 'tp': 2.0},
    'XRPUSDT': {'sm': 2.3, 'ct': 68, 'fd': 60, 'tp': 1.8},
    'BNBUSDT': {'sm': 2.8, 'ct': 68, 'fd': 30, 'tp': 1.6},
}

OLD_RF_PATH = "ml_model.pkl"
RESULTS_PATH = "opt_completa_results.json"

def fetch(sym, iv, sd, ed):
    ab = []; n = 0; st = sd
    while st < ed and n < 50:
        n += 1
        params = {"symbol": sym, "interval": iv, "limit": 1500, "startTime": int(st.timestamp() * 1000)}
        for _ in range(3):
            try:
                r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines", params=params, timeout=30)
                if r.status_code == 200: break
            except: pass
        else: break
        rows = r.json()
        if not rows: break
        cols = ["ot", "o", "h", "l", "c", "v", "ct", "qv", "tr", "tbb", "tbq", "ig"]
        df = pd.DataFrame(rows, columns=cols)
        df["ot"] = pd.to_datetime(df["ot"], unit="ms", utc=True)
        for c in ["o", "h", "l", "c", "v"]: df[c] = pd.to_numeric(df[c])
        ab.append(df)
        st = df["ot"].iloc[-1] + timedelta(minutes=1)
        time.sleep(0.1)
    if not ab: return None
    full = pd.concat(ab, ignore_index=True)
    full = full.drop_duplicates(subset=["ot"]).sort_values("ot").reset_index(drop=True)
    full = full[full["ot"] < ed].reset_index(drop=True)
    # ot is datetime64[ms, UTC], astype(int64) gives milliseconds
    full["ts"] = full["ot"].astype("int64")
    return full

def supertrend(h, l, c, m, span=10):
    n = len(h); hl2 = (h + l) / 2
    tr = np.maximum.reduce([h - l, np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))]); tr[0] = h[0] - l[0]
    atr = pd.Series(tr).ewm(span=span, adjust=False).mean().values
    ub = hl2 + m * atr; lb = hl2 - m * atr; up = ub.copy(); lo = lb.copy(); d = np.ones(n, dtype=int)
    for i in range(1, n):
        if c[i - 1] <= up[i - 1]: up[i] = min(up[i], up[i - 1])
        if c[i - 1] >= lo[i - 1]: lo[i] = max(lo[i], lo[i - 1])
        d[i] = 1 if c[i] > up[i - 1] else (-1 if c[i] < lo[i - 1] else d[i - 1])
    return d

def simular_salida(direction, entry_real, sl_original, tp_ratio, contracts_total, h5, l5, c5, start_idx, atr_5m):
    slip = SLIPPAGE_ATR_MULT * atr_5m
    if direction == "bullish":
        tp_dist = abs(tp_ratio * (entry_real - sl_original))
        tp1 = entry_real + tp_dist
        tp2 = entry_real + 2 * tp_dist
        half = entry_real + 0.5 * tp_dist
    else:
        tp_dist = abs(tp_ratio * (sl_original - entry_real))
        tp1 = entry_real - tp_dist
        tp2 = entry_real - 2 * tp_dist
        half = entry_real - 0.5 * tp_dist
    remain = contracts_total; sl_act = sl_original
    be = False; partial = False; pnl = 0.0; comm_total = 0.0
    exit_type = "full_sl"
    for j in range(start_idx + 1, len(h5)):
        hi = h5[j]; lo = l5[j]
        if direction == "bullish":
            if not be and hi >= half:
                sl_act = entry_real; be = True
            if not partial and hi >= tp1:
                close_n = contracts_total * 0.6
                pnl += (tp1 - entry_real) * close_n
                comm_total += tp1 * close_n * COMMISSION
                remain = contracts_total * 0.4; sl_act = entry_real; be = True; partial = True
            if partial and hi >= tp2:
                pnl += (tp2 - entry_real) * remain
                comm_total += tp2 * remain * COMMISSION
                exit_type = "tp2_reached"; remain = 0; break
            if lo <= sl_act:
                ex = sl_act - slip
                if not partial:
                    pnl += (ex - entry_real) * contracts_total
                    comm_total += ex * contracts_total * COMMISSION
                    exit_type = "full_sl"
                else:
                    pnl += (ex - entry_real) * remain
                    comm_total += ex * remain * COMMISSION
                    exit_type = "tp1_partial_then_sl"
                remain = 0; break
        else:
            if not be and lo <= half:
                sl_act = entry_real; be = True
            if not partial and lo <= tp1:
                close_n = contracts_total * 0.6
                pnl += (entry_real - tp1) * close_n
                comm_total += tp1 * close_n * COMMISSION
                remain = contracts_total * 0.4; sl_act = entry_real; be = True; partial = True
            if partial and lo <= tp2:
                pnl += (entry_real - tp2) * remain
                comm_total += tp2 * remain * COMMISSION
                exit_type = "tp2_reached"; remain = 0; break
            if hi >= sl_act:
                ex = sl_act + slip
                if not partial:
                    pnl += (entry_real - ex) * contracts_total
                    comm_total += ex * contracts_total * COMMISSION
                    exit_type = "full_sl"
                else:
                    pnl += (entry_real - ex) * remain
                    comm_total += ex * remain * COMMISSION
                    exit_type = "tp1_partial_then_sl"
                remain = 0; break
    if remain > 0:
        lp = c5[-1]
        if direction == "bullish": pnl += (lp - entry_real) * remain
        else: pnl += (entry_real - lp) * remain
        comm_total += lp * remain * COMMISSION
        exit_type = "forced"
    comm_total += entry_real * contracts_total * COMMISSION
    return pnl - comm_total, exit_type

def run():
    sd = datetime(2025, 10, 1, tzinfo=timezone.utc)
    ed = datetime.now(timezone.utc)
    # Split: use first 2/3 as train, last 1/3 as test
    total_seconds = (ed - sd).total_seconds()
    split_ts = int((sd.timestamp() + total_seconds * 0.67) * 1000)

    print("=" * 70)
    print("  OPTIMIZACIÓN COMPLETA — RF sizing + XGBoost + Walk-Forward")
    print(f"  Periodo: {sd.date()} → {ed.date()}")
    print(f"  Split 67/33: train < {datetime.fromtimestamp(split_ts/1000, tz=timezone.utc).date()}")
    print("=" * 70)

    # ─── FETCH + PRECOMPUTE ───
    all_data = {}
    for sym in SYM:
        print(f"\n  {sym}: fetching...", end=" ", flush=True)
        d = {tf: fetch(sym, tf, sd, ed) for tf in ["1d", "4h", "1h", "15m", "5m"]}
        o1h = d["1h"]["o"].values; h1h = d["1h"]["h"].values; l1h = d["1h"]["l"].values
        c1h = d["1h"]["c"].values; v1h = d["1h"]["v"].values; ts1h = d["1h"]["ts"].values; n1h = len(c1h)

        ema20_1h = pd.Series(c1h).ewm(span=20, adjust=False).mean().values
        ema50_1h = pd.Series(c1h).ewm(span=50, adjust=False).mean().values
        tr1h = np.array([max(h1h[i] - l1h[i], abs(h1h[i] - c1h[i - 1]), abs(l1h[i] - c1h[i - 1])) for i in range(1, n1h)])
        atr14_1h = np.full(n1h, np.nan)
        atr14_1h[14:] = pd.Series(tr1h).rolling(14).mean().values[13:]
        bb_sma = np.full(n1h, np.nan); bb_std = np.full(n1h, np.nan)
        for j in range(19, n1h):
            bb_sma[j] = c1h[j - 19:j + 1].mean()
            bb_std[j] = c1h[j - 19:j + 1].std()

        h5 = d["5m"]["h"].values; l5 = d["5m"]["l"].values
        c5 = d["5m"]["c"].values; v5 = d["5m"]["v"].values; ts5 = d["5m"]["ts"].values; n5 = len(c5)
        h15 = d["15m"]["h"].values; l15 = d["15m"]["l"].values
        c15 = d["15m"]["c"].values; v15 = d["15m"]["v"].values; ts15 = d["15m"]["ts"].values; n15 = len(c15)

        ema20_5 = pd.Series(c5).ewm(span=20, adjust=False).mean().values
        ema50_5 = pd.Series(c5).ewm(span=50, adjust=False).mean().values
        ema20_15 = pd.Series(c15).ewm(span=20, adjust=False).mean().values
        ema50_15 = pd.Series(c15).ewm(span=50, adjust=False).mean().values

        st5 = supertrend(h5, l5, c5, CFG[sym]["sm"], 10)
        st15 = supertrend(h15, l15, c15, CFG[sym]["sm"], 10)
        st1h = supertrend(h1h, l1h, c1h, CFG[sym]["sm"], 14) if n1h >= 16 else np.ones(n1h, dtype=int)

        vr5 = np.full(n5, 1.0); vr15 = np.full(n15, 1.0)
        for j in range(19, n5): vr5[j] = v5[j] / v5[j - 19:j + 1].mean()
        for j in range(19, n15): vr15[j] = v15[j] / v15[j - 19:j + 1].mean()

        d["_p"] = {
            "n1h": n1h, "ts1h": ts1h, "c1h": c1h, "h1h": h1h, "l1h": l1h, "o1h": o1h, "v1h": v1h,
            "ema20_1h": ema20_1h, "ema50_1h": ema50_1h, "atr14_1h": atr14_1h,
            "bb_sma": bb_sma, "bb_std": bb_std,
            "n5": n5, "ts5": ts5, "c5": c5, "h5": h5, "l5": l5, "v5": v5,
            "n15": n15, "ts15": ts15, "c15": c15, "h15": h15, "l15": l15, "v15": v15,
            "ema20_5": ema20_5, "ema50_5": ema50_5, "ema20_15": ema20_15, "ema50_15": ema50_15,
            "st5": st5, "st15": st15, "st1h": st1h, "vr5": vr5, "vr15": vr15,
        }
        d["_4h"] = {"h": d["4h"]["h"].values, "l": d["4h"]["l"].values,
                     "c": d["4h"]["c"].values, "o": d["4h"]["o"].values,
                     "v": d["4h"]["v"].values, "ts": d["4h"]["ts"].values}
        d["_1d"] = {"h": d["1d"]["h"].values, "l": d["1d"]["l"].values,
                     "c": d["1d"]["c"].values, "o": d["1d"]["o"].values,
                     "v": d["1d"]["v"].values, "ts": d["1d"]["ts"].values}
        all_data[sym] = d
        print(f"{n1h} 1h bars")

    # ─── GENERATE TRADES ───
    print(f"\n{'=' * 70}\n  Generando señales...\n{'=' * 70}")

    all_trades = []  # {symbol, ts, features dict, pnl, direction}
    for sym in SYM:
        d = all_data[sym]; p = d["_p"]; cfg = CFG[sym]
        n1h = p["n1h"]; ts1h = p["ts1h"]; c1h = p["c1h"]; h1h = p["h1h"]; l1h = p["l1h"]; o1h = p["o1h"]; v1h = p["v1h"]
        ema20_1h = p["ema20_1h"]; ema50_1h = p["ema50_1h"]; atr14_1h = p["atr14_1h"]
        bb_sma = p["bb_sma"]; bb_std = p["bb_std"]
        n5 = p["n5"]; ts5 = p["ts5"]; c5 = p["c5"]; h5 = p["h5"]; l5 = p["l5"]; v5 = p["v5"]
        n15 = p["n15"]; ts15 = p["ts15"]; c15 = p["c15"]; h15 = p["h15"]; l15 = p["l15"]; v15 = p["v15"]
        ema20_5 = p["ema20_5"]; ema50_5 = p["ema50_5"]; ema20_15 = p["ema20_15"]; ema50_15 = p["ema50_15"]
        st5 = p["st5"]; st15 = p["st15"]; st1h = p["st1h"]; vr5 = p["vr5"]; vr15 = p["vr15"]
        d4h = d["_4h"]; d1d = d["_1d"]

        sym_tr = 0
        for i in range(10, n1h):
            ts = ts1h[i]
            i5 = int(np.searchsorted(ts5, ts, side="right") - 1)
            i15 = int(np.searchsorted(ts15, ts, side="right") - 1)
            i4h = int(np.searchsorted(d4h["ts"], ts, side="right") - 1)
            i1d = int(np.searchsorted(d1d["ts"], ts, side="right") - 1)
            if i5 < 30 or i15 < 30 or i4h < 5 or i1d < 2: continue

            # Phase detection
            slope = linregress(np.arange(20), c1h[i - 19:i + 1])[0] if i >= 19 else 0
            bw = (bb_sma[i] + 2 * bb_std[i] - (bb_sma[i] - 2 * bb_std[i])) / c1h[i] if not np.isnan(bb_sma[i]) and bb_std[i] > 0 else 0.05
            atr_v = atr14_1h[i]
            vol_r = float(v1h[i] / v1h[i - 19:i + 1].mean()) if i >= 19 else 1.0
            atr_r = atr_v / c1h[i] if atr_v and atr_v == atr_v and c1h[i] > 0 else 0
            if bw < 0.03 and atr_r < 0.01 and vol_r < 0.8: ph = "compressing"
            elif bw > 0.06 and vol_r > 1.3 and abs(slope) > 0.005: ph = "expanding"
            elif atr_r > 0.015 and abs(slope) > 0.01: ph = "trending"
            elif atr_r < 0.008 and abs(slope) < 0.003: ph = "ranging"
            else: ph = "neutral"

            # Momentum multi-TF
            ms = {}
            for tf, wt in [("4h", 0.5), ("1h", 0.35), ("15m", 0.15)]:
                if tf == "4h":
                    ii = i4h; cc = d4h["c"][:ii + 1]; vv = d4h["v"][:ii + 1]
                    e20 = pd.Series(cc).ewm(span=20, adjust=False).mean().iloc[-1]
                    e50 = pd.Series(cc).ewm(span=50, adjust=False).mean().iloc[-1]
                elif tf == "1h":
                    ii = i; cc = c1h[:ii + 1]; vv = v1h[:ii + 1]
                    e20 = ema20_1h[ii]; e50 = ema50_1h[ii]
                else:
                    ii = i15; cc = c15[:ii + 1]; vv = v15[:ii + 1]
                    e20 = ema20_15[ii]; e50 = ema50_15[ii]
                if pd.isna(e20) or pd.isna(e50): continue
                d_s = 1 if e20 > e50 else -1
                vrr = vv[-1] / vv[-20:].mean() if len(vv) >= 20 else 1
                ms[tf] = (d_s * 0.7 + (vrr - 1) * 0.5 * 0.3) * wt
            ms_tot = sum(ms.values()) if ms else 0
            md = "bullish" if ms_tot > 0.15 else ("bearish" if ms_tot < -0.15 else "neutral")

            # Fibonacci
            fb = None
            if i1d >= 1:
                fd = cfg["fd"]; start = max(0, i1d - fd + 1)
                rd_h = d1d["h"][start:i1d + 1]; rd_l = d1d["l"][start:i1d + 1]
                fh = rd_h.max(); fl = rd_l.min(); rng = fh - fl
                if rng > 0:
                    lvls = {k: fl + rng * k for k in [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]}
                    sl = sorted(lvls.items(), key=lambda x: x[1])
                    pr = c1h[i]
                    for j in range(len(sl) - 1):
                        if sl[j][1] <= pr <= sl[j + 1][1]: fb = (sl[j][0], sl[j][1], sl[j + 1][0], sl[j + 1][1]); break
            if fb is None: continue

            # Engulfing detection (vectorized: check at current 4h candle)
            def be(ko, kc):
                if len(kc) < 2: return False
                return ko[-2] < kc[-2] and ko[-1] > kc[-1] and ko[-1] > kc[-2] and kc[-1] < ko[-2]
            def bu(ko, kc):
                if len(kc) < 2: return False
                return ko[-2] > kc[-2] and ko[-1] < kc[-1] and ko[-1] < kc[-2] and kc[-1] > ko[-2]

            sig = "WAIT"; dr = "neutral"
            if be(d4h["o"][:i4h + 1], d4h["c"][:i4h + 1]): sig = "SHORT"; dr = "bearish"
            elif bu(d4h["o"][:i4h + 1], d4h["c"][:i4h + 1]): sig = "LONG"; dr = "bullish"
            if ph in ("ranging", "neutral"): sig = "WAIT"

            # Filters (CI, WR, ST)
            cv = 50.0
            if i15 >= 14:
                hs = h15[i15 - 13:i15 + 1]; ls = l15[i15 - 13:i15 + 1]; cs = c15[i15 - 13:i15 + 1]
                tr = np.maximum.reduce([hs - ls, np.abs(hs - np.roll(cs, 1)), np.abs(ls - np.roll(cs, 1))]); tr[0] = hs[0] - ls[0]
                total = hs.max() - ls.min()
                if total > 0: cv = float(np.clip(100 * np.log10(tr.sum() / total) / np.log10(14), 0, 100))
            tradeable = cv < cfg["ct"]

            wr5 = -50; wr5p = -50
            if i5 >= 14:
                hh = h5[i5 - 13:i5 + 1].max(); ll = l5[i5 - 13:i5 + 1].min()
                wr5 = -100 * (hh - c5[i5]) / (hh - ll) if hh != ll else -50
                if i5 >= 15:
                    hhp = h5[i5 - 14:i5].max(); llp = l5[i5 - 14:i5].min()
                    wr5p = -100 * (hhp - c5[i5 - 1]) / (hhp - llp) if hhp != llp else -50
            wr15 = -50
            if i15 >= 14:
                hh = h15[i15 - 13:i15 + 1].max(); ll = l15[i15 - 13:i15 + 1].min()
                wr15 = -100 * (hh - c15[i15]) / (hh - ll) if hh != ll else -50
            lt = wr5p <= -80 and wr5 > -80
            st_wr = wr5p >= -20 and wr5 < -20

            st5_v = "bullish" if st5[i5] == 1 else "bearish"
            st15_v = "bullish" if st15[i15] == 1 else "bearish"
            st1h_v = "bullish" if st1h[i4h] == 1 else "bearish"
            dirs = [st5_v, st15_v, st1h_v]
            b = dirs.count("bullish"); bc = dirs.count("bearish")
            al = b == 3 or bc == 3
            st_bias = "bullish" if b > bc else ("bearish" if bc > b else "mixed")

            # Apply veto (existing filter logic)
            if sig in ("LONG", "SHORT"):
                veto = False
                if not tradeable: veto = True
                if not veto:
                    if (sig == "SHORT" and lt) or (sig == "LONG" and st_wr): veto = True
                if not veto:
                    if (sig == "SHORT" and st_bias == "bullish" and al) or (sig == "LONG" and st_bias == "bearish" and al): veto = True
                if veto: sig = "WAIT"

            # Execute trade
            if sig in ("LONG", "SHORT"):
                ep = float(c5[i5])
                if sig == "SHORT": sl = float(h15[i15 - 9:i15 + 1].max()) * 1.002
                else: sl = float(l15[i15 - 9:i15 + 1].min()) * 0.998
                sl_pct = abs(ep - sl) / ep
                if sl_pct > 0.018:
                    sl = ep * (1 - 0.018) if ep > sl else ep * (1 + 0.018); sl_pct = 0.018
                notional = (CAP * RISK) / sl_pct
                contracts = notional / ep
                tp_ratio = cfg["tp"]

                # Entry with slippage
                er = ep * (1 + SPREAD) if sig == "LONG" else ep * (1 - SPREAD)

                # ATR for slippage
                atr_5m = 0.0
                if i5 >= 14:
                    tr_arr = np.array([max(h5[k] - l5[k], abs(h5[k] - c5[k - 1]), abs(l5[k] - c5[k - 1])) for k in range(i5 - 13, i5 + 1)])
                    atr_5m = tr_arr.mean()

                pnl_neto, exit_type = simular_salida(
                    "bullish" if sig == "LONG" else "bearish",
                    er, sl, tp_ratio, contracts, h5, l5, c5, i5, atr_5m
                )

                # Build features
                f = {}
                f["fib_low_key"] = fb[0]; f["fib_high_key"] = fb[2]
                f["fib_width"] = (fb[3] - fb[1]) / fb[1] if fb[1] != 0 else 0
                f["ci_value"] = cv; f["wr_5m"] = wr5; f["wr_15m"] = wr15
                f["st_aligned"] = 1 if al else 0
                f["st_bias_bullish"] = 1 if st_bias == "bullish" else 0
                f["st_bias_bearish"] = 1 if st_bias == "bearish" else 0
                f["mom_score"] = ms_tot
                f["vol_ratio_5m"] = float(vr5[i5])
                f["body_ratio_4h"] = 0.0
                if i4h >= 0:
                    tr4 = d4h["h"][i4h] - d4h["l"][i4h]
                    b4 = abs(d4h["c"][i4h] - d4h["o"][i4h])
                    f["body_ratio_4h"] = b4 / tr4 if tr4 > 0 else 0
                f["hour_of_day"] = int(datetime.fromtimestamp(ts / 1000, tz=timezone.utc).hour)
                f["direction_long"] = 1 if dr == "LONG" else 0
                f["phase_compressing"] = 1 if ph == "compressing" else 0
                f["phase_expanding"] = 1 if ph == "expanding" else 0
                f["phase_trending"] = 1 if ph == "trending" else 0
                f["mom_bullish"] = 1 if md == "bullish" else 0
                f["mom_bearish"] = 1 if md == "bearish" else 0
                f["mom_neutral"] = 1 if md == "neutral" else 0

                # ─── NEW FEATURES (D) ───
                # Volatility
                f["atr_ratio_4h"] = float(atr_r)
                f["bb_width"] = float(bw)
                atr14_trend = 0
                if i >= 28 and atr14_1h[i] and atr14_1h[i - 14] and not np.isnan(atr14_1h[i]) and not np.isnan(atr14_1h[i - 14]):
                    atr14_trend = 1 if atr14_1h[i] > atr14_1h[i - 14] else -1
                f["atr_trend_14h"] = atr14_trend
                vol_trend = 0
                if i >= 5:
                    vol_trend = 1 if v1h[i] > v1h[i - 5] else -1
                f["vol_trend_5h"] = vol_trend

                # Short-term momentum
                mom_short = (c1h[i] - c1h[i - 3]) / c1h[i - 3] if i >= 3 else 0
                f["mom_short_3h"] = float(mom_short)

                # BB position (where is price within BB)
                bb_pos = 0.0
                if not np.isnan(bb_sma[i]) and not np.isnan(bb_std[i]) and bb_std[i] > 0:
                    bb_pos = (c1h[i] - bb_sma[i]) / (2 * bb_std[i])
                f["bb_position"] = float(np.clip(bb_pos, -1, 1))

                all_trades.append({"symbol": sym, "ts": ts, "features": f, "pnl": pnl_neto, "direction": dr, "exit_type": exit_type})
                sym_tr += 1

        print(f"  {sym}: {sym_tr} señales")

    # Debug: check a few trade timestamps vs split
    if all_trades:
        sample_ts = [t["ts"] for t in all_trades[:10]]
        print(f"  [debug] Sample timestamps: min={min(sample_ts)}, max={max(sample_ts)}, split={split_ts}")
        print(f"  [debug] Split date: {datetime.fromtimestamp(split_ts/1000, tz=timezone.utc).date()}")
        print(f"  [debug] Min trade ts: {min(t['ts'] for t in all_trades)} -> {datetime.fromtimestamp(min(t['ts'] for t in all_trades)/1000, tz=timezone.utc).date()}")
        print(f"  [debug] Max trade ts: {max(t['ts'] for t in all_trades)} -> {datetime.fromtimestamp(max(t['ts'] for t in all_trades)/1000, tz=timezone.utc).date()}")

    # ─── SPLIT TRAIN/TEST ───
    train = [t for t in all_trades if t["ts"] < split_ts]
    test = [t for t in all_trades if t["ts"] >= split_ts]
    print(f"\n{'=' * 70}")
    print(f"  Total trades: {len(all_trades)}  |  Train Q1: {len(train)}  |  Test Q2: {len(test)}")
    print(f"  Train winners: {sum(1 for t in train if t['pnl'] > 0)}/{len(train)} ({sum(1 for t in train if t['pnl'] > 0) / len(train) * 100:.1f}%)" if train else "  No train data")
    print(f"  Test winners: {sum(1 for t in test if t['pnl'] > 0)}/{len(test)} ({sum(1 for t in test if t['pnl'] > 0) / len(test) * 100:.1f}%)" if test else "  No test data")

    # ─── TRAIN MODELS ───
    results = {"period": f"{sd.date()} -> {ed.date()}", "symbols": SYM}
    all_feature_keys = list(train[0]["features"].keys()) if train else list(test[0]["features"].keys())

    def pnl_stats(trades, name=""):
        if not trades: return {"trades": 0, "wr": 0, "pnl": 0, "ret": 0}
        n = len(trades); w = sum(1 for t in trades if t["pnl"] > 0)
        tp = sum(t["pnl"] for t in trades)
        ret = (tp + CAP * len(SYM)) / (CAP * len(SYM)) - 1
        return {"trades": n, "wr": round(w / n * 100, 1), "pnl": round(tp, 2), "ret": round(ret * 100, 2)}

    def print_stats(label, tr):
        s = pnl_stats(tr)
        print(f"  {label:30s}: {s['trades']:4d}t  WR {s['wr']:5.1f}%  PnL ${s['pnl']:+8.2f}  Ret {s['ret']:+6.2f}%")
        return s

    # Baseline (no ML)
    print(f"\n{'─' * 70}\n  --- SIN ML (baseline) ---\n{'─' * 70}")
    print_stats("Train Q1", train)
    test_no_ml = print_stats("Test Q2", test)
    results["no_ml"] = {"train": pnl_stats(train), "test": pnl_stats(test)}

    if len(train) < 20:
        print(f"\n  [!] Muy pocos trades de entrenamiento ({len(train)}). No se puede entrenar.")
        Path(RESULTS_PATH).write_text(json.dumps(results, indent=2))
        return

    # Prepare features
    train_X = pd.DataFrame([t["features"] for t in train])
    train_y = np.array([1 if t["pnl"] > 0 else 0 for t in train])
    test_X = pd.DataFrame([t["features"] for t in test])
    test_y = np.array([1 if t["pnl"] > 0 else 0 for t in test])

    # Fill missing cols
    for c in all_feature_keys:
        if c not in train_X.columns: train_X[c] = 0
        if c not in test_X.columns: test_X[c] = 0
    train_X = train_X[all_feature_keys].fillna(0)
    test_X = test_X[all_feature_keys].fillna(0)

    # ─── RF ───
    print(f"\n{'=' * 70}\n  --- RANDOM FOREST ---\n{'=' * 70}")
    rf = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight="balanced", random_state=42)
    rf.fit(train_X, train_y)
    train_acc = accuracy_score(train_y, rf.predict(train_X))
    test_acc = accuracy_score(test_y, rf.predict(test_X))
    print(f"  Train acc: {train_acc * 100:.1f}%  |  Test acc: {test_acc * 100:.1f}%")

    rf_train_probs = rf.predict_proba(train_X)[:, 1]
    rf_test_probs = rf.predict_proba(test_X)[:, 1]

    # Feature importance
    fi = rf.feature_importances_
    top5 = np.argsort(fi)[-5:][::-1]
    print(f"  Top features:")
    for j in top5: print(f"    {all_feature_keys[j]}: {fi[j] * 100:.1f}%")

    # RF as GATE (existing approach)
    print(f"\n  --- RF como GATE (test Q2) ---")
    rf_gate_results = {}
    for th in [0.3, 0.4, 0.5, 0.55, 0.6, 0.7]:
        sel = [test[i] for i in range(len(test)) if rf_test_probs[i] >= th]
        label = f"RF gate th={th}"
        s = print_stats(label, sel)
        rf_gate_results[f"th_{th}"] = s
    results["rf_gate"] = rf_gate_results

    # RF as SIZING (new approach A)
    print(f"\n  --- RF como SIZING (test Q2) ---")
    # Sizing: position = base_risk * prob (no trades rejected)
    # Simulate portfolio
    sizing_pnl = 0.0
    sizing_trades = []
    for i, t in enumerate(test):
        prob = rf_test_probs[i]
        # Scale position: risk_pct * prob (min 0.1 to avoid zero)
        adjusted_risk_prob = max(RISK * prob, RISK * 0.05)
        # Recalculate position with adjusted risk
        ep = t["features"].get("entry_price", 0)  # We don't store entry, approximate
        # Instead, scale the existing PnL by the ratio of adjusted_risk / base_risk
        scaling = adjusted_risk_prob / RISK
        adj_pnl = t["pnl"] * scaling
        sizing_pnl += adj_pnl
        sizing_trades.append({**t, "pnl": adj_pnl, "prob": float(prob), "scaling": float(scaling)})

    n = len(sizing_trades)
    w = sum(1 for t in sizing_trades if t["pnl"] > 0)
    sizing_wr = w / n * 100 if n else 0
    sizing_ret = (sizing_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
    print(f"  RF sizing (proba)           : {n:4d}t  WR {sizing_wr:5.1f}%  PnL ${sizing_pnl:+8.2f}  Ret {sizing_ret * 100:+6.2f}%")
    results["rf_sizing"] = {"trades": n, "wr": round(sizing_wr, 1), "pnl": round(sizing_pnl, 2), "ret": round(sizing_ret * 100, 2)}

    # ─── XGBoost ───
    xgb_gate_results = {}
    xgb_sizing_results = {}
    xgb_test_probs = None
    if HAS_XGB and len(train) >= 20:
        print(f"\n{'=' * 70}\n  --- XGBOOST ---\n{'=' * 70}")
        xgb_model = xgb.XGBClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.1,
            scale_pos_weight=(len(train_y) - train_y.sum()) / train_y.sum() if train_y.sum() > 0 else 1,
            random_state=42, eval_metric="logloss"
        )
        xgb_model.fit(train_X, train_y)
        xgb_train_acc = accuracy_score(train_y, xgb_model.predict(train_X))
        xgb_test_acc = accuracy_score(test_y, xgb_model.predict(test_X))
        print(f"  Train acc: {xgb_train_acc * 100:.1f}%  |  Test acc: {xgb_test_acc * 100:.1f}%")

        xgb_train_probs = xgb_model.predict_proba(train_X)[:, 1]
        xgb_test_probs = xgb_model.predict_proba(test_X)[:, 1]

        # XGBoost feature importance
        xgb_fi = xgb_model.feature_importances_
        xgb_top5 = np.argsort(xgb_fi)[-5:][::-1]
        print(f"  Top features:")
        for j in xgb_top5: print(f"    {all_feature_keys[j]}: {xgb_fi[j] * 100:.1f}%")

        # XGBoost as GATE
        print(f"\n  --- XGBoost como GATE (test Q2) ---")
        for th in [0.3, 0.4, 0.5, 0.55, 0.6, 0.7]:
            sel = [test[i] for i in range(len(test)) if xgb_test_probs[i] >= th]
            label = f"XGB gate th={th}"
            s = print_stats(label, sel)
            xgb_gate_results[f"th_{th}"] = s
        results["xgb_gate"] = xgb_gate_results

        # XGBoost as SIZING
        print(f"\n  --- XGBoost como SIZING (test Q2) ---")
        xgb_sizing_pnl = 0.0
        xgb_sizing_trades = []
        for i, t in enumerate(test):
            prob = xgb_test_probs[i]
            adjusted_risk_prob = max(RISK * prob, RISK * 0.05)
            scaling = adjusted_risk_prob / RISK
            adj_pnl = t["pnl"] * scaling
            xgb_sizing_pnl += adj_pnl
            xgb_sizing_trades.append({**t, "pnl": adj_pnl, "prob": float(prob), "scaling": float(scaling)})
        n_xgb = len(xgb_sizing_trades)
        w_xgb = sum(1 for t in xgb_sizing_trades if t["pnl"] > 0)
        xgb_sizing_wr = w_xgb / n_xgb * 100 if n_xgb else 0
        xgb_sizing_ret = (xgb_sizing_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
        print(f"  XGB sizing (proba)          : {n_xgb:4d}t  WR {xgb_sizing_wr:5.1f}%  PnL ${xgb_sizing_pnl:+8.2f}  Ret {xgb_sizing_ret * 100:+6.2f}%")
        results["xgb_sizing"] = {"trades": n_xgb, "wr": round(xgb_sizing_wr, 1), "pnl": round(xgb_sizing_pnl, 2), "ret": round(xgb_sizing_ret * 100, 2)}

    # ─── WALK-FORWARD: retrain RF on Q1, test Q2 ───
    print(f"\n{'=' * 70}\n  --- WALK-FORWARD: RF RETRAINED Q1 -> Q2 ---\n{'=' * 70}")
    rf_wf = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight="balanced", random_state=42)
    rf_wf.fit(train_X, train_y)
    wf_train_acc = accuracy_score(train_y, rf_wf.predict(train_X))

    # Test RF as gate on Q2
    wf_test_probs = rf_wf.predict_proba(test_X)[:, 1]
    wf_gate_results = {}
    for th in [0.3, 0.4, 0.5, 0.55, 0.6, 0.7]:
        sel = [test[i] for i in range(len(test)) if wf_test_probs[i] >= th]
        label = f"WF RF gate th={th}"
        s = print_stats(label, sel)
        wf_gate_results[f"th_{th}"] = s
    results["walkforward_rf_gate"] = wf_gate_results

    # Test RF as sizing on Q2
    wf_sizing_pnl = 0.0
    for i, t in enumerate(test):
        prob = wf_test_probs[i]
        adj_risk = max(RISK * prob, RISK * 0.05)
        scaling = adj_risk / RISK
        wf_sizing_pnl += t["pnl"] * scaling
    n_wf = len(test)
    wf_sizing_ret = (wf_sizing_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
    print(f"  WF RF sizing (proba)         : {n_wf:4d}t  PnL ${wf_sizing_pnl:+8.2f}  Ret {wf_sizing_ret * 100:+6.2f}%")
    results["walkforward_rf_sizing"] = {"trades": n_wf, "pnl": round(wf_sizing_pnl, 2), "ret": round(wf_sizing_ret * 100, 2)}

    # ─── COMPARISON TABLE ───
    print(f"\n{'=' * 70}")
    print(f"  {'=' * 66}")
    print(f"  {'':30s} {'Trades':>6s} {'WR':>6s} {'PnL':>10s} {'Ret':>8s}")
    print(f"  {'─' * 66}")
    print(f"  {'SIN ML (baseline)':30s} {test_no_ml['trades']:6d} {test_no_ml['wr']:6.1f}% ${test_no_ml['pnl']:+8.2f} {test_no_ml['ret']:+7.2f}%")
    results["comparison"] = {"no_ml": test_no_ml}

    # Best RF gate
    best_rf_gate = max(rf_gate_results.values(), key=lambda x: x["ret"])
    print(f"  {'RF gate (best)':30s} {best_rf_gate['trades']:6d} {best_rf_gate['wr']:6.1f}% ${best_rf_gate['pnl']:+8.2f} {best_rf_gate['ret']:+7.2f}%")
    results["comparison"]["rf_gate_best"] = best_rf_gate

    rf_siz = results["rf_sizing"]
    print(f"  {'RF sizing (proba)':30s} {rf_siz['trades']:6d} {rf_siz['wr']:6.1f}% ${rf_siz['pnl']:+8.2f} {rf_siz['ret']:+7.2f}%")
    results["comparison"]["rf_sizing"] = rf_siz

    if HAS_XGB and xgb_test_probs is not None:
        best_xgb_gate = max(xgb_gate_results.values(), key=lambda x: x["ret"])
        print(f"  {'XGB gate (best)':30s} {best_xgb_gate['trades']:6d} {best_xgb_gate['wr']:6.1f}% ${best_xgb_gate['pnl']:+8.2f} {best_xgb_gate['ret']:+7.2f}%")
        results["comparison"]["xgb_gate_best"] = best_xgb_gate

        xgb_siz = results["xgb_sizing"]
        print(f"  {'XGB sizing (proba)':30s} {xgb_siz['trades']:6d} {xgb_siz['wr']:6.1f}% ${xgb_siz['pnl']:+8.2f} {xgb_siz['ret']:+7.2f}%")
        results["comparison"]["xgb_sizing"] = xgb_siz

    wf_rf_siz = results["walkforward_rf_sizing"]
    print(f"  {'WF RF sizing (proba)':30s} {wf_rf_siz['trades']:6d} {'':>6s} ${wf_rf_siz['pnl']:+8.2f} {wf_rf_siz['ret']:+7.2f}%")
    results["comparison"]["walkforward_rf_sizing"] = wf_rf_siz
    print(f"  {'─' * 66}")

    # Save best XGBoost model for live trading
    if HAS_XGB and xgb_test_probs is not None:
        xgb_model.save_model("xgb_model.json")
        print(f"\n  ✅ XGBoost model saved: xgb_model.json")
        # Also save feature names
        with open("xgb_features.json", "w") as f:
            json.dump(all_feature_keys, f)

    Path(RESULTS_PATH).write_text(json.dumps(results, indent=2))
    print(f"\n  ✅ Resultados guardados en {RESULTS_PATH}")
    print(f"  ⏱  {time.time() - t0:.0f}s")

if __name__ == "__main__":
    t0 = time.time()
    run()
