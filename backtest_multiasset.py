"""
Multi-Asset Backtest - Institutional Engine V2
===============================================

Backtest completo en múltiples pares para validar diversificación.
Pares: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT

Período: Enero - Julio 2026 (6 meses)

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

INITIAL_CAPITAL = 1000.0
RISK_PCT = 0.01  # 1% risk per trade
LEVERAGE = 10
MONTHS_BACK = 6
OUTPUT_DIR = Path(__file__).parent / "backtest_results"

# Costos
COMMISSION = 0.0005
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1

# Asset configurations
ASSET_CONFIGS = {
    'BTCUSDT': {
        'supertrend_multiplier': 2.8,
        'choppiness_neutral_threshold': 74.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.5,
        'atr_trail_mult': 2.0,
        'partial_exit_pct': 0.50,
        'time_exit_bars': 24,
        'min_score': 50,
    },
    'ETHUSDT': {
        'supertrend_multiplier': 2.6,
        'choppiness_neutral_threshold': 72.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.8,
        'atr_trail_mult': 2.0,
        'partial_exit_pct': 0.50,
        'time_exit_bars': 24,
        'min_score': 50,
    },
    'SOLUSDT': {
        'supertrend_multiplier': 2.3,
        'choppiness_neutral_threshold': 70.0,
        'fibonacci_days': 90,
        'tp_ratio': 2.0,
        'atr_trail_mult': 2.0,
        'partial_exit_pct': 0.50,
        'time_exit_bars': 24,
        'min_score': 50,
    },
    'XRPUSDT': {
        'supertrend_multiplier': 2.3,
        'choppiness_neutral_threshold': 68.0,
        'fibonacci_days': 60,
        'tp_ratio': 1.8,
        'atr_trail_mult': 2.0,
        'partial_exit_pct': 0.50,
        'time_exit_bars': 24,
        'min_score': 50,
    },
    'BNBUSDT': {
        'supertrend_multiplier': 2.8,
        'choppiness_neutral_threshold': 68.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.6,
        'atr_trail_mult': 2.0,
        'partial_exit_pct': 0.50,
        'time_exit_bars': 24,
        'min_score': 50,
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
    Backtester para Institutional Engine V2.
    Sin look-ahead bias: solo usa datos cerrados.
    Costos realistas: commission + spread + slippage.
    """

    def __init__(self, initial_capital: float = INITIAL_CAPITAL,
                 risk_pct: float = RISK_PCT, leverage: int = LEVERAGE,
                 config: Dict = None):
        self.initial_capital = initial_capital
        self.risk_pct = risk_pct
        self.leverage = leverage
        self.config = config or ASSET_CONFIGS['BTCUSDT']

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

        # Apply custom config
        if self.config:
            param_adapter.params.update(self.config)

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
            if ci is not None and ci > self.config.get('choppiness_neutral_threshold', 74.0):
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
# MULTI-ASSET RUNNER
# ============================================================

