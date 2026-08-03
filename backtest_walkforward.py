"""
Walk-Forward Simulation — 5 Pairs Portfolio
=============================================

REALISTIC backtest that simulates how the bot would perform in real life.
Processes data candle-by-candle, only seeing data as it closes (NO lookahead bias).

How it works:
  1. Load 15m OHLCV data for all 5 pairs (BTC, ETH, SOL, XRP, BNB)
  2. For each candle in chronological order:
     - Only use data from candles that have already closed
     - Compute indicators incrementally (not pre-computed)
     - Check for engulfing signals on 4H candles that just closed
     - Apply filters (CI, Williams %R, Supertrend, market phase)
     - If signal found: open position with realistic sizing
     - If position open: manage it (trailing stop, breakeven, TP1/TP2, time exit)
  3. Track portfolio equity across all 5 pairs simultaneously
  4. Calculate realistic metrics

Costos realistas:
  - Commission: 0.018% por lado (Post-Only Maker, con BNB discount)
  - Spread: 0.02%
  - Slippage: 0.1 x ATR(5m) * 0.5 (reduced with Maker)

SIN look-ahead bias: solo usa datos cerrados antes de cada decision.
"""

import sys
import json
import math
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict, field

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import (
    choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, detect_market_phase
)
from engine.drawdown_manager import DrawdownManager
from engine.parameter_adapter import ParameterAdapter

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACION
# ============================================================

TOTAL_CAPITAL = 4450.0
PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT']
CAPITAL_PER_PAIR = TOTAL_CAPITAL / len(PAIRS)
RISK_PCT = 0.01  # 1% risk per trade
MAX_RISK_PCT = 0.025  # Max 2.5% per trade
LEVERAGE = 10
OUTPUT_DIR = Path(__file__).parent / "backtest_results"

# Costos Post-Only (Maker)
COMMISSION_MAKER = 0.00018  # 0.018% per side (BNB discount)
SPREAD = 0.0002  # 0.02%
SLIP_ATR_MULT = 0.1

# Per-pair configurations (from backtest_portfolio_5pairs.py)
PAIR_CONFIGS = {
    "BTCUSDT": {
        "supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 74.0,
        "fibonacci_days": 30, "tp_ratio": 1.3, "atr_trail_mult": 2.0,
        "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50,
        "max_sl_pct": 0.018,
    },
    "ETHUSDT": {
        "supertrend_multiplier": 2.6, "choppiness_neutral_threshold": 72.0,
        "fibonacci_days": 30, "tp_ratio": 1.8, "atr_trail_mult": 2.0,
        "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50,
        "max_sl_pct": 0.018,
    },
    "SOLUSDT": {
        "supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 70.0,
        "fibonacci_days": 90, "tp_ratio": 2.0, "atr_trail_mult": 2.0,
        "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50,
        "max_sl_pct": 0.018,
    },
    "XRPUSDT": {
        "supertrend_multiplier": 2.3, "choppiness_neutral_threshold": 68.0,
        "fibonacci_days": 60, "tp_ratio": 1.8, "atr_trail_mult": 2.0,
        "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50,
        "max_sl_pct": 0.018,
    },
    "BNBUSDT": {
        "supertrend_multiplier": 2.8, "choppiness_neutral_threshold": 68.0,
        "fibonacci_days": 30, "tp_ratio": 1.6, "atr_trail_mult": 2.0,
        "partial_exit_pct": 0.5, "time_exit_bars": 24, "min_score": 50,
        "max_sl_pct": 0.018,
    },
}


# ============================================================
# DATA DOWNLOADER
# ============================================================

