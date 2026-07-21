#!/usr/bin/env python3
"""Validar salidas VWAP Breakout: ORIGINAL (todo/nada) vs NUEVO (TP1 60% + trailing 2ATR)"""

import numpy as np
import pandas as pd
import json

# ── config ──────────────────────────────────────────────────────────
CSV_15M = "/Users/franciscootamendi/ai-agents-v3/backtest_cache/BTCUSDT_15m.csv"
OUTPUT  = "/Users/franciscootamendi/ai-agents-v3/validacion_exits.json"

VWAP_N   = 12
DEV_THR  = 1.0
SL_MULT  = 2.0
TP_MULT  = 2.0
ATR_N    = 14


# ── helpers ─────────────────────────────────────────────────────────
def rolling_vwap_pd(close, high, low, volume, n):
    tp = (high + low + close) / 3.0
    vp = tp * volume
    return (vp.rolling(n).sum() / volume.rolling(n).sum()).values


def atr_wilder(high, low, close, n):
    pc = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum.reduce([high - low, np.abs(high - pc), np.abs(low - pc)])
    out = np.full(len(tr), np.nan)
    out[n - 1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


# ── load data ───────────────────────────────────────────────────────
print("=" * 60)
print("  VALIDACIÓN EXITS — VWAP BREAKOUT")
print("=" * 60)
print(f"\nCargando datos 15m desde {CSV_15M} ...")
df = pd.read_csv(CSV_15M)
df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
print(f"  Filas: {len(df):,}")
print(f"  Rango: {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")

# ── indicators ──────────────────────────────────────────────────────
print("\nCalculando VWAP, desviación, ATR ...")
close = df["close"].values.astype(np.float64)
high  = df["high"].values.astype(np.float64)
low   = df["low"].values.astype(np.float64)
vol   = df["volume"].values.astype(np.float64)

vwap = rolling_vwap_pd(
    pd.Series(close), pd.Series(high), pd.Series(low), pd.Series(vol), VWAP_N
)
dev = (close - vwap) / vwap * 100.0
dev_prev = np.roll(dev, 1)
dev_prev[0] = np.nan

atr_arr = atr_wilder(high, low, close, ATR_N)

# ── signals ─────────────────────────────────────────────────────────
print("\nDetectando señales VWAP breakout ...")
thr = DEV_THR
long_sig  = (dev_prev >= -thr) & (dev < -thr)
short_sig = (dev_prev <=  thr) & (dev >  thr)
all_sigs  = long_sig | short_sig
sig_idx_arr = np.where(all_sigs)[0]
print(f"  Señales totales: {len(sig_idx_arr)}  (long: {long_sig.sum()}, short: {short_sig.sum()})")

n = len(df)


# ═══════════════════════════════════════════════════════════════════
#  SIMULACIÓN ORIGINAL (todo o nada)
# ═══════════════════════════════════════════════════════════════════
def simulate_original(ix, direction):
    entry = close[ix]
    a = atr_arr[ix]
    if direction == 1:
        sl = entry - SL_MULT * a
        tp = entry + TP_MULT * a
        for i in range(ix + 1, n):
            if low[i] <= sl:
                return sl, i, "SL"
            if high[i] >= tp:
                return tp, i, "TP"
    else:
        sl = entry + SL_MULT * a
        tp = entry - TP_MULT * a
        for i in range(ix + 1, n):
            if high[i] >= sl:
                return sl, i, "SL"
            if low[i] <= tp:
                return tp, i, "TP"
    return close[-1], n - 1, "EXP"


print("\nSimulando ORIGINAL (todo/nada) ...")
trades_orig = []
for ix in sig_idx_arr:
    direction = 1 if long_sig[ix] else -1
    entry = close[ix]
    exit_price, exit_ix, reason = simulate_original(ix, direction)
    bars = exit_ix - ix
    pnl_pct = (exit_price / entry - 1) * direction
    trades_orig.append({
        "entry_idx": int(ix),
        "dir": int(direction),
        "entry": float(entry),
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "bars_held": int(bars),
        "pnl_pct": float(pnl_pct),
    })
print(f"  Ejecutados: {len(trades_orig)} trades")


# ═══════════════════════════════════════════════════════════════════
#  SIMULACIÓN NUEVA (TP1 60% + trailing 2×ATR)
# ═══════════════════════════════════════════════════════════════════
def simulate_new(ix, direction):
    """Return (blended_pnl_pct, hit_tp1, final_reason)."""
    entry = close[ix]
    a = atr_arr[ix]
    if direction == 1:
        sl = entry - SL_MULT * a
        tp = entry + TP_MULT * a
        tp1 = entry + 0.6 * (tp - entry)

        hit_tp1 = False
        highest = entry
        trail = None
        remaining_pnl = 0.0
        tp1_pnl = 0.0

        for i in range(ix + 1, n):
            h_i, l_i = high[i], low[i]

            # update running max
            if h_i > highest:
                highest = h_i

            if not hit_tp1:
                if l_i <= sl:
                    return (sl / entry - 1), False, "SL"
                if h_i >= tp:
                    return (tp / entry - 1), False, "TP_FULL"
                if h_i >= tp1:
                    hit_tp1 = True
                    tp1_pnl = 0.6 * (tp1 / entry - 1)
                    sl_be = entry
                    trail = highest - 2 * a
                    continue
            else:
                if trail is not None and h_i > entry:
                    trail = max(trail, highest - 2 * a)
                if trail is not None and l_i <= trail:
                    remaining_pnl = 0.4 * (trail / entry - 1)
                    return (tp1_pnl + remaining_pnl), True, "TRAIL"
                if l_i <= entry:
                    remaining_pnl = 0.4 * (entry / entry - 1)
                    return (tp1_pnl + remaining_pnl), True, "BE"
                if h_i >= tp:
                    remaining_pnl = 0.4 * (tp / entry - 1)
                    return (tp1_pnl + remaining_pnl), True, "TP_FULL"
        # expired
        if hit_tp1:
            remaining_pnl = 0.4 * (close[-1] / entry - 1)
            return (tp1_pnl + remaining_pnl), True, "EXP"
        return (close[-1] / entry - 1), False, "EXP"

    else:  # short
        sl = entry + SL_MULT * a
        tp = entry - TP_MULT * a
        tp1 = entry - 0.6 * (entry - tp)

        hit_tp1 = False
        lowest = entry
        trail = None
        remaining_pnl = 0.0
        tp1_pnl = 0.0

        for i in range(ix + 1, n):
            h_i, l_i = high[i], low[i]

            if l_i < lowest:
                lowest = l_i

            if not hit_tp1:
                if h_i >= sl:
                    return (1 - sl / entry), False, "SL"
                if l_i <= tp:
                    return (1 - tp / entry), False, "TP_FULL"
                if l_i <= tp1:
                    hit_tp1 = True
                    tp1_pnl = 0.6 * (1 - tp1 / entry)
                    sl_be = entry
                    trail = lowest + 2 * a
                    continue
            else:
                if trail is not None and l_i < entry:
                    trail = min(trail, lowest + 2 * a)
                if trail is not None and h_i >= trail:
                    remaining_pnl = 0.4 * (1 - trail / entry)
                    return (tp1_pnl + remaining_pnl), True, "TRAIL"
                if h_i >= entry:
                    remaining_pnl = 0.4 * (1 - entry / entry)
                    return (tp1_pnl + remaining_pnl), True, "BE"
                if l_i <= tp:
                    remaining_pnl = 0.4 * (1 - tp / entry)
                    return (tp1_pnl + remaining_pnl), True, "TP_FULL"

        if hit_tp1:
            remaining_pnl = 0.4 * (1 - close[-1] / entry)
            return (tp1_pnl + remaining_pnl), True, "EXP"
        return (1 - close[-1] / entry), False, "EXP"


print("Simulando NUEVO (TP1 60% + trailing 2ATR) ...")
trades_new = []
for ix in sig_idx_arr:
    direction = 1 if long_sig[ix] else -1
    entry = close[ix]
    pnl_pct, hit_tp1, reason = simulate_new(ix, direction)
    trades_new.append({
        "entry_idx": int(ix),
        "dir": int(direction),
        "entry": float(entry),
        "pnl_pct": float(pnl_pct),
        "exit_reason": reason,
        "hit_tp1": bool(hit_tp1),
    })
print(f"  Ejecutados: {len(trades_new)} trades")


# ═══════════════════════════════════════════════════════════════════
#  MÉTRICAS
# ═══════════════════════════════════════════════════════════════════
def compute_metrics(trades):
    if not trades:
        return {}
    pnls = np.array([t["pnl_pct"] for t in trades])
    wins = pnls > 0
    losses = pnls <= 0
    n_t = len(trades)
    wr = wins.mean()
    ret = pnls.sum()
    gp = pnls[wins].sum() if wins.any() else 0.0
    gl = abs(pnls[losses].sum()) if losses.any() else 0.0
    pf = gp / gl if gl > 1e-12 else (np.inf if gp > 1e-12 else 0.0)
    avg_w = pnls[wins].mean() if wins.any() else 0.0
    avg_l = pnls[losses].mean() if losses.any() else 0.0

    # Sharpe anualizado (96 * 365 períodos 15m)
    if n_t > 1 and pnls.std() > 1e-12:
        sharpe = np.sqrt(96.0 * 365.0) * pnls.mean() / pnls.std()
    else:
        sharpe = 0.0

    # Max drawdown sobre equity simulada
    cum = np.cumprod(1 + pnls)
    running_max = np.maximum.accumulate(cum)
    dd = (cum - running_max) / running_max
    max_dd = dd.min()

    return {
        "total_trades": n_t,
        "win_rate_pct": round(wr * 100, 2),
        "total_return_pct": round(ret * 100, 2),
        "profit_factor": round(pf, 3),
        "sharpe_ratio": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "avg_win_pct": round(avg_w * 100, 2),
        "avg_loss_pct": round(avg_l * 100, 2),
    }


m_orig = compute_metrics(trades_orig)
m_new  = compute_metrics(trades_new)
m_new["tp1_hits"] = sum(1 for t in trades_new if t["hit_tp1"])
m_new["tp1_hit_rate_pct"] = round(m_new["tp1_hits"] / len(trades_new) * 100, 2) if trades_new else 0.0

# breakdown de razones de salida (nuevo)
reasons_new = {}
for t in trades_new:
    r = t["exit_reason"]
    reasons_new[r] = reasons_new.get(r, 0) + 1
m_new["exit_reason_breakdown"] = reasons_new

reasons_orig = {}
for t in trades_orig:
    r = t["exit_reason"]
    reasons_orig[r] = reasons_orig.get(r, 0) + 1
m_orig["exit_reason_breakdown"] = reasons_orig


# ═══════════════════════════════════════════════════════════════════
#  GUARDAR
# ═══════════════════════════════════════════════════════════════════
output = {
    "config": {
        "vwap_n": VWAP_N,
        "dev_thr": DEV_THR,
        "sl_mult": SL_MULT,
        "tp_mult": TP_MULT,
        "atr_n": ATR_N,
        "data_range": f"{df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}",
        "n_candles_15m": len(df),
    },
    "original": m_orig,
    "nuevo": m_new,
}

with open(OUTPUT, "w") as f:
    json.dump(output, f, indent=2)
print(f"\nResultados guardados en {OUTPUT}")


# ═══════════════════════════════════════════════════════════════════
#  IMPRIMIR RESUMEN
# ═══════════════════════════════════════════════════════════════════
print("\n" + "=" * 67)
print(f"  COMPARACIÓN DE ESTRATEGIAS DE SALIDA")
print("=" * 67)
print(f"  Período        : {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}")
print(f"  Velas 15m      : {len(df):,}")
print(f"  Señales totales: {len(sig_idx_arr)}")
print("=" * 67)
print(f"  {'Métrica':<25} {'ORIGINAL':>15} {'NUEVO':>15}")
print("-" * 67)
print(f"  {'Trades':<25} {m_orig['total_trades']:>15} {m_new['total_trades']:>15}")
print(f"  {'Win Rate (%)':<25} {m_orig['win_rate_pct']:>14.2f}% {m_new['win_rate_pct']:>14.2f}%")
print(f"  {'Retorno Total (%)':<25} {m_orig['total_return_pct']:>14.2f}% {m_new['total_return_pct']:>14.2f}%")
print(f"  {'Profit Factor':<25} {m_orig['profit_factor']:>15.3f} {m_new['profit_factor']:>15.3f}")
print(f"  {'Sharpe Ratio':<25} {m_orig['sharpe_ratio']:>15.3f} {m_new['sharpe_ratio']:>15.3f}")
print(f"  {'Max Drawdown (%)':<25} {m_orig['max_drawdown_pct']:>14.2f}% {m_new['max_drawdown_pct']:>14.2f}%")
print(f"  {'Avg Win (%)':<25} {m_orig['avg_win_pct']:>14.2f}% {m_new['avg_win_pct']:>14.2f}%")
print(f"  {'Avg Loss (%)':<25} {m_orig['avg_loss_pct']:>14.2f}% {m_new['avg_loss_pct']:>14.2f}%")
print("-" * 67)
print(f"  {'TP1 hits':<25} {'—':>15} {m_new['tp1_hits']:>14} ({m_new['tp1_hit_rate_pct']:.1f}%)")
print("=" * 67)

# exit reason breakdowns
print(f"\n  Desglose salidas ORIGINAL:")
for r, c in sorted(reasons_orig.items()):
    print(f"    {r:<10}: {c:>5} ({c/len(trades_orig)*100:5.1f}%)")
print(f"\n  Desglose salidas NUEVO:")
for r, c in sorted(reasons_new.items()):
    print(f"    {r:<10}: {c:>5} ({c/len(trades_new)*100:5.1f}%)")
print()
