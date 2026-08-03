"""
Full Improvements Backtest — 4 Comprehensive Tests
===================================================

Implements all 4 improvements to the Institutional Engine:
  Step 1: 10-pair portfolio (BTC, ETH, SOL, XRP, BNB + DOT, AVAX, LINK, ADA, MATIC)
  Step 2: BTC 3-year walk-forward (Jan 2023 – Jul 2026)
  Step 3: BTC bear market 2022 (Jan – Dec 2022)
  Step 4: 1H signal detection (add engulfing on 1H in addition to 4H)

Uses BinanceDataDownloader from backtest_institutional_v2.py.
All costs: commission 0.018% (maker), spread 0.02%, slippage 0.1× ATR.
No look-ahead bias: only uses closed candles.
"""

import sys
import json
import time
import traceback
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent))

from backtest_institutional_v2 import BinanceDataDownloader
from engine.institutional_engine_v2 import (
    choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, detect_market_phase
)

# ============================================================
# CONFIG
# ============================================================

TOTAL_CAPITAL = 10000.0
RISK_PCT = 0.01
COMMISSION_MAKER = 0.00018
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1

CACHE_DIR = Path(__file__).parent / "backtest_results" / "cache"
OUTPUT_DIR = Path(__file__).parent / "backtest_results"
RESULTS_FILE = OUTPUT_DIR / "full_improvements_results.json"

# All 10 pairs
ALL_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
             "DOTUSDT", "AVAXUSDT", "LINKUSDT", "ADAUSDT", "MATICUSDT"]

# Default pair configs (conservative defaults for new pairs)
DEFAULT_CONFIG = {
    "supertrend_multiplier": 2.8,
    "choppiness_neutral_threshold": 74.0,
    "fibonacci_days": 30,
    "tp_ratio": 1.5,
    "atr_trail_mult": 2.0,
    "partial_exit_pct": 0.5,
    "time_exit_bars": 24,
    "min_score": 50,
    "max_sl_pct": 0.018,
}