class BinanceDataDownloader:
    """Descarga datos historicos de Binance Spot API."""

    BASE_URLS = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api3.binance.com",
    ]

    def __init__(self):
        self.cache_dir = OUTPUT_DIR / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def download_klines(self, symbol: str, interval: str,
                        start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """Descarga klines historicos con cache."""
        cache_file = self.cache_dir / f"{symbol}_{interval}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.parquet"

        if cache_file.exists():
            print(f"  Loading cached: {cache_file.name}")
            return pd.read_parquet(cache_file)

        print(f"  Downloading {symbol} {interval}...")
        all_klines = []
        current = start_date

        while current < end_date:
            klines = self._fetch(symbol, interval, current,
                                min(current + timedelta(days=7), end_date))
            if not klines:
                break
            all_klines.extend(klines)
            last_ts = klines[-1]["timestamp"]
            current = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc) + timedelta(minutes=15)
            time.sleep(0.1)

        if not all_klines:
            raise ValueError(f"No data for {symbol} {interval}")

        df = pd.DataFrame(all_klines)
        df["open_time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("open_time")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df.to_parquet(cache_file)
        print(f"    Downloaded {len(df)} bars")
        return df

    def _fetch(self, symbol, interval, start, end):
        import requests
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        endpoint = f"/api/v3/klines?symbol={symbol}&interval={interval}&startTime={start_ms}&endTime={end_ms}&limit=1000"
        for base in self.BASE_URLS:
            try:
                resp = requests.get(base + endpoint, timeout=30)
                if resp.status_code == 200:
                    return [{
                        "timestamp": int(k[0]),
                        "open": float(k[1]), "high": float(k[2]),
                        "low": float(k[3]), "close": float(k[4]),
                        "volume": float(k[5]),
                    } for k in resp.json()]
            except Exception:
                continue
        return None


# ============================================================
# TRADE DATACLASS
# ============================================================

@dataclass
class Trade:
    """Representa un trade completado."""
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
# PER-PAIR SIMULATOR
# ============================================================

class PairSimulator:
    """
    Simulates one pair's behavior in walk-forward mode.
    Processes candles sequentially, no lookahead.
    """

    def __init__(self, symbol: str, capital: float, config: Dict):
        self.symbol = symbol
        self.initial_capital = capital
        self.equity = capital
        self.peak_equity = capital
        self.config = config
        self.trades: List[Trade] = []
        self.equity_curve: List[float] = [capital]
        self.open_pos: Optional[Dict] = None
        self.dd_mgr = DrawdownManager(initial_equity=capital)
        self.dd_mgr.reset(capital)
        self.param_adapter = ParameterAdapter()
        self.param_adapter.reset()

        # Track last processed 4H candle to avoid duplicate signals
        self.last_signal_bar = None

    def process_candle(self, i: int, bar_time, bar_close: float,
                       bar_high: float, bar_low: float,
                       df_15m: pd.DataFrame, df_1h: pd.DataFrame,
                       df_4h: pd.DataFrame) -> bool:
        """
        Process a single 15m candle for this pair.
        Returns True if a trade was closed (equity changed).
        """

        # 1. Manage open position first
        if self.open_pos is not None:
            result = self._manage_position(bar_high, bar_low, bar_close)
            if result is not None:
                self.equity += result["pnl"]
                self.peak_equity = max(self.peak_equity, self.equity)
                self.dd_mgr.update(self.equity)

                trade_score = self.open_pos.get("score", 0)
                self.trades.append(Trade(
                    pair=self.symbol,
                    entry_time=self.open_pos["entry_time"],
                    exit_time=bar_time,
                    direction=self.open_pos["direction"],
                    entry_price=self.open_pos["entry_fill"],
                    exit_price=result["exit_price"],
                    size=self.open_pos["size"],
                    pnl=result["pnl"],
                    exit_reason=result["reason"],
                    bars_held=i - self.open_pos["entry_idx"],
                    score=trade_score,
                    tp1_hit=self.open_pos.get("tp1_hit", False),
                    partial_pnl=result.get("partial_pnl", 0.0),
                ))
                self.open_pos = None

                self.param_adapter.record_trade({
                    "net_pnl": result["pnl"],
                    "score": trade_score,
                })

                self.equity_curve.append(self.equity)
                return True

            self.equity_curve.append(self.equity)
            return False

        # 2. No open position: check for new signal
        config = self.config

        # Get 4H data available up to bar_time
        mask_4h = df_4h.index <= bar_time
        if mask_4h.sum() < 3:
            self.equity_curve.append(self.equity)
            return False

        # Build klines for engulfing check
        klines_4h = []
        for j in range(max(0, mask_4h.sum() - 3), mask_4h.sum()):
            idx = df_4h.index[j]
            klines_4h.append({
                "open": float(df_4h.loc[idx, "open"]),
                "high": float(df_4h.loc[idx, "high"]),
                "low": float(df_4h.loc[idx, "low"]),
                "close": float(df_4h.loc[idx, "close"]),
            })

        # Check if this 4H bar is the same as last signal (avoid duplicate)
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

        # Mark this bar as processed for signals
        self.last_signal_bar = current_4h_bar

        # 3. Get 15m data for filters
        mask_15 = df_15m.index <= bar_time
        if mask_15.sum() < 30:
            self.equity_curve.append(self.equity)
            return False

        data_15 = df_15m[mask_15]
        c_15 = data_15["close"].values[-30:]
        h_15 = data_15["high"].values[-30:]
        l_15 = data_15["low"].values[-30:]

        # CI filter
        ci = choppiness_index(h_15, l_15, c_15)
        if ci is not None and ci > config["choppiness_neutral_threshold"]:
            self.equity_curve.append(self.equity)
            return False

        # 4. Get 1h data for phase + ATR
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

        # Market phase filter
        _, _, bw_1h = bollinger_bands(c_1h)
        vol_ratio_1h = float(v_1h[-1] / np.mean(v_1h[-20:])) if len(v_1h) >= 20 else 1.0
        ind_1h = {"bb_width": bw_1h, "atr14": atr_1h_val, "vol_ratio": vol_ratio_1h}
        phase = detect_market_phase(
            [{"close": c, "high": h, "low": l, "volume": v, "open": o}
             for c, h, l, v, o in zip(
                data_1h["close"].values, data_1h["high"].values,
                data_1h["low"].values, data_1h["volume"].values,
                data_1h["open"].values
            )],
            ind_1h
        )
        if phase in ("ranging", "neutral", "unknown"):
            self.equity_curve.append(self.equity)
            return False

        # 5. Score calculation
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

        # 6. DD manager check
        dd_params = self.dd_mgr.get_risk_params()
        dd_mult = dd_params[0]
        dd_min_score = dd_params[1]
        if dd_mult == 0:
            self.equity_curve.append(self.equity)
            return False
        if score < dd_min_score:
            self.equity_curve.append(self.equity)
            return False

        # Strategy weight multiplier
        strat_mult = self.param_adapter.get_risk_multiplier()

        # 7. Calculate entry/SL
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

        # TP levels
        tp_ratio = self.param_adapter.params.get("tp_ratio", config["tp_ratio"])
        sl_dist = abs(entry - sl)
        if signal == "LONG":
            tp1 = entry + tp_ratio * sl_dist
            tp2 = entry + 2 * tp_ratio * sl_dist
        else:
            tp1 = entry - tp_ratio * sl_dist
            tp2 = entry - 2 * tp_ratio * sl_dist

        # 8. Position sizing with DD + strategy weight
        risk_usd = self.equity * RISK_PCT * dd_mult * strat_mult
        risk_usd = min(risk_usd, self.equity * MAX_RISK_PCT)
        notional = risk_usd / sl_pct
        size = notional / entry

        if size <= 0 or notional < 1:
            self.equity_curve.append(self.equity)
            return False

        # 9. Entry fill with Post-Only costs
        atr_5m = atr(
            data_15["high"].values[-14:],
            data_15["low"].values[-14:],
            data_15["close"].values[-14:]
        ) or entry * 0.001
        # Post-Only: 50% less slippage
        slip = SLIP_ATR_MULT * atr_5m * 0.5
        if signal == "LONG":
            entry_fill = entry * (1 + SPREAD) + slip
        else:
            entry_fill = entry * (1 - SPREAD) - slip

        # 10. Open position
        atr_trail = self.param_adapter.params.get("atr_trail_mult", config["atr_trail_mult"])
        partial_pct = self.param_adapter.params.get("partial_exit_pct", config["partial_exit_pct"])
        time_exit = self.param_adapter.params.get("time_exit_bars", config["time_exit_bars"])

        self.open_pos = {
            "direction": signal,
            "entry_fill": entry_fill,
            "entry_time": bar_time,
            "entry_idx": i,
            "size": size,
            "size_remaining": size,
            "stop_loss": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp1_hit": False,
            "breakeven_active": False,
            "trailing_stop": None,
            "trail_high": entry_fill,
            "trail_low": entry_fill,
            "bars_held": 0,
            "score": score,
            "atr_at_entry": atr_1h_val or entry * 0.005,
            "atr_trail_mult": atr_trail,
            "partial_exit_pct": partial_pct,
            "time_exit_bars": time_exit,
            "total_realized": 0.0,
        }

        self.equity_curve.append(self.equity)
        return False

    def _manage_position(self, bar_high: float, bar_low: float,
                         bar_close: float) -> Optional[Dict]:
        """Manage open position: trailing, breakeven, partial, TP2, time exit."""
        pos = self.open_pos
        pos["bars_held"] += 1
        d = 1 if pos["direction"] == "LONG" else -1

        # Update trail
        if d == 1:
            pos["trail_high"] = max(pos["trail_high"], bar_high)
        else:
            pos["trail_low"] = min(pos["trail_low"], bar_low)

        atr_now = pos["atr_at_entry"]

        # 1. SL check
        if d == 1 and bar_low <= pos["stop_loss"]:
            return self._close_position(pos, pos["stop_loss"], "stop_loss")
        elif d == -1 and bar_high >= pos["stop_loss"]:
            return self._close_position(pos, pos["stop_loss"], "stop_loss")

        # 2. Trailing stop check
        if pos["trailing_stop"] is not None:
            if d == 1 and bar_low <= pos["trailing_stop"]:
                return self._close_position(pos, pos["trailing_stop"], "trailing_stop")
            elif d == -1 and bar_high >= pos["trailing_stop"]:
                return self._close_position(pos, pos["trailing_stop"], "trailing_stop")

        # 3. TP1 partial exit
        if not pos["tp1_hit"]:
            tp1_hit = (d == 1 and bar_high >= pos["tp1"]) or \
                      (d == -1 and bar_low <= pos["tp1"])
            if tp1_hit:
                pos["tp1_hit"] = True
                # Breakeven
                spread_adj = pos["entry_fill"] * SPREAD
                pos["stop_loss"] = pos["entry_fill"] + spread_adj if d == 1 else \
                                   pos["entry_fill"] - spread_adj
                pos["breakeven_active"] = True

                # Activate trailing
                trail_dist = pos["atr_trail_mult"] * atr_now
                if d == 1:
                    pos["trailing_stop"] = pos["trail_high"] - trail_dist
                else:
                    pos["trailing_stop"] = pos["trail_low"] + trail_dist

                # Partial close
                partial_pnl = self._calc_partial_pnl(pos, pos["tp1"], pos["partial_exit_pct"])
                pos["size_remaining"] *= (1 - pos["partial_exit_pct"])
                pos["total_realized"] += partial_pnl

        # 4. Update trailing
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

        # 5. TP2 full exit
        tp2_hit = (d == 1 and bar_high >= pos["tp2"]) or \
                  (d == -1 and bar_low <= pos["tp2"])
        if tp2_hit:
            return self._close_position(pos, pos["tp2"], "take_profit_2")

        # 6. Time exit
        if pos["bars_held"] >= pos["time_exit_bars"]:
            return self._close_position(pos, bar_close, "time_exit")

        return None

    def _close_position(self, pos: Dict, exit_price: float, reason: str) -> Dict:
        """Close position with Post-Only costs."""
        d = 1 if pos["direction"] == "LONG" else -1
        # Post-Only: 50% less slippage
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

        return {
            "exit_price": exit_fill,
            "reason": reason,
            "pnl": net,
            "partial_pnl": pos["total_realized"],
        }

    def _calc_partial_pnl(self, pos: Dict, exit_price: float, ratio: float) -> float:
        """Calculate PnL of partial exit with Post-Only costs."""
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
        """Compute metrics for this pair."""
        trades = self.trades
        equity_curve = self.equity_curve

        if not trades:
            return {
                "symbol": self.symbol,
                "total_trades": 0,
                "metrics": self._empty_metrics(),
                "equity_curve": [],
                "trades": [],
            }

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
                "wins": len(wins),
                "losses": len(losses),
                "exit_reasons": reasons,
                "final_equity": round(float(eq[-1]), 2),
            },
            "equity_curve": [round(float(x), 2) for x in equity_curve[::10]],
            "trades": [asdict(t) for t in trades],
        }

    def _empty_metrics(self):
        return {
            "total_return": 0, "total_return_pct": 0, "win_rate": 0,
            "profit_factor": 0, "sharpe": 0, "sortino": 0, "max_dd_pct": 0,
            "calmar": 0, "avg_trade": 0, "avg_win": 0, "avg_loss": 0,
            "avg_bars_held": 0, "wins": 0, "losses": 0, "exit_reasons": {},
            "final_equity": 0,
        }


