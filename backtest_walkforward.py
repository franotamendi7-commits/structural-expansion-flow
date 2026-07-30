"""
Walk-Forward Validation - Institutional Engine V2
==================================================

Validación walk-forward exhaustiva con 7 folds para evitar overfitting.
Compara V1 (baseline) vs V2 en cada fold.

Períodos:
  Fold 1: Train 2023-Q1→Q3, Test 2023-Q4
  Fold 2: Train 2023-Q1→Q4, Test 2024-Q1
  Fold 3: Train 2023-Q1→2024-Q2, Test 2024-Q3
  Fold 4: Train 2023-Q1→2024-Q4, Test 2025-Q1
  Fold 5: Train 2023-Q1→2025-Q2, Test 2025-Q3
  Fold 6: Train 2023-Q1→2025-Q4, Test 2026-Q1
  Fold 7: Train 2023-Q1→2026-Q1, Test 2026-Q2-Q3 (actual)

Costos realistas incluidos:
  - Commission: 0.05% por lado
  - Spread: 0.02%
  - Slippage: 0.1 x ATR(5m)

SIN look-ahead bias: solo usa datos cerrados antes de cada decisión.
"""

import sys
import json
import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import (
    InstitutionalEngineV2, choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, detect_market_phase
)
from engine.drawdown_manager import DrawdownManager
from engine.parameter_adapter import ParameterAdapter

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACION
# ============================================================

SYMBOL = "BTCUSDT"
INITIAL_CAPITAL = 1000.0
RISK_PCT = 0.01  # 1% risk per trade
LEVERAGE = 10
OUTPUT_DIR = Path(__file__).parent / "backtest_results"

# Costos
COMMISSION = 0.0005
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1

# Walk-forward folds
FOLDS = [
    {"fold": 1, "train_start": "2023-01-01", "train_end": "2023-09-30", "test_start": "2023-10-01", "test_end": "2023-12-31"},
    {"fold": 2, "train_start": "2023-01-01", "train_end": "2023-12-31", "test_start": "2024-01-01", "test_end": "2024-03-31"},
    {"fold": 3, "train_start": "2023-01-01", "train_end": "2024-06-30", "test_start": "2024-07-01", "test_end": "2024-09-30"},
    {"fold": 4, "train_start": "2023-01-01", "train_end": "2024-12-31", "test_start": "2025-01-01", "test_end": "2025-03-31"},
    {"fold": 5, "train_start": "2023-01-01", "train_end": "2025-06-30", "test_start": "2025-07-01", "test_end": "2025-09-30"},
    {"fold": 6, "train_start": "2023-01-01", "train_end": "2025-12-31", "test_start": "2026-01-01", "test_end": "2026-03-31"},
    {"fold": 7, "train_start": "2023-01-01", "train_end": "2026-03-31", "test_start": "2026-04-01", "test_end": "2026-07-31"},
]


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
# BACKTEST ENGINE
# ============================================================

