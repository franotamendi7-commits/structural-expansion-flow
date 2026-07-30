"""
Portfolio Backtest — 5 Pairs CONSISTENT 7-Month Period
======================================================

CRITICAL FIX: All pairs use the SAME date range (Jan 30 – Jul 29, 2026)
to ensure fair comparison. Previous backtest gave BTC 3.5 years vs 7 months
for other pairs, inflating BTC's contribution.

Uses Institutional Engine V2 on all 5 pairs.
$4,450 total capital, $890 per pair.
"""

import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import (
    choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, detect_market_phase
)

# ============================================================
# CONFIG
# ============================================================

TOTAL_CAPITAL = 4450.0
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
CAPITAL_PER_PAIR = TOTAL_CAPITAL / len(PAIRS)
RISK_PCT = 0.01
CACHE_DIR = Path(__file__).parent / "backtest_results" / "cache"
OUTPUT_DIR = Path(__file__).parent / "backtest_results"

COMMISSION_MAKER = 0.00018
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1

# COMMON PERIOD: All pairs must have data in this range
# XRP/BNB start Jan 30, BTC/ETH/SOL start Jan 1 → common = Jan 30 – Jul 29
START_DATE = pd.Timestamp("2026-01-30", tz="UTC")
END_DATE = pd.Timestamp("2026-07-29 23:59:59", tz="UTC")