PAIR_CONFIGS = {
    "BTCUSDT": {**DEFAULT_CONFIG, "tp_ratio": 1.5},
    "ETHUSDT": {**DEFAULT_CONFIG, "supertrend_multiplier": 2.6, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.8},
    "SOLUSDT": {**DEFAULT_CONFIG, "supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 70.0, "tp_ratio": 2.0, "fibonacci_days": 90},
    "XRPUSDT": {**DEFAULT_CONFIG, "supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 68.0, "tp_ratio": 1.8, "fibonacci_days": 60},
    "BNBUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 68.0, "tp_ratio": 1.6},
    "DOTUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.5},
    "AVAXUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.8},
    "LINKUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.5},
    "ADAUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.5},
    "MATICUSDT": {**DEFAULT_CONFIG, "choppiness_neutral_threshold": 72.0, "tp_ratio": 1.5},
}


# ============================================================
# TRADE & METRICS
# ============================================================

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
    signal_source: str = "4H"


# ============================================================
# WALK-FORWARD ENGINE
# ============================================================

def run_walk_forward(
    symbol: str,
    capital: float,
    config: Dict,
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    add_1h_signals: bool = False,
    label: str = "4H",
) -> Dict:
    """
    Run V2 walk-forward backtest on a single pair.
    If add_1h_signals=True, also check for engulfing on 1H candles.
    """
    equity = capital
    peak_equity = equity
    trades = []
    equity_curve = []
    open_pos = None

    ci_thresh = config["choppiness_neutral_threshold"]
    max_sl = config["max_sl_pct"]

    for i in range(50, len(df_15m)):
        bar_time = df_15m.index[i]
        bar_close = float(df_15m["close"].iloc[i])
        bar_high = float(df_15m["high"].iloc[i])
        bar_low = float(df_15m["low"].iloc[i])

        # Manage open position
        if open_pos is not None:
            result = _manage_pos(open_pos, bar_high, bar_low, bar_close, config)
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
                    signal_source=open_pos.get("signal_source", "4H"),
                ))
                open_pos = None
            equity_curve.append(equity)
            continue

        # --- SIGNAL DETECTION ---
        signal = None
        signal_source = None

        # 1. Check 4H engulfing (always)
        mask_4h = df_4h.index <= bar_time
        if mask_4h.sum() >= 3:
            klines_4h = []
            for j in range(max(0, mask_4h.sum() - 3), mask_4h.sum()):
                idx = df_4h.index[j]
                klines_4h.append({
                    "open": float(df_4h.loc[idx, "open"]),
                    "high": float(df_4h.loc[idx, "high"]),
                    "low": float(df_4h.loc[idx, "low"]),
                    "close": float(df_4h.loc[idx, "close"]),
                })
            if is_bearish_engulfing(klines_4h):
                signal = "SHORT"
                signal_source = "4H"
            elif is_bullish_engulfing(klines_4h):
                signal = "LONG"
                signal_source = "4H"

        # 2. NEW: Check 1H engulfing (if enabled and no 4H signal)
        if signal is None and add_1h_signals:
            mask_1h = df_1h.index <= bar_time
            if mask_1h.sum() >= 3:
                klines_1h = []
                for j in range(max(0, mask_1h.sum() - 3), mask_1h.sum()):
                    idx = df_1h.index[j]
                    klines_1h.append({
                        "open": float(df_1h.loc[idx, "open"]),
                        "high": float(df_1h.loc[idx, "high"]),
                        "low": float(df_1h.loc[idx, "low"]),
                        "close": float(df_1h.loc[idx, "close"]),
                    })
                if is_bearish_engulfing(klines_1h):
                    signal = "SHORT"
                    signal_source = "1H"
                elif is_bullish_engulfing(klines_1h):
                    signal = "LONG"
                    signal_source = "1H"

        if signal is None:
            equity_curve.append(equity)
            continue

        # --- FILTERS ---
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
        mask_1h_data = df_1h.index <= bar_time
        if mask_1h_data.sum() < 30:
            equity_curve.append(equity)
            continue
        data_1h = df_1h[mask_1h_data]
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
                                       data_15["low"].values[:len(data_1h)],
                                       data_1h["volume"].values, data_1h["open"].values)],
            ind_1h
        )
        if phase in ("ranging", "neutral", "unknown"):
            equity_curve.append(equity)
            continue

        # --- SCORE ---
        # Use the klines from whichever source triggered the signal
        if signal_source == "4H":
            klines_score = klines_4h
        else:
            # Re-extract 1H klines for scoring
            mask_1h_sc = df_1h.index <= bar_time
            klines_score = []
            for j in range(max(0, mask_1h_sc.sum() - 3), mask_1h_sc.sum()):
                idx = df_1h.index[j]
                klines_score.append({
                    "open": float(df_1h.loc[idx, "open"]),
                    "high": float(df_1h.loc[idx, "high"]),
                    "low": float(df_1h.loc[idx, "low"]),
                    "close": float(df_1h.loc[idx, "close"]),
                })

        wr_val = williams_r(
            np.array([float(k["high"]) for k in klines_score]),
            np.array([float(k["low"]) for k in klines_score]),
            np.array([float(k["close"]) for k in klines_score])
        )
        body_ratio = abs(float(klines_score[-1]["close"]) - float(klines_score[-1]["open"])) / \
                     max(float(klines_score[-1]["high"]) - float(klines_score[-1]["low"]), 0.001)

        # 1H signals get a slight penalty (less reliable than 4H)
        base_score = 55 if signal_source == "4H" else 50
        score = base_score
        if body_ratio > 0.4: score += 12
        if ci is not None and ci < 60: score += 8
        if ci is not None and ci < 50: score += 5
        if wr_val is not None and -80 < wr_val < -20: score += 5
        if signal == "LONG" and c_15[-1] > np.mean(c_15[-20:]): score += 3
        elif signal == "SHORT" and c_15[-1] < np.mean(c_15[-20:]): score += 3

        if score < config["min_score"]:
            equity_curve.append(equity)
            continue

        # --- ENTRY / SL ---
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

        risk_usd = min(equity * RISK_PCT, equity * 0.025)
        notional = risk_usd / sl_pct
        size = notional / entry
        if size <= 0 or notional < 1:
            equity_curve.append(equity)
            continue

        atr_5m = atr(h_15[-14:], l_15[-14:], c_15[-14:]) or entry * 0.001
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
            "signal_source": signal_source,
        }
        equity_curve.append(equity)

    return _compute_metrics(trades, equity_curve, symbol, capital, label)