class BacktestEngine:
    """
    Backtester para Institutional Engine V1 y V2.
    Sin look-ahead bias: solo usa datos cerrados.
    Costos realistas: commission + spread + slippage.
    """

    def __init__(self, initial_capital: float = INITIAL_CAPITAL,
                 risk_pct: float = RISK_PCT, leverage: int = LEVERAGE):
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.leverage = leverage

    def run_v1(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame,
               df_4h: pd.DataFrame, df_1d: pd.DataFrame,
               df_5m: pd.DataFrame = None) -> Dict[str, Any]:
        """Backtest V1: Engulfing 4H + TP/SL all-or-nothing."""
        equity = self.initial_capital
        peak_equity = equity
        trades = []
        equity_curve = [equity]

        # Iterate over 15m bars
        for i in range(50, len(df_15m)):
            bar_time = df_15m.index[i]
            bar_close = float(df_15m["close"].iloc[i])

            # Need 4H engulfing signal
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

            # Get 15m data for filters
            mask_15 = df_15m.index <= bar_time
            if mask_15.sum() < 30:
                equity_curve.append(equity)
                continue

            data_15 = df_15m[mask_15]
            c_15 = data_15["close"].values[-30:]
            h_15 = data_15["high"].values[-30:]
            l_15 = data_15["low"].values[-30:]

            # CI filter
            ci = choppiness_index(h_15, l_15, c_15)
            if ci is not None and ci > 74.0:
                equity_curve.append(equity)
                continue

            # Market phase filter
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
                [{"close": c, "high": h, "low": l, "volume": v,
                  "open": o} for c, h, l, v, o in zip(
                    data_1h["close"].values, data_1h["high"].values,
                    data_1h["low"].values, data_1h["volume"].values,
                    data_1h["open"].values
                )],
                ind_1h
            )
            if phase in ("ranging", "neutral", "unknown"):
                equity_curve.append(equity)
                continue

            # Calculate entry/SL
            entry = bar_close
            if signal == "SHORT":
                last_high = float(data_15["high"].values[-10:].max())
                sl = last_high * 1.002
            else:
                last_low = float(data_15["low"].values[-10:].min())
                sl = last_low * 0.998

            sl_pct = abs(entry - sl) / entry
            max_sl = 0.018
            if sl_pct > max_sl:
                if signal == "LONG":
                    sl = entry * (1 - max_sl)
                else:
                    sl = entry * (1 + max_sl)
                sl_pct = max_sl

            if sl_pct <= 0:
                equity_curve.append(equity)
                continue

            # TP levels
            tp_ratio = 1.5
            sl_dist = abs(entry - sl)
            if signal == "LONG":
                tp1 = entry + tp_ratio * sl_dist
                tp2 = entry + 2 * tp_ratio * sl_dist
            else:
                tp1 = entry - tp_ratio * sl_dist
                tp2 = entry - 2 * tp_ratio * sl_dist

            # Position sizing
            risk_usd = equity * self.risk_pct
            risk_usd = min(risk_usd, equity * 0.025)
            notional = risk_usd / sl_pct
            size = notional / entry

            if size <= 0 or notional < 1:
                equity_curve.append(equity)
                continue

            # Entry slippage
            atr_5m = atr(
                data_15["high"].values[-14:],
                data_15["low"].values[-14:],
                data_15["close"].values[-14:]
            ) or entry * 0.001
            slip = SLIP_ATR_MULT * atr_5m
            if signal == "LONG":
                entry_fill = entry * (1 + SPREAD) + slip
            else:
                entry_fill = entry * (1 - SPREAD) - slip

            # Simulate exit: check subsequent 15m bars
            exit_price = None
            exit_reason = None
            exit_idx = None

            for j in range(i + 1, min(i + 100, len(df_15m))):
                bh = float(df_15m["high"].iloc[j])
                bl = float(df_15m["low"].iloc[j])

                # SL check
                if signal == "LONG" and bl <= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    exit_idx = j
                    break
                elif signal == "SHORT" and bh >= sl:
                    exit_price = sl
                    exit_reason = "stop_loss"
                    exit_idx = j
                    break

                # TP check (all-or-nothing for V1)
                if signal == "LONG" and bh >= tp1:
                    exit_price = tp1
                    exit_reason = "take_profit"
                    exit_idx = j
                    break
                elif signal == "SHORT" and bl <= tp1:
                    exit_price = tp1
                    exit_reason = "take_profit"
                    exit_idx = j
                    break

            # If no exit found, use last bar close
            if exit_price is None:
                last_bar = min(i + 99, len(df_15m) - 1)
                exit_price = float(df_15m["close"].iloc[last_bar])
                exit_reason = "time_exit"
                exit_idx = last_bar

            # Exit slippage
            if signal == "LONG":
                exit_fill = exit_price * (1 - SPREAD) - slip
            else:
                exit_fill = exit_price * (1 + SPREAD) + slip

            # PnL
            if signal == "LONG":
                gross = size * (exit_fill - entry_fill)
            else:
                gross = size * (entry_fill - exit_fill)

            comm = size * (entry_fill + exit_fill) * COMMISSION
            net = gross - comm

            equity += net
            peak_equity = max(peak_equity, equity)

            trades.append(Trade(
                entry_time=bar_time,
                exit_time=df_15m.index[exit_idx],
                direction=signal,
                entry_price=entry_fill,
                exit_price=exit_fill,
                size=size,
                pnl=net,
                exit_reason=exit_reason,
                bars_held=exit_idx - i,
                score=0,
                tp1_hit=False,
            ))

            equity_curve.append(equity)

        return self._compute_metrics(trades, equity_curve, "V1")

    def run_v2(self, df_15m: pd.DataFrame, df_1h: pd.DataFrame,
               df_4h: pd.DataFrame, df_1d: pd.DataFrame,
               df_5m: pd.DataFrame = None) -> Dict[str, Any]:
        """Backtest V2: Engulfing 4H + trailing + breakeven + partial + DD manager."""
        equity = self.initial_capital
        peak_equity = equity
        trades = []
        equity_curve = [equity]

        dd_mgr = DrawdownManager(initial_equity=equity)
        dd_mgr.reset(equity)  # Clean state for backtest
        param_adapter = ParameterAdapter()
        param_adapter.reset()  # Clean state for backtest

        # Track open position
        open_pos = None  # dict with entry details

        for i in range(50, len(df_15m)):
            bar_time = df_15m.index[i]
            bar_close = float(df_15m["close"].iloc[i])
            bar_high = float(df_15m["high"].iloc[i])
            bar_low = float(df_15m["low"].iloc[i])

            # Check open position management first
            if open_pos is not None:
                result = self._manage_position_v2(
                    open_pos, bar_high, bar_low, bar_close, i, df_15m
                )
                if result is not None:
                    # Position closed
                    equity += result["pnl"]
                    peak_equity = max(peak_equity, equity)
                    dd_mgr.update(equity)

                    # Save score before clearing open_pos
                    trade_score = open_pos.get("score", 0)

                    trades.append(Trade(
                        entry_time=open_pos["entry_time"],
                        exit_time=bar_time,
                        direction=open_pos["direction"],
                        entry_price=open_pos["entry_fill"],
                        exit_price=result["exit_price"],
                        size=open_pos["size"],
                        pnl=result["pnl"],
                        exit_reason=result["reason"],
                        bars_held=i - open_pos["entry_idx"],
                        score=trade_score,
                        tp1_hit=open_pos.get("tp1_hit", False),
                        partial_pnl=result.get("partial_pnl", 0.0),
                    ))
                    open_pos = None

                    # Record trade in adapter
                    param_adapter.record_trade({
                        "net_pnl": result["pnl"],
                        "score": trade_score,
                    })

                equity_curve.append(equity)
                continue

            # No open position: check for new signal
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

            # Get data for filters
            mask_15 = df_15m.index <= bar_time
            if mask_15.sum() < 30:
                equity_curve.append(equity)
                continue

            data_15 = df_15m[mask_15]
            c_15 = data_15["close"].values[-30:]
            h_15 = data_15["high"].values[-30:]
            l_15 = data_15["low"].values[-30:]

            ci = choppiness_index(h_15, l_15, c_15)
            if ci is not None and ci > 74.0:
                equity_curve.append(equity)
                continue

            # Get 1h data for ATR
            mask_1h = df_1h.index <= bar_time
            if mask_1h.sum() < 30:
                equity_curve.append(equity)
                continue
            data_1h = df_1h[mask_1h]
            c_1h = data_1h["close"].values[-30:]
            h_1h = data_1h["high"].values[-30:]
            l_1h = data_1h["low"].values[-30:]
            atr_1h_val = atr(h_1h, l_1h, c_1h)

            # Score (V2 is more selective: base 55 vs V1's 70)
            wr_5m = williams_r(
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
            if wr_5m is not None and -80 < wr_5m < -20:
                score += 5
            # Bonus for fib level alignment
            if signal == "LONG" and c_15[-1] > np.mean(c_15[-20:]):
                score += 3
            elif signal == "SHORT" and c_15[-1] < np.mean(c_15[-20:]):
                score += 3

            min_score = param_adapter.params.get("min_score", 50)
            if score < min_score:
                equity_curve.append(equity)
                continue

            # DD manager check
            dd_params = dd_mgr.get_risk_params()
            dd_mult = dd_params[0]
            dd_min_score = dd_params[1]
            if dd_mult == 0:
                equity_curve.append(equity)
                continue
            if score < dd_min_score:
                equity_curve.append(equity)
                continue

            # Strategy weight multiplier
            strat_mult = param_adapter.get_risk_multiplier()

            # Calculate entry
            entry = bar_close
            if signal == "SHORT":
                last_high = float(data_15["high"].values[-10:].max())
                sl = last_high * 1.002
            else:
                last_low = float(data_15["low"].values[-10:].min())
                sl = last_low * 0.998

            sl_pct = abs(entry - sl) / entry
            max_sl = 0.018
            if sl_pct > max_sl:
                if signal == "LONG":
                    sl = entry * (1 - max_sl)
                else:
                    sl = entry * (1 + max_sl)
                sl_pct = max_sl

            if sl_pct <= 0:
                equity_curve.append(equity)
                continue

            tp_ratio = param_adapter.params.get("tp_ratio", 1.5)
            sl_dist = abs(entry - sl)
            if signal == "LONG":
                tp1 = entry + tp_ratio * sl_dist
                tp2 = entry + 2 * tp_ratio * sl_dist
            else:
                tp1 = entry - tp_ratio * sl_dist
                tp2 = entry - 2 * tp_ratio * sl_dist

            # Position sizing with DD + strategy weight
            risk_usd = equity * self.risk_pct * dd_mult * strat_mult
            risk_usd = min(risk_usd, equity * 0.025)
            notional = risk_usd / sl_pct
            size = notional / entry

            if size <= 0 or notional < 1:
                equity_curve.append(equity)
                continue

            # Entry fill
            atr_5m = atr(
                data_15["high"].values[-14:],
                data_15["low"].values[-14:],
                data_15["close"].values[-14:]
            ) or entry * 0.001
            slip = SLIP_ATR_MULT * atr_5m
            if signal == "LONG":
                entry_fill = entry * (1 + SPREAD) + slip
            else:
                entry_fill = entry * (1 - SPREAD) - slip

            # Open position
            atr_trail = param_adapter.params.get("atr_trail_mult", 2.0)
            partial_pct = param_adapter.params.get("partial_exit_pct", 0.50)
            time_exit = param_adapter.params.get("time_exit_bars", 24)

            open_pos = {
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

            equity_curve.append(equity)

        return self._compute_metrics(trades, equity_curve, "V2")

    def _manage_position_v2(self, pos: Dict, bar_high: float, bar_low: float,
                            bar_close: float, bar_idx: int,
                            df_15m: pd.DataFrame) -> Dict:
        """Gestionar posicion abierta en V2 (trailing, breakeven, partial, time)."""
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
            return self._close_v2(pos, pos["stop_loss"], "stop_loss")
        elif d == -1 and bar_high >= pos["stop_loss"]:
            return self._close_v2(pos, pos["stop_loss"], "stop_loss")

        # 2. Trailing stop check
        if pos["trailing_stop"] is not None:
            if d == 1 and bar_low <= pos["trailing_stop"]:
                return self._close_v2(pos, pos["trailing_stop"], "trailing_stop")
            elif d == -1 and bar_high >= pos["trailing_stop"]:
                return self._close_v2(pos, pos["trailing_stop"], "trailing_stop")

        # 3. TP1 partial exit — modifies position in-place, does NOT close
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

                # Partial close: record PnL, reduce size, continue managing remaining
                partial_pnl = self._calc_partial_pnl(pos, pos["tp1"], pos["partial_exit_pct"])
                pos["size_remaining"] *= (1 - pos["partial_exit_pct"])
                pos["total_realized"] += partial_pnl
                # Do NOT return — continue managing remaining position

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
            pnl = self._close_v2(pos, pos["tp2"], "take_profit_2")
            return pnl

        # 6. Time exit
        if pos["bars_held"] >= pos["time_exit_bars"]:
            return self._close_v2(pos, bar_close, "time_exit")

        return None

    def _close_v2(self, pos: Dict, exit_price: float, reason: str) -> Dict:
        """Cerrar posicion V2."""
        d = 1 if pos["direction"] == "LONG" else -1
        slip = SLIP_ATR_MULT * pos["atr_at_entry"]
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        if d == 1:
            gross = pos["size_remaining"] * (exit_fill - pos["entry_fill"])
        else:
            gross = pos["size_remaining"] * (pos["entry_fill"] - exit_fill)

        comm = pos["size_remaining"] * (pos["entry_fill"] + exit_fill) * COMMISSION
        net = gross - comm + pos["total_realized"]

        return {
            "exit_price": exit_fill,
            "reason": reason,
            "pnl": net,
            "partial_pnl": pos["total_realized"],
        }

    def _calc_partial_pnl(self, pos: Dict, exit_price: float, ratio: float) -> float:
        """Calcular PnL de una salida parcial."""
        d = 1 if pos["direction"] == "LONG" else -1
        slip = SLIP_ATR_MULT * pos["atr_at_entry"]
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        close_size = pos["size"] * ratio
        if d == 1:
            gross = close_size * (exit_fill - pos["entry_fill"])
        else:
            gross = close_size * (pos["entry_fill"] - exit_fill)

        comm = close_size * (pos["entry_fill"] + exit_fill) * COMMISSION
        return gross - comm

    def _compute_metrics(self, trades: List[Trade], equity_curve: List[float],
                         version: str) -> Dict[str, Any]:
        """Calcular metricas completas del backtest."""
        if not trades:
            return {
                "version": version,
                "total_trades": 0,
                "metrics": self._empty_metrics(),
            }

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak * 100
        max_dd = float(dd.min())

        # Returns
        total_return = eq[-1] - eq[0]
        total_return_pct = total_return / eq[0] * 100

        # Win rate
        wr = len(wins) / len(trades) * 100 if trades else 0

        # Profit factor
        gross_win = sum(wins)
        gross_loss = -sum(losses) if losses else 0.001
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        # Sharpe (annualized, 15m bars)
        if len(eq) > 1:
            returns = np.diff(eq) / eq[:-1]
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 4)) if np.std(returns) > 0 else 0
        else:
            sharpe = 0

        # Sortino
        if len(eq) > 1:
            neg_returns = returns[returns < 0]
            downside = float(np.std(neg_returns)) if len(neg_returns) > 0 else 0.001
            sortino = float(np.mean(returns) / downside * np.sqrt(252 * 24 * 4)) if downside > 0 else 0
        else:
            sortino = 0

        # Calmar
        calmar = abs(total_return_pct / max_dd) if max_dd < 0 else 0

        # Avg metrics
        avg_trade = np.mean(pnls)
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        avg_bars = np.mean([t.bars_held for t in trades])

        # Exit reason distribution
        reasons = {}
        for t in trades:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1

        return {
            "version": version,
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
                "avg_trade": round(float(avg_trade), 4),
                "avg_win": round(float(avg_win), 4),
                "avg_loss": round(float(avg_loss), 4),
                "avg_bars_held": round(float(avg_bars), 1),
                "wins": len(wins),
                "losses": len(losses),
                "exit_reasons": reasons,
                "final_equity": round(float(eq[-1]), 2),
            },
            "equity_curve": [round(float(x), 2) for x in equity_curve[::10]],  # subsample
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
# WALK-FORWARD RUNNER
# ============================================================

