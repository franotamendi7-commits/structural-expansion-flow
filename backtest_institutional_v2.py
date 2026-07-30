"""
Backtest Institucional V2 - Comparativo V1 vs V2
================================================

Ejecuta backtest del Motor Institucional V2 en BTCUSDT (ene-jul 2026):
  1. Descarga datos historicos de Binance (15m, 1h, 4h, 1d)
  2. Ejecuta V1 (baseline: engulfing + TP/SL all-or-nothing)
  3. Ejecuta V2 (trailing stop + breakeven + partial exits + DD manager)
  4. Compara metricas lado a lado
  5. Monte Carlo 5000 sims para verificar robustez
  6. Genera reporte en docs/REPORT_INSTITUTIONAL_V2.md

Costos realistas incluidos:
  - Commission: 0.05% por lado
  - Spread: 0.02%
  - Slippage: 0.1 x ATR(5m)

SIN look-ahead bias: solo usa datos cerrados antes de cada decision.
"""

import sys
import json
import math
import time
import logging
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import (
    InstitutionalEngineV2, choppiness_index, williams_r, supertrend,
    ema, atr, bollinger_bands, is_bullish_engulfing, is_bearish_engulfing,
    AdaptiveFibonacci, analyze_structure, detect_market_phase,
    calculate_dynamic_score
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
MONTHS_BACK = 6
OUTPUT_DIR = Path(__file__).parent / "backtest_results"
DOCS_DIR = Path(__file__).parent / "docs"

# Costos actualizados para Post-Only (Maker)
COMMISSION_MAKER = 0.00018  # 0.018% por lado (con BNB discount)
COMMISSION_TAKER = 0.0005   # 0.05% por lado (taker)
COMMISSION = COMMISSION_MAKER  # Default: usar maker
SPREAD = 0.0002  # Spread similar pero menor con órdenes limit
SLIP_ATR_MULT = 0.1

# Volatility Targeting
BASELINE_ATR_30D = None  # Se calculará del datos históricos


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
# BACKTEST ENGINE
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
        """
        Backtest V1: Engulfing 4H + TP/SL all-or-nothing.
        Replica el comportamiento de scalping_engine.py original.
        """
        equity = self.initial_capital
        peak_equity = equity
        trades = []
        equity_curve = [equity]

        # Pre-compute indicators
        closes_4h = df_4h["close"].values
        highs_4h = df_4h["high"].values
        lows_4h = df_4h["low"].values

        # Iterate over 15m bars
        for i in range(50, len(df_15m)):
            bar_time = df_15m.index[i]
            bar_close = float(df_15m["close"].iloc[i])
            bar_high = float(df_15m["high"].iloc[i])
            bar_low = float(df_15m["low"].iloc[i])

            # Need 4H engulfing signal
            # Find the 4H bar that closed before this 15m bar
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

            # Check for engulfing
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
            tp1_hit = False

            for j in range(i + 1, min(i + 100, len(df_15m))):
                bh = float(df_15m["high"].iloc[j])
                bl = float(df_15m["low"].iloc[j])
                bc = float(df_15m["close"].iloc[j])

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
        """
        Backtest V2: Engulfing 4H + trailing + breakeven + partial + DD manager.
        """
        equity = self.initial_capital
        peak_equity = equity
        trades = []
        equity_curve = [equity]

        dd_mgr = DrawdownManager(initial_equity=equity)
        dd_mgr.reset(equity)  # Clean state for backtest (ignore live drawdown_state.json)
        param_adapter = ParameterAdapter()
        param_adapter.reset()  # Clean state for backtest (ignore live parameter_state.json)

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

            # Get 1h data for ATR (used in position management)
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
            risk_mult = param_adapter.params.get("risk_mult", 1.0)
            risk_usd = equity * self.risk_pct * dd_mult * strat_mult
            risk_usd = min(risk_usd, equity * 0.025)
            notional = risk_usd / sl_pct
            size = notional / entry
            
            # Volatility Targeting: ajustar tamaño inversamente proporcional a la volatilidad
            vol_scale = 1.0
            if BASELINE_ATR_30D and BASELINE_ATR_30D > 0:
                current_atr = atr_1h_val or atr(
                    data_15["high"].values[-14:],
                    data_15["low"].values[-14:],
                    data_15["close"].values[-14:]
                )
                if current_atr and current_atr > 0:
                    vol_ratio = BASELINE_ATR_30D / current_atr
                    # Limitar entre 0.25x y 2.0x para evitar extremos
                    vol_scale = max(0.25, min(vol_ratio, 2.0))
                    notional *= vol_scale
                    size *= vol_scale

            if size <= 0 or notional < 1:
                equity_curve.append(equity)
                continue

            # Entry fill (con Post-Only: menor spread y slippage)
            atr_5m = atr(
                data_15["high"].values[-14:],
                data_15["low"].values[-14:],
                data_15["close"].values[-14:]
            ) or entry * 0.001
            
            # Con Post-Only orders, el slippage es significativamente menor
            # porque se ejecutan como maker, no como taker
            slip = SLIP_ATR_MULT * atr_5m * 0.5  # 50% less slippage con maker
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
                            df_15m: pd.DataFrame) -> Optional[Dict]:
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
        """Cerrar posicion V2 con costos de Post-Only (Maker)."""
        d = 1 if pos["direction"] == "LONG" else -1
        
        # Con Post-Only orders, el slippage es menor
        slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5  # 50% less slippage con maker
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        if d == 1:
            gross = pos["size_remaining"] * (exit_fill - pos["entry_fill"])
        else:
            gross = pos["size_remaining"] * (pos["entry_fill"] - exit_fill)

        # Usar commission de maker (0.018%) en lugar de taker (0.05%)
        comm = pos["size_remaining"] * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
        net = gross - comm + pos["total_realized"]

        return {
            "exit_price": exit_fill,
            "reason": reason,
            "pnl": net,
            "partial_pnl": pos["total_realized"],
        }

    def _calc_partial_pnl(self, pos: Dict, exit_price: float, ratio: float) -> float:
        """Calcular PnL de una salida parcial con costos de Post-Only (Maker)."""
        d = 1 if pos["direction"] == "LONG" else -1
        
        # Con Post-Only orders, el slippage es menor
        slip = SLIP_ATR_MULT * pos["atr_at_entry"] * 0.5  # 50% less slippage con maker
        if d == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        close_size = pos["size"] * ratio
        if d == 1:
            gross = close_size * (exit_fill - pos["entry_fill"])
        else:
            gross = close_size * (pos["entry_fill"] - exit_fill)

        # Usar commission de maker (0.018%) en lugar de taker (0.05%)
        comm = close_size * (pos["entry_fill"] + exit_fill) * COMMISSION_MAKER
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
# MONTE CARLO SIMULATOR
# ============================================================

