"""
Walk-Forward Scenario Comparison — Multi-Pair Portfolio
=======================================================

Tests 7 scenarios varying number of pairs and risk % on the same period
(Jan 30 - Jul 29, 2026). Uses Institutional Engine V2.

Scenarios:
  A: 4 pairs (BTC,ETH,SOL,BNB), risk=1%
  B: 7 pairs (+AVAX,LINK,DOT), risk=1%
  C: 10 pairs (+ADA,XRP,POL), risk=1%
  D: 4 pairs, risk=1.5%
  E: 4 pairs, risk=2%
  F: 4 pairs, risk=2.5%
  G: 7 pairs, risk=1.5%

NO lookahead bias — processes candles sequentially.
"""

import sys
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import (
    choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, detect_market_phase
)
from engine.drawdown_manager import DrawdownManager
from engine.parameter_adapter import ParameterAdapter

# ============================================================
# CONFIGURATION
# ============================================================

TOTAL_CAPITAL = 4450.0
MAX_RISK_PCT = 0.025
LEVERAGE = 10
OUTPUT_DIR = Path(__file__).parent / "backtest_results"
CACHE_DIR = OUTPUT_DIR / "cache"

COMMISSION_MAKER = 0.00018
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1

TARGET_START = datetime(2026, 1, 30, tzinfo=timezone.utc)
TARGET_END = datetime(2026, 7, 29, tzinfo=timezone.utc)

# PAIR_CONFIGS from backtest_walkforward.py + backtest_1year_10pairs.py
PAIR_CONFIGS = {
    "BTCUSDT": {"supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 74.0, "fibonacci_days": 30, "tp_ratio": 1.3, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "ETHUSDT": {"supertrend_multiplier": 2.6, "choppiness_neutral_threshold": 72.0, "fibonacci_days": 30, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "SOLUSDT": {"supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 70.0, "fibonacci_days": 90, "tp_ratio": 2.0, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "XRPUSDT": {"supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 60, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "BNBUSDT": {"supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 30, "tp_ratio": 1.6, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "DOTUSDT": {"supertrend_multiplier": 2.5, "choppiness_neutral_threshold": 70.0, "fibonacci_days": 45, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "AVAXUSDT": {"supertrend_multiplier": 2.4, "choppiness_neutral_threshold": 65.0, "fibonacci_days": 60, "tp_ratio": 2.0, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "LINKUSDT": {"supertrend_multiplier": 2.6, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 45, "tp_ratio": 1.9, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "ADAUSDT": {"supertrend_multiplier": 2.5, "choppiness_neutral_threshold": 68.0, "fibonacci_days": 60, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
    "POLUSDT": {"supertrend_multiplier": 2.4, "choppiness_neutral_threshold": 65.0, "fibonacci_days": 45, "tp_ratio": 1.8, "atr_trail_mult": 2.0, "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50, "max_sl_pct": 0.018},
}

# Scenario definitions
SCENARIOS = {
    "A": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], "risk_pct": 0.01, "label": "4p 1% risk (baseline)"},
    "B": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"], "risk_pct": 0.01, "label": "7p 1% risk"},
    "C": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "ADAUSDT", "XRPUSDT", "POLUSDT"], "risk_pct": 0.01, "label": "10p 1% risk"},
    "D": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], "risk_pct": 0.015, "label": "4p 1.5% risk"},
    "E": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], "risk_pct": 0.02, "label": "4p 2% risk"},
    "F": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"], "risk_pct": 0.025, "label": "4p 2.5% risk"},
    "G": {"pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT"], "risk_pct": 0.015, "label": "7p 1.5% risk"},
}


# ============================================================
# DATA LOADER (cache only)
# ============================================================