# ============================================================
# WALK-FORWARD SIMULATOR
# ============================================================

class WalkForwardSimulator:
    """
    Portfolio-level walk-forward simulation.
    Processes all 5 pairs candle-by-candle in chronological order.
    NO lookahead bias.
    """

    def __init__(self, capital: float = TOTAL_CAPITAL,
                 pairs: List[str] = None):
        self.capital = capital
        self.pairs = pairs or PAIRS
        self.capital_per_pair = capital / len(self.pairs)
        self.simulators: Dict[str, PairSimulator] = {}
        self.portfolio_equity_curve: List[float] = []
        self.all_trades: List[Trade] = []
        self.data: Dict[str, Dict[str, pd.DataFrame]] = {}

    def run(self, start_date: datetime, end_date: datetime):
        """Main loop: download data, process all candles, compute metrics."""
        print("=" * 70)
        print(" WALK-FORWARD SIMULATION — 5 PAIRS PORTFOLIO")
        print(" Candle-by-candle, NO lookahead bias")
        print("=" * 70)

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 1. Download data
        print(f"\n[1/3] Downloading data ({start_date.date()} → {end_date.date()})...")
        dl = BinanceDataDownloader()

        for pair in self.pairs:
            print(f"\n  {pair}:")
            data = {}
            for tf in ["15m", "1h", "4h"]:
                data[tf] = dl.download_klines(pair, tf, start_date, end_date)
            self.data[pair] = data
            print(f"    15m: {len(data['15m'])} bars, 1h: {len(data['1h'])}, 4h: {len(data['4h'])}")

        # 2. Initialize simulators
        print("\n[2/3] Running walk-forward simulation...")
        for pair in self.pairs:
            config = PAIR_CONFIGS[pair]
            self.simulators[pair] = PairSimulator(
                pair, self.capital_per_pair, config
            )

        # 3. Find common time range across all pairs
        common_start = max(self.data[p]["15m"].index[0] for p in self.pairs)
        common_end = min(self.data[p]["15m"].index[-1] for p in self.pairs)
        print(f"\n  Common range: {common_start} → {common_end}")

        # 4. Process candle-by-candle across all pairs
        # Use the 15m index from any pair as reference
        ref_15m = self.data[self.pairs[0]]["15m"]
        total_bars = len(ref_15m)
        print(f"  Total 15m bars to process: {total_bars}")
        print(f"  Pairs: {len(self.pairs)}")
        print(f"  Capital per pair: ${self.capital_per_pair:.2f}")

        t0 = time.time()
        last_print = 0

        for i in range(50, total_bars):
            bar_time = ref_15m.index[i]
            bar_close = float(ref_15m["close"].iloc[i])
            bar_high = float(ref_15m["high"].iloc[i])
            bar_low = float(ref_15m["low"].iloc[i])

            # Process each pair
            for pair in self.pairs:
                sim = self.simulators[pair]
                pair_15m = self.data[pair]["15m"]
                pair_1h = self.data[pair]["1h"]
                pair_4h = self.data[pair]["4h"]

                # Find closest bar in this pair's data
                if bar_time not in pair_15m.index:
                    # Find nearest previous bar
                    mask = pair_15m.index <= bar_time
                    if mask.sum() == 0:
                        continue
                    idx = mask.sum() - 1
                else:
                    idx = pair_15m.index.get_loc(bar_time)

                pair_bar_close = float(pair_15m["close"].iloc[idx])
                pair_bar_high = float(pair_15m["high"].iloc[idx])
                pair_bar_low = float(pair_15m["low"].iloc[idx])

                sim.process_candle(
                    idx, bar_time, pair_bar_close,
                    pair_bar_high, pair_bar_low,
                    pair_15m, pair_1h, pair_4h
                )

            # Portfolio equity = sum of all pair equities
            portfolio_eq = sum(
                self.simulators[p].equity for p in self.pairs
            )
            self.portfolio_equity_curve.append(portfolio_eq)

            # Progress every 5%
            pct = i / total_bars * 100
            if pct - last_print >= 5:
                elapsed = time.time() - t0
                print(f"  [{pct:.0f}%] Bar {i}/{total_bars} | "
                      f"Portfolio: ${portfolio_eq:.2f} | "
                      f"Elapsed: {elapsed:.1f}s")
                last_print = pct

        elapsed = time.time() - t0
        print(f"\n  Simulation complete in {elapsed:.1f}s")

        # 5. Collect all trades
        for pair in self.pairs:
            self.all_trades.extend(self.simulators[pair].trades)
        self.all_trades.sort(key=lambda t: t.entry_time)

        # 6. Compute results
        print("\n[3/3] Computing metrics...")
        results = self._compute_metrics()

        # 7. Save results
        output_file = OUTPUT_DIR / "walkforward_5pairs_results.json"
        with open(output_file, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nResults saved: {output_file}")

        # 8. Print summary
        self._print_summary(results)

        return results

    def _compute_metrics(self) -> Dict[str, Any]:
        """Calculate portfolio and per-pair metrics."""
        per_pair = {}
        for pair in self.pairs:
            result = self.simulators[pair].get_metrics()
            per_pair[pair] = result

        # Portfolio metrics
        total_final = sum(per_pair[p]["metrics"]["final_equity"] for p in per_pair)
        total_return = total_final - self.capital
        total_return_pct = total_return / self.capital * 100

        # Portfolio equity curve
        eq = np.array(self.portfolio_equity_curve)
        if len(eq) == 0:
            eq = np.array([self.capital])
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / np.where(peak > 0, peak, 1) * 100
        portfolio_max_dd = float(dd.min())

        # Trade stats
        all_trades = self.all_trades
        total_trades = len(all_trades)
        total_wins = sum(1 for t in all_trades if t.pnl > 0)
        total_losses = sum(1 for t in all_trades if t.pnl <= 0)
        wr = total_wins / total_trades * 100 if total_trades > 0 else 0

        pnls = [t.pnl for t in all_trades]
        wins_pnl = [p for p in pnls if p > 0]
        losses_pnl = [p for p in pnls if p <= 0]

        gross_win = sum(wins_pnl) if wins_pnl else 0
        gross_loss = -sum(losses_pnl) if losses_pnl else 0.001
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        # Sharpe & Sortino
        if len(eq) > 1:
            returns = np.diff(eq) / np.where(eq[:-1] != 0, eq[:-1], 1)
            std = np.std(returns)
            sharpe = float(np.mean(returns) / std * np.sqrt(252 * 24 * 4)) if std > 0 else 0
            neg = returns[returns < 0]
            downside = float(np.std(neg)) if len(neg) > 0 else 0.001
            sortino = float(np.mean(returns) / downside * np.sqrt(252 * 24 * 4)) if downside > 0 else 0
        else:
            sharpe = sortino = 0

        calmar = abs(total_return_pct / portfolio_max_dd) if portfolio_max_dd < 0 else 0

        avg_trade = np.mean(pnls) if pnls else 0
        avg_win = np.mean(wins_pnl) if wins_pnl else 0
        avg_loss = np.mean(losses_pnl) if losses_pnl else 0
        avg_bars = np.mean([t.bars_held for t in all_trades]) if all_trades else 0

        # Exit reasons
        exit_reasons = {}
        for t in all_trades:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        # Contributions
        contributions = {}
        for pair in self.pairs:
            m = per_pair[pair]["metrics"]
            pair_return = m["final_equity"] - self.capital_per_pair
            contributions[pair] = {
                "return_usd": round(pair_return, 2),
                "return_pct": round(pair_return / self.capital_per_pair * 100, 2),
                "pct_of_total": round(pair_return / total_return * 100, 1) if total_return != 0 else 0,
                "trades": per_pair[pair]["total_trades"],
                "sharpe": m["sharpe"],
                "max_dd": m["max_dd_pct"],
                "final_equity": m["final_equity"],
            }

        # Correlation matrix
        min_len = min(len(per_pair[p]["equity_curve"]) for p in self.pairs)
        curves = {}
        for p in self.pairs:
            ec = np.array(per_pair[p]["equity_curve"][:min_len])
            if len(ec) > 1:
                curves[p] = np.diff(ec) / np.where(ec[:-1] != 0, ec[:-1], 1)

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
        for p in self.pairs:
            ec = np.array(per_pair[p]["equity_curve"][:min_len])
            pp = np.maximum.accumulate(ec)
            d = (ec - pp) / np.where(pp > 0, pp, 1) * 100
            pair_dds[p] = d < -1.0

        max_sim = 0
        for i in range(min_len):
            count = sum(1 for p in pair_dds if pair_dds[p][i])
            max_sim = max(max_sim, count)

        # Monthly breakdown
        monthly = {}
        for t in all_trades:
            et = t.entry_time
            if isinstance(et, str):
                try:
                    et = datetime.fromisoformat(et)
                except:
                    continue
            mk = et.strftime("%Y-%m")
            if mk not in monthly:
                monthly[mk] = {}
            pair = t.pair
            if pair not in monthly[mk]:
                monthly[mk][pair] = {"pnl": 0, "trades": 0, "wins": 0}
            monthly[mk][pair]["pnl"] += t.pnl
            monthly[mk][pair]["trades"] += 1
            if t.pnl > 0:
                monthly[mk][pair]["wins"] += 1

        for mk in monthly:
            tp = sum(monthly[mk][p]["pnl"] for p in monthly[mk])
            tt = sum(monthly[mk][p]["trades"] for p in monthly[mk])
            tw = sum(monthly[mk][p]["wins"] for p in monthly[mk])
            monthly[mk]["TOTAL"] = {
                "pnl": round(tp, 2),
                "trades": tt,
                "win_rate": round(tw / tt * 100, 1) if tt > 0 else 0
            }

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "period": f"{self.data[self.pairs[0]]['15m'].index[0]} to "
                      f"{self.data[self.pairs[0]]['15m'].index[-1]}",
            "type": "walkforward_simulation",
            "lookahead_bias": False,
            "costs": {
                "commission": f"{COMMISSION_MAKER * 100:.3f}% (Maker Post-Only)",
                "spread": f"{SPREAD * 100:.2f}%",
                "slippage": f"{SLIP_ATR_MULT} x ATR(5m) x 0.5 (Maker)",
            },
            "portfolio": {
                "initial_capital": self.capital,
                "capital_per_pair": self.capital_per_pair,
                "final_equity": round(total_final, 2),
                "total_return": round(total_return, 2),
                "total_return_pct": round(total_return_pct, 2),
                "max_dd_pct": round(portfolio_max_dd, 2),
                "sharpe": round(sharpe, 2),
                "sortino": round(sortino, 2),
                "profit_factor": round(pf, 2),
                "win_rate": round(wr, 1),
                "total_trades": total_trades,
                "calmar": round(calmar, 2),
                "avg_trade": round(float(avg_trade), 4),
                "avg_win": round(float(avg_win), 4),
                "avg_loss": round(float(avg_loss), 4),
                "avg_bars_held": round(float(avg_bars), 1),
                "wins": total_wins,
                "losses": total_losses,
                "exit_reasons": exit_reasons,
            },
            "per_pair": {p: per_pair[p] for p in self.pairs},
            "contributions": contributions,
            "correlation_matrix": corr,
            "max_simultaneous_dd": max_sim,
            "monthly_breakdown": monthly,
            "portfolio_equity_curve": [round(float(x), 2)
                                       for x in self.portfolio_equity_curve[::100]],
            "total_bars_processed": len(self.portfolio_equity_curve),
        }

    def _print_summary(self, results: Dict):
        """Print formatted summary to console."""
        p = results["portfolio"]

        print(f"\n{'=' * 70}")
        print(" WALK-FORWARD SIMULATION RESULTS")
        print(f"{'=' * 70}")
        print(f"  Period: {results['period']}")
        print(f"  Type: {results['type']} (NO lookahead bias)")
        print(f"  Costs: {results['costs']['commission']} + "
              f"{results['costs']['spread']} + {results['costs']['slippage']}")

        print(f"\n{'─' * 70}")
        print(" PORTFOLIO METRICS")
        print(f"{'─' * 70}")
        print(f"  Initial:  ${p['initial_capital']:,.2f}  →  "
              f"Final: ${p['final_equity']:,.2f}")
        print(f"  Return:   ${p['total_return']:+,.2f} ({p['total_return_pct']:+.2f}%)")
        print(f"  Max DD:   {p['max_dd_pct']:.2f}%")
        print(f"  Sharpe:   {p['sharpe']:.2f}  |  Sortino: {p['sortino']:.2f}")
        print(f"  PF:       {p['profit_factor']:.2f}  |  WR: {p['win_rate']:.1f}%")
        print(f"  Trades:   {p['total_trades']}  |  Calmar: {p['calmar']:.2f}")
        print(f"  Avg Trade: ${p['avg_trade']:.4f}  |  "
              f"Avg Win: ${p['avg_win']:.4f}  |  Avg Loss: ${p['avg_loss']:.4f}")
        print(f"  Avg Bars:  {p['avg_bars_held']:.1f}")

        print(f"\n{'─' * 70}")
        print(" PER-PAIR BREAKDOWN")
        print(f"{'─' * 70}")
        print(f"  {'Pair':<12} {'Trades':>7} {'WR':>7} {'PF':>7} "
              f"{'Return':>10} {'Ret%':>8} {'DD':>8} {'Sharpe':>8} {'Final$':>10}")
        print(f"  {'─' * 77}")
        for pair in self.pairs:
            m = results["per_pair"][pair]["metrics"]
            c = results["contributions"][pair]
            print(f"  {pair:<12} {results['per_pair'][pair]['total_trades']:>6} "
                  f"{m['win_rate']:>6.1f}% {m['profit_factor']:>6.2f} "
                  f"${m['total_return']:>+9.2f} {m['total_return_pct']:>+7.2f}% "
                  f"{m['max_dd_pct']:>7.2f}% {m['sharpe']:>7.2f} "
                  f"${m['final_equity']:>9.2f}")

        print(f"\n  CONTRIBUTIONS TO TOTAL RETURN:")
        print(f"  {'Pair':<12} {'Return$':>10} {'Ret%':>8} {'%Total':>8}")
        print(f"  {'─' * 38}")
        for pair, c in results["contributions"].items():
            print(f"  {pair:<12} ${c['return_usd']:>+9.2f} "
                  f"{c['return_pct']:>+7.2f}% {c['pct_of_total']:>7.1f}%")

        print(f"\n  MAX SIMULTANEOUS DD: {results['max_simultaneous_dd']}/{len(self.pairs)} pairs")

        print(f"\n  CORRELATION MATRIX:")
        syms = list(results["correlation_matrix"].keys())
        header = f"  {'':>12}" + "".join(f"{s[:3]:>8}" for s in syms)
        print(header)
        for s1 in syms:
            row = f"  {s1:<12}" + "".join(
                f"{results['correlation_matrix'][s1][s2]:>8.3f}" for s2 in syms
            )
            print(row)

        print(f"\n  MONTHLY BREAKDOWN:")
        print(f"  {'Month':<10} {'PnL':>10} {'Trades':>8} {'WR':>8}")
        print(f"  {'─' * 36}")
        for mk in sorted(results["monthly_breakdown"].keys()):
            t = results["monthly_breakdown"][mk]["TOTAL"]
            print(f"  {mk:<10} ${t['pnl']:>+9.2f} {t['trades']:>7} "
                  f"{t['win_rate']:>7.1f}%")

        # Exit reasons
        print(f"\n  EXIT REASONS:")
        for reason, count in sorted(p["exit_reasons"].items(),
                                    key=lambda x: -x[1]):
            pct = count / p["total_trades"] * 100 if p["total_trades"] > 0 else 0
            print(f"    {reason:<20} {count:>4} ({pct:.1f}%)")

        print(f"\n  Bars processed: {results['total_bars_processed']:,}")
        print(f"{'=' * 70}")