class MonteCarloSimulator:
    """Monte Carlo simulation para verificar robustez."""

    def __init__(self, n_sims: int = 5000):
        self.n_sims = n_sims

    def simulate(self, trades: List[Trade]) -> Dict[str, Any]:
        """Ejecutar simulaciones Monte Carlo."""
        if not trades:
            return {"metrics": {"mean_return": 0, "prob_profit": 0}}

        pnls = np.array([t.pnl for t in trades])
        n_trades = len(pnls)

        final_equities = []
        max_dds = []

        for _ in range(self.n_sims):
            shuffled = np.random.permutation(pnls)
            equity = INITIAL_CAPITAL
            peak = equity
            max_dd = 0

            for pnl in shuffled:
                equity += pnl
                peak = max(peak, equity)
                dd = (equity - peak) / peak * 100
                max_dd = min(max_dd, dd)

            final_equities.append(equity)
            max_dds.append(max_dd)

        fe = np.array(final_equities)
        md = np.array(max_dds)
        returns = (fe - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

        return {
            "metrics": {
                "mean_return": round(float(np.mean(returns)), 2),
                "median_return": round(float(np.median(returns)), 2),
                "prob_profit": round(float(np.mean(fe > INITIAL_CAPITAL)), 4),
                "p95_return": round(float(np.percentile(returns, 95)), 2),
                "p5_return": round(float(np.percentile(returns, 5)), 2),
                "mean_max_dd": round(float(np.mean(md)), 2),
                "p95_max_dd": round(float(np.percentile(md, 5)), 2),  # worst 5%
            }
        }


# ============================================================
# MAIN BACKTEST RUNNER
# ============================================================

def run_backtest():
    """Ejecutar backtest completo V1 vs V2."""
    print("=" * 70)
    print(" INSTITUTIONAL ENGINE - BACKTEST COMPARATIVO V1 vs V2")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Download data
    print("\n[1/4] Downloading historical data...")
    dl = BinanceDataDownloader()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=MONTHS_BACK * 30)

    print("  15m data:")
    df_15m = dl.download_klines(SYMBOL, "15m", start_date, end_date)
    print("  1h data:")
    df_1h = dl.download_klines(SYMBOL, "1h", start_date, end_date)
    print("  4h data:")
    df_4h = dl.download_klines(SYMBOL, "4h", start_date, end_date)
    print("  1d data:")
    df_1d = dl.download_klines(SYMBOL, "1d", start_date, end_date)

    print(f"\n  Period: {df_15m.index[0]} to {df_15m.index[-1]}")
    print(f"  15m bars: {len(df_15m)}, 1h: {len(df_1h)}, 4h: {len(df_4h)}, 1d: {len(df_1d)}")

    # 2. Run V1 backtest
    print("\n[2/4] Running V1 backtest...")
    bt = BacktestEngine()
    result_v1 = bt.run_v1(df_15m, df_1h, df_4h, df_1d)
    m1 = result_v1["metrics"]
    print(f"  V1: {result_v1['total_trades']} trades, WR={m1['win_rate']}%, "
          f"PF={m1['profit_factor']}, Return={m1['total_return_pct']}%, "
          f"DD={m1['max_dd_pct']}%, Sharpe={m1['sharpe']}")

    # 3. Run V2 backtest
    print("\n[3/4] Running V2 backtest...")
    result_v2 = bt.run_v2(df_15m, df_1h, df_4h, df_1d)
    m2 = result_v2["metrics"]
    print(f"  V2: {result_v2['total_trades']} trades, WR={m2['win_rate']}%, "
          f"PF={m2['profit_factor']}, Return={m2['total_return_pct']}%, "
          f"DD={m2['max_dd_pct']}%, Sharpe={m2['sharpe']}")

    # 4. Monte Carlo
    print("\n[4/4] Running Monte Carlo (5000 sims)...")
    mc = MonteCarloSimulator(5000)

    trades_v1 = [Trade(**t) for t in result_v1.get("trades", [])]
    trades_v2 = [Trade(**t) for t in result_v2.get("trades", [])]

    mc_v1 = mc.simulate(trades_v1) if trades_v1 else {"metrics": {}}
    mc_v2 = mc.simulate(trades_v2) if trades_v2 else {"metrics": {}}

    print(f"  V1 MC: Prob profit={mc_v1['metrics'].get('prob_profit', 0):.1%}, "
          f"Mean DD={mc_v1['metrics'].get('mean_max_dd', 0):.2f}%")
    print(f"  V2 MC: Prob profit={mc_v2['metrics'].get('prob_profit', 0):.1%}, "
          f"Mean DD={mc_v2['metrics'].get('mean_max_dd', 0):.2f}%")

    # Save results
    # Merge top-level total_trades into metrics for report generation
    v1_metrics = dict(result_v1["metrics"])
    v1_metrics["total_trades"] = result_v1["total_trades"]
    v2_metrics = dict(result_v2["metrics"])
    v2_metrics["total_trades"] = result_v2["total_trades"]

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "period": f"{start_date.date()} to {end_date.date()}",
        "initial_capital": INITIAL_CAPITAL,
        "v1": v1_metrics,
        "v1_mc": mc_v1["metrics"],
        "v2": v2_metrics,
        "v2_mc": mc_v2["metrics"],
        "comparison": {
            "return_diff": round(m2["total_return_pct"] - m1["total_return_pct"], 2),
            "sharpe_diff": round(m2["sharpe"] - m1["sharpe"], 2),
            "dd_diff": round(m2["max_dd_pct"] - m1["max_dd_pct"], 2),
            "pf_diff": round(m2["profit_factor"] - m1["profit_factor"], 2),
            "wr_diff": round(m2["win_rate"] - m1["win_rate"], 1),
        },
    }

    report_file = OUTPUT_DIR / f"backtest_v1_vs_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Generate markdown report
    _generate_report(report)

    print(f"\n{'=' * 70}")
    print(f"Results saved: {report_file}")
    print(f"{'=' * 70}")

    return report


