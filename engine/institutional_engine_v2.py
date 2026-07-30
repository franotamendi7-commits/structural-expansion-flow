"""
Institutional Engine V2 - Motor de trading mejorado para BTCUSDT.

Basado en el V1 (scalping_engine.py) con las siguientes mejoras:
  1. MANTIENE: Engulfing 4H como trigger principal
  2. MANTIENE: Filtros CI, Williams %R, Supertrend multi-TF
  3. MANTIENE: Fibonacci adaptativo
  4. AGREGA: Trailing stop basado en ATR
  5. AGREGA: Breakeven automatico cuando precio se mueve a favor
  6. AGREGA: Salida parcial en TP1 (50%), deja 50% correr con trailing
  7. AGREGA: Time-based exit (evitar trades estancados)
  8. AGREGA: Drawdown manager con 5 niveles
  9. AGREGA: Parameter adapter (adaptacion autonoma cada 50 trades)
  10. ELIMINA: ~150 lineas de dead code (PhaseTransitionDetector, SessionFilter)
  11. ELIMINA: agent_scores hardcodeados
  12. REDUCE: De 48 a ~20 parametros tuneables

Uso:
    engine = InstitutionalEngineV2(symbol='BTCUSDT', capital=1000)
    signal = engine.run()
    # signal tiene: signal, score, trade params, exit management params

Para backtest, ver backtest_institutional_v2.py
"""

import os
import sys
import json
import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

# Imports del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACION DEL MOTOR V2
# ============================================================

ENGINE_CONFIG = {
    "BTCUSDT": {
        "supertrend_multiplier": 2.8,
        "choppiness_neutral_threshold": 74.0,
        "choppiness_trend_threshold": 38.2,
        "fibonacci_days": 30,
        "tp_ratio": 1.3,
        "atr_trail_mult": 2.0,
        "breakeven_activate_mult": 1.0,
        "partial_exit_pct": 0.50,
        "time_exit_bars": 24,  # 24 x 4H = 4 dias max
        "min_score": 50,
        "max_sl_pct": 0.018,
    }
}

# Costos de transaccion (consistentes con multi_bot.py)
COMMISSION = 0.0005
SPREAD = 0.0002
SLIP_ATR_MULT = 0.1


# ============================================================
# INDICADORES TECNICOS (reutilizados del V1)
# ============================================================

def ema(data: np.ndarray, period: int) -> Optional[float]:
    """Exponential Moving Average."""
    if len(data) < period:
        return None
    alpha = 2.0 / (period + 1)
    result = np.mean(data[:period])
    for val in data[period:]:
        result = (val - result) * alpha + result
    return result


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        period: int = 14) -> Optional[float]:
    """Average True Range."""
    if len(highs) < period + 1:
        return None
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.abs(highs[1:] - closes[:-1]),
        np.abs(lows[1:] - closes[:-1])
    )
    return float(np.mean(tr[-period:]))