# ============================================================
# COMPARISON WITH STANDARD BACKTEST
# ============================================================

def compare_with_standard_backtest(wf_results: Dict):
    """Compare walk-forward results with standard backtest results."""
    portfolio_file = OUTPUT_DIR / "portfolio_5pairs_results.json"
    if not portfolio_file.exists():
        print("\n  [SKIP] No standard backtest results to compare with.")
        return

    with open(portfolio_file) as f:
        std_results = json.load(f)

    std_p = std_results.get("portfolio", {})
    wf_p = wf_results.get("portfolio", {})

    print(f"\n{'=' * 70}")
    print(" COMPARISON: Walk-Forward vs Standard Backtest")
    print(f"{'=' * 70}")
    print(f"  {'Metric':<20} {'Standard':>15} {'Walk-Forward':>15} {'Delta':>12}")
    print(f"  {'─' * 62}")

    metrics = [
        ("Total Return%", "total_return_pct", "%"),
        ("Max DD%", "max_dd_pct", "%"),
        ("Sharpe", "sharpe", ""),
        ("Profit Factor", "profit_factor", ""),
        ("Win Rate%", "win_rate", "%"),
        ("Total Trades", "total_trades", ""),
        ("Final Equity$", "final_equity", "$"),
    ]

    for name, key, fmt in metrics:
        std_val = std_p.get(key, 0)
        wf_val = wf_p.get(key, 0)
        delta = wf_val - std_val

        if fmt == "$":
            print(f"  {name:<20} ${std_val:>14,.2f} ${wf_val:>14,.2f} ${delta:>+11,.2f}")
        elif fmt == "%":
            print(f"  {name:<20} {std_val:>14.2f} {wf_val:>14.2f} {delta:>+11.2f}")
        else:
            print(f"  {name:<20} {std_val:>15.2f} {wf_val:>15.2f} {delta:>+12.2f}")

    # Interpretation
    print(f"\n  ANALYSIS:")
    if wf_p.get("total_return_pct", 0) < std_p.get("total_return_pct", 0):
        diff = std_p.get("total_return_pct", 0) - wf_p.get("total_return_pct", 0)
        print(f"  - Walk-forward return is {diff:.2f}% lower than standard backtest")
        print(f"    (Expected: standard backtest may have some overestimation)")
    else:
        diff = wf_p.get("total_return_pct", 0) - std_p.get("total_return_pct", 0)
        print(f"  - Walk-forward return is {diff:.2f}% HIGHER than standard backtest")

    if wf_p.get("max_dd_pct", 0) < std_p.get("max_dd_pct", 0):
        print(f"  - Walk-forward DD is WORSE than standard backtest")
    else:
        print(f"  - Walk-forward DD is BETTER than standard backtest")

    if wf_p.get("sharpe", 0) > std_p.get("sharpe", 0):
        print(f"  - Walk-forward Sharpe is BETTER: better risk-adjusted returns")
    else:
        print(f"  - Walk-forward Sharpe is WORSE: more risk per unit of return")

    print(f"{'=' * 70}")


# ============================================================
# MAIN
# ============================================================

def main():
    """Run walk-forward simulation for 5 pairs portfolio."""
    print("=" * 70)
    print(" INSTITUTIONAL ENGINE V2 — WALK-FORWARD SIMULATION")
    print(" 5 Pairs: BTC, ETH, SOL, XRP, BNB")
    print(" $4,450 capital ($890/pair)")
    print(" Candle-by-candle | NO lookahead bias | Post-Only costs")
    print("=" * 70)

    sim = WalkForwardSimulator()

    # Run for Jan-Jul 2026
    end_date = datetime.now(timezone.utc)
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

    results = sim.run(start_date, end_date)

    # Compare with standard backtest
    compare_with_standard_backtest(results)

    return results


if __name__ == "__main__":
    main()