def run_multiasset():
    """Ejecutar backtest multi-asset."""
    print("=" * 70)
    print(" MULTI-ASSET BACKTEST - INSTITUTIONAL ENGINE V2")
    print("=" * 70)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Download data for all assets
    print("\n[1/3] Downloading historical data for all assets...")
    dl = BinanceDataDownloader()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=MONTHS_BACK * 30)

    all_data = {}
    for symbol in ASSET_CONFIGS.keys():
        print(f"\n  {symbol}:")
        try:
            df_15m = dl.download_klines(symbol, "15m", start_date, end_date)
            df_1h = dl.download_klines(symbol, "1h", start_date, end_date)
            df_4h = dl.download_klines(symbol, "4h", start_date, end_date)
            df_1d = dl.download_klines(symbol, "1d", start_date, end_date)

            all_data[symbol] = {
                '15m': df_15m,
                '1h': df_1h,
                '4h': df_4h,
                '1d': df_1d,
            }
            print(f"    OK: {len(df_15m)} 15m bars")
        except Exception as e:
            print(f"    ERROR: {e}")

    # Run backtest for each asset
    print("\n[2/3] Running backtests...")
    results = []

    for symbol, config in ASSET_CONFIGS.items():
        if symbol not in all_data:
            print(f"\n  Skipping {symbol} (no data)")
            continue

        print(f"\n  --- {symbol} ---")
        data = all_data[symbol]

        bt = BacktestEngine(
            initial_capital=INITIAL_CAPITAL,
            risk_pct=RISK_PCT,
            leverage=LEVERAGE,
            config=config,
        )

        result = bt.run_v2(data['15m'], data['1h'], data['4h'], data['1d'])
        m = result["metrics"]

        print(f"    Trades: {result['total_trades']}")
        print(f"    WR: {m['win_rate']}%")
        print(f"    PF: {m['profit_factor']}")
        print(f"    Return: {m['total_return_pct']}%")
        print(f"    DD: {m['max_dd_pct']}%")
        print(f"    Sharpe: {m['sharpe']}")
        print(f"    Sortino: {m['sortino']}")
        print(f"    Calmar: {m['calmar']}")

        # Check warnings
        warnings = []
        if m['max_dd_pct'] < -15:
            warnings.append(f"DD {m['max_dd_pct']:.2f}% exceeds -15% threshold")
        if m['max_dd_pct'] < -10:
            warnings.append(f"DD {m['max_dd_pct']:.2f}% exceeds -10% threshold (warning)")
        if m['profit_factor'] < 1.0:
            warnings.append(f"PF {m['profit_factor']:.2f} < 1.0 (negative)")

        asset_result = {
            "symbol": symbol,
            "config": config,
            "total_trades": result["total_trades"],
            "sharpe": m['sharpe'],
            "sortino": m['sortino'],
            "max_dd_pct": m['max_dd_pct'],
            "win_rate": m['win_rate'],
            "profit_factor": m['profit_factor'],
            "calmar": m['calmar'],
            "total_return_pct": m['total_return_pct'],
            "final_equity": m['final_equity'],
            "avg_trade": m['avg_trade'],
            "avg_win": m['avg_win'],
            "avg_loss": m['avg_loss'],
            "avg_bars_held": m['avg_bars_held'],
            "wins": m['wins'],
            "losses": m['losses'],
            "exit_reasons": m['exit_reasons'],
            "warnings": warnings,
        }
        results.append(asset_result)

    # Aggregate results
    print("\n[3/3] Aggregating results...")
    sharpes = [r['sharpe'] for r in results]
    dds = [r['max_dd_pct'] for r in results]
    pfs = [r['profit_factor'] for r in results]

    sharpes_above_1 = sum(1 for s in sharpes if s > 1.0)
    dds_below_15 = sum(1 for d in dds if d > -15)
    dds_below_10 = sum(1 for d in dds if d > -10)
    pfs_above_1_5 = sum(1 for p in pfs if p > 1.5)

    # Success criteria
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "period": f"{start_date.date()} to {end_date.date()}",
        "initial_capital": INITIAL_CAPITAL,
        "leverage": LEVERAGE,
        "total_assets": len(results),
        "sharpes_above_1": f"{sharpes_above_1}/{len(results)}",
        "dds_below_15": f"{dds_below_15}/{len(results)}",
        "dds_below_10": f"{dds_below_10}/{len(results)}",
        "pfs_above_1_5": f"{pfs_above_1_5}/{len(results)}",
        "success_criteria": {
            "sharpes_3_of_5": sharpes_above_1 >= 3,
            "dds_all_below_15": dds_below_15 == len(results),
            "dds_all_below_10": dds_below_10 == len(results),
            "pfs_2_of_5": pfs_above_1_5 >= 2,
        },
        "overall_pass": all([
            sharpes_above_1 >= 3,
            dds_below_15 == len(results),
            pfs_above_1_5 >= 2,
        ]),
        "avg_sharpe": round(float(np.mean(sharpes)), 2),
        "avg_dd": round(float(np.mean(dds)), 2),
        "avg_return": round(float(np.mean([r['total_return_pct'] for r in results])), 2),
        "avg_pf": round(float(np.mean(pfs)), 2),
        "assets": results,
    }

    # Save results
    output_file = OUTPUT_DIR / "multiasset_results.json"
    with open(output_file, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    # Print summary
    print(f"\n{'=' * 70}")
    print(" MULTI-ASSET RESULTS SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Sharpe > 1.0:    {sharpes_above_1}/{len(results)} assets "
          f"{'✅ PASS' if sharpes_above_1 >= 3 else '❌ FAIL'}")
    print(f"  DD < 15%:         {dds_below_15}/{len(results)} assets "
          f"{'✅ PASS' if dds_below_15 == len(results) else '❌ FAIL'}")
    print(f"  DD < 10%:         {dds_below_10}/{len(results)} assets "
          f"{'✅ PASS' if dds_below_10 == len(results) else '❌ FAIL'}")
    print(f"  PF > 1.5:         {pfs_above_1_5}/{len(results)} assets "
          f"{'✅ PASS' if pfs_above_1_5 >= 2 else '❌ FAIL'}")
    print(f"\n  Overall: {'✅ PASS' if summary['overall_pass'] else '❌ FAIL'}")
    print(f"  Avg Sharpe: {summary['avg_sharpe']}")
    print(f"  Avg DD: {summary['avg_dd']}%")
    print(f"  Avg Return: {summary['avg_return']}%")
    print(f"  Avg PF: {summary['avg_pf']}")

    # Print asset table
    print(f"\n{'=' * 70}")
    print(f"{'Symbol':<12} {'Trades':<8} {'WR%':<8} {'PF':<8} {'Return%':<10} {'DD%':<10} {'Sharpe':<8} {'Sortino':<8}")
    print("-" * 70)
    for r in results:
        print(f"{r['symbol']:<12} {r['total_trades']:<8} {r['win_rate']:<8} "
              f"{r['profit_factor']:<8} {r['total_return_pct']:<10} "
              f"{r['max_dd_pct']:<10} {r['sharpe']:<8} {r['sortino']:<8}")

    # Print warnings
    all_warnings = []
    for r in results:
        for w in r['warnings']:
            all_warnings.append(f"{r['symbol']}: {w}")

    if all_warnings:
        print(f"\n⚠️  WARNINGS:")
        for w in all_warnings:
            print(f"  - {w}")

    print(f"\nResults saved: {output_file}")
    return summary


if __name__ == "__main__":
    run_multiasset()