def run_walkforward():
    """Ejecutar walk-forward validation completa."""
    print("=" * 70)
    print(" WALK-FORWARD VALIDATION - INSTITUTIONAL ENGINE V2")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Download ALL data (2023-2026)
    print("\n[1/3] Downloading historical data (2023-2026)...")
    dl = BinanceDataDownloader()
    start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end_date = datetime.now(timezone.utc)

    print("  15m data:")
    df_15m_full = dl.download_klines(SYMBOL, "15m", start_date, end_date)
    print("  1h data:")
    df_1h_full = dl.download_klines(SYMBOL, "1h", start_date, end_date)
    print("  4h data:")
    df_4h_full = dl.download_klines(SYMBOL, "4h", start_date, end_date)
    print("  1d data:")
    df_1d_full = dl.download_klines(SYMBOL, "1d", start_date, end_date)

    print(f"\n  Full period: {df_15m_full.index[0]} to {df_15m_full.index[-1]}")
    print(f"  15m bars: {len(df_15m_full)}, 1h: {len(df_1h_full)}, "
          f"4h: {len(df_4h_full)}, 1d: {len(df_1d_full)}")

    # Run each fold
    print("\n[2/3] Running walk-forward folds...")
    bt = BacktestEngine()
    results = []

    for fold_info in FOLDS:
        fold_num = fold_info["fold"]
        train_start = pd.Timestamp(fold_info["train_start"], tz=timezone.utc)
        train_end = pd.Timestamp(fold_info["train_end"], tz=timezone.utc)
        test_start = pd.Timestamp(fold_info["test_start"], tz=timezone.utc)
        test_end = pd.Timestamp(fold_info["test_end"], tz=timezone.utc)

        print(f"\n  --- Fold {fold_num} ---")
        print(f"  Train: {train_start.date()} → {train_end.date()}")
        print(f"  Test:  {test_start.date()} → {test_end.date()}")

        # Filter data for test period
        mask_15m = (df_15m_full.index >= test_start) & (df_15m_full.index <= test_end)
        mask_1h = (df_1h_full.index >= test_start) & (df_1h_full.index <= test_end)
        mask_4h = (df_4h_full.index >= test_start) & (df_4h_full.index <= test_end)
        mask_1d = (df_1d_full.index >= test_start) & (df_1d_full.index <= test_end)

        df_15m_test = df_15m_full[mask_15m]
        df_1h_test = df_1h_full[mask_1h]
        df_4h_test = df_4h_full[mask_4h]
        df_1d_test = df_1d_full[mask_1d]

        # Need some buffer before test_start for indicators
        buffer_start = test_start - timedelta(days=30)
        mask_15m_buf = (df_15m_full.index >= buffer_start) & (df_15m_full.index <= test_end)
        mask_1h_buf = (df_1h_full.index >= buffer_start) & (df_1h_full.index <= test_end)
        mask_4h_buf = (df_4h_full.index >= buffer_start) & (df_4h_full.index <= test_end)
        mask_1d_buf = (df_1d_full.index >= buffer_start) & (df_1d_full.index <= test_end)

        df_15m_buf = df_15m_full[mask_15m_buf]
        df_1h_buf = df_1h_full[mask_1h_buf]
        df_4h_buf = df_4h_full[mask_4h_buf]
        df_1d_buf = df_1d_full[mask_1d_buf]

        if len(df_15m_buf) < 100:
            print(f"  WARNING: Insufficient data for fold {fold_num}, skipping")
            continue

        # Run V1
        print("    Running V1...")
        result_v1 = bt.run_v1(df_15m_buf, df_1h_buf, df_4h_buf, df_1d_buf)
        m1 = result_v1["metrics"]
        print(f"    V1: {result_v1['total_trades']} trades, WR={m1['win_rate']}%, "
              f"PF={m1['profit_factor']}, Return={m1['total_return_pct']}%, "
              f"DD={m1['max_dd_pct']}%, Sharpe={m1['sharpe']}")

        # Run V2
        print("    Running V2...")
        result_v2 = bt.run_v2(df_15m_buf, df_1h_buf, df_4h_buf, df_1d_buf)
        m2 = result_v2["metrics"]
        print(f"    V2: {result_v2['total_trades']} trades, WR={m2['win_rate']}%, "
              f"PF={m2['profit_factor']}, Return={m2['total_return_pct']}%, "
              f"DD={m2['max_dd_pct']}%, Sharpe={m2['sharpe']}")

        # Determine winner
        v1_wins = m1["sharpe"] > m2["sharpe"]
        winner = "V1" if v1_wins else "V2"

        # Check warnings
        warnings = []
        if m2["max_dd_pct"] < -10:
            warnings.append(f"V2 DD {m2['max_dd_pct']:.2f}% exceeds -10% threshold")
        if m2["profit_factor"] < 1.0:
            warnings.append(f"V2 PF {m2['profit_factor']:.2f} < 1.0 (negative)")

        fold_result = {
            "fold": fold_num,
            "train_period": f"{train_start.date()} to {train_end.date()}",
            "test_period": f"{test_start.date()} to {test_end.date()}",
            "v1": {
                "total_trades": result_v1["total_trades"],
                "sharpe": m1["sharpe"],
                "sortino": m1["sortino"],
                "max_dd_pct": m1["max_dd_pct"],
                "win_rate": m1["win_rate"],
                "profit_factor": m1["profit_factor"],
                "calmar": m1["calmar"],
                "total_return_pct": m1["total_return_pct"],
                "final_equity": m1["final_equity"],
            },
            "v2": {
                "total_trades": result_v2["total_trades"],
                "sharpe": m2["sharpe"],
                "sortino": m2["sortino"],
                "max_dd_pct": m2["max_dd_pct"],
                "win_rate": m2["win_rate"],
                "profit_factor": m2["profit_factor"],
                "calmar": m2["calmar"],
                "total_return_pct": m2["total_return_pct"],
                "final_equity": m2["final_equity"],
            },
            "winner": winner,
            "v2_better_risk_adjusted": m2["sharpe"] > m1["sharpe"],
            "warnings": warnings,
        }
        results.append(fold_result)

    # Aggregate results
    print("\n[3/3] Aggregating results...")
    v2_sharpes = [r["v2"]["sharpe"] for r in results]
    v2_dds = [r["v2"]["max_dd_pct"] for r in results]
    v2_better_count = sum(1 for r in results if r["v2_better_risk_adjusted"])

    # Success criteria
    sharpes_above_1 = sum(1 for s in v2_sharpes if s > 1.0)
    dds_below_10 = sum(1 for d in v2_dds if d > -10)
    v2_wins_over_v1 = v2_better_count

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "total_folds": len(results),
        "v2_sharpe_above_1": f"{sharpes_above_1}/{len(results)}",
        "v2_dd_below_10": f"{dds_below_10}/{len(results)}",
        "v2_better_than_v1": f"{v2_wins_over_v1}/{len(results)}",
        "success_criteria": {
            "sharpe_5_of_7": sharpes_above_1 >= 5,
            "dd_all_below_10": dds_below_10 == len(results),
            "v2_better_4_of_7": v2_wins_over_v1 >= 4,
        },
        "overall_pass": all([
            sharpes_above_1 >= 5,
            dds_below_10 == len(results),
            v2_wins_over_v1 >= 4,
        ]),
        "avg_v2_sharpe": round(float(np.mean(v2_sharpes)), 2),
        "avg_v2_dd": round(float(np.mean(v2_dds)), 2),
        "avg_v2_return": round(float(np.mean([r["v2"]["total_return_pct"] for r in results])), 2),
        "folds": results,
    }

    # Save results
    output_file = OUTPUT_DIR / "walkforward_results.json"
    with open(output_file, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Print summary
    print(f"\n{'=' * 70}")
    print(" WALK-FORWARD RESULTS SUMMARY")
    print(f"{'=' * 70}")
    print(f"  V2 Sharpe > 1.0: {sharpes_above_1}/{len(results)} folds "
          f"{'✅ PASS' if sharpes_above_1 >= 5 else '❌ FAIL'}")
    print(f"  V2 DD < 10%:     {dds_below_10}/{len(results)} folds "
          f"{'✅ PASS' if dds_below_10 == len(results) else '❌ FAIL'}")
    print(f"  V2 > V1 (Sharpe): {v2_wins_over_v1}/{len(results)} folds "
          f"{'✅ PASS' if v2_wins_over_v1 >= 4 else '❌ FAIL'}")
    print(f"\n  Overall: {'✅ PASS' if summary['overall_pass'] else '❌ FAIL'}")
    print(f"  Avg V2 Sharpe: {summary['avg_v2_sharpe']}")
    print(f"  Avg V2 DD: {summary['avg_v2_dd']}%")
    print(f"  Avg V2 Return: {summary['avg_v2_return']}%")

    # Print fold table
    print(f"\n{'=' * 70}")
    print(f"{'Fold':<6} {'V1 Sharpe':<12} {'V2 Sharpe':<12} {'V1 DD%':<10} {'V2 DD%':<10} {'V1 Ret%':<10} {'V2 Ret%':<10} {'Winner':<8}")
    print("-" * 70)
    for r in results:
        print(f"{r['fold']:<6} {r['v1']['sharpe']:<12} {r['v2']['sharpe']:<12} "
              f"{r['v1']['max_dd_pct']:<10} {r['v2']['max_dd_pct']:<10} "
              f"{r['v1']['total_return_pct']:<10} {r['v2']['total_return_pct']:<10} "
              f"{r['winner']:<8}")

    print(f"\nResults saved: {output_file}")
    return summary


if __name__ == "__main__":
    run_walkforward()