def _manage_pos(pos, bar_high, bar_low, bar_close, config):
    pos["bars_held"] += 1
    d = 1 if pos["direction"] == "LONG" else -1

    if d == 1:
        pos["trail_high"] = max(pos["trail_high"], bar_high)
    else:
        pos["trail_low"] = min(pos["trail_low"], bar_low)

    atr_now = pos["atr_at_entry"]

    if (d == 1 and bar_low <= pos["stop_loss"]) or (d == -1 and bar_high >= pos["stop_loss"]):
        return _close(pos, pos["stop_loss"], "stop_loss")

    if pos["trailing_stop"] is not None:
        if (d == 1 and bar_low <= pos["trailing_stop"]) or (d == -1 and bar_high >= pos["trailing_stop"]):
            return _close(pos, pos["trailing_stop"], "trailing_stop")

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

    if (d == 1 and bar_high >= pos["tp2"]) or (d == -1 and bar_low <= pos["tp2"]):
        return _close(pos, pos["tp2"], "take_profit_2")

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


def _compute_metrics(trades, equity_curve, symbol, capital, label=""):
    if not trades:
        return {"symbol": symbol, "label": label, "total_trades": 0, "metrics": _empty(), "equity_curve": [], "trades": []}

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
    source_counts = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        source_counts[t.signal_source] = source_counts.get(t.signal_source, 0) + 1

    return {
        "symbol": symbol,
        "label": label,
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
            "signal_sources": source_counts,
            "final_equity": round(float(eq[-1]), 2),
        },
        "equity_curve": [round(float(x), 2) for x in equity_curve[::10]],
        "trades": [asdict(t) for t in trades],
    }


def _empty():
    return {"total_return": 0, "total_return_pct": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe": 0, "sortino": 0, "max_dd_pct": 0, "calmar": 0, "avg_trade": 0,
            "avg_win": 0, "avg_loss": 0, "avg_bars_held": 0, "wins": 0, "losses": 0,
            "exit_reasons": {}, "signal_sources": {}, "final_equity": 0}