def load_cached_data(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Load parquet data from cache directory. Tries exact match, nearby dates, then any superset."""
    primary = CACHE_DIR / f"{symbol}_{interval}_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.parquet"
    if primary.exists():
        return pd.read_parquet(primary)

    # Try nearby date ranges (±1 day)
    for ds in [start, start + timedelta(days=1), start - timedelta(days=1)]:
        for de in [end, end + timedelta(days=1), end - timedelta(days=1)]:
            f = CACHE_DIR / f"{symbol}_{interval}_{ds.strftime('%Y%m%d')}_{de.strftime('%Y%m%d')}.parquet"
            if f.exists():
                return pd.read_parquet(f)

    # Try any cache file that contains our date range (superset)
    pattern = f"{symbol}_{interval}_*.parquet"
    for f in sorted(CACHE_DIR.glob(pattern)):
        try:
            parts = f.stem.split("_")
            file_start = datetime.strptime(parts[-2], "%Y%m%d")
            file_end = datetime.strptime(parts[-1], "%Y%m%d")
            file_start_utc = file_start.replace(tzinfo=timezone.utc)
            file_end_utc = file_end.replace(tzinfo=timezone.utc)
            if file_start_utc <= start and file_end_utc >= end:
                return pd.read_parquet(f)
        except (ValueError, IndexError):
            continue

    return pd.DataFrame()


# ============================================================
# TRADE DATACLASS
# ============================================================

@dataclass
class Trade:
    pair: str
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


# ============================================================
# PER-PAIR SIMULATOR (exact copy from backtest_walkforward.py)
# ============================================================

class PairSimulator:
    """Walk-forward simulator for a single pair."""

    def __init__(self, symbol: str, capital: float, config: Dict, risk_pct: float):
        self.symbol = symbol
        self.initial_capital = capital
        self.equity = capital
        self.peak_equity = capital
        self.config = config
        self.risk_pct = risk_pct
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = [capital]
        self.open_pos: Optional[Dict] = None
        self.dd_mgr = DrawdownManager(initial_equity=capital)
        self.dd_mgr.reset(capital)
        self.param_adapter = ParameterAdapter()
        self.param_adapter.reset()
        self.last_signal_bar = None

    def process_candle(self, i: int, bar_time, bar_close: float,
                       bar_high: float, bar_low: float,
                       df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                       df_4h: pd.DataFrame) -> bool:
        """Process a single 15m candle. Returns True if trade closed."""
        if self.open_pos is not None:
            result = self._manage_position(bar_high, bar_low, bar_close)
            if result is not None:
                self.equity += result["pnl"]
                self.peak_equity = max(self.peak_equity, self.equity)
                self.dd_mgr.update(self.equity)
                trade_score = self.open_pos.get("score", 0)
                self.trades.append(Trade(
                    pair=self.symbol, entry_time=self.open_pos["entry_time"],
                    exit_time=bar_time, direction=self.open_pos["direction"],
                    entry_price=self.open_pos["entry_fill"], exit_price=result["exit_price"],
                    size=self.open_pos["size"], pnl=result["pnl"],
                    exit_reason=result["reason"], bars_held=i - self.open_pos["entry_idx"],
                    score=trade_score, tp1_hit=self.open_pos.get("tp1_hit", False),
                    partial_pnl=result.get("partial_pnl", 0.0),
                ))
                self.open_pos = None
                self.param_adapter.record_trade({"net_pnl": result["pnl"], "score": trade_score})
                self.equity_curve.append(self.equity)
                return True
            self.equity_curve.append(self.equity)
            return False

        config = self.config
        mask_4h = df_4h.index <= bar_time
        if mask_4h.sum() < 3:
            self.equity_curve.append(self.equity)
            return False

        klines_4h = []
        for j in range(max(0, mask_4h.sum() - 3), mask_4h.sum()):
            idx = df_4h.index[j]
            klines_4h.append({
                "open": float(df_4h.loc[idx, "open"]),
                "high": float(df_4h.loc[idx, "high"]),
                "low": float(df_4h.loc[idx, "low"]),
                "close": float(df_4h.loc[idx, "close"]),
            })

        current_4h_bar = df_4h.index[mask_4h.sum() - 1]
        if current_4h_bar == self.last_signal_bar:
            self.equity_curve.append(self.equity)
            return False

        signal = None
        if is_bearish_engulfing(klines_4h):
            signal = "SHORT"
        elif is_bullish_engulfing(klines_4h):
            signal = "LONG"

        if signal is None:
            self.equity_curve.append(self.equity)
            return False

        self.last_signal_bar = current_4h_bar

        mask_15 = df_15m.index <= bar_time
        if mask_15.sum() < 30:
            self.equity_curve.append(self.equity)
            return False

        data_15 = df_15m[mask_15]
        c_15 = data_15["close"].values[-30:]
        h_15 = data_15["high"].values[-30:]
        l_15 = data_15["low"].values[-30:]

        ci = choppiness_index(h_15, l_15, c_15)
        if ci is not None and ci > config["choppiness_neutral_threshold"]:
            self.equity_curve.append(self.equity)
            return False

        mask_1h = df_1h.index <= bar_time
        if mask_1h.sum() < 30:
            self.equity_curve.append(self.equity)
            return False

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
             for c, h, l, v, o in zip(
                data_1h["close"].values, data_1h["high"].values,
                data_1h["low"].values, data_1h["volume"].values,
                data_1h["open"].values
            )], ind_1h
        )
        if phase in ("ranging", "neutral", "unknown"):
            self.equity_curve.append(self.equity)
            return False

        wr_4h = williams_r(
            np.array([float(k["high"]) for k in klines_4h]),
            np.array([float(k["low"]) for k in klines_4h]),
            np.array([float(k["close"]) for k in klines_4h])
        )
        body_ratio_4h = abs(float(klines_4h[-1]["close"]) - float(klines_4h[-1]["open"])) / \
                        max(float(klines_4h[-1]["high"]) - float(klines_4h[-1]["low"]), 0.001)

        score = 55
        if body_ratio_4h > 0.4:
            score += 12
        if ci is not None and ci < 60:
            score += 8
        if ci is not None and ci < 50:
            score += 5
        if wr_4h is not None and -80 < wr_4h < -20:
            score += 5
        if signal == "LONG" and c_15[-1] > np.mean(c_15[-20:]):
            score += 3
        elif signal == "SHORT" and c_15[-1] < np.mean(c_15[-20:]):
            score += 3

        min_score = self.param_adapter.params.get("min_score", config["min_score"])
        if score < min_score:
            self.equity_curve.append(self.equity)
            return False

        dd_params = self.dd_mgr.get_risk_params()
        dd_mult = dd_params[0]
        dd_min_score = dd_params[1]
        if dd_mult == 0 or score < dd_min_score:
            self.equity_curve.append(self.equity)
            return False

        strat_mult = self.param_adapter.get_risk_multiplier()

        entry = bar_close
        if signal == "SHORT":
            sl = float(data_15["high"].values[-10:].max()) * 1.002
        else:
            sl = float(data_15["low"].values[-10:].min()) * 0.998

        sl_pct = abs(entry - sl) / entry
        max_sl = config["max_sl_pct"]
        if sl_pct > max_sl:
            sl = entry * (1 - max_sl) if signal == "LONG" else entry * (1 + max_sl)
            sl_pct = max_sl
        if sl_pct <= 0:
            self.equity_curve.append(self.equity)
            return False

        tp_ratio = self.param_adapter.params.get("tp_ratio", config["tp_ratio"])
        sl_dist = abs(entry - sl)
        if signal == "LONG":
            tp1 = entry + tp_ratio * sl_dist
            tp2 = entry + 2 * tp_ratio * sl_dist
        else:
            tp1 = entry - tp_ratio * sl_dist
            tp2 = entry - 2 * tp_ratio * sl_dist

        risk_usd = self.equity * self.risk_pct * dd_mult * strat_mult
        risk_usd = min(risk_usd, self.equity * MAX_RISK_PCT)
        notional = risk_usd / sl_pct
        size = notional / entry

        if size <= 0 or notional < 1:
            self.equity_curve.append(self.equity)
            return False

        atr_5m = atr(
            data_15["high"].values[-14:],
            data_15["low"].values[-14:],
            data_15["close"].values[-14:]
        ) or entry * 0.001
        slip = SLIP_ATR_MULT * atr_5m * 0.5
        if signal == "LONG":
            entry_fill = entry * (1 + SPREAD) + slip
        else:
            entry_fill = entry * (1 - SPREAD) - slip

        atr_trail = self.param_adapter.params.get("atr_trail_mult", config["atr_trail_mult"])
        partial_pct = self.param_adapter.params.get("partial_exit_pct", config["partial_exit_pct"])
        time_exit = self.param_adapter.params.get("time_exit_bars", config["time_exit_bars"])

        self.open_pos = {
            "direction": signal, "entry_fill": entry_fill, "entry_time": bar_time,
            "entry_idx": i, "size": size, "size_remaining": size, "stop_loss": sl,
            "tp1": tp1, "tp2": tp2, "tp1_hit": False, "breakeven_active": False,
            "trailing_stop": None, "trail_high": entry_fill, "trail_low": entry_fill,
            "bars_held": 0, "score": score, "atr_at_entry": atr_1h_val or entry * 0.005,
            "atr_trail_mult": atr_trail, "partial_exit_pct": partial_pct,
            "time_exit_bars": time_exit, "total_realized": 0.0,
        }
        self.equity_curve.append(self.equity)
        return False

    def _manage_position(self, bar_high: float, bar_low: float, bar_close: float) -> Optional[Dict]:
        pos = self.open_pos
        pos["bars_held"] += 1
        d = 1 if pos["direction"] == "LONG" else -1

        if d == 1:
            pos["trail_high"] = max(pos["trail_high"], bar_high)
        else:
            pos["trail_low"] = min(pos["trail_low"], bar_low)

        atr_now = pos["atr_at_entry"]

        if (d == 1 and bar_low <= pos["stop_loss"]) or (d == -1 and bar_high >= pos["stop_loss"]):
            return self._close_position(pos, pos["stop_loss"], "stop_loss")

        if pos["trailing_stop"] is not None:
            if (d == 1 and bar_low <= pos["trailing_stop"]) or (d == -1 and bar_high >= pos["trailing_stop"]):
                return self._close_position(pos, pos["trailing_stop"], "trailing_stop")

        if not pos["tp1_hit"]:
            tp1_hit = (d == 1 and bar_high >= pos["tp1"]) or (d == -1 and bar_low <= pos["tp1"])
            if tp1_hit:
                pos["tp1_hit"] = True
                spread_adj = pos["entry_fill"] * SPREAD
                pos["stop_loss"] = pos["entry_fill"] + spread_adj if d == 1 else pos["entry_fill"] - spread_adj
                pos["breakeven_active"] = True
                trail_dist = pos["atr_trail_mult"] * atr_now
                pos["trailing_stop"] = pos["trail_high"] - trail_dist if d == 1 else pos["trail_low"] + trail_dist
                partial_pnl = self._calc_partial_pnl(pos, pos["tp1"], pos["partial_exit_pct"])
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
            return self._close_position(pos, pos["tp2"], "take_profit_2")

        if pos["bars_held"] >= pos["time_exit_bars"]:
            return self._close_position(pos, bar_close, "time_exit")

        return None

    def _close_position(self, pos: Dict, exit_price: float, reason: str) -> Dict:
        d = 1 if pos["direction"] == "LONG" else -1
        slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        if d == 1:
            gross = pos["size_remaining"] * (exit_fill - pos["entry_fill"])
        else:
            gross = pos["size_remaining"] * (pos["entry_fill"] - exit_fill)

        comm = pos["size_remaining"] * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
        net = gross - comm + pos["total_realized"]
        return {"exit_price": exit_fill, "reason": reason, "pnl": net, "partial_pnl": pos["total_realized"]}

    def _calc_partial_pnl(self, pos: Dict, exit_price: float, ratio: float) -> float:
        d = 1 if pos["direction"] == "LONG" else -1
        slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip
        close_size = pos["size"] * ratio
        if d == 1:
            gross = close_size * (exit_fill - pos["entry_fill"])
        else:
            gross = close_size * (pos["entry_fill"] - exit_fill)
        comm = close_size * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
        return gross - comm

    def get_metrics(self) -> Dict[str, Any]:
        trades = self.trades
        equity_curve = self.equity_curve

        if not trades:
            return {"symbol": self.symbol, "total_trades": 0, "metrics": _empty_metrics(),
                    "equity_curve": [], "trades": []}

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
            "symbol": self.symbol,
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
                "wins": len(wins), "losses": len(losses),
                "exit_reasons": reasons,
                "final_equity": round(float(eq[-1]), 2),
            },
            "equity_curve": [round(float(x), 2) for x in equity_curve[::10]],
            "trades": [asdict(t) for t in trades],
        }


def _empty_metrics():
    return {"total_return": 0, "total_return_pct": 0, "win_rate": 0, "profit_factor": 0,
            "sharpe": 0, "sortino": 0, "max_dd_pct": 0, "calmar": 0, "avg_trade": 0,
            "avg_win": 0, "avg_loss": 0, "avg_bars_held": 0, "wins": 0, "losses": 0,
            "exit_reasons": {}, "final_equity": 0}


# ============================================================
# SCENARIO RUNNER
# ============================================================

def run_scenario(scenario_name: str, pairs: List[str], risk_pct: float,
                 data_cache: Dict[str, Dict[str, pd.DataFrame]]) -> Dict:
    """Run one scenario and return results."""
    capital_per_pair = TOTAL_CAPITAL / len(pairs)
    simulators = {}
    for pair in pairs:
        simulators[pair] = PairSimulator(pair, capital_per_pair, PAIR_CONFIGS[pair], risk_pct)

    # Find common time range
    common_start = max(data_cache[p]["15m"].index[0] for p in pairs)
    common_end = min(data_cache[p]["15m"].index[-1] for p in pairs)

    ref_15m = data_cache[pairs[0]]["15m"]
    total_bars = len(ref_15m)

    portfolio_equity_curve = []
    t0 = time.time()

    for i in range(50, total_bars):
        bar_time = ref_15m.index[i]

        for pair in pairs:
            sim = simulators[pair]
            pair_15m = data_cache[pair]["15m"]
            pair_1h = data_cache[pair]["1h"]
            pair_4h = data_cache[pair]["4h"]

            if bar_time not in pair_15m.index:
                mask = pair_15m.index <= bar_time
                if mask.sum() == 0:
                    continue
                idx = mask.sum() - 1
            else:
                idx = pair_15m.index.get_loc(bar_time)

            sim.process_candle(
                idx, bar_time,
                float(pair_15m["close"].iloc[idx]),
                float(pair_15m["high"].iloc[idx]),
                float(pair_15m["low"].iloc[idx]),
                pair_15m, pair_1h, pair_4h
            )

        portfolio_eq = sum(simulators[p].equity for p in pairs)
        portfolio_equity_curve.append(portfolio_eq)

    elapsed = time.time() - t0

    # Collect trades
    all_trades = []
    for pair in pairs:
        all_trades.extend(simulators[pair].trades)
    all_trades.sort(key=lambda t: t.entry_time)

    # Portfolio metrics
    per_pair = {}
    for pair in pairs:
        per_pair[pair] = simulators[pair].get_metrics()

    total_final = sum(per_pair[p]["metrics"]["final_equity"] for p in per_pair)
    total_return = total_final - TOTAL_CAPITAL
    total_return_pct = total_return / TOTAL_CAPITAL * 100

    eq = np.array(portfolio_equity_curve) if portfolio_equity_curve else np.array([TOTAL_CAPITAL])
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1) * 100
    portfolio_max_dd = float(dd.min())

    total_trades = len(all_trades)
    total_wins = sum(1 for t in all_trades if t.pnl > 0)
    wr = total_wins / total_trades * 100 if total_trades > 0 else 0

    pnls = [t.pnl for t in all_trades]
    wins_pnl = [p for p in pnls if p > 0]
    losses_pnl = [p for p in pnls if p <= 0]
    gross_win = sum(wins_pnl) if wins_pnl else 0
    gross_loss = -sum(losses_pnl) if losses_pnl else 0.001
    pf = gross_win / gross_loss if gross_loss > 0 else 99.0

    if len(eq) > 1:
        returns = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1)
        std = np.std(returns)
        sharpe = float(np.mean(returns) / std * np.sqrt(252 * 24 * 4)) if std > 0 else 0
    else:
        sharpe = 0

    return {
        "scenario": scenario_name,
        "pairs": len(pairs),
        "risk_pct": risk_pct,
        "capital_per_pair": round(capital_per_pair, 2),
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 2),
        "max_dd_pct": round(portfolio_max_dd, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "total_trades": total_trades,
        "final_equity": round(total_final, 2),
        "elapsed_sec": round(elapsed, 1),
        "per_pair": {p: per_pair[p]["metrics"] for p in pairs},
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 75)
    print(" SCENARIO COMPARISON — INSTITUTIONAL ENGINE V2")
    print(f" Period: {TARGET_START.date()} → {TARGET_END.date()} (7 months)")
    print(f" Capital: ${TOTAL_CAPITAL:,.2f} | Scenarios: 7")
    print("=" * 75)

    # 1. Load all data once
    all_pairs_needed = set()
    for s in SCENARIOS.values():
        all_pairs_needed.update(s["pairs"])

    print(f"\n[1/2] Loading cached data for {len(all_pairs_needed)} pairs...")
    data_cache = {}
    for pair in sorted(all_pairs_needed):
        print(f"  {pair}:")
        data = {}
        ok = True
        for tf in ["15m", "1h", "4h"]:
            df = load_cached_data(pair, tf, TARGET_START, TARGET_END)
            if df.empty:
                print(f"    {tf}: NOT FOUND in cache")
                ok = False
            else:
                data[tf] = df
        if ok:
            # Filter to target date range
            for tf in data:
                data[tf] = data[tf][(data[tf].index >= TARGET_START) & (data[tf].index <= TARGET_END)]
            data_cache[pair] = data
            print(f"    Loaded: 15m={len(data['15m'])} bars, 1h={len(data['1h'])}, 4h={len(data['4h'])}")
        else:
            print(f"    SKIPPED — missing timeframes")

    available = [p for p in sorted(all_pairs_needed) if p in data_cache]
    print(f"\n  Available: {len(available)}/{len(all_pairs_needed)} pairs")

    # 2. Run all scenarios
    print(f"\n[2/2] Running {len(SCENARIOS)} scenarios...\n")
    results = []
    for name in ["A", "B", "C", "D", "E", "F", "G"]:
        sc = SCENARIOS[name]
        # Check all pairs for this scenario are available
        missing = [p for p in sc["pairs"] if p not in data_cache]
        if missing:
            print(f"  Scenario {name}: SKIPPED (missing: {', '.join(missing)})")
            continue

        print(f"  Running Scenario {name}: {sc['label']}...")
        r = run_scenario(name, sc["pairs"], sc["risk_pct"], data_cache)
        results.append(r)
        print(f"    → Return: {r['total_return_pct']:+.2f}%  DD: {r['max_dd_pct']:.2f}%  "
              f"Sharpe: {r['sharpe']:.2f}  Trades: {r['total_trades']}  ({r['elapsed_sec']}s)\n")

    # 3. Print comparison table
    print("\n" + "=" * 105)
    print(" SCENARIO COMPARISON TABLE")
    print("=" * 105)
    print(f"  {'Scn':>3} {'Pairs':>5} {'Risk%':>6} {'$/Pair':>9} {'Return$':>10} {'Ret%':>8} "
          f"{'MaxDD%':>8} {'Sharpe':>7} {'WR%':>7} {'PF':>6} {'Trades':>7}")
    print(f"  {'─' * 98}")

    for r in results:
        print(f"  {r['scenario']:>3} {r['pairs']:>5} {r['risk_pct']*100:>5.1f}% "
              f"${r['capital_per_pair']:>8.2f} ${r['total_return']:>+9.2f} "
              f"{r['total_return_pct']:>+7.2f}% {r['max_dd_pct']:>7.2f}% "
              f"{r['sharpe']:>6.2f} {r['win_rate']:>6.1f}% {r['profit_factor']:>5.2f} "
              f"{r['total_trades']:>6}")

    print(f"  {'─' * 98}")

    # 4. Per-pair breakdown for best scenario
    if results:
        best = max(results, key=lambda x: x["total_return_pct"])
        print(f"\n  BEST SCENARIO: {best['scenario']} — {SCENARIOS[best['scenario']]['label']}")
        print(f"  Return: {best['total_return_pct']:+.2f}%  DD: {best['max_dd_pct']:.2f}%  "
              f"Sharpe: {best['sharpe']:.2f}  PF: {best['profit_factor']:.2f}")
        print(f"\n  Per-pair breakdown (Scenario {best['scenario']}):")
        print(f"  {'Pair':<12} {'Return$':>10} {'Ret%':>8} {'DD%':>8} {'Sharpe':>8} {'Final$':>10}")
        print(f"  {'─' * 56}")
        for pair, m in best["per_pair"].items():
            print(f"  {pair:<12} ${m['total_return']:>+9.2f} {m['total_return_pct']:>+7.2f}% "
                  f"{m['max_dd_pct']:>7.2f}% {m['sharpe']:>7.2f} ${m['final_equity']:>9.2f}")

    # 5. Risk scaling analysis (same pairs, different risk)
    print(f"\n  RISK SCALING (4 pairs: BTC,ETH,SOL,BNB):")
    print(f"  {'Risk%':>7} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>7} {'PF':>6} {'Trades':>7}")
    print(f"  {'─' * 44}")
    for r in results:
        if r["pairs"] == 4:
            print(f"  {r['risk_pct']*100:>6.1f}% {r['total_return_pct']:>+8.2f}% "
                  f"{r['max_dd_pct']:>7.2f}% {r['sharpe']:>6.2f} {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>6}")

    # 6. Diversification analysis (same risk, different pairs)
    print(f"\n  DIVERSIFICATION (risk=1%):")
    print(f"  {'Pairs':>5} {'Return%':>9} {'MaxDD%':>8} {'Sharpe':>7} {'PF':>6} {'Trades':>7}")
    print(f"  {'─' * 44}")
    for r in results:
        if r["risk_pct"] == 0.01:
            print(f"  {r['pairs']:>5} {r['total_return_pct']:>+8.2f}% "
                  f"{r['max_dd_pct']:>7.2f}% {r['sharpe']:>6.2f} {r['profit_factor']:>5.2f} "
                  f"{r['total_trades']:>6}")

    # 7. Save results
    output_file = OUTPUT_DIR / "scenario_comparison_results.json"
    with open(output_file, "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(),
                    "period": f"{TARGET_START.date()} to {TARGET_END.date()}",
                    "scenarios": results}, f, indent=2, default=str)
    print(f"\n  Results saved: {output_file}")

    print("=" * 105)
    return results


if __name__ == "__main__":
    main()
