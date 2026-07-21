#!/usr/bin/env python3
"""
BTC Adaptive Trading Agent
==========================
Agente de trading adaptativo para BTCUSDT que opera según el régimen de mercado
detectado en tiempo real. Diseñado para ejecutarse en Mac con Python 3.10+.

Requisitos:
    pip install numpy pandas requests

Uso:
    python3 btc_adaptive_agent.py

Salida:
    - Métricas de backtest sobre 6 meses (2026-01-01 a 2026-06-23)
    - Detalle de operaciones por régimen
    - Equity curve y drawdown
"""
from __future__ import annotations

import time
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
import requests

# =============================================================================
# CONFIGURACIÓN
# =============================================================================
SYMBOL = "BTCUSDT"
START_DATE = "2026-01-01"
END_DATE = "2026-06-23"
TIMEFRAMES = ("1h", "15m", "5m")

# Parámetros de costos
COMMISSION_PCT = 0.05      # 0.05% taker
SPREAD_PCT = 0.02          # 0.02% spread fijo
SLIPPAGE_ATR_MULT = 0.1    # 0.1 × ATR(5m, 14)

# Gestión de riesgo
RISK_PER_TRADE = 0.01      # 1% del equity por operación
INITIAL_EQUITY = 10000.0   # USD inicial para el backtest

# Períodos de indicadores
ADX_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
EMA_PERIOD = 20
ATR_PERIOD = 14
VOLUME_MA_PERIOD = 20

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("btc_adaptive")