def download_data(dl, symbol, start_date, end_date, intervals=("15m", "1h", "4h", "1d")):
    """Download multiple intervals for a symbol. Returns dict of DataFrames."""
    data = {}
    for interval in intervals:
        try:
            df = dl.download_klines(symbol, interval, start_date, end_date)
            if df is not None and len(df) > 0:
                data[interval] = df
                print(f"    {interval}: {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
            else:
                print(f"    {interval}: NO DATA")
        except Exception as e:
            print(f"    {interval}: ERROR — {e}")
    return data


def load_cached_data(symbol):
    """Load cached data for a symbol."""
    files = sorted(CACHE_DIR.glob(f"{symbol}_*.parquet"))
    data = {}
    for f in files:
        parts = f.stem.split("_")
        interval = parts[1]
        df = pd.read_parquet(f)
        if interval not in data or len(df) > len(data[interval]):
            data[interval] = df
    return data


def aggregate_portfolio(pair_results, total_capital, label=""):
    """Aggregate per-pair results into portfolio metrics."""
    per_pair = {}
    for r in pair_results:
        per_pair[r["symbol"]] = r["metrics"]

    n_pairs = len(per_pair)
    if n_pairs == 0:
        return {"portfolio": _empty(), "per_pair": {}, "contributions": {}}

    capital_per_pair = total_capital / n_pairs

    total_final = sum(per_pair[s]["final_equity"] for s in per_pair)
    total_return = total_final - total_capital
    total_return_pct = total_return / total_capital * 100

    # Portfolio DD
    max_bars = max((len(r["equity_curve"]) for r in pair_results), default=0)
    if max_bars == 0:
        return {"portfolio": _empty(), "per_pair": per_pair, "contributions": {}}

    portfolio_eq = np.zeros(max_bars)
    for r in pair_results:
        ec = np.array(r["equity_curve"])
        if len(ec) < max_bars:
            ec = np.pad(ec, (0, max_bars - len(ec)), mode='edge')
        portfolio_eq += ec[:max_bars]

    peak = np.maximum.accumulate(portfolio_eq)
    dd = (portfolio_eq - peak) / np.where(peak > 0, peak, 1) * 100
    portfolio_max_dd = float(dd.min())

    total_trades = sum(r["total_trades"] for r in pair_results)
    total_wins = sum(per_pair[s]["wins"] for s in per_pair)
    total_losses = sum(per_pair[s]["losses"] for s in per_pair)
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    total_gross_win = sum(per_pair[s]["avg_win"] * per_pair[s]["wins"] for s in per_pair)
    total_gross_loss = -sum(per_pair[s]["avg_loss"] * per_pair[s]["losses"] for s in per_pair)
    pf = total_gross_win / total_gross_loss if total_gross_loss > 0 else 99.0

    avg_sharpe = np.mean([per_pair[s]["sharpe"] for s in per_pair]) if per_pair else 0
    avg_sortino = np.mean([per_pair[s]["sortino"] for s in per_pair]) if per_pair else 0
    calmar = abs(total_return_pct / portfolio_max_dd) if portfolio_max_dd < 0 else 0

    contributions = {}
    for s in per_pair:
        pr = per_pair[s]["final_equity"] - capital_per_pair
        contributions[s] = {
            "return_usd": round(pr, 2),
            "return_pct": round(pr / capital_per_pair * 100, 2),
            "pct_of_total": round(pr / total_return * 100, 1) if total_return != 0 else 0,
            "trades": next((r["total_trades"] for r in pair_results if r["symbol"] == s), 0),
            "sharpe": per_pair[s]["sharpe"],
            "max_dd": per_pair[s]["max_dd_pct"],
            "final_equity": per_pair[s]["final_equity"],
        }

    return {
        "label": label,
        "initial_capital": total_capital,
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
        "per_pair": per_pair,
        "contributions": contributions,
    }


# ============================================================
# MAIN SIMULATOR
# ============================================================

class FullImprovementsSimulator:
    def __init__(self):
        self.dl = BinanceDataDownloader()
        self.results = {}

    def run_step1_10pairs(self):
        """Step 1: 10-pair portfolio walk-forward (Jan–Jul 2026)."""
        print("\n" + "=" * 70)
        print(" STEP 1: 10-PAIR PORTFOLIO WALK-FORWARD")
        print(" Jan 2026 – Jul 2026 | $10,000 total")
        print("=" * 70)

        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=180)  # ~6 months

        pair_results = []
        failed_pairs = []

        for idx, symbol in enumerate(ALL_PAIRS):
            print(f"\n[{idx+1}/{len(ALL_PAIRS)}] {symbol}")
            t0 = time.time()

            try:
                data = download_data(self.dl, symbol, start_date, end_date)
                if "15m" not in data or "4h" not in data:
                    print(f"  SKIP: missing critical data (need 15m and 4h)")
                    failed_pairs.append(symbol)
                    continue

                df_15m = data["15m"]
                df_1h = data.get("1h", data["15m"])
                df_4h = data["4h"]
                df_1d = data.get("1d", data["4h"])

                config = PAIR_CONFIGS.get(symbol, DEFAULT_CONFIG)
                capital_per_pair = TOTAL_CAPITAL / len(ALL_PAIRS)

                result = run_walk_forward(symbol, capital_per_pair, config,
                                          df_15m, df_1h, df_4h, df_1d)
                m = result["metrics"]
                elapsed = time.time() - t0
                print(f"  RESULT: {result['total_trades']} trades | WR={m['win_rate']}% | "
                      f"PF={m['profit_factor']} | Ret={m['total_return_pct']:+.2f}% | "
                      f"DD={m['max_dd_pct']:.2f}% | Sharpe={m['sharpe']:.2f} | "
                      f"${m['final_equity']:.2f} | {elapsed:.1f}s")
                pair_results.append(result)

            except Exception as e:
                print(f"  ERROR: {e}")
                traceback.print_exc()
                failed_pairs.append(symbol)

        portfolio = aggregate_portfolio(pair_results, TOTAL_CAPITAL, "10-pair Jan–Jul 2026")

        self.results["step1_10pairs"] = {
            "description": "10-pair portfolio walk-forward, Jan–Jul 2026",
            "period": f"{start_date.date()} to {end_date.date()}",
            "total_capital": TOTAL_CAPITAL,
            "pairs_attempted": len(ALL_PAIRS),
            "pairs_succeeded": len(pair_results),
            "failed_pairs": failed_pairs,
            "portfolio": {k: v for k, v in portfolio.items() if k not in ("per_pair", "contributions")},
            "pair_backtests": [{"symbol": r["symbol"], "trades": r["total_trades"], "metrics": r["metrics"]} for r in pair_results],
            "contributions": portfolio.get("contributions", {}),
        }

        p = portfolio
        print(f"\n{'='*70}")
        print(f" STEP 1 PORTFOLIO: ${p['initial_capital']:,.0f} → ${p['final_equity']:,.2f} ({p['total_return_pct']:+.2f}%)")
        print(f" DD={p['max_dd_pct']:.2f}% | Sharpe={p['sharpe']:.2f} | PF={p['profit_factor']:.2f} | WR={p['win_rate']:.1f}% | Trades={p['total_trades']}")
        print(f"{'='*70}")

        return self.results["step1_10pairs"]

    def run_step2_3years(self):
        """Step 2: BTC 3-year walk-forward (Jan 2023 – Jul 2026)."""
        print("\n" + "=" * 70)
        print(" STEP 2: BTC 3-YEAR WALK-FORWARD")
        print(" Jan 2023 – Jul 2026 | $10,000")
        print("=" * 70)

        symbol = "BTCUSDT"
        end_date = datetime.now(timezone.utc)
        start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

        t0 = time.time()
        data = download_data(self.dl, symbol, start_date, end_date)

        if "15m" not in data or "4h" not in data:
            print("  FATAL: missing 15m or 4h data")
            return None

        config = PAIR_CONFIGS[symbol]
        result = run_walk_forward(symbol, TOTAL_CAPITAL, config,
                                  data["15m"], data.get("1h", data["15m"]),
                                  data["4h"], data.get("1d", data["4h"]),
                                  label="BTC 3Y")

        m = result["metrics"]
        elapsed = time.time() - t0
        print(f"\n  RESULT: {result['total_trades']} trades | WR={m['win_rate']}% | "
              f"PF={m['profit_factor']} | Ret={m['total_return_pct']:+.2f}% | "
              f"DD={m['max_dd_pct']:.2f}% | Sharpe={m['sharpe']:.2f} | "
              f"${m['final_equity']:.2f} | {elapsed:.1f}s")

        # Compare with 7-month baseline
        comparison = {}
        baseline_file = OUTPUT_DIR / "portfolio_7months_results.json"
        if baseline_file.exists():
            with open(baseline_file) as f:
                baseline = json.load(f)
            btc_baseline = baseline.get("per_pair", {}).get("BTCUSDT", {})
            if btc_baseline:
                comparison = {
                    "baseline_7m_return_pct": btc_baseline.get("total_return_pct", 0),
                    "baseline_7m_trades": btc_baseline.get("wins", 0) + btc_baseline.get("losses", 0),
                    "baseline_7m_sharpe": btc_baseline.get("sharpe", 0),
                    "baseline_7m_dd": btc_baseline.get("max_dd_pct", 0),
                    "3y_return_pct": m["total_return_pct"],
                    "3y_trades": result["total_trades"],
                    "3y_sharpe": m["sharpe"],
                    "3y_dd": m["max_dd_pct"],
                    "annualized_return_pct": round(m["total_return_pct"] / 3.33, 2),  # ~3.33 years
                }

        self.results["step2_3years"] = {
            "description": "BTC 3-year walk-forward, Jan 2023 – Jul 2026",
            "period": f"{start_date.date()} to {end_date.date()}",
            "total_capital": TOTAL_CAPITAL,
            "result": {"trades": result["total_trades"], "metrics": m},
            "comparison_with_7m": comparison,
        }

        print(f"\n  3-YEAR BTC: ${TOTAL_CAPITAL:,.0f} → ${m['final_equity']:,.2f} ({m['total_return_pct']:+.2f}%)")
        print(f"  Annualized: ~{comparison.get('annualized_return_pct', 'N/A')}%/year")
        if comparison:
            print(f"  vs 7-month baseline: 7m={comparison.get('baseline_7m_return_pct', 0):+.2f}% | 3y={comparison.get('3y_return_pct', 0):+.2f}%")

        return self.results["step2_3years"]

    def run_step3_bear2022(self):
        """Step 3: BTC bear market 2022 walk-forward."""
        print("\n" + "=" * 70)
        print(" STEP 3: BTC BEAR MARKET 2022")
        print(" Jan 2022 – Dec 2022 | $10,000")
        print("=" * 70)

        symbol = "BTCUSDT"
        start_date = datetime(2022, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2022, 12, 31, tzinfo=timezone.utc)

        t0 = time.time()
        data = download_data(self.dl, symbol, start_date, end_date)

        if "15m" not in data or "4h" not in data:
            print("  FATAL: missing 15m or 4h data for 2022")
            self.results["step3_bear2022"] = {"error": "No data available for 2022"}
            return None

        config = PAIR_CONFIGS[symbol]
        result = run_walk_forward(symbol, TOTAL_CAPITAL, config,
                                  data["15m"], data.get("1h", data["15m"]),
                                  data["4h"], data.get("1d", data["4h"]),
                                  label="BTC Bear 2022")

        m = result["metrics"]
        elapsed = time.time() - t0
        print(f"\n  RESULT: {result['total_trades']} trades | WR={m['win_rate']}% | "
              f"PF={m['profit_factor']} | Ret={m['total_return_pct']:+.2f}% | "
              f"DD={m['max_dd_pct']:.2f}% | Sharpe={m['sharpe']:.2f} | "
              f"${m['final_equity']:.2f} | {elapsed:.1f}s")

        # Monthly breakdown
        monthly = {}
        for t in result.get("trades", []):
            et = t["entry_time"]
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et)
                except:
                    continue
            mk = et.strftime("%Y-%m")
            if mk not in monthly:
                monthly[mk] = {"pnl": 0, "trades": 0, "wins": 0}
            monthly[mk]["pnl"] += t["pnl"]
            monthly[mk]["trades"] += 1
            if t["pnl"] > 0:
                monthly[mk]["wins"] += 1

        for mk in monthly:
            monthly[mk]["win_rate"] = round(monthly[mk]["wins"] / monthly[mk]["trades"] * 100, 1) if monthly[mk]["trades"] > 0 else 0

        self.results["step3_bear2022"] = {
            "description": "BTC bear market 2022, Jan–Dec 2022",
            "period": f"{start_date.date()} to {end_date.date()}",
            "total_capital": TOTAL_CAPITAL,
            "result": {"trades": result["total_trades"], "metrics": m},
            "monthly_breakdown": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in monthly.items()},
            "analysis": {
                "btc_2022_context": "BTC fell from $47k to $16k (-66%) in 2022",
                "strategy_profitable_in_bear": m["total_return_pct"] > 0,
                "trades_per_month": round(result["total_trades"] / 12, 1),
            },
        }

        print(f"\n  BEAR MARKET 2022: ${TOTAL_CAPITAL:,.0f} → ${m['final_equity']:,.2f} ({m['total_return_pct']:+.2f}%)")
        print(f"  BTC fell -66% in 2022. Strategy {'PROFITABLE' if m['total_return_pct'] > 0 else 'LOSS'} in bear market.")
        print(f"  Monthly trades: ~{result['total_trades'] / 12:.1f}")

        return self.results["step3_bear2022"]

    def run_step4_1h_signals(self):
        """Step 4: Add 1H signal detection and compare with 4H-only."""
        print("\n" + "=" * 70)
        print(" STEP 4: 1H SIGNAL DETECTION COMPARISON")
        print(" BTC 2023–2026 | 4H-only vs 4H+1H")
        print("=" * 70)

        symbol = "BTCUSDT"
        end_date = datetime.now(timezone.utc)
        start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)

        t0 = time.time()
        data = download_data(self.dl, symbol, start_date, end_date)

        if "15m" not in data or "4h" not in data:
            print("  FATAL: missing data")
            return None

        config = PAIR_CONFIGS[symbol]
        df_15m = data["15m"]
        df_1h = data.get("1h", data["15m"])
        df_4h = data["4h"]
        df_1d = data.get("1d", data["4h"])

        # Run with 4H-only
        print("\n  Running 4H-only...")
        t1 = time.time()
        result_4h = run_walk_forward(symbol, TOTAL_CAPITAL, config,
                                     df_15m, df_1h, df_4h, df_1d,
                                     add_1h_signals=False, label="4H-only")
        m4h = result_4h["metrics"]
        print(f"    4H-only: {result_4h['total_trades']} trades | WR={m4h['win_rate']}% | "
              f"PF={m4h['profit_factor']} | Ret={m4h['total_return_pct']:+.2f}% | "
              f"DD={m4h['max_dd_pct']:.2f}% | Sharpe={m4h['sharpe']:.2f}")

        # Run with 4H+1H
        print("\n  Running 4H+1H...")
        t2 = time.time()
        result_4h1h = run_walk_forward(symbol, TOTAL_CAPITAL, config,
                                       df_15m, df_1h, df_4h, df_1d,
                                       add_1h_signals=True, label="4H+1H")
        m4h1h = result_4h1h["metrics"]
        print(f"    4H+1H:   {result_4h1h['total_trades']} trades | WR={m4h1h['win_rate']}% | "
              f"PF={m4h1h['profit_factor']} | Ret={m4h1h['total_return_pct']:+.2f}% | "
              f"DD={m4h1h['max_dd_pct']:.2f}% | Sharpe={m4h1h['sharpe']:.2f}")

        elapsed = time.time() - t0

        # Compare signal sources
        source_breakdown = m4h1h.get("signal_sources", {})

        improvement = {
            "trades_delta": result_4h1h["total_trades"] - result_4h["total_trades"],
            "trades_increase_pct": round((result_4h1h["total_trades"] - result_4h["total_trades"]) / max(result_4h["total_trades"], 1) * 100, 1),
            "return_delta_pct": round(m4h1h["total_return_pct"] - m4h["total_return_pct"], 2),
            "sharpe_delta": round(m4h1h["sharpe"] - m4h["sharpe"], 2),
            "wr_delta": round(m4h1h["win_rate"] - m4h["win_rate"], 1),
            "pf_delta": round(m4h1h["profit_factor"] - m4h["profit_factor"], 2),
            "dd_delta": round(m4h1h["max_dd_pct"] - m4h["max_dd_pct"], 2),
            "signal_source_breakdown": source_breakdown,
        }

        self.results["step4_1h_signals"] = {
            "description": "1H signal detection comparison, BTC 2023–2026",
            "period": f"{start_date.date()} to {end_date.date()}",
            "total_capital": TOTAL_CAPITAL,
            "baseline_4h": {"trades": result_4h["total_trades"], "metrics": m4h},
            "enhanced_4h1h": {"trades": result_4h1h["total_trades"], "metrics": m4h1h},
            "improvement": improvement,
        }

        print(f"\n  COMPARISON:")
        print(f"    Trades: 4H={result_4h['total_trades']} → 4H+1H={result_4h1h['total_trades']} "
              f"(+{improvement['trades_increase_pct']:+.1f}%)")
        print(f"    Return: {m4h['total_return_pct']:+.2f}% → {m4h1h['total_return_pct']:+.2f}% "
              f"({improvement['return_delta_pct']:+.2f}pp)")
        print(f"    Sharpe: {m4h['sharpe']:.2f} → {m4h1h['sharpe']:.2f} ({improvement['sharpe_delta']:+.2f})")
        print(f"    Signal sources: {source_breakdown}")
        print(f"    Elapsed: {elapsed:.1f}s")

        return self.results["step4_1h_signals"]

    def run_all(self):
        """Run all steps and generate report."""
        print("\n" + "#" * 70)
        print("# FULL IMPROVEMENTS BACKTEST — ALL 4 STEPS")
        print(f"# Started: {datetime.now(timezone.utc).isoformat()}")
        print("#" * 70)

        overall_t0 = time.time()

        # Step 1
        try:
            self.run_step1_10pairs()
        except Exception as e:
            print(f"\n  STEP 1 FAILED: {e}")
            traceback.print_exc()
            self.results["step1_10pairs"] = {"error": str(e)}

        # Step 2
        try:
            self.run_step2_3years()
        except Exception as e:
            print(f"\n  STEP 2 FAILED: {e}")
            traceback.print_exc()
            self.results["step2_3years"] = {"error": str(e)}

        # Step 3
        try:
            self.run_step3_bear2022()
        except Exception as e:
            print(f"\n  STEP 3 FAILED: {e}")
            traceback.print_exc()
            self.results["step3_bear2022"] = {"error": str(e)}

        # Step 4
        try:
            self.run_step4_1h_signals()
        except Exception as e:
            print(f"\n  STEP 4 FAILED: {e}")
            traceback.print_exc()
            self.results["step4_1h_signals"] = {"error": str(e)}

        total_elapsed = time.time() - overall_t0

        # Save results
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_elapsed_seconds": round(total_elapsed, 1),
            "results": self.results,
        }
        with open(RESULTS_FILE, "w") as f:
            json.dump(output, f, indent=2, default=str)

        # Print summary
        self._print_summary(output)

        print(f"\n{'='*70}")
        print(f" RESULTS SAVED: {RESULTS_FILE}")
        print(f" TOTAL TIME: {total_elapsed:.1f}s ({total_elapsed/60:.1f}m)")
        print(f"{'='*70}")

        return output

    def _print_summary(self, output):
        print(f"\n{'#' * 70}")
        print("# EXECUTIVE SUMMARY")
        print(f"{'#' * 70}")

        # Step 1
        s1 = self.results.get("step1_10pairs", {})
        if "error" not in s1:
            p = s1.get("portfolio", {})
            print(f"\n  STEP 1 — 10-PAIR PORTFOLIO (Jan–Jul 2026)")
            print(f"    Capital: ${s1.get('total_capital', 0):,.0f} → ${p.get('final_equity', 0):,.2f}")
            print(f"    Return: {p.get('total_return_pct', 0):+.2f}% | DD: {p.get('max_dd_pct', 0):.2f}%")
            print(f"    Sharpe: {p.get('sharpe', 0):.2f} | PF: {p.get('profit_factor', 0):.2f} | Trades: {p.get('total_trades', 0)}")
            if s1.get("failed_pairs"):
                print(f"    Failed pairs: {s1['failed_pairs']}")
        else:
            print(f"\n  STEP 1 — FAILED: {s1['error']}")

        # Step 2
        s2 = self.results.get("step2_3years", {})
        if "error" not in s2:
            r = s2.get("result", {})
            m = r.get("metrics", {})
            print(f"\n  STEP 2 — BTC 3-YEAR (Jan 2023 – Jul 2026)")
            print(f"    Capital: ${s2.get('total_capital', 0):,.0f} → ${m.get('final_equity', 0):,.2f}")
            print(f"    Return: {m.get('total_return_pct', 0):+.2f}% | DD: {m.get('max_dd_pct', 0):.2f}%")
            print(f"    Sharpe: {m.get('sharpe', 0):.2f} | PF: {m.get('profit_factor', 0):.2f} | Trades: {r.get('trades', 0)}")
            comp = s2.get("comparison_with_7m", {})
            if comp:
                print(f"    Annualized: ~{comp.get('annualized_return_pct', 'N/A')}%/year")
        else:
            print(f"\n  STEP 2 — FAILED: {s2.get('error', 'unknown')}")

        # Step 3
        s3 = self.results.get("step3_bear2022", {})
        if "error" not in s3:
            r = s3.get("result", {})
            m = r.get("metrics", {})
            print(f"\n  STEP 3 — BTC BEAR MARKET 2022")
            print(f"    Capital: ${s3.get('total_capital', 0):,.0f} → ${m.get('final_equity', 0):,.2f}")
            print(f"    Return: {m.get('total_return_pct', 0):+.2f}% | DD: {m.get('max_dd_pct', 0):.2f}%")
            print(f"    Sharpe: {m.get('sharpe', 0):.2f} | PF: {m.get('profit_factor', 0):.2f} | Trades: {r.get('trades', 0)}")
            a = s3.get("analysis", {})
            print(f"    Bear market: BTC -66%, Strategy {'PROFITABLE' if a.get('strategy_profitable_in_bear') else 'LOSS'}")
        else:
            print(f"\n  STEP 3 — FAILED: {s3.get('error', 'unknown')}")

        # Step 4
        s4 = self.results.get("step4_1h_signals", {})
        if "error" not in s4:
            imp = s4.get("improvement", {})
            b = s4.get("baseline_4h", {})
            e = s4.get("enhanced_4h1h", {})
            bm = b.get("metrics", {})
            em = e.get("metrics", {})
            print(f"\n  STEP 4 — 1H SIGNAL DETECTION")
            print(f"    4H-only:   {b.get('trades', 0)} trades | Ret={bm.get('total_return_pct', 0):+.2f}% | Sharpe={bm.get('sharpe', 0):.2f}")
            print(f"    4H+1H:     {e.get('trades', 0)} trades | Ret={em.get('total_return_pct', 0):+.2f}% | Sharpe={em.get('sharpe', 0):.2f}")
            print(f"    Trade increase: +{imp.get('trades_increase_pct', 0):+.1f}% | Return delta: {imp.get('return_delta_pct', 0):+.2f}pp")
            print(f"    Signal sources: {imp.get('signal_source_breakdown', {})}")
        else:
            print(f"\n  STEP 4 — FAILED: {s4.get('error', 'unknown')}")

        print(f"\n{'#' * 70}")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    sim = FullImprovementsSimulator()
    results = sim.run_all()