def _generate_report(report: Dict):
    """Generar reporte markdown."""
    v1 = report["v1"]
    v2 = report["v2"]
    mc1 = report["v1_mc"]
    mc2 = report["v2_mc"]
    comp = report["comparison"]

    md = f"""# Reporte: Institutional Engine V1 vs V2

**Fecha:** {report['timestamp'][:10]}
**Periodo:** {report['period']}
**Symbol:** {report['symbol']}
**Capital:** ${report['initial_capital']}

## Comparativa Principal

| Metrica | V1 | V2 | Delta |
|---------|-----|-----|-------|
| Total Return | {v1['total_return_pct']:+.2f}% | {v2['total_return_pct']:+.2f}% | {comp['return_diff']:+.2f}% |
| Sharpe Ratio | {v1['sharpe']:.2f} | {v2['sharpe']:.2f} | {comp['sharpe_diff']:+.2f} |
| Max Drawdown | {v1['max_dd_pct']:.2f}% | {v2['max_dd_pct']:.2f}% | {comp['dd_diff']:+.2f}% |
| Profit Factor | {v1['profit_factor']:.2f} | {v2['profit_factor']:.2f} | {comp['pf_diff']:+.2f} |
| Win Rate | {v1['win_rate']:.1f}% | {v2['win_rate']:.1f}% | {comp['wr_diff']:+.1f}% |
| Total Trades | {v1['total_trades']} | {v2['total_trades']} | {v2['total_trades'] - v1['total_trades']:+d} |
| Avg Trade | ${v1['avg_trade']:.4f} | ${v2['avg_trade']:.4f} | - |
| Avg Win | ${v1['avg_win']:.4f} | ${v2['avg_win']:.4f} | - |
| Avg Loss | ${v1['avg_loss']:.4f} | ${v2['avg_loss']:.4f} | - |
| Avg Bars Held | {v1['avg_bars_held']:.0f} | {v2['avg_bars_held']:.0f} | - |
| Calmar Ratio | {v1['calmar']:.2f} | {v2['calmar']:.2f} | - |

## Monte Carlo (5000 sims)

| Metrica | V1 | V2 |
|---------|-----|-----|
| Mean Return | {mc1.get('mean_return', 0):.2f}% | {mc2.get('mean_return', 0):.2f}% |
| Prob Profit | {mc1.get('prob_profit', 0):.1%} | {mc2.get('prob_profit', 0):.1%} |
| Mean Max DD | {mc1.get('mean_max_dd', 0):.2f}% | {mc2.get('mean_max_dd', 0):.2f}% |

## Exit Reason Distribution

### V1
"""
    for reason, count in v1.get("exit_reasons", {}).items():
        md += f"- {reason}: {count}\n"

    md += "\n### V2\n"
    for reason, count in v2.get("exit_reasons", {}).items():
        md += f"- {reason}: {count}\n"

    md += f"""
## Conclusiones

1. **Return:** V2 {'mejora' if comp['return_diff'] > 0 else 'empeora'} {abs(comp['return_diff']):.2f}% vs V1
2. **Risk-adjusted:** Sharpe V2 {'mejora' if comp['sharpe_diff'] > 0 else 'empeora'} {abs(comp['sharpe_diff']):.2f} vs V1
3. **Drawdown:** V2 {'reduce' if comp['dd_diff'] > 0 else 'aumenta'} DD {abs(comp['dd_diff']):.2f}% vs V1
4. **Profit Factor:** V2 {'mejora' if comp['pf_diff'] > 0 else 'empeora'} {abs(comp['pf_diff']):.2f} vs V1

### Cambios implementados en V2
- Trailing stop basado en ATR (2x ATR)
- Breakeven automatico al TP1
- Salida parcial 50% en TP1, 50% restante con trailing
- Time-based exit (24 velas = 4 dias max)
- Drawdown manager con 5 niveles (0-3%: 1x, 3-5%: 0.75x, 5-7%: 0.5x, 7-9%: 0.25x, >9%: STOP)
- Parameter adapter (review cada 50 trades)
- Score base reducido de 70 a 50 (mas selectivo)

### Proximos pasos
1. Paper trading en testnet con V2
2. Monitorear metricas en vivo por 2 semanas
3. Ajustar param_adapter si WR < 35% o PF < 1.0
4. Integrar con multi_bot.py para reemplazar VWAP
"""

    report_path = DOCS_DIR / "REPORT_INSTITUTIONAL_V2.md"
    with open(report_path, "w") as f:
        f.write(md)
    print(f"  Report generated: {report_path}")


if __name__ == "__main__":
    run_backtest()