PAIR_CONFIGS = {
    "BTCUSDT": {"supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 74.0, "fibonacci_days": 30, "tp_ratio": 1.5, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "ETHUSDT": {"supertrend_multiplier": 2.6, "choppiness_neutral_threshold": 72.0, "fibonacci_days": 30, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "SOLUSDT": {"supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 70.0, "fibonacci_days": 90, "tp_ratio": 2.0, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "XRPUSDT": {"supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 60, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "BNBUSDT": {"supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 30, "tp_ratio": 1.6, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
}


def load_and_trim_data(symbol: str) -> Dict[str, pd.DataFrame]:
    """Load cached Parquet data and trim to common period (Jan 30 – Jul 29, 2026)."""
    files = sorted(CACHE_DIR.glob(f"{symbol}_*.parquet"))
    data = {}
    for f in files:
        parts = f.stem.split("_")
        interval = parts[1]
        date_range = "_".join(parts[2:])
        df = pd.read_parquet(f)
        # Keep the widest date range per interval
        if interval not in data or len(df) > len(data[interval]):
            data[interval] = df

    # CRITICAL: Trim ALL intervals to the common period
    trimmed = {}
    for interval, df in data.items():
        before = len(df)
        mask = (df.index >= START_DATE) & (df.index <= END_DATE)
        df_trimmed = df.loc[mask].copy()
        trimmed[interval] = df_trimmed
        print(f"  {interval}: {before} → {len(df_trimmed)} bars ({df_trimmed.index[0]} → {df_trimmed.index[-1]})")

    return trimmed


@dataclass
class Trade:
    entry_time: datetime
    exit_time: datetime
    direction: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    exit_reason: str
    bars_held: int
    score: float
    tp1_hit: bool = False
    partial_pnl: float = 0.0


def run_single_pair(symbol: str, capital: float, config: Dict,
                    df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                    df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> Dict:
    """Run V2 backtest on a single pair."""
    equity = capital
    peak_equity = equity
    trades = []
    equity_curve = []

    open_pos = None
    risk_pct = RISK_PCT
    ci_thresh = config["choppiness_neutral_threshold"]
    max_sl = config["max_sl_pct"]

    closes_4h = df_4h["close"].values
    highs_4h = df_4h["high"].values
    lows_4h = df_4h["low"].values

    for i in range(50, len(df_15m)):
        bar_time = df_15m.index[i]
        bar_close = float(df_15m["close"].iloc[i])
        bar_high = float(df_15m["high"].iloc[i])
        bar_low = float(df_15m["low"].iloc[i])

        # Manage open position
        if open_pos is not None:
            result = _manage_pos(open_pos, bar_high, bar_low, bar_close, df_15m, config)
            if result is not None:
                equity += result["pnl"]
                peak_equity = max(peak_equity, equity)
                trades.append(Trade(
                    entry_time=open_pos["entry_time"], exit_time=bar_time,
                    direction=open_pos["direction"], entry_price=open_pos["entry_fill"],
                    exit_price=result["exit_price"], size=open_pos["size"],
                    pnl=result["pnl"], exit_reason=result["reason"],
                    bars_held=i - open_pos["entry_idx"], score=open_pos["score"],
                    tp1_hit=open_pos.get("tp1_hit", False),
                    partial_pnl=result.get("partial_pnl", 0.0),
                ))
                open_pos = None
            equity_curve.append(equity)
            continue

        # Check for new signal — 4H engulfing
        mask_4h = df_4h.index <= bar_time
        if mask_4h.sum() < 3:
            equity_curve.append(equity)
            continue

        klines_4h = []
        for j in range(max(0, mask_4h.sum() - 3), mask_4h.sum()):
            idx = df_4h.index[j]
            klines_4h.append({
                "open": float(df_4h.loc[idx, "open"]),
                "high": float(df_4h.loc[idx, "high"]),
                "low": float(df_4h.loc[idx, "low"]),
                "close": float(df_4h.loc[idx, "close"]),
            })

        signal = None
        if is_bearish_engulfing(klines_4h):
            signal = "SHORT"
        elif is_bullish_engulfing(klines_4h):
            signal = "LONG"

        if signal is None:
            equity_curve.append(equity)
            continue

        # 15m data for filters
        mask_15 = df_15m.index <= bar_time
        if mask_15.sum() < 30:
            equity_curve.append(equity)
            continue

        data_15 = df_15m[mask_15]
        c_15 = data_15["close"].values[-30:]
        h_15 = data_15["high"].values[-30:]
        l_15 = data_15["low"].values[-30:]

        ci = choppiness_index(h_15, l_15, c_15)
        if ci is not None and ci > ci_thresh:
            equity_curve.append(equity)
            continue

        # 1h data for phase + ATR
        mask_1h = df_1h.index <= bar_time
        if mask_1h.sum() < 30:
            equity_curve.append(equity)
            continue
        data_1h = df_1h[mask_1h]
        c_1h = data_1h["close"].values[-30:]
        h_1h = data_1h["high"].values[-30:]
        l_1h = data_1h["low"].values[-30:]
        v_1h = data_1h["volume"].values[-30:]
        atr_1h_val = atr(h_1h, l_1h, c_1h)
        _, _, bw_1h = bollinger_bands(c_1h)
        vol_ratio_1h = float(v_1h[-1] / np.mean(v_1h[-20:])) if len(v_1h) >= 20 else 1.0
        ind_1h = {"bb_width": bw_1h, "atr14": atr_1h_val, "vol_ratio": vol_ratio_1h}

        phase = detect_market_phase(
            [{"close": c, "high": h, "low": l, "volume": v, "open": o}
             for c, h, l, v, o in zip(data_1h["close"].values, data_1h["high"].values,
                                       data_1h["low"].values, data_1h["volume"].values,
                                       data_1h["open"].values)],
            ind_1h
        )
        if phase in ("ranging", "neutral", "unknown"):
            equity_curve.append(equity)
            continue

        # Score
        wr_5m = williams_r(
            np.array([float(k["high"]) for k in klines_4h]),
            np.array([float(k["low"]) for k in klines_4h]),
            np.array([float(k["close"]) for k in klines_4h])
        )
        body_ratio_4h = abs(float(klines_4h[-1]["close"]) - float(klines_4h[-1]["open"])) / \
                       max(float(klines_4h[-1]["high"]) - float(klines_4h[-1]["low"]), 0.001)

        score = 55
        if body_ratio_4h > 0.4: score += 12
        if ci is not None and ci < 60: score += 8
        if ci is not None and ci < 50: score += 5
        if wr_5m is not None and -80 < wr_5m < -20: score += 5
        if signal == "LONG" and c_15[-1] > np.mean(c_15[-20:]): score += 3
        elif signal == "SHORT" and c_15[-1] < np.mean(c_15[-20:]): score += 3

        if score < config["min_score"]:
            equity_curve.append(equity)
            continue

        # Entry/SL
        entry = bar_close
        if signal == "SHORT":
            sl = float(data_15["high"].values[-10:].max()) * 1.002
        else:
            sl = float(data_15["low"].values[-10:].min()) * 0.998

        sl_pct = abs(entry - sl) / entry
        if sl_pct > max_sl:
            sl = entry * (1 - max_sl) if signal == "LONG" else entry * (1 + max_sl)
            sl_pct = max_sl
        if sl_pct <= 0:
            equity_curve.append(equity)
            continue

        sl_dist = abs(entry - sl)
        tp1 = entry + config["tp_ratio"] * sl_dist if signal == "LONG" else entry - config["tp_ratio"] * sl_dist
        tp2 = entry + 2 * config["tp_ratio"] * sl_dist if signal == "LONG" else entry - 2 * config["tp_ratio"] * sl_dist

        risk_usd = min(equity * risk_pct, equity * 0.025)
        notional = risk_usd / sl_pct
        size = notional / entry
        if size <= 0 or notional < 1:
            equity_curve.append(equity)
            continue

        atr_5m = atr(data_15["high"].values[-14:], data_15["low"].values[-14:], data_15["close"].values[-14:]) or entry * 0.001
        slip = SLIP_ATR_MULT * atr_5m * 0.5
        entry_fill = entry * (1 + SPREAD) + slip if signal == "LONG" else entry * (1 - SPREAD) - slip

        open_pos = {
            "direction": signal, "entry_fill": entry_fill, "entry_time": bar_time,
            "entry_idx": i, "size": size, "size_remaining": size, "stop_loss": sl,
            "tp1": tp1, "tp2": tp2, "tp1_hit": False, "breakeven_active": False,
            "trailing_stop": None, "trail_high": entry_fill, "trail_low": entry_fill,
            "bars_held": 0, "score": score, "atr_at_entry": atr_1h_val or entry * 0.005,
            "atr_trail_mult": config["atr_trail_mult"], "partial_exit_pct": config["partial_exit_pct"],
            "time_exit_bars": config["time_exit_bars"], "total_realized": 0.0,
        }
        equity_curve.append(equity)

    return _compute_metrics(trades, equity_curve, symbol, capital)


def _manage_pos(pos, bar_high, bar_low, bar_close, df_15m, config):
    pos["bars_held"] += 1
    d = 1 if pos["direction"] == "LONG" else -1

    if d == 1:
        pos["trail_high"] = max(pos["trail_high"], bar_high)
    else:
        pos["trail_low"] = min(pos["trail_low"], bar_low)

    atr_now = pos["atr_at_entry"]

    # SL
    if (d == 1 and bar_low <= pos["stop_loss"]) or (d == -1 and bar_high >= pos["stop_loss"]):
        return _close(pos, pos["stop_loss"], "stop_loss")

    # Trailing
    if pos["trailing_stop"] is not None:
        if (d == 1 and bar_low <= pos["trailing_stop"]) or (d == -1 and bar_high >= pos["trailing_stop"]):
            return _close(pos, pos["trailing_stop"], "trailing_stop")

    # TP1 partial
    if not pos["tp1_hit"]:
        tp1_hit = (d == 1 and bar_high >= pos["tp1"]) or (d == -1 and bar_low <= pos["tp1"])
        if tp1_hit:
            pos["tp1_hit"] = True
            pos["stop_loss"] = pos["entry_fill"] + pos["entry_fill"] * SPREAD if d == 1 else pos["entry_fill"] - pos["entry_fill"] * SPREAD
            pos["breakeven_active"] = True
            trail_dist = pos["atr_trail_mult"] * atr_now
            pos["trailing_stop"] = pos["trail_high"] - trail_dist if d == 1 else pos["trail_low"] + trail_dist
            partial_pnl = _calc_partial(pos, pos["tp1"], pos["partial_exit_pct"])
            pos["size_remaining"] *= (1 - pos["partial_exit_pct"])
            pos["total_realized"] += partial_pnl

    # Update trailing
    if pos["tp1_hit"] and pos["trailing_stop"] is not None:
        trail_dist = pos["atr_trail_mult"] * atr_now
        if d == 1:
            new_trail = pos["trail_high"] - trail_dist
            if new_trail > pos["trailing_stop"]:
                pos["trailing_stop"] = new_trail
        else:
            new_trail = pos["trail_low"] + trail_dist
            if new_trail < pos["trailing_stop"]:
                pos["trailing_stop"] = new_trail

    # TP2
    if (d == 1 and bar_high >= pos["tp2"]) or (d == -1 and bar_low <= pos["tp2"]):
        return _close(pos, pos["tp2"], "take_profit_2")

    # Time
    if pos["bars_held"] >= pos["time_exit_bars"]:
        return _close(pos, bar_close, "time_exit")

    return None


def _close(pos, exit_price, reason):
    d = 1 if pos["direction"] == "LONG" else -1
    slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5
    exit_fill = exit_price * (1 - SPREAD) - slip if d == 1 else exit_price * (1 + SPREAD) + slip

    gross = pos["size_remaining"] * (exit_fill - pos["entry_fill"]) if d == 1 else pos["size_remaining"] * (pos["entry_fill"] - exit_fill)
    comm = pos["size_remaining"] * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
    return {"exit_price": exit_fill, "reason": reason, "pnl": gross - comm + pos["total_realized"], "partial_pnl": pos["total_realized"]}


def _calc_partial(pos, exit_price, ratio):
    d = 1 if pos["direction"] == "LONG" else -1
    slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5
    exit_fill = exit_price * (1 - SPREAD) - slip if d == 1 else exit_price * (1 + SPREAD) + slip
    close_size = pos["size"] * ratio
    gross = close_size * (exit_fill - pos["entry_fill"]) if d == 1 else close_size * (pos["entry_fill"] - exit_fill)
    comm = close_size * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
    return gross - comm


def _compute_metrics(trades, equity_curve, symbol, capital):
    if not trades:
        return {"symbol": symbol, "total_trades": 0, "metrics": _empty(), "equity_curve": [], "trades": []}

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    eq = np.array(equity_curve)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1) * 100
    max_dd = float(dd.min())

    total_return = eq[-1] - eq[0]
    total_return_pct = total_return / eq[0] * 100
    wr = len(wins) / len(trades) * 100

    gross_win = sum(wins)
    gross_loss = -sum(losses) if losses else 0.001
    pf = gross_win / gross_loss if gross_loss > 0 else 99.0

    if len(eq) > 1:
        returns = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1)
        std = np.std(returns)
        sharpe = float(np.mean(returns) / std * np.sqrt(252 * 24 * 4)) if std > 0 else 0
        neg = returns[returns < 0]
        downside = float(np.std(neg)) if len(neg) > 0 else 0.001
        sortino = float(np.mean(returns) / downside * np.sqrt(252 * 24 * 4)) if downside > 0 else 0
    else:
        sharpe = sortino = 0

    calmar = abs(total_return_pct / max_dd) if max_dd < 0 else 0

    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

    return {
        "symbol": symbol,
        "total_trades": len(trades),
        "metrics": {
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "max_dd_pct": round(max_dd, 2),
            "calmar": round(calmar, 2),
            "avg_trade": round(float(np.mean(pnls)), 4),
            "avg_win": round(float(np.mean(wins)), 4) if wins else 0,
            "avg_loss": round(float(np.mean(losses)), 4) if losses else 0,
            "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
            "wins": len(wins),
            "losses": len(losses),
            "exit_reasons": reasons,
            "final_equity": round(float(eq[-1]), 2),
        },
        "equity_curve": [round(float(x), 2) for x in equity_curve[::10]],
        "trades": [asdict(t) for t in trades],
    }


def _empty():
    return {"total_return": 0, "total_return_pct": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe": 0, "sortino": 0, "max_dd_pct": 0, "calmar": 0, "avg_trade": 0,
            "avg_win": 0, "avg_loss": 0, "avg_bars_held": 0, "wins": 0, "losses": 0,
            "exit_reasons": {}, "final_equity": 0}


def main():
    print("=" * 70)
    print(" PORTFOLIO BACKTEST — 5 PAIRS CONSISTENT 7-MONTH PERIOD")
    print(" Institutional Engine V2 | Jan 30 – Jul 29, 2026 (ALL PAIRS)")
    print("=" * 70)

    pair_results = []

    for idx, symbol in enumerate(PAIRS):
        print(f"\n[{idx+1}/5] {symbol} — ${CAPITAL_PER_PAIR:.0f}")
        t0 = time.time()

        data = load_and_trim_data(symbol)
        if "15m" not in data or "4h" not in data:
            print(f"  SKIP: missing critical data for {symbol}")
            continue

        df_15m = data["15m"]
        df_1h = data.get("1h", data["15m"])
        df_4h = data["4h"]
        df_1d = data.get("1d", data["4h"])

        print(f"  Final: 15m={len(df_15m)}, 1h={len(df_1h)}, 4h={len(df_4h)}, 1d={len(df_1d)}")

        result = run_single_pair(symbol, CAPITAL_PER_PAIR, PAIR_CONFIGS[symbol],
                                 df_15m, df_1h, df_4h, df_1d)

        m = result["metrics"]
        elapsed = time.time() - t0
        print(f"  {result['total_trades']} trades | WR={m['win_rate']}% | PF={m['profit_factor']} | "
              f"Ret={m['total_return_pct']:+.2f}% | DD={m['max_dd_pct']:.2f}% | Sharpe={m['sharpe']:.2f} | "
              f"${m['final_equity']:.2f} | {elapsed:.1f}s")

        pair_results.append(result)

    # Portfolio aggregation
    print(f"\n{'='*70}")
    print(" AGGREGATING PORTFOLIO")
    print(f"{'='*70}")

    per_pair = {}
    pair_trades = {}
    for r in pair_results:
        per_pair[r["symbol"]] = r["metrics"]
        pair_trades[r["symbol"]] = r["total_trades"]

    total_final = sum(per_pair[s]["final_equity"] for s in per_pair)
    total_return = total_final - TOTAL_CAPITAL
    total_return_pct = total_return / TOTAL_CAPITAL * 100

    # Portfolio DD
    max_bars = max(len(r["equity_curve"]) for r in pair_results)
    portfolio_eq = np.zeros(max_bars)
    for r in pair_results:
        ec = np.array(r["equity_curve"])
        if len(ec) < max_bars:
            ec = np.pad(ec, (0, max_bars - len(ec)), mode='edge')
        portfolio_eq += ec[:max_bars]

    peak = np.maximum.accumulate(portfolio_eq)
    dd = (portfolio_eq - peak) / np.where(peak > 0, peak, 1) * 100
    portfolio_max_dd = float(dd.min())

    total_trades = sum(pair_trades[s] for s in pair_trades)
    total_wins = sum(per_pair[s]["wins"] for s in per_pair)
    total_losses = sum(per_pair[s]["losses"] for s in per_pair)
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    total_gross_win = sum(per_pair[s]["avg_win"] * per_pair[s]["wins"] for s in per_pair)
    total_gross_loss = -sum(per_pair[s]["avg_loss"] * per_pair[s]["losses"] for s in per_pair)
    pf = total_gross_win / total_gross_loss if total_gross_loss > 0 else 99.0

    avg_sharpe = np.mean([per_pair[s]["sharpe"] for s in per_pair])
    avg_sortino = np.mean([per_pair[s]["sortino"] for s in per_pair])
    calmar = abs(total_return_pct / portfolio_max_dd) if portfolio_max_dd < 0 else 0

    # Per-pair contributions
    contributions = {}
    for s in per_pair:
        pr = per_pair[s]["final_equity"] - CAPITAL_PER_PAIR
        contributions[s] = {
            "return_usd": round(pr, 2),
            "return_pct": round(pr / CAPITAL_PER_PAIR * 100, 2),
            "pct_of_total": round(pr / total_return * 100, 1) if total_return != 0 else 0,
            "trades": pair_trades[s],
            "sharpe": per_pair[s]["sharpe"],
            "max_dd": per_pair[s]["max_dd_pct"],
            "final_equity": per_pair[s]["final_equity"],
        }

    # Correlation
    min_len = min(len(r["equity_curve"]) for r in pair_results)
    curves = {}
    for r in pair_results:
        ec = np.array(r["equity_curve"][:min_len])
        if len(ec) > 1:
            curves[r["symbol"]] = np.diff(ec) / np.where(ec[:-1] != 0, ec[:-1], 1)

    syms = list(curves.keys())
    corr = {}
    for s1 in syms:
        corr[s1] = {}
        for s2 in syms:
            if s1 == s2:
                corr[s1][s2] = 1.0
            elif s2 in corr and s1 in corr[s2]:
                corr[s1][s2] = corr[s2][s1]
            else:
                c = np.corrcoef(curves[s1], curves[s2])[0, 1]
                corr[s1][s2] = round(float(c), 3)

    # Max simultaneous DD
    pair_dds = {}
    for r in pair_results:
        ec = np.array(r["equity_curve"][:min_len])
        p = np.maximum.accumulate(ec)
        d = (ec - p) / np.where(p > 0, p, 1) * 100
        pair_dds[r["symbol"]] = d < -1.0

    max_sim = 0
    for i in range(min_len):
        count = sum(1 for s in pair_dds if pair_dds[s][i])
        max_sim = max(max_sim, count)

    # Monthly breakdown
    monthly = {}
    for r in pair_results:
        for t in r["trades"]:
            et = t["entry_time"]
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et)
                except:
                    continue
            mk = et.strftime("%Y-%m")
            if mk not in monthly:
                monthly[mk] = {}
            sym = r["symbol"]
            if sym not in monthly[mk]:
                monthly[mk][sym] = {"pnl": 0, "trades": 0, "wins": 0}
            monthly[mk][sym]["pnl"] += t["pnl"]
            monthly[mk][sym]["trades"] += 1
            if t["pnl"] > 0:
                monthly[mk][sym]["wins"] += 1

    for mk in monthly:
        tp = sum(monthly[mk][s]["pnl"] for s in monthly[mk])
        tt = sum(monthly[mk][s]["trades"] for s in monthly[mk])
        tw = sum(monthly[mk][s]["wins"] for s in monthly[mk])
        monthly[mk]["TOTAL"] = {"pnl": round(tp, 2), "trades": tt, "win_rate": round(tw/tt*100, 1) if tt > 0 else 0}

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "period": "2026-01-30 to 2026-07-29 (CONSISTENT ALL PAIRS)",
        "portfolio": {
            "initial_capital": TOTAL_CAPITAL,
            "capital_per_pair": CAPITAL_PER_PAIR,
            "final_equity": round(total_final, 2),
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "max_dd_pct": round(portfolio_max_dd, 2),
            "sharpe": round(avg_sharpe, 2),
            "sortino": round(avg_sortino, 2),
            "profit_factor": round(pf, 2),
            "win_rate": round(wr, 1),
            "total_trades": total_trades,
            "calmar": round(calmar, 2),
        },
        "per_pair": per_pair,
        "contributions": contributions,
        "correlation_matrix": corr,
        "max_simultaneous_dd": max_sim,
        "monthly_breakdown": monthly,
        "pair_backtests": [{"symbol": r["symbol"], "total_trades": r["total_trades"], "metrics": r["metrics"]} for r in pair_results],
    }

    output_file = OUTPUT_DIR / "portfolio_7months_results.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, default=str)

    # Print results
    p = result["portfolio"]
    print(f"\n{'='*70}")
    print(" PORTFOLIO RESULTS (CONSISTENT 7-MONTH PERIOD)")
    print(f"{'='*70}")
    print(f"  Period:  Jan 30 – Jul 29, 2026 (ALL PAIRS IDENTICAL)")
    print(f"  Initial: ${p['initial_capital']:,.2f}  →  Final: ${p['final_equity']:,.2f}")
    print(f"  Return:  ${p['total_return']:+,.2f} ({p['total_return_pct']:+.2f}%)")
    print(f"  Max DD:  {p['max_dd_pct']:.2f}%")
    print(f"  Sharpe:  {p['sharpe']:.2f}  |  Sortino: {p['sortino']:.2f}")
    print(f"  PF:      {p['profit_factor']:.2f}  |  WR: {p['win_rate']:.1f}%")
    print(f"  Trades:  {p['total_trades']}  |  Calmar: {p['calmar']:.2f}")

    print(f"\n  PER-PAIR CONTRIBUTIONS:")
    print(f"  {'Pair':<12} {'Return':>10} {'Ret%':>8} {'%Total':>8} {'Trades':>7} {'Sharpe':>8} {'DD':>8}")
    print(f"  {'-'*61}")
    for sym, c in contributions.items():
        print(f"  {sym:<12} ${c['return_usd']:>+9.2f} {c['return_pct']:>+7.2f}% {c['pct_of_total']:>7.1f}% {c['trades']:>6} {c['sharpe']:>7.2f} {c['max_dd']:>7.2f}%")

    print(f"\n  CORRELATION MATRIX:")
    header = f"  {'':>12}" + "".join(f"{s:>10}" for s in syms)
    print(header)
    for s1 in syms:
        row = f"  {s1:<12}" + "".join(f"{corr[s1][s2]:>10.3f}" for s2 in syms)
        print(row)

    print(f"\n  MAX SIMULTANEOUS DD: {max_sim}/5 pairs")

    print(f"\n  MONTHLY BREAKDOWN:")
    print(f"  {'Month':<10} {'PnL':>10} {'Trades':>8} {'WR':>8}")
    print(f"  {'-'*36}")
    for mk in sorted(monthly.keys()):
        t = monthly[mk]["TOTAL"]
        print(f"  {mk:<10} ${t['pnl']:>+9.2f} {t['trades']:>7} {t['win_rate']:>7.1f}%")

    print(f"\nResults saved: {output_file}")
    return result


if __name__ == "__main__":
    main()