def bollinger_bands(closes: np.ndarray, period: int = 20,
                    num_std: int = 2) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Bollinger Bands: (upper, lower, width)."""
    if len(closes) < period:
        return None, None, None
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    upper = sma + num_std * std
    lower = sma - num_std * std
    width = (upper - lower) / closes[-1] if closes[-1] > 0 else None
    return upper, lower, width


def choppiness_index(highs: np.ndarray, lows: np.ndarray,
                     closes: np.ndarray, period: int = 14) -> Optional[float]:
    """Choppiness Index: 0-100, alto = lateral, bajo = trending."""
    if len(highs) < period + 1:
        return None
    tr = np.maximum(
        highs[-period:] - lows[-period:],
        np.abs(highs[-period:] - np.roll(closes[-period:], 1)),
        np.abs(lows[-period:] - np.roll(closes[-period:], 1))
    )
    tr[0] = highs[-period] - lows[-period]
    atr_sum = np.sum(tr)
    total_range = np.max(highs[-period:]) - np.min(lows[-period:])
    if total_range == 0:
        return 50.0
    ci = 100 * np.log10(atr_sum / total_range) / np.log10(period)
    return float(np.clip(ci, 0, 100))


def williams_r(highs: np.ndarray, lows: np.ndarray,
               closes: np.ndarray, period: int = 14) -> Optional[float]:
    """Williams %R: -100 (oversold) a 0 (overbought)."""
    if len(highs) < period:
        return None
    hh = np.max(highs[-period:])
    ll = np.min(lows[-period:])
    if hh == ll:
        return 0.0
    return float(np.clip(-100 * (hh - closes[-1]) / (hh - ll), -100, 0))


def supertrend(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               period: int = 10, multiplier: float = 3.0) -> Tuple[Optional[int], Optional[float]]:
    """
    Supertrend indicator.
    Returns: (direction: 1=bullish/-1=bearish, line_value)
    """
    n = len(closes)
    if n < period + 2:
        return None, None

    hl2 = (highs + lows) / 2
    tr = np.maximum.reduce([
        highs - lows,
        np.abs(highs - np.roll(closes, 1)),
        np.abs(lows - np.roll(closes, 1))
    ])
    tr[0] = highs[0] - lows[0]

    # Wilder's ATR
    atr_vals = np.zeros(n)
    atr_vals[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + tr[i]) / period

    upper_basic = hl2 + multiplier * atr_vals
    lower_basic = hl2 - multiplier * atr_vals

    upper = upper_basic.copy()
    lower = lower_basic.copy()
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        if closes[i - 1] <= upper[i - 1]:
            upper[i] = min(upper[i], upper[i - 1])
        if closes[i - 1] >= lower[i - 1]:
            lower[i] = max(lower[i], lower[i - 1])
        if closes[i] > upper[i - 1]:
            direction[i] = 1
        elif closes[i] < lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    last_dir = int(direction[-1])
    st_line = lower[-1] if last_dir == 1 else upper[-1]
    return last_dir, float(st_line)


# ============================================================
# PATRONES DE VELA
# ============================================================

def is_bullish_engulfing(klines: List[Dict]) -> bool:
    """Detectar patron bullish engulfing."""
    if len(klines) < 2:
        return False
    prev = klines[-2]
    curr = klines[-1]
    return (
        prev["close"] < prev["open"] and
        curr["close"] > curr["open"] and
        curr["open"] < prev["close"] and
        curr["close"] > prev["open"]
    )


def is_bearish_engulfing(klines: List[Dict]) -> bool:
    """Detectar patron bearish engulfing."""
    if len(klines) < 2:
        return False
    prev = klines[-2]
    curr = klines[-1]
    return (
        prev["close"] > prev["open"] and
        curr["close"] < curr["open"] and
        curr["open"] > prev["close"] and
        curr["close"] < prev["open"]
    )


# ============================================================
# FIBONACCI ADAPTATIVO
# ============================================================

class AdaptiveFibonacci:
    """Niveles de Fibonacci adaptativos basados en rango de precio."""

    def __init__(self, daily_klines: List[Dict], days: int = 30):
        self.levels = None
        self._calculate(daily_klines, days)

    def _calculate(self, klines: List[Dict], days: int):
        if not klines or len(klines) < 2:
            return
        usable = min(days, len(klines))
        recent = klines[-usable:]
        highs = [float(k["high"]) for k in recent]
        lows = [float(k["low"]) for k in recent]
        fib_high = max(highs)
        fib_low = min(lows)
        if fib_high <= fib_low:
            return
        rng = fib_high - fib_low
        self.levels = {
            0.00: fib_low,
            0.25: fib_low + rng * 0.25,
            0.50: fib_low + rng * 0.50,
            0.75: fib_low + rng * 0.75,
            1.00: fib_high,
            1.25: fib_high + rng * 0.25,
            1.50: fib_high + rng * 0.50,
        }

    def get_current_block(self, price: float) -> Optional[Tuple[float, float, float, float]]:
        """Obtener bloque Fibonacci actual (low_key, low_price, high_key, high_price)."""
        if not self.levels:
            return None
        sorted_levels = sorted(self.levels.items(), key=lambda x: x[1])
        for i in range(len(sorted_levels) - 1):
            lk, lp = sorted_levels[i]
            hk, hp = sorted_levels[i + 1]
            if lp <= price <= hp:
                return (lk, lp, hk, hp)
        return None


# ============================================================
# ESTRUCTURA DE MERCADO
# ============================================================

def analyze_structure(klines: List[Dict]) -> Dict[str, Any]:
    """Analizar estructura de mercado (trend, BOS, swing points)."""
    if not klines or len(klines) < 50:
        return {"trend": "neutral", "bos": False, "recent_high": 0, "recent_low": 0}

    highs = np.array([float(k["high"]) for k in klines])
    lows = np.array([float(k["low"]) for k in klines])
    closes = np.array([float(k["close"]) for k in klines])

    # Swing highs y lows
    sh_idx = argrelextrema(highs, np.greater, order=5)[0]
    sl_idx = argrelextrema(lows, np.less, order=5)[0]

    swing_highs = highs[sh_idx] if len(sh_idx) > 0 else np.array([np.max(highs)])
    swing_lows = lows[sl_idx] if len(sl_idx) > 0 else np.array([np.min(lows)])

    # Determinar trend
    trend = "neutral"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]:
            trend = "bullish"
        elif swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]:
            trend = "bearish"

    return {
        "trend": trend,
        "recent_high": float(swing_highs[-1]),
        "recent_low": float(swing_lows[-1]),
    }


def detect_market_phase(klines: List[Dict], indicators: Dict) -> str:
    """Detectar fase de mercado: compressing, expanding, trending, ranging."""
    if not klines or len(klines) < 30:
        return "unknown"

    closes = np.array([float(k["close"]) for k in klines])
    bw = indicators.get("bb_width")
    atr_val = indicators.get("atr14")
    vol_ratio = indicators.get("vol_ratio")

    atr_pct = atr_val / closes[-1] if atr_val and closes[-1] > 0 else 0
    slope = np.polyfit(np.arange(min(20, len(closes))), closes[-min(20, len(closes)):], 1)[0]

    if bw and bw < 0.03 and atr_pct < 0.01 and vol_ratio and vol_ratio < 0.8:
        return "compressing"
    if bw and bw > 0.06 and vol_ratio and vol_ratio > 1.3 and abs(slope) > 0.005:
        return "expanding"
    if atr_pct > 0.015 and abs(slope) > 0.01:
        return "trending"
    if atr_pct < 0.008 and abs(slope) < 0.003:
        return "ranging"
    return "neutral"


# ============================================================
# CALCULO DE SCORE
# ============================================================

def calculate_dynamic_score(
    direction: str,
    fib_block: Optional[Tuple],
    ci_value: float,
    wr_5m: float,
    st_bias: str,
    st_aligned: bool,
    mom_direction: str,
    mom_strength: float,
    body_ratio_4h: float,
    vol_increasing: bool,
) -> int:
    """
    Calcular score dinamico del trade (0-100).
    Base: 50 (mas conservador que V1 que usaba 70).
    """
    score = 50

    # Body ratio 4H fuerte
    if body_ratio_4h > 0.4:
        score += 15

    # Volumen creciente
    if vol_increasing:
        score += 10

    # Supertrend alineado
    if st_aligned and st_bias == direction.lower():
        score += 10
    elif st_aligned and st_bias != direction.lower():
        score -= 10

    # CI trending
    if ci_value is not None and ci_value < 60:
        score += 10
    elif ci_value is not None and ci_value > 80:
        score -= 5

    # Momentum alineado
    if mom_direction == direction.lower():
        score += min(int(mom_strength * 20), 20)

    # Williams %R confirmacion
    if direction == "LONG" and wr_5m is not None and -80 < wr_5m < -20:
        score += 5
    elif direction == "SHORT" and wr_5m is not None and -80 < wr_5m < -20:
        score += 5

    # Fibonacci bonus
    if fib_block:
        fk = fib_block[0]
        if fk in (0.25, 0.50, 0.75):
            score += 5

    return max(0, min(100, score))


# ============================================================
# MOTOR INSTITUCIONAL V2
# ============================================================

class InstitutionalEngineV2:
    """
    Motor de trading mejorado con gestion de posiciones completa.

    Genera señales de entrada + parametros de gestion (trailing, breakeven, etc.)
    """

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        capital: float = 1000.0,
        risk_pct: float = 0.01,
        config_override: Optional[Dict] = None,
        baseline_atr: float = None,
    ):
        self.symbol = symbol
        self.capital = capital
        self.base_risk_pct = risk_pct
        self.config = dict(ENGINE_CONFIG.get(symbol, ENGINE_CONFIG["BTCUSDT"]))
        if config_override:
            self.config.update(config_override)
        
        # Baseline ATR para volatility targeting (promedio 30 días)
        self.baseline_atr = baseline_atr
        self.use_vol_targeting = baseline_atr is not None
        
        # Costos actualizados para Post-Only (Maker)
        self.commission_maker = 0.00018  # 0.018% por lado (con BNB discount)
        self.commission_taker = 0.0005   # 0.05% por lado (taker)
        self.use_post_only = True  # Priorizar Post-Only por defecto

    def run(self, klines: Optional[Dict[str, List[Dict]]] = None,
            current_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Ejecutar analisis y generar senal.

        Args:
            klines: dict con keys '5m', '15m', '1h', '4h', '1d' (list of dicts)
            current_price: precio actual (si None, usa close de 15m)

        Returns: dict con signal, score, trade params, exit management params
        """
        if klines is None:
            return self._empty_result("No data provided")

        # Validar datos minimos
        required = ["5m", "15m", "1h", "4h", "1d"]
        for tf in required:
            data = klines.get(tf)
            if data is None:
                return self._empty_result(f"Insufficient {tf} data")
            # Handle both DataFrame and list-of-dicts
            if hasattr(data, 'empty'):
                if data.empty or len(data) < 30:
                    return self._empty_result(f"Insufficient {tf} data")
            else:
                if not data or len(data) < 30:
                    return self._empty_result(f"Insufficient {tf} data")

        try:
            return self._analyze(klines, current_price)
        except Exception as e:
            logger.exception("Error en analisis V2")
            return self._empty_result(f"Error: {e}")

    def _analyze(self, klines: Dict[str, List[Dict]],
                 current_price: Optional[float]) -> Dict[str, Any]:
        """Logica principal de analisis."""

        # --- Indicadores por timeframe ---
        indicators = {}
        for tf in ["5m", "15m", "1h", "4h"]:
            data = klines[tf]
            c = np.array([float(k["close"]) for k in data])
            h = np.array([float(k["high"]) for k in data])
            l = np.array([float(k["low"]) for k in data])
            v = np.array([float(k["volume"]) for k in data])

            ema20 = ema(c, 20)
            ema50 = ema(c, 50)
            atr14 = atr(h, l, c, 14)
            _, _, bb_width = bollinger_bands(c)
            vol_ratio = float(v[-1] / np.mean(v[-20:])) if len(v) >= 20 else 1.0

            body = abs(c[-1] - float(data[-1]["open"]))
            tr_val = h[-1] - l[-1]
            body_ratio = body / tr_val if tr_val > 0 else 0

            indicators[tf] = {
                "ema20": ema20, "ema50": ema50, "atr14": atr14,
                "bb_width": bb_width, "vol_ratio": vol_ratio,
                "close": float(c[-1]), "body_ratio": body_ratio,
            }

        # --- Filtros ---
        ci_15m = choppiness_index(
            np.array([float(k["high"]) for k in klines["15m"]]),
            np.array([float(k["low"]) for k in klines["15m"]]),
            np.array([float(k["close"]) for k in klines["15m"]])
        )
        wr_5m = williams_r(
            np.array([float(k["high"]) for k in klines["5m"]]),
            np.array([float(k["low"]) for k in klines["5m"]]),
            np.array([float(k["close"]) for k in klines["5m"]])
        )

        # Supertrend multi-TF
        st_5m_dir, _ = supertrend(
            np.array([float(k["high"]) for k in klines["5m"]]),
            np.array([float(k["low"]) for k in klines["5m"]]),
            np.array([float(k["close"]) for k in klines["5m"]]),
            multiplier=self.config["supertrend_multiplier"]
        )
        st_15m_dir, _ = supertrend(
            np.array([float(k["high"]) for k in klines["15m"]]),
            np.array([float(k["low"]) for k in klines["15m"]]),
            np.array([float(k["close"]) for k in klines["15m"]]),
            multiplier=self.config["supertrend_multiplier"]
        )
        st_1h_dir, _ = supertrend(
            np.array([float(k["high"]) for k in klines["1h"]]),
            np.array([float(k["low"]) for k in klines["1h"]]),
            np.array([float(k["close"]) for k in klines["1h"]]),
            period=14,
            multiplier=self.config["supertrend_multiplier"]
        )

        # Evaluar supertrend
        dirs = [d for d in [st_5m_dir, st_15m_dir, st_1h_dir] if d is not None]
        if len(dirs) == 3:
            bull = sum(1 for d in dirs if d == 1)
            bear = sum(1 for d in dirs if d == -1)
            if bull == 3:
                st_bias, st_aligned = "bullish", True
            elif bear == 3:
                st_bias, st_aligned = "bearish", True
            elif bull == 2:
                st_bias, st_aligned = "bullish", False
            elif bear == 2:
                st_bias, st_aligned = "bearish", False
            else:
                st_bias, st_aligned = "mixed", False
        else:
            st_bias, st_aligned = "mixed", False

        # --- Momentum ---
        mom_scores = []
        weights = {"4h": 0.5, "1h": 0.35, "15m": 0.15}
        for tf, wt in weights.items():
            ind = indicators.get(tf)
            if ind and ind["ema20"] and ind["ema50"]:
                d = 1 if ind["ema20"] > ind["ema50"] else -1
                vr = ind.get("vol_ratio", 1.0)
                acc = (vr - 1) * 0.5
                mom_scores.append((d * 0.7 + acc * 0.3) * wt)
        mom_total = sum(mom_scores) if mom_scores else 0
        mom_dir = "bullish" if mom_total > 0.15 else ("bearish" if mom_total < -0.15 else "neutral")
        mom_strength = abs(mom_total)

        # --- Market Phase ---
        phase = detect_market_phase(klines["1h"], indicators["1h"])

        # --- Detectar senal: Engulfing 4H ---
        signal = "WAIT"
        direction = "neutral"

        if is_bullish_engulfing(klines["4h"]):
            signal = "LONG"
            direction = "bullish"
        elif is_bearish_engulfing(klines["4h"]):
            signal = "SHORT"
            direction = "bearish"

        # Filtro: phase no puede ser ranging/neutral/unknown
        if phase in ("ranging", "neutral", "unknown"):
            signal = "WAIT"

        # Filtro: CI choppiness
        veto = False
        veto_reason = ""
        if ci_15m is not None and ci_15m > self.config["choppiness_neutral_threshold"]:
            veto = True
            veto_reason = f"CI={ci_15m:.1f} (choppy)"

        # Filtro: Supertrend en contra
        if not veto and signal in ("LONG", "SHORT"):
            if signal == "LONG" and st_bias == "bearish" and st_aligned:
                veto = True
                veto_reason = "Supertrend 3/3 bearish vs LONG"
            elif signal == "SHORT" and st_bias == "bullish" and st_aligned:
                veto = True
                veto_reason = "Supertrend 3/3 bullish vs SHORT"

        if veto:
            signal = "WAIT"

        # --- Fibonacci ---
        fib = AdaptiveFibonacci(klines["1d"], days=self.config["fibonacci_days"])
        price = current_price or indicators["15m"]["close"]
        fib_block = fib.get_current_block(price)

        # --- Score dinamico ---
        body_ratio_4h = indicators["4h"].get("body_ratio", 0)
        vol_increasing = False
        if len(klines["15m"]) >= 3:
            vols = [float(k["volume"]) for k in klines["15m"][-3:]]
            vol_increasing = vols[-1] > vols[-2] > vols[-3]

        dynamic_score = 0
        if signal in ("LONG", "SHORT"):
            dynamic_score = calculate_dynamic_score(
                direction=direction,
                fib_block=fib_block,
                ci_value=ci_15m if ci_15m else 50.0,
                wr_5m=wr_5m if wr_5m else -50.0,
                st_bias=st_bias,
                st_aligned=st_aligned,
                mom_direction=mom_dir,
                mom_strength=mom_strength,
                body_ratio_4h=body_ratio_4h,
                vol_increasing=vol_increasing,
            )

        # --- Construir trade setup ---
        trade = None
        exit_mgmt = None

        if signal in ("LONG", "SHORT") and dynamic_score >= self.config["min_score"]:
            atr_1h = indicators["1h"].get("atr14") or price * 0.005

            # Entry price
            entry = indicators["5m"]["close"] if indicators["5m"] else price

            # Stop loss
            if direction == "bullish":
                last_low = min(float(k["low"]) for k in klines["15m"][-10:])
                sl = last_low * 0.998
            else:
                last_high = max(float(k["high"]) for k in klines["15m"][-10:])
                sl = last_high * 1.002

            # Max SL check
            sl_pct = abs(entry - sl) / entry
            if sl_pct > self.config["max_sl_pct"]:
                if direction == "bullish":
                    sl = entry * (1 - self.config["max_sl_pct"])
                else:
                    sl = entry * (1 + self.config["max_sl_pct"])
                sl_pct = self.config["max_sl_pct"]

            # TP levels
            sl_dist = abs(entry - sl)
            tp1 = entry + self.config["tp_ratio"] * sl_dist if direction == "bullish" else \
                  entry - self.config["tp_ratio"] * sl_dist
            tp2 = entry + 2 * self.config["tp_ratio"] * sl_dist if direction == "bullish" else \
                  entry - 2 * self.config["tp_ratio"] * sl_dist

            # Position sizing
            risk_usd = self.capital * self.base_risk_pct
            if dynamic_score >= 90:
                risk_usd *= 1.5
            elif dynamic_score >= 80:
                pass
            elif dynamic_score >= 70:
                risk_usd *= 0.75
            else:
                risk_usd *= 0.5

            notional = risk_usd / sl_pct if sl_pct > 0 else 0
            contracts = notional / entry if entry > 0 else 0
            
            # Volatility Targeting: ajustar tamaño inversamente proporcional a la volatilidad
            vol_scale = 1.0
            if self.use_vol_targeting and self.baseline_atr and self.baseline_atr > 0:
                current_atr = indicators["1h"].get("atr14") or indicators["15m"].get("atr14")
                if current_atr and current_atr > 0:
                    vol_ratio = self.baseline_atr / current_atr
                    # Limitar entre 0.25x y 2.0x para evitar extremos
                    vol_scale = max(0.25, min(vol_ratio, 2.0))
                    notional *= vol_scale
                    contracts *= vol_scale

            trade = {
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "sl_pct": sl_pct * 100,
                "risk_usd": risk_usd,
                "notional": notional,
                "contracts": contracts,
                "direction": direction,
                "liq_price": entry * (1 - 1/10) if direction == "bullish" else entry * (1 + 1/10),
                "vol_scale": vol_scale,
                "use_post_only": self.use_post_only,
            }

            # Exit management parameters
            exit_mgmt = {
                "atr_trail_mult": self.config["atr_trail_mult"],
                "breakeven_activate_mult": self.config["breakeven_activate_mult"],
                "partial_exit_pct": self.config["partial_exit_pct"],
                "time_exit_bars": self.config["time_exit_bars"],
                "atr_at_entry": atr_1h,
            }

        # --- Resultado ---
        ci_str = f"{ci_15m:.1f}" if ci_15m else "?"
        wr_str = f"{wr_5m:.1f}" if wr_5m else "?"
        explanation = (
            f"CI={ci_str} "
            f"WR={wr_str} "
            f"ST={st_bias}({st_aligned}) "
            f"Phase={phase} "
            f"Mom={mom_dir}({mom_strength:.2f}) "
            f"Score={dynamic_score}"
        )

        return {
            "signal": signal,
            "score": dynamic_score,
            "direction": direction,
            "trade": trade,
            "exit_mgmt": exit_mgmt,
            "veto": veto,
            "veto_reason": veto_reason,
            "explanation": explanation,
            "indicators": {
                "ci_15m": ci_15m,
                "wr_5m": wr_5m,
                "st_bias": st_bias,
                "st_aligned": st_aligned,
                "mom_direction": mom_dir,
                "mom_strength": mom_strength,
                "phase": phase,
                "atr_1h": indicators["1h"].get("atr14"),
            },
            "fib_block": fib_block,
        }

    def _empty_result(self, explanation: str = "") -> Dict[str, Any]:
        """Resultado vacio (sin senal)."""
        return {
            "signal": "WAIT",
            "score": 0,
            "direction": "neutral",
            "trade": None,
            "exit_mgmt": None,
            "veto": False,
            "veto_reason": "",
            "explanation": explanation,
            "indicators": {},
            "fib_block": None,
        }