# =============================================================================
# MÓDULO 1: DESCARGA DE DATOS
# =============================================================================
class BinanceFetcher:
    """Descarga velas históricas desde la API pública de Binance."""

    BASE_URL = "https://api.binance.com"
    KLINES_ENDPOINT = "/api/v3/klines"

    def __init__(self, request_delay: float = 0.2, max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "BTCAdaptiveAgent/1.0 (educational)"
        })
        self.request_delay = request_delay
        self.max_retries = max_retries

    @staticmethod
    def date_to_ms(date_str: str) -> int:
        """Convierte 'YYYY-MM-DD' a epoch milliseconds (UTC)."""
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    def _get_with_retry(self, params: dict) -> list:
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(
                    f"{self.BASE_URL}{self.KLINES_ENDPOINT}",
                    params=params,
                    timeout=15,
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning("Rate limit (429). Esperando %ss.", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = 2 ** attempt
                    logger.warning("Error %s. Reintentando en %ss.", resp.status_code, wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.ConnectionError, requests.Timeout) as exc:
                wait = 2 ** attempt
                logger.warning("Error de red: %s. Reintentando en %ss.", exc, wait)
                time.sleep(wait)
        raise RuntimeError(f"Falló request tras {self.max_retries} intentos")

    def fetch_klines(self, symbol: str, interval: str,
                     start_ms: int, end_ms: int) -> list[dict]:
        """Descarga velas con paginación automática."""
        all_candles: list[dict] = []
        current_start = start_ms
        while current_start < end_ms:
            params = {
                "symbol": symbol, "interval": interval,
                "startTime": current_start, "endTime": end_ms,
                "limit": 1000,
            }
            raw = self._get_with_retry(params)
            if not raw:
                break
            for k in raw:
                all_candles.append({
                    "timestamp": k[0],
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            last_ts = raw[-1][0]
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
            time.sleep(self.request_delay)
            if len(raw) < 1000:
                break
        logger.info("  %s %s: %d velas", symbol, interval, len(all_candles))
        return all_candles

    def fetch_all_timeframes(self, symbol: str, start_str: str,
                             end_str: str, timeframes=TIMEFRAMES) -> dict:
        """Descarga todos los timeframes solicitados."""
        start_ms = self.date_to_ms(start_str)
        end_ms = self.date_to_ms(end_str)
        data = {}
        for tf in timeframes:
            logger.info("Descargando %s %s ...", symbol, tf)
            data[tf] = self.fetch_klines(symbol, tf, start_ms, end_ms)
        return data


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    """Convierte lista de velas a DataFrame indexado por datetime UTC."""
    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(candles)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("datetime").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


# =============================================================================
# MÓDULO 2: INDICADORES TÉCNICOS
# =============================================================================
def ema(series: pd.Series, period: int) -> pd.Series:
    """Media móvil exponencial."""
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index (versión Wilder)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
                     / atr_w.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
                      / atr_w.replace(0, np.nan))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1/period, min_periods=2*period, adjust=False).mean()


def bollinger_width(df: pd.DataFrame, period: int = 20,
                    num_std: float = 2.0) -> pd.Series:
    """Ancho de Bollinger normalizado = (upper - lower) / middle."""
    middle = df["close"].rolling(period, min_periods=period).mean()
    std = df["close"].rolling(period, min_periods=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    return (upper - lower) / middle.replace(0, np.nan)


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Volumen actual / media móvil de volumen."""
    vol_ma = df["volume"].rolling(period, min_periods=period).mean()
    return df["volume"] / vol_ma.replace(0, np.nan)


def ema_slope(series: pd.Series, lookback: int = 3) -> pd.Series:
    """Diferencia entre EMA actual y EMA de hace N velas (pendiente suavizada)."""
    return series.diff(lookback)


def swing_high(df: pd.DataFrame, lookback: int = 2) -> pd.Series:
    """Swing high de las últimas N velas (excluyendo la actual)."""
    return df["high"].rolling(lookback, min_periods=lookback).max().shift(1)


def swing_low(df: pd.DataFrame, lookback: int = 2) -> pd.Series:
    """Swing low de las últimas N velas (excluyendo la actual)."""
    return df["low"].rolling(lookback, min_periods=lookback).min().shift(1)


# =============================================================================
# MÓDULO 3: DETECTOR DE RÉGIMEN
# =============================================================================
class RegimeDetector:
    """
    Clasifica el mercado en:
        - RANGO
        - TENDENCIA_ALCISTA
        - TENDENCIA_BAJISTA
        - RUPTURA
    """

    def __init__(self,
                 adx_range_max: float = 20.0,
                 adx_trend_min: float = 25.0,
                 bb_range_max: float = 0.04,
                 bb_breakout_min: float = 0.06,
                 volume_breakout_mult: float = 1.5,
                 trend_slope_lookback: int = 3):
        self.adx_range_max = adx_range_max
        self.adx_trend_min = adx_trend_min
        self.bb_range_max = bb_range_max
        self.bb_breakout_min = bb_breakout_min
        self.volume_breakout_mult = volume_breakout_mult
        self.trend_slope_lookback = trend_slope_lookback

    def detect(self, df_1h: pd.DataFrame) -> pd.Series:
        """Devuelve Serie con el régimen para cada vela de 1h."""
        # Pre-calcular indicadores
        adx_vals = adx(df_1h, ADX_PERIOD)
        bb_w = bollinger_width(df_1h, BB_PERIOD, BB_STD)
        vol_ratio = volume_ratio(df_1h, VOLUME_MA_PERIOD)
        ema20 = ema(df_1h["close"], EMA_PERIOD)
        slope = ema_slope(ema20, self.trend_slope_lookback)

        regime = pd.Series("INDEFINIDO", index=df_1h.index, dtype=object)

        # Reglas (evaluadas en orden de prioridad: RUPTURA > TENDENCIA > RANGO)
        breakout_mask = (bb_w > self.bb_breakout_min) & \
                        (vol_ratio > self.volume_breakout_mult)

        bull_trend_mask = (adx_vals > self.adx_trend_min) & (slope > 0)

        bear_trend_mask = (adx_vals > self.adx_trend_min) & (slope < 0)

        range_mask = (adx_vals < self.adx_range_max) & (bb_w < self.bb_range_max)

        # Aplicar en orden de prioridad
        regime[range_mask] = "RANGO"
        regime[bull_trend_mask] = "TENDENCIA_ALCISTA"
        regime[bear_trend_mask] = "TENDENCIA_BAJISTA"
        # RUPTURA tiene prioridad máxima
        regime[breakout_mask] = "RUPTURA"

        return regime

    def detect_at(self, df_1h: pd.DataFrame, idx: int) -> str:
        """Detecta régimen en un índice específico (para uso en backtest)."""
        if idx < max(ADX_PERIOD * 2, BB_PERIOD, VOLUME_MA_PERIOD, EMA_PERIOD + self.trend_slope_lookback):
            return "INDEFINIDO"

        sub = df_1h.iloc[:idx + 1]
        adx_v = adx(sub, ADX_PERIOD).iloc[-1]
        bb_v = bollinger_width(sub, BB_PERIOD, BB_STD).iloc[-1]
        vol_v = volume_ratio(sub, VOLUME_MA_PERIOD).iloc[-1]
        ema20 = ema(sub["close"], EMA_PERIOD)
        slope_v = ema_slope(ema20, self.trend_slope_lookback).iloc[-1]

        if pd.isna(adx_v) or pd.isna(bb_v) or pd.isna(vol_v) or pd.isna(slope_v):
            return "INDEFINIDO"

        # RUPTURA (prioridad máxima)
        if bb_v > self.bb_breakout_min and vol_v > self.volume_breakout_mult:
            return "RUPTURA"

        # TENDENCIAS
        if adx_v > self.adx_trend_min:
            if slope_v > 0:
                return "TENDENCIA_ALCISTA"
            elif slope_v < 0:
                return "TENDENCIA_BAJISTA"

        # RANGO
        if adx_v < self.adx_range_max and bb_v < self.bb_range_max:
            return "RANGO"

        return "INDEFINIDO"


# =============================================================================
# MÓDULO 4: SEÑALES DE MICRO-ESTRATEGIAS
# =============================================================================
@dataclass
class Signal:
    """Señal de trading generada por una micro-estrategia."""
    timestamp: pd.Timestamp
    direction: int             # 1 = long, -1 = short
    entry_price: float
    stop_loss: float
    take_profit: float
    regime: str
    strategy: str              # 'range', 'trend', 'breakout'
    atr_at_signal: float = 0.0


class MicroStrategies:
    """
    Genera señales según el régimen detectado:
        - RANGO: rebote en swings
        - TENDENCIA: continuación tras corrección
        - RUPTURA: ruptura con volumen
    """

    def __init__(self,
                 range_swing_lookback: int = 5,
                 range_tp_ratio: float = 0.5,
                 trend_pullback_min: float = 0.20,
                 trend_pullback_max: float = 0.30,
                 trend_tp_atr_mult: float = 2.0,
                 breakout_lookback: int = 6,
                 breakout_vol_mult: float = 1.5,
                 breakout_tp_ratio: float = 1.5):
        self.range_swing_lookback = range_swing_lookback
        self.range_tp_ratio = range_tp_ratio
        self.trend_pullback_min = trend_pullback_min
        self.trend_pullback_max = trend_pullback_max
        self.trend_tp_atr_mult = trend_tp_atr_mult
        self.breakout_lookback = breakout_lookback
        self.breakout_vol_mult = breakout_vol_mult
        self.breakout_tp_ratio = breakout_tp_ratio

    def range_signal(self, df_1h: pd.DataFrame, idx: int,
                     regime: str, atr_val: float) -> Optional[Signal]:
        """RANGO: detectar rebote en swing high/low de las últimas N velas de 1h."""
        if idx < self.range_swing_lookback + 2:
            return None

        sub = df_1h.iloc[:idx + 1]
        close = sub["close"].iloc[-1]
        high = sub["high"].iloc[-1]
        low = sub["low"].iloc[-1]
        ts = sub.index[-1]

        swing_h = sub["high"].iloc[-self.range_swing_lookback - 1:-1].max()
        swing_l = sub["low"].iloc[-self.range_swing_lookback - 1:-1].min()

        if pd.isna(swing_h) or pd.isna(swing_l) or swing_h == swing_l:
            return None

        tol = 0.002 * close
        range_width = swing_h - swing_l

        # LONG: low tocó swing_l y rebota
        if abs(low - swing_l) < tol and close > swing_l:
            entry = close
            stop_loss = swing_h
            take_profit = entry + self.range_tp_ratio * range_width
            if take_profit > entry and stop_loss > entry:
                return Signal(ts, 1, entry, stop_loss, take_profit,
                              regime, "range", atr_val)

        # SHORT: high tocó swing_h y rebota
        if abs(high - swing_h) < tol and close < swing_h:
            entry = close
            stop_loss = swing_l
            take_profit = entry - self.range_tp_ratio * range_width
            if take_profit < entry and stop_loss < entry:
                return Signal(ts, -1, entry, stop_loss, take_profit,
                              regime, "range", atr_val)

        return None

    def trend_signal(self, df_1h: pd.DataFrame, idx: int,
                     regime: str, atr_val: float) -> Optional[Signal]:
        """TENDENCIA: corrección del 20-30% del ATR del último impulso."""
        if idx < 5 or pd.isna(atr_val) or atr_val == 0:
            return None

        sub = df_1h.iloc[:idx + 1]
        close = sub["close"].iloc[-1]
        ts = sub.index[-1]

        if regime == "TENDENCIA_ALCISTA":
            swing_against = sub["low"].iloc[-10:-1].min()
            if pd.isna(swing_against):
                return None
            recent_high = sub["high"].iloc[-20:-1].max()
            if pd.isna(recent_high):
                return None
            pullback = recent_high - close
            pullback_ratio = pullback / atr_val if atr_val > 0 else 0
            if not (self.trend_pullback_min <= pullback_ratio <= self.trend_pullback_max):
                return None
            entry = close
            stop_loss = swing_against
            take_profit = entry + self.trend_tp_atr_mult * atr_val
            if take_profit > entry and stop_loss < entry:
                return Signal(ts, 1, entry, stop_loss, take_profit,
                              regime, "trend", atr_val)

        elif regime == "TENDENCIA_BAJISTA":
            swing_against = sub["high"].iloc[-10:-1].max()
            if pd.isna(swing_against):
                return None
            recent_low = sub["low"].iloc[-20:-1].min()
            if pd.isna(recent_low):
                return None
            pullback = close - recent_low
            pullback_ratio = pullback / atr_val if atr_val > 0 else 0
            if not (self.trend_pullback_min <= pullback_ratio <= self.trend_pullback_max):
                return None
            entry = close
            stop_loss = swing_against
            take_profit = entry - self.trend_tp_atr_mult * atr_val
            if take_profit < entry and stop_loss > entry:
                return Signal(ts, -1, entry, stop_loss, take_profit,
                              regime, "trend", atr_val)

        return None

    def breakout_signal(self, df_1h: pd.DataFrame, idx: int,
                        regime: str, atr_val: float,
                        vol_ratio: float) -> Optional[Signal]:
        """RUPTURA: cierre fuera del rango de 6 velas con volumen > 1.5× media."""
        if idx < self.breakout_lookback + 1:
            return None
        if pd.isna(vol_ratio) or vol_ratio < self.breakout_vol_mult:
            return None

        sub = df_1h.iloc[:idx + 1]
        close = sub["close"].iloc[-1]
        ts = sub.index[-1]

        range_high = sub["high"].iloc[-self.breakout_lookback - 1:-1].max()
        range_low = sub["low"].iloc[-self.breakout_lookback - 1:-1].min()
        if pd.isna(range_high) or pd.isna(range_low):
            return None

        range_width = range_high - range_low
        range_center = (range_high + range_low) / 2

        if range_width == 0:
            return None

        if close > range_high:
            entry = close
            stop_loss = range_center
            take_profit = entry + self.breakout_tp_ratio * range_width
            if take_profit > entry and stop_loss < entry:
                return Signal(ts, 1, entry, stop_loss, take_profit,
                              regime, "breakout", atr_val)

        if close < range_low:
            entry = close
            stop_loss = range_center
            take_profit = entry - self.breakout_tp_ratio * range_width
            if take_profit < entry and stop_loss > entry:
                return Signal(ts, -1, entry, stop_loss, take_profit,
                              regime, "breakout", atr_val)

        return None

    def generate_signal(self, df_1h: pd.DataFrame, idx: int,
                        regime: str, atr_val: float,
                        vol_ratio: float) -> Optional[Signal]:
        """Genera señal según el régimen actual."""
        if regime == "RANGO":
            return self.range_signal(df_1h, idx, regime, atr_val)
        elif regime in ("TENDENCIA_ALCISTA", "TENDENCIA_BAJISTA"):
            return self.trend_signal(df_1h, idx, regime, atr_val)
        elif regime == "RUPTURA":
            return self.breakout_signal(df_1h, idx, regime, atr_val, vol_ratio)
        return None


# =============================================================================
# MÓDULO 5: POSICIÓN Y GESTOR DE REVERSIÓN
# =============================================================================
@dataclass
class Position:
    """Posición abierta."""
    signal: Signal
    entry_time: pd.Timestamp
    entry_price: float       # precio efectivo con costos
    stop_loss: float
    take_profit: float
    size: float              # tamaño en BTC
    direction: int
    regime_at_entry: str


@dataclass
class ClosedTrade:
    """Trade cerrado."""
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    size: float
    pnl_abs: float
    pnl_pct: float
    exit_reason: str         # 'tp', 'sl', 'reversal', 'timeout'
    regime_at_entry: str
    strategy: str


class PositionManager:
    """
    Gestiona posiciones abiertas y aplica el gestor de reversión:
    si el régimen cambia a una tendencia en dirección contraria,
    cierra la posición actual y abre una nueva.
    """

    def __init__(self,
                 max_bars_in_trade: int = 96,
                 reversal_regimes: tuple = ("TENDENCIA_ALCISTA", "TENDENCIA_BAJISTA")):
        self.max_bars_in_trade = max_bars_in_trade
        self.reversal_regimes = reversal_regimes

    def check_exit(self, position: Position, df_15m: pd.DataFrame,
                   current_time: pd.Timestamp,
                   current_regime: str) -> tuple[Optional[ClosedTrade], Optional[Signal]]:
        """Verifica si la posición debe cerrarse. Devuelve (trade_cerrado, nueva_señal_si_reversión)."""
        future_mask = df_15m.index > position.entry_time
        future_idx = df_15m.index[future_mask]
        if len(future_idx) == 0:
            return None, None

        future_idx = future_idx[:self.max_bars_in_trade]
        future_df = df_15m.loc[future_idx]

        for ts, row in future_df.iterrows():
            high, low, close = row["high"], row["low"], row["close"]

            if position.direction == 1:
                if low <= position.stop_loss:
                    return self._close_trade(position, position.stop_loss, ts, "sl"), None
                if high >= position.take_profit:
                    return self._close_trade(position, position.take_profit, ts, "tp"), None
            else:
                if high >= position.stop_loss:
                    return self._close_trade(position, position.stop_loss, ts, "sl"), None
                if low <= position.take_profit:
                    return self._close_trade(position, position.take_profit, ts, "tp"), None

            if ts >= current_time:
                break

        if len(future_df) > 0:
            last_ts = future_df.index[-1]
            last_close = future_df.iloc[-1]["close"]
            return self._close_trade(position, last_close, last_ts, "timeout"), None

        return None, None

    def check_reversal(self, position: Position,
                       current_regime: str,
                       df_1h: pd.DataFrame, idx: int,
                       atr_val: float) -> tuple[bool, Optional[Signal]]:
        """Verifica si el régimen cambió a tendencia en contra y genera nueva señal."""
        if current_regime not in self.reversal_regimes:
            return False, None

        if position.direction == 1 and current_regime == "TENDENCIA_BAJISTA":
            new_signal = Signal(
                timestamp=df_1h.index[idx],
                direction=-1,
                entry_price=df_1h["close"].iloc[idx],
                stop_loss=df_1h["high"].iloc[-10:-1].max(),
                take_profit=df_1h["close"].iloc[idx] - 2 * atr_val,
                regime=current_regime,
                strategy="reversal",
                atr_at_signal=atr_val,
            )
            return True, new_signal
        elif position.direction == -1 and current_regime == "TENDENCIA_ALCISTA":
            new_signal = Signal(
                timestamp=df_1h.index[idx],
                direction=1,
                entry_price=df_1h["close"].iloc[idx],
                stop_loss=df_1h["low"].iloc[-10:-1].min(),
                take_profit=df_1h["close"].iloc[idx] + 2 * atr_val,
                regime=current_regime,
                strategy="reversal",
                atr_at_signal=atr_val,
            )
            return True, new_signal

        return False, None

    def _close_trade(self, position: Position, exit_price: float,
                     exit_time: pd.Timestamp, reason: str) -> ClosedTrade:
        """Calcula PnL de un trade cerrado."""
        slip = SLIPPAGE_ATR_MULT * position.signal.atr_at_signal
        spread = SPREAD_PCT / 100.0 * position.entry_price

        if position.direction == 1:
            eff_exit = exit_price - slip - spread
            pnl_abs = (eff_exit - position.entry_price) * position.size
        else:
            eff_exit = exit_price + slip + spread
            pnl_abs = (position.entry_price - eff_exit) * position.size

        comm = (COMMISSION_PCT / 100.0) * (position.entry_price + eff_exit) * position.size
        pnl_abs -= comm

        pnl_pct = pnl_abs / INITIAL_EQUITY

        return ClosedTrade(
            entry_time=position.entry_time,
            exit_time=exit_time,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=eff_exit,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            size=position.size,
            pnl_abs=pnl_abs,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            regime_at_entry=position.regime_at_entry,
            strategy=position.signal.strategy,
        )


# =============================================================================
# MÓDULO 6: MOTOR DE BACKTEST
# =============================================================================
class AdaptiveBacktester:
    """
    Motor de backtest que integra:
    - Detector de régimen
    - Micro-estrategias
    - Gestor de posición con reversión
    - Gestión de riesgo (1% por trade)
    """

    def __init__(self, cooldown_bars: int = 12):
        self.regime_detector = RegimeDetector()
        self.strategies = MicroStrategies()
        self.position_manager = PositionManager()
        self.equity = INITIAL_EQUITY
        self.cooldown_bars = cooldown_bars
        self.last_close_idx = -cooldown_bars - 1

    def _compute_position_size(self, entry_price: float,
                                stop_loss: float, direction: int) -> float:
        """Calcula tamaño de posición para arriesgar 1% del equity."""
        risk_amount = self.equity * RISK_PER_TRADE
        price_risk = abs(entry_price - stop_loss)
        if price_risk == 0:
            return 0.0
        return risk_amount / price_risk

    def _apply_entry_costs(self, entry_price: float, atr_val: float,
                            direction: int) -> float:
        """Aplica slippage y spread al precio de entrada."""
        slip = SLIPPAGE_ATR_MULT * atr_val
        spread = SPREAD_PCT / 100.0 * entry_price
        if direction == 1:
            return entry_price + slip + spread
        else:
            return entry_price - slip - spread

    def run(self, data: dict) -> tuple[list[ClosedTrade], pd.DataFrame]:
        """Ejecuta backtest completo. Devuelve (trades, df_con_régimen)."""
        df_1h = candles_to_df(data["1h"])
        df_15m = candles_to_df(data["15m"])
        df_5m = candles_to_df(data["5m"])

        if df_1h.empty or df_15m.empty or df_5m.empty:
            logger.error("Datos insuficientes para backtest.")
            return [], pd.DataFrame()

        logger.info("Iniciando backtest sobre %d velas 1h...", len(df_1h))

        atr_5m = atr(df_5m, ATR_PERIOD)

        logger.info("Detectando régimen para cada hora...")
        regimes = self.regime_detector.detect(df_1h)
        regime_summary = pd.DataFrame({
            "close": df_1h["close"],
            "regime": regimes,
        })

        atr_1h = atr(df_1h, ATR_PERIOD)
        vol_ratio_1h = volume_ratio(df_1h, VOLUME_MA_PERIOD)

        trades: list[ClosedTrade] = []
        position: Optional[Position] = None
        n_signals_generated = 0
        n_reversals = 0

        for i in range(len(df_1h)):
            ts = df_1h.index[i]
            current_regime = regimes.iloc[i]
            atr_val = atr_1h.iloc[i] if not pd.isna(atr_1h.iloc[i]) else 0.0
            vol_ratio = vol_ratio_1h.iloc[i]

            if position is not None:
                closed_trade, _ = self.position_manager.check_exit(
                    position, df_15m, ts, current_regime
                )
                if closed_trade is not None:
                    trades.append(closed_trade)
                    self.equity += closed_trade.pnl_abs
                    position = None
                    self.last_close_idx = i
                else:
                    should_reverse, new_signal = self.position_manager.check_reversal(
                        position, current_regime, df_1h, i, atr_val
                    )
                    if should_reverse and new_signal is not None:
                        current_price = df_1h["close"].iloc[i]
                        closed_trade = self.position_manager._close_trade(
                            position, current_price, ts, "reversal"
                        )
                        trades.append(closed_trade)
                        self.equity += closed_trade.pnl_abs
                        n_reversals += 1
                        self.last_close_idx = i

                        size = self._compute_position_size(
                            new_signal.entry_price, new_signal.stop_loss, new_signal.direction
                        )
                        if size > 0:
                            eff_entry = self._apply_entry_costs(
                                new_signal.entry_price, atr_val, new_signal.direction
                            )
                            position = Position(
                                signal=new_signal,
                                entry_time=ts,
                                entry_price=eff_entry,
                                stop_loss=new_signal.stop_loss,
                                take_profit=new_signal.take_profit,
                                size=size,
                                direction=new_signal.direction,
                                regime_at_entry=current_regime,
                            )
                            n_signals_generated += 1

            if position is None and (i - self.last_close_idx) >= self.cooldown_bars:
                signal = self.strategies.generate_signal(
                    df_1h, i, current_regime, atr_val, vol_ratio
                )
                if signal is not None:
                    size = self._compute_position_size(
                        signal.entry_price, signal.stop_loss, signal.direction
                    )
                    if size > 0:
                        eff_entry = self._apply_entry_costs(
                            signal.entry_price, atr_val, signal.direction
                        )
                        position = Position(
                            signal=signal,
                            entry_time=ts,
                            entry_price=eff_entry,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                            size=size,
                            direction=signal.direction,
                            regime_at_entry=current_regime,
                        )
                        n_signals_generated += 1

            if (i + 1) % 500 == 0:
                logger.info("  Procesadas %d/%d horas | Equity: $%.2f | Trades: %d",
                           i + 1, len(df_1h), self.equity, len(trades))

        if position is not None:
            last_ts = df_1h.index[-1]
            last_price = df_1h["close"].iloc[-1]
            closed_trade = self.position_manager._close_trade(
                position, last_price, last_ts, "end_of_data"
            )
            trades.append(closed_trade)
            self.equity += closed_trade.pnl_abs

        logger.info("Backtest completado: %d trades, %d señales generadas, %d reversiones",
                    len(trades), n_signals_generated, n_reversals)
        return trades, regime_summary


# =============================================================================
# MÓDULO 7: CÁLCULO DE MÉTRICAS
# =============================================================================
def compute_metrics(trades: list[ClosedTrade], period_days: int) -> dict:
    """Calcula métricas agregadas del backtest."""
    if not trades:
        return {
            "num_trades": 0, "trades_per_month": 0,
            "profit_factor": 0, "win_rate": 0,
            "max_drawdown": 0, "total_pnl": 0, "final_equity": INITIAL_EQUITY,
        }

    pnls = np.array([t.pnl_abs for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
        99.0 if gross_profit > 0 else 0.0
    )
    if not np.isfinite(profit_factor):
        profit_factor = 99.0 if gross_profit > 0 else 0.0

    win_rate = len(wins) / len(trades)
    total_pnl = float(pnls.sum())
    n_months = period_days / 30.0

    equity = INITIAL_EQUITY + np.cumsum(pnls)
    running_max = np.maximum.accumulate(equity)
    drawdowns = equity - running_max
    max_drawdown_abs = float(abs(drawdowns.min())) if len(drawdowns) > 0 else 0.0
    max_drawdown_pct = max_drawdown_abs / running_max.max() if running_max.max() > 0 else 0.0

    return {
        "num_trades": len(trades),
        "trades_per_month": round(len(trades) / n_months, 2),
        "profit_factor": round(profit_factor, 4),
        "win_rate": round(win_rate, 4),
        "max_drawdown_abs": round(max_drawdown_abs, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "total_pnl": round(total_pnl, 2),
        "final_equity": round(float(equity[-1]), 2),
        "gross_profit": round(float(gross_profit), 2),
        "gross_loss": round(float(gross_loss), 2),
        "avg_pnl_per_trade": round(float(pnls.mean()), 2),
        "best_trade": round(float(pnls.max()), 2),
        "worst_trade": round(float(pnls.min()), 2),
    }


def print_report(metrics: dict, trades: list[ClosedTrade],
                 regime_summary: pd.DataFrame):
    """Imprime reporte final en consola."""
    print("\n" + "=" * 72)
    print("  🤖 BTC ADAPTIVE AGENT - REPORTE DE BACKTEST")
    print("=" * 72)
    print(f"  Símbolo:        {SYMBOL}")
    print(f"  Período:        {START_DATE} → {END_DATE}")
    print(f"  Timeframes:     {', '.join(TIMEFRAMES)}")
    print(f"  Equity inicial: ${INITIAL_EQUITY:,.2f}")
    print(f"  Risk/trade:     {RISK_PER_TRADE:.1%}")
    print("=" * 72)

    print("\n📊 MÉTRICAS PRINCIPALES:")
    print(f"  Total de trades:        {metrics['num_trades']}")
    print(f"  Trades por mes:         {metrics['trades_per_month']}")
    print(f"  Profit Factor:          {metrics['profit_factor']:.4f}")
    print(f"  Win Rate:               {metrics['win_rate']:.1%}")
    print(f"  Max Drawdown:           ${metrics['max_drawdown_abs']:,.2f} "
          f"({metrics['max_drawdown_pct']:.1%})")
    print(f"  PnL total:              ${metrics['total_pnl']:,.2f}")
    print(f"  Equity final:           ${metrics['final_equity']:,.2f}")
    print(f"  Gross Profit:           ${metrics['gross_profit']:,.2f}")
    print(f"  Gross Loss:             ${metrics['gross_loss']:,.2f}")
    print(f"  Avg PnL/trade:          ${metrics['avg_pnl_per_trade']:,.2f}")
    print(f"  Mejor trade:            ${metrics['best_trade']:,.2f}")
    print(f"  Peor trade:             ${metrics['worst_trade']:,.2f}")

    if trades:
        print("\n📈 DISTRIBUCIÓN POR RÉGIMEN DE ENTRADA:")
        regime_counts = {}
        regime_pnl = {}
        for t in trades:
            r = t.regime_at_entry
            regime_counts[r] = regime_counts.get(r, 0) + 1
            regime_pnl[r] = regime_pnl.get(r, 0) + t.pnl_abs
        for r in sorted(regime_counts.keys()):
            n = regime_counts[r]
            pnl = regime_pnl[r]
            print(f"  {r:25s}: {n:>4} trades, PnL=${pnl:>12,.2f}")

        print("\n🎯 DISTRIBUCIÓN POR ESTRATEGIA:")
        strat_counts = {}
        strat_pnl = {}
        for t in trades:
            s = t.strategy
            strat_counts[s] = strat_counts.get(s, 0) + 1
            strat_pnl[s] = strat_pnl.get(s, 0) + t.pnl_abs
        for s in sorted(strat_counts.keys()):
            n = strat_counts[s]
            pnl = strat_pnl[s]
            print(f"  {s:15s}: {n:>4} trades, PnL=${pnl:>12,.2f}")

        print("\n🚪 DISTRIBUCIÓN POR RAZÓN DE SALIDA:")
        reason_counts = {}
        for t in trades:
            r = t.exit_reason
            reason_counts[r] = reason_counts.get(r, 0) + 1
        for r in sorted(reason_counts.keys()):
            print(f"  {r:15s}: {reason_counts[r]:>4} trades")

    if not regime_summary.empty:
        print("\n🕐 DISTRIBUCIÓN TEMPORAL DE RÉGIMEN:")
        regime_dist = regime_summary["regime"].value_counts()
        total_hours = len(regime_summary)
        for r, count in regime_dist.items():
            pct = count / total_hours
            print(f"  {r:25s}: {count:>5} horas ({pct:.1%})")

    print("\n" + "=" * 72)
    pf_ok = metrics['profit_factor'] > 1.5
    freq_ok = metrics['trades_per_month'] >= 20
    dd_ok = metrics['max_drawdown_pct'] < 0.20
    print("  EVALUACIÓN DE CRITERIOS (referencia):")
    print(f"    PF > 1.5:             {'✅' if pf_ok else '❌'} ({metrics['profit_factor']:.3f})")
    print(f"    ≥20 trades/mes:       {'✅' if freq_ok else '❌'} ({metrics['trades_per_month']})")
    print(f"    DD < 20%:             {'✅' if dd_ok else '❌'} ({metrics['max_drawdown_pct']:.1%})")
    print("=" * 72 + "\n")


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("\n" + "#" * 72)
    print("  🤖 BTC ADAPTIVE TRADING AGENT")
    print("#" * 72)
    print(f"  Símbolo: {SYMBOL}")
    print(f"  Período: {START_DATE} → {END_DATE}")
    print(f"  Equity inicial: ${INITIAL_EQUITY:,.2f}")
    print(f"  Risk por trade: {RISK_PER_TRADE:.1%}")
    print("#" * 72)

    print("\n📥 Descargando datos desde Binance API...")
    fetcher = BinanceFetcher()
    data = fetcher.fetch_all_timeframes(SYMBOL, START_DATE, END_DATE)

    total_candles = sum(len(v) for v in data.values())
    if total_candles == 0:
        print("❌ No se pudieron descargar datos. Verificá la conexión a internet.")
        sys.exit(1)

    print(f"\n✅ Datos descargados: {total_candles} velas totales")
    for tf, candles in data.items():
        print(f"   {tf}: {len(candles)} velas")

    print("\n🔄 Ejecutando backtest adaptativo...")
    backtester = AdaptiveBacktester()
    trades, regime_summary = backtester.run(data)

    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    period_days = (end_dt - start_dt).days

    metrics = compute_metrics(trades, period_days)
    print_report(metrics, trades, regime_summary)

    if trades:
        trades_df = pd.DataFrame([
            {
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "direction": t.direction,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "size": t.size,
                "pnl_abs": t.pnl_abs,
                "pnl_pct": t.pnl_pct,
                "exit_reason": t.exit_reason,
                "regime_at_entry": t.regime_at_entry,
                "strategy": t.strategy,
            }
            for t in trades
        ])
        csv_path = "./btc_adaptive_trades.csv"
        trades_df.to_csv(csv_path, index=False)
        print(f"💾 Trades guardados en: {csv_path}")

    return metrics, trades


if __name__ == "__main__":
    main()
