#!/usr/bin/env python3
"""
Agente de Trading Adaptativo para BTC + Filtro ML
=================================================
Agente de trading adaptativo para BTCUSDT con filtro de Machine Learning.

Versión ML del btc_adaptive_agent.py: integra un modelo Random Forest
(ml_model.pkl) como capa adicional de confirmación. Antes de aceptar una
señal generada por el detector de régimen, se calculan 16 características
institucionales (Fibonacci adaptativo, Choppiness, Williams %R, Supertrend,
Momentum, etc.) y se pasan por el modelo. Si la probabilidad predicha es
mayor a 0.55, la señal se ejecuta; si no, se descarta.

Requisitos:
    pip install numpy pandas requests scikit-learn joblib

Uso:
    python3 btc_adaptive_agent_ml.py

Salida:
    - Métricas de backtest sobre 6 meses (2026-01-01 a 2026-06-23)
    - Comparativa con/sin filtro ML
    - Detalle de operaciones por régimen
"""
from __future__ import annotations

import time
import logging
import sys
import os
import pickle
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
RISK_PER_TRADE = 0.01      # 1% del capital por operación
INITIAL_EQUITY = 10000.0   # USD inicial para el backtest

# Períodos de indicadores
ADX_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
EMA_PERIOD = 20
ATR_PERIOD = 14
VOLUME_MA_PERIOD = 20

# =============================================================================
# CONFIGURACIÓN ML
# =============================================================================
ML_MODEL_PATH = "ml_model.pkl"   # ruta al modelo Random Forest entrenado
ML_THRESHOLD = 0.55              # umbral de probabilidad para aceptar señal
ML_FALLBACK_ENABLED = True       # si True y no hay modelo, usa regla heurística

# Las 16 características esperadas por el modelo (orden IMPORTANTE)
ML_FEATURE_COLUMNS = [
    "fib_low_key", "fib_high_key", "fib_width",
    "phase_h1_ranging", "phase_h1_neutral", "phase_h1_trending",
    "ci_value", "wr_5m", "wr_15m",
    "st_aligned", "st_bias_bullish", "st_bias_bearish",
    "mom_score", "mom_direction_bullish", "mom_direction_bearish",
    "vol_ratio_5m", "body_ratio_4h", "hour_of_day", "direction_long",
]

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
            "User-Agent": "BTCAdaptiveAgent/1.0 (educativo)"
        })
        self.request_delay = request_delay
        self.max_retries = max_retries

    @staticmethod
    def date_to_ms(date_str: str) -> int:
        """Convierte 'YYYY-MM-DD' a milisegundos epoch (UTC)."""
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
                    logger.warning("Límite de tasa (429). Esperando %ss.", wait)
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
        raise RuntimeError(f"Falló la solicitud tras {self.max_retries} intentos")

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
    """Average True Range (Rango Verdadero Promedio)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Índice Direccional Promedio (versión Wilder)."""
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
    """Ancho de Bollinger normalizado = (superior - inferior) / media."""
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
    """Máximo de las últimas N velas (excluyendo la actual)."""
    return df["high"].rolling(lookback, min_periods=lookback).max().shift(1)


def swing_low(df: pd.DataFrame, lookback: int = 2) -> pd.Series:
    """Mínimo de las últimas N velas (excluyendo la actual)."""
    return df["low"].rolling(lookback, min_periods=lookback).min().shift(1)


# =============================================================================
# MÓDULO 2b: CARACTERÍSTICAS INSTITUCIONALES (para ML)
# =============================================================================
def resample_ohlc(df_1h: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Remuestrea velas 1h al timeframe indicado (4h por defecto para características)."""
    if timeframe == "1h":
        return df_1h
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df_1h.resample(timeframe, label="left", closed="left").agg(agg).dropna()


def choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Índice de Choppiness (CHOP)."""
    atr_s = atr(df, period)
    hh = df["high"].rolling(period, min_periods=period).max()
    ll = df["low"].rolling(period, min_periods=period).min()
    sum_atr = atr_s.rolling(period, min_periods=period).sum()
    range_ = (hh - ll).replace(0, np.nan)
    return 100 * np.log10(sum_atr / range_) / np.log10(period)


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R = (HH - Close) / (HH - LL) * -100."""
    hh = df["high"].rolling(period, min_periods=period).max()
    ll = df["low"].rolling(period, min_periods=period).min()
    range_ = (hh - ll).replace(0, np.nan)
    return (hh - df["close"]) / range_ * -100.0


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """Supertrend. Positivo = alcista, negativo = bajista."""
    high, low, close = df["high"], df["low"], df["close"]
    atr_s = atr(df, period)
    hl2 = (high + low) / 2.0
    upper_band = hl2 + multiplier * atr_s
    lower_band = hl2 - multiplier * atr_s

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    trend = pd.Series(index=df.index, dtype=float)

    for i in range(1, len(df)):
        if (close.iloc[i] > final_upper.iloc[i - 1]) or np.isnan(final_upper.iloc[i - 1]):
            final_upper.iloc[i] = min(upper_band.iloc[i], final_upper.iloc[i - 1]) if not np.isnan(final_upper.iloc[i - 1]) else upper_band.iloc[i]
        else:
            final_upper.iloc[i] = upper_band.iloc[i]
        if (close.iloc[i] < final_lower.iloc[i - 1]) or np.isnan(final_lower.iloc[i - 1]):
            final_lower.iloc[i] = max(lower_band.iloc[i], final_lower.iloc[i - 1]) if not np.isnan(final_lower.iloc[i - 1]) else lower_band.iloc[i]
        else:
            final_lower.iloc[i] = lower_band.iloc[i]

        prev_trend = trend.iloc[i - 1] if i > 1 else np.nan
        if np.isnan(prev_trend) or prev_trend >= 0:
            if close.iloc[i] < final_lower.iloc[i]:
                trend.iloc[i] = -final_upper.iloc[i]
            else:
                trend.iloc[i] = final_lower.iloc[i] if not np.isnan(final_lower.iloc[i]) else np.nan
        else:
            if close.iloc[i] > final_upper.iloc[i]:
                trend.iloc[i] = final_lower.iloc[i]
            else:
                trend.iloc[i] = -final_upper.iloc[i] if not np.isnan(final_upper.iloc[i]) else np.nan

    trend.iloc[0] = np.nan
    return trend


def weekly_adaptive_fibonacci(df_1h: pd.DataFrame, lookback: int = 168) -> tuple[Optional[float], Optional[float], float]:
    """Fibonacci adaptativo semanal (9 niveles, 168 velas de 1h)."""
    FIB_LEVELS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    if df_1h is None or len(df_1h) < 10:
        return None, None, 0.0
    n = min(lookback, len(df_1h))
    sub = df_1h.iloc[-n:]
    swing_high = float(sub["high"].max())
    swing_low = float(sub["low"].min())
    current_close = float(sub["close"].iloc[-1])
    if swing_high <= swing_low or swing_low <= 0:
        return None, None, 0.0
    diff = swing_high - swing_low
    fib_prices = [swing_low + level * diff for level in FIB_LEVELS]
    fib_low_key = None
    fib_high_key = None
    levels_below = [p for p in fib_prices if p <= current_close]
    levels_above = [p for p in fib_prices if p > current_close]
    if levels_below:
        fib_low_key = max(levels_below)
    if levels_above:
        fib_high_key = min(levels_above)
    if fib_low_key is None and fib_high_key is not None:
        fib_low_key = fib_prices[0]
    if fib_high_key is None and fib_low_key is not None:
        fib_high_key = fib_prices[-1]
    fib_width = diff / swing_low if swing_low > 0 else 0.0
    return fib_low_key, fib_high_key, fib_width


def momentum_analyzer(df_1h: pd.DataFrame, period: int = 14) -> tuple[float, str]:
    """Momentum usando ROC suavizado."""
    if len(df_1h) < period + 1:
        return 0.0, "neutral"
    closes = df_1h["close"]
    roc = (closes.iloc[-1] / closes.iloc[-period - 1] - 1) * 100
    mom_score = float(np.tanh(roc / 5.0))
    if mom_score > 0.1:
        return mom_score, "bullish"
    elif mom_score < -0.1:
        return mom_score, "bearish"
    else:
        return mom_score, "neutral"


def classify_phase_h1(df_1h: pd.DataFrame) -> str:
    """Clasifica la fase de mercado en H1: trending, ranging, neutral."""
    if len(df_1h) < 50:
        return "neutral"
    adx_val = adx(df_1h, 14).iloc[-1]
    bb_w = bollinger_width(df_1h, 20, 2.0).iloc[-1]
    if pd.isna(adx_val) or pd.isna(bb_w):
        return "neutral"
    if adx_val > 25 and bb_w > 0.03:
        return "trending"
    elif adx_val < 20 and bb_w < 0.025:
        return "ranging"
    else:
        return "neutral"


class FeatureExtractor:
    """Calcula las 16 características institucionales para el modelo ML."""

    def __init__(self):
        self._cache = {}

    def _get_or_calc(self, key: str, df: pd.DataFrame, func, *args, **kwargs):
        cache_key = (key, id(df))
        if cache_key not in self._cache:
            self._cache[cache_key] = func(df, *args, **kwargs)
        return self._cache[cache_key]

    def extract(self,
                df_1h: pd.DataFrame,
                df_15m: pd.DataFrame,
                df_5m: pd.DataFrame,
                df_4h: pd.DataFrame,
                signal_direction: int,
                current_idx_1h: int) -> dict:
        sub_1h = df_1h.iloc[:current_idx_1h + 1]
        sub_15m = df_15m.loc[df_15m.index <= df_1h.index[current_idx_1h]]
        sub_5m = df_5m.loc[df_5m.index <= df_1h.index[current_idx_1h]]
        sub_4h = df_4h.loc[df_4h.index <= df_1h.index[current_idx_1h]]
        ts = df_1h.index[current_idx_1h]

        fib_low_key, fib_high_key, fib_width = weekly_adaptive_fibonacci(sub_1h, lookback=168)
        phase_h1 = classify_phase_h1(sub_1h)
        ci_val = choppiness_index(sub_1h, 14).iloc[-1]
        if pd.isna(ci_val):
            ci_val = 50.0
        wr_5m = williams_r(sub_5m, 14).iloc[-1] if len(sub_5m) >= 15 else -50.0
        wr_15m = williams_r(sub_15m, 14).iloc[-1] if len(sub_15m) >= 15 else -50.0
        if pd.isna(wr_5m): wr_5m = -50.0
        if pd.isna(wr_15m): wr_15m = -50.0

        st_4h = supertrend(sub_4h, 10, 3.0).iloc[-1] if len(sub_4h) >= 20 else np.nan
        st_aligned = 0
        st_bias_bullish = 0
        st_bias_bearish = 0
        if not pd.isna(st_4h):
            if st_4h > 0:
                st_bias_bullish = 1
                if signal_direction == 1:
                    st_aligned = 1
            else:
                st_bias_bearish = 1
                if signal_direction == -1:
                    st_aligned = 1

        mom_score, mom_direction = momentum_analyzer(sub_1h, 14)
        vol_ratio_5m = volume_ratio(sub_5m, 20).iloc[-1] if len(sub_5m) >= 21 else 1.0
        if pd.isna(vol_ratio_5m): vol_ratio_5m = 1.0

        if len(sub_4h) > 0:
            last_4h = sub_4h.iloc[-1]
            body = abs(last_4h["close"] - last_4h["open"])
            range_ = last_4h["high"] - last_4h["low"]
            body_ratio_4h = body / range_ if range_ > 0 else 0.0
        else:
            body_ratio_4h = 0.0

        hour_of_day = ts.hour
        direction_long = 1 if signal_direction == 1 else 0

        return {
            "fib_low_key": fib_low_key,
            "fib_high_key": fib_high_key,
            "fib_width": fib_width,
            "phase_h1": phase_h1,
            "ci_value": ci_val,
            "wr_5m": wr_5m,
            "wr_15m": wr_15m,
            "st_aligned": st_aligned,
            "st_bias_bullish": st_bias_bullish,
            "st_bias_bearish": st_bias_bearish,
            "mom_score": mom_score,
            "mom_direction": mom_direction,
            "vol_ratio_5m": vol_ratio_5m,
            "body_ratio_4h": body_ratio_4h,
            "hour_of_day": hour_of_day,
            "direction_long": direction_long,
        }

    def to_model_vector(self, features: dict) -> pd.DataFrame:
        phase = features.get("phase_h1", "neutral")
        phase_ranging = 1 if phase == "ranging" else 0
        phase_neutral = 1 if phase == "neutral" else 0
        phase_trending = 1 if phase == "trending" else 0
        mom_dir = features.get("mom_direction", "neutral")
        mom_bullish = 1 if mom_dir == "bullish" else 0
        mom_bearish = 1 if mom_dir == "bearish" else 0
        fib_low = features["fib_low_key"] if features["fib_low_key"] is not None else 0.0
        fib_high = features["fib_high_key"] if features["fib_high_key"] is not None else 0.0

        row = {
            "fib_low_key": fib_low,
            "fib_high_key": fib_high,
            "fib_width": features["fib_width"],
            "phase_h1_ranging": phase_ranging,
            "phase_h1_neutral": phase_neutral,
            "phase_h1_trending": phase_trending,
            "ci_value": features["ci_value"],
            "wr_5m": features["wr_5m"],
            "wr_15m": features["wr_15m"],
            "st_aligned": features["st_aligned"],
            "st_bias_bullish": features["st_bias_bullish"],
            "st_bias_bearish": features["st_bias_bearish"],
            "mom_score": features["mom_score"],
            "mom_direction_bullish": mom_bullish,
            "mom_direction_bearish": mom_bearish,
            "vol_ratio_5m": features["vol_ratio_5m"],
            "body_ratio_4h": features["body_ratio_4h"],
            "hour_of_day": features["hour_of_day"],
            "direction_long": features["direction_long"],
        }
        return pd.DataFrame([row])


# =============================================================================
# MÓDULO 2c: CLASIFICADOR ML
# =============================================================================
class MLClassifier:
    """Clasificador que carga ml_model.pkl (Random Forest) y predice aceptación."""

    def __init__(self, model_path: str = ML_MODEL_PATH,
                 threshold: float = ML_THRESHOLD):
        self.threshold = threshold
        self.model = None
        self.feature_extractor = FeatureExtractor()
        self.uses_fallback = False
        self.n_accepted = 0
        self.n_rejected = 0
        self.n_total = 0
        self._load_model(model_path)

    def _load_model(self, path: str):
        if not os.path.exists(path):
            logger.warning("⚠️  Modelo ML no encontrado en %s", path)
            if ML_FALLBACK_ENABLED:
                logger.warning("   Usando regla heurística de respaldo (ML_FALLBACK_ENABLED=True)")
                self.uses_fallback = True
            else:
                logger.error("   ML_FALLBACK_ENABLED=False. Todas las señales serán rechazadas.")
            return
        try:
            with open(path, "rb") as f:
                self.model = pickle.load(f)
            logger.info("✅ Modelo ML cargado desde %s", path)
            if hasattr(self.model, "n_estimators"):
                logger.info("   Random Forest con %d árboles", self.model.n_estimators)
            if hasattr(self.model, "feature_names_in_"):
                logger.info("   Características esperadas: %d", len(self.model.feature_names_in_))
        except Exception as exc:
            logger.error("❌ Error cargando modelo ML: %s", exc)
            if ML_FALLBACK_ENABLED:
                logger.warning("   Usando regla heurística de respaldo")
                self.uses_fallback = True

    def predict(self, features: dict) -> tuple[bool, float]:
        self.n_total += 1
        if self.uses_fallback:
            prob = self._heuristic_probability(features)
        else:
            try:
                X = self.feature_extractor.to_model_vector(features)
                if hasattr(self.model, "feature_names_in_"):
                    expected = list(self.model.feature_names_in_)
                    for col in expected:
                        if col not in X.columns:
                            X[col] = 0
                    X = X[expected]
                proba = self.model.predict_proba(X)[0]
                prob = float(proba[1]) if len(proba) > 1 else float(proba[0])
            except Exception as exc:
                logger.warning("Error en predicción: %s. Usando respaldo.", exc)
                prob = self._heuristic_probability(features)
        accepted = prob >= self.threshold
        if accepted:
            self.n_accepted += 1
        else:
            self.n_rejected += 1
        return accepted, prob

    def _heuristic_probability(self, features: dict) -> float:
        score = 0.5
        if features.get("st_aligned", 0) == 1:
            score += 0.15
        mom_dir = features.get("mom_direction", "neutral")
        direction_long = features.get("direction_long", 0)
        if direction_long == 1 and mom_dir == "bullish":
            score += 0.10
        elif direction_long == 0 and mom_dir == "bearish":
            score += 0.10
        phase = features.get("phase_h1", "neutral")
        if phase == "trending":
            score += 0.08
        elif phase == "ranging":
            score -= 0.05
        wr_5m = features.get("wr_5m", -50)
        if direction_long == 1 and wr_5m < -80:
            score += 0.05
        elif direction_long == 0 and wr_5m > -20:
            score += 0.05
        body_ratio = features.get("body_ratio_4h", 0)
        if body_ratio > 0.6:
            score += 0.05
        vol_ratio = features.get("vol_ratio_5m", 1.0)
        if vol_ratio > 1.5:
            score += 0.05
        fib_width = features.get("fib_width", 0.0)
        if 0.02 <= fib_width <= 0.15:
            score += 0.05
        elif fib_width > 0.20:
            score -= 0.03
        return max(0.0, min(1.0, score))

    def stats(self) -> dict:
        return {
            "total_evaluated": self.n_total,
            "accepted": self.n_accepted,
            "rejected": self.n_rejected,
            "acceptance_rate": self.n_accepted / max(1, self.n_total),
            "uses_fallback": self.uses_fallback,
            "threshold": self.threshold,
        }


# =============================================================================
# MÓDULO 3: DETECTOR DE RÉGIMEN
# =============================================================================
class RegimeDetector:
    """Clasifica el mercado en RANGO, TENDENCIA_ALCISTA, TENDENCIA_BAJISTA, RUPTURA."""

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
        adx_vals = adx(df_1h, ADX_PERIOD)
        bb_w = bollinger_width(df_1h, BB_PERIOD, BB_STD)
        vol_ratio = volume_ratio(df_1h, VOLUME_MA_PERIOD)
        ema20 = ema(df_1h["close"], EMA_PERIOD)
        slope = ema_slope(ema20, self.trend_slope_lookback)
        regime = pd.Series("INDEFINIDO", index=df_1h.index, dtype=object)
        breakout_mask = (bb_w > self.bb_breakout_min) & (vol_ratio > self.volume_breakout_mult)
        bull_trend_mask = (adx_vals > self.adx_trend_min) & (slope > 0)
        bear_trend_mask = (adx_vals > self.adx_trend_min) & (slope < 0)
        range_mask = (adx_vals < self.adx_range_max) & (bb_w < self.bb_range_max)
        regime[range_mask] = "RANGO"
        regime[bull_trend_mask] = "TENDENCIA_ALCISTA"
        regime[bear_trend_mask] = "TENDENCIA_BAJISTA"
        regime[breakout_mask] = "RUPTURA"
        return regime

    def detect_at(self, df_1h: pd.DataFrame, idx: int) -> str:
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
        if bb_v > self.bb_breakout_min and vol_v > self.volume_breakout_mult:
            return "RUPTURA"
        if adx_v > self.adx_trend_min:
            if slope_v > 0:
                return "TENDENCIA_ALCISTA"
            elif slope_v < 0:
                return "TENDENCIA_BAJISTA"
        if adx_v < self.adx_range_max and bb_v < self.bb_range_max:
            return "RANGO"
        return "INDEFINIDO"


# =============================================================================
# MÓDULO 4: SEÑALES DE MICRO-ESTRATEGIAS
# =============================================================================
@dataclass
class Signal:
    timestamp: pd.Timestamp
    direction: int
    entry_price: float
    stop_loss: float
    take_profit: float
    regime: str
    strategy: str
    atr_at_signal: float = 0.0


class MicroStrategies:
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
        if abs(low - swing_l) < tol and close > swing_l:
            entry = close
            stop_loss = swing_h
            take_profit = entry + self.range_tp_ratio * range_width
            if take_profit > entry and stop_loss > entry:
                return Signal(ts, 1, entry, stop_loss, take_profit, regime, "range", atr_val)
        if abs(high - swing_h) < tol and close < swing_h:
            entry = close
            stop_loss = swing_l
            take_profit = entry - self.range_tp_ratio * range_width
            if take_profit < entry and stop_loss < entry:
                return Signal(ts, -1, entry, stop_loss, take_profit, regime, "range", atr_val)
        return None

    def trend_signal(self, df_1h: pd.DataFrame, idx: int,
                     regime: str, atr_val: float) -> Optional[Signal]:
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
                return Signal(ts, 1, entry, stop_loss, take_profit, regime, "trend", atr_val)
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
                return Signal(ts, -1, entry, stop_loss, take_profit, regime, "trend", atr_val)
        return None

    def breakout_signal(self, df_1h: pd.DataFrame, idx: int,
                        regime: str, atr_val: float,
                        vol_ratio: float) -> Optional[Signal]:
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
                return Signal(ts, 1, entry, stop_loss, take_profit, regime, "breakout", atr_val)
        if close < range_low:
            entry = close
            stop_loss = range_center
            take_profit = entry - self.breakout_tp_ratio * range_width
            if take_profit < entry and stop_loss > entry:
                return Signal(ts, -1, entry, stop_loss, take_profit, regime, "breakout", atr_val)
        return None

    def generate_signal(self, df_1h: pd.DataFrame, idx: int,
                        regime: str, atr_val: float,
                        vol_ratio: float) -> Optional[Signal]:
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
    signal: Signal
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float
    direction: int
    regime_at_entry: str


@dataclass
class ClosedTrade:
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
    exit_reason: str
    regime_at_entry: str
    strategy: str


class PositionManager:
    def __init__(self, max_bars_in_trade: int = 96,
                 reversal_regimes: tuple = ("TENDENCIA_ALCISTA", "TENDENCIA_BAJISTA")):
        self.max_bars_in_trade = max_bars_in_trade
        self.reversal_regimes = reversal_regimes

    def check_exit(self, position: Position, df_15m: pd.DataFrame,
                   current_time: pd.Timestamp,
                   current_regime: str) -> tuple[Optional[ClosedTrade], Optional[Signal]]:
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
    def __init__(self, cooldown_bars: int = 12, use_ml_filter: bool = True):
        self.regime_detector = RegimeDetector()
        self.strategies = MicroStrategies()
        self.position_manager = PositionManager()
        self.equity = INITIAL_EQUITY
        self.cooldown_bars = cooldown_bars
        self.last_close_idx = -cooldown_bars - 1
        self.use_ml_filter = use_ml_filter
        self.ml_classifier = MLClassifier() if use_ml_filter else None
        self.n_ml_evaluations = 0
        self.n_ml_accepted = 0
        self.n_ml_rejected = 0

    def _compute_position_size(self, entry_price: float,
                                stop_loss: float, direction: int) -> float:
        risk_amount = self.equity * RISK_PER_TRADE
        price_risk = abs(entry_price - stop_loss)
        if price_risk == 0:
            return 0.0
        return risk_amount / price_risk

    def _apply_entry_costs(self, entry_price: float, atr_val: float,
                            direction: int) -> float:
        slip = SLIPPAGE_ATR_MULT * atr_val
        spread = SPREAD_PCT / 100.0 * entry_price
        if direction == 1:
            return entry_price + slip + spread
        else:
            return entry_price - slip - spread

    def _check_ml_filter(self, signal: Signal, df_1h: pd.DataFrame,
                          df_15m: pd.DataFrame, df_5m: pd.DataFrame,
                          df_4h: pd.DataFrame, idx: int) -> tuple[bool, float]:
        if not self.use_ml_filter or self.ml_classifier is None:
            return True, 1.0
        features = self.ml_classifier.feature_extractor.extract(
            df_1h=df_1h,
            df_15m=df_15m,
            df_5m=df_5m,
            df_4h=df_4h,
            signal_direction=signal.direction,
            current_idx_1h=idx,
        )
        accepted, prob = self.ml_classifier.predict(features)
        self.n_ml_evaluations += 1
        if accepted:
            self.n_ml_accepted += 1
        else:
            self.n_ml_rejected += 1
        return accepted, prob

    def run(self, data: dict) -> tuple[list[ClosedTrade], pd.DataFrame]:
        df_1h = candles_to_df(data["1h"])
        df_15m = candles_to_df(data["15m"])
        df_5m = candles_to_df(data["5m"])
        if df_1h.empty or df_15m.empty or df_5m.empty:
            logger.error("Datos insuficientes para backtest.")
            return [], pd.DataFrame()
        df_4h = resample_ohlc(df_1h, "4h")
        logger.info("Iniciando backtest sobre %d velas 1h...", len(df_1h))
        if self.use_ml_filter:
            if self.ml_classifier and self.ml_classifier.uses_fallback:
                logger.info("🔄 Filtro ML activo (modo FALLBACK heurístico)")
            else:
                logger.info("🔄 Filtro ML activo (modelo %s, umbral %.2f)", ML_MODEL_PATH, ML_THRESHOLD)
        else:
            logger.info("Filtro ML desactivado")
        regimes = self.regime_detector.detect(df_1h)
        regime_summary = pd.DataFrame({"close": df_1h["close"], "regime": regimes})
        atr_1h = atr(df_1h, ATR_PERIOD)
        vol_ratio_1h = volume_ratio(df_1h, VOLUME_MA_PERIOD)
        trades: list[ClosedTrade] = []
        position: Optional[Position] = None
        n_signals_generated = 0
        n_reversals = 0
        n_ml_skipped = 0
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
                        ml_accepted, ml_prob = self._check_ml_filter(
                            new_signal, df_1h, df_15m, df_5m, df_4h, i
                        )
                        current_price = df_1h["close"].iloc[i]
                        closed_trade = self.position_manager._close_trade(
                            position, current_price, ts, "reversal"
                        )
                        trades.append(closed_trade)
                        self.equity += closed_trade.pnl_abs
                        n_reversals += 1
                        self.last_close_idx = i
                        if ml_accepted:
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
                        else:
                            n_ml_skipped += 1
            if position is None and (i - self.last_close_idx) >= self.cooldown_bars:
                signal = self.strategies.generate_signal(
                    df_1h, i, current_regime, atr_val, vol_ratio
                )
                if signal is not None:
                    ml_accepted, ml_prob = self._check_ml_filter(
                        signal, df_1h, df_15m, df_5m, df_4h, i
                    )
                    if ml_accepted:
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
                    else:
                        n_ml_skipped += 1
            if (i + 1) % 500 == 0:
                logger.info("  Procesadas %d/%d horas | Capital: $%.2f | Operaciones: %d | ML skip: %d",
                           i + 1, len(df_1h), self.equity, len(trades), n_ml_skipped)
        if position is not None:
            last_ts = df_1h.index[-1]
            last_price = df_1h["close"].iloc[-1]
            closed_trade = self.position_manager._close_trade(
                position, last_price, last_ts, "end_of_data"
            )
            trades.append(closed_trade)
            self.equity += closed_trade.pnl_abs
        logger.info("Backtest completado: %d operaciones, %d señales generadas, %d reversiones, %d rechazadas por ML",
                    len(trades), n_signals_generated, n_reversals, n_ml_skipped)
        if self.use_ml_filter and self.n_ml_evaluations > 0:
            logger.info("Estadísticas ML: %d evaluadas, %d aceptadas (%.1f%%), %d rechazadas",
                       self.n_ml_evaluations, self.n_ml_accepted,
                       100 * self.n_ml_accepted / self.n_ml_evaluations,
                       self.n_ml_rejected)
        return trades, regime_summary


# =============================================================================
# MÓDULO 7: CÁLCULO DE MÉTRICAS
# =============================================================================
def compute_metrics(trades: list[ClosedTrade], period_days: int) -> dict:
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
    print("\n" + "=" * 72)
    print("  🤖 BTC ADAPTIVE AGENT - REPORTE DE BACKTEST")
    print("=" * 72)
    print(f"  Símbolo:        {SYMBOL}")
    print(f"  Período:        {START_DATE} → {END_DATE}")
    print(f"  Timeframes:     {', '.join(TIMEFRAMES)}")
    print(f"  Capital inicial: ${INITIAL_EQUITY:,.2f}")
    print(f"  Riesgo/operación: {RISK_PER_TRADE:.1%}")
    print("=" * 72)
    print("\n📊 MÉTRICAS PRINCIPALES:")
    print(f"  Total de operaciones:    {metrics['num_trades']}")
    print(f"  Operaciones por mes:     {metrics['trades_per_month']}")
    print(f"  Profit Factor:           {metrics['profit_factor']:.4f}")
    print(f"  Win Rate:                {metrics['win_rate']:.1%}")
    print(f"  Máximo Drawdown:         ${metrics['max_drawdown_abs']:,.2f} ({metrics['max_drawdown_pct']:.1%})")
    print(f"  PnL total:               ${metrics['total_pnl']:,.2f}")
    print(f"  Capital final:           ${metrics['final_equity']:,.2f}")
    print(f"  Ganancia bruta:          ${metrics['gross_profit']:,.2f}")
    print(f"  Pérdida bruta:           ${metrics['gross_loss']:,.2f}")
    print(f"  PnL promedio/operación:  ${metrics['avg_pnl_per_trade']:,.2f}")
    print(f"  Mejor operación:         ${metrics['best_trade']:,.2f}")
    print(f"  Peor operación:          ${metrics['worst_trade']:,.2f}")
    if trades:
        print("\n📈 DISTRIBUCIÓN POR RÉGIMEN DE ENTRADA:")
        regime_counts = {}
        regime_pnl = {}
        for t in trades:
            r = t.regime_at_entry
            regime_counts[r] = regime_counts.get(r, 0) + 1
            regime_pnl[r] = regime_pnl.get(r, 0) + t.pnl_abs
        for r in sorted(regime_counts.keys()):
            print(f"  {r:25s}: {regime_counts[r]:>4} operaciones, PnL=${regime_pnl[r]:>12,.2f}")
        print("\n🎯 DISTRIBUCIÓN POR ESTRATEGIA:")
        strat_counts = {}
        strat_pnl = {}
        for t in trades:
            s = t.strategy
            strat_counts[s] = strat_counts.get(s, 0) + 1
            strat_pnl[s] = strat_pnl.get(s, 0) + t.pnl_abs
        for s in sorted(strat_counts.keys()):
            print(f"  {s:15s}: {strat_counts[s]:>4} operaciones, PnL=${strat_pnl[s]:>12,.2f}")
        print("\n🚪 DISTRIBUCIÓN POR RAZÓN DE SALIDA:")
        reason_counts = {}
        for t in trades:
            r = t.exit_reason
            reason_counts[r] = reason_counts.get(r, 0) + 1
        for r in sorted(reason_counts.keys()):
            print(f"  {r:15s}: {reason_counts[r]:>4} operaciones")
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
    print(f"    ≥20 operaciones/mes:  {'✅' if freq_ok else '❌'} ({metrics['trades_per_month']})")
    print(f"    DD < 20%:             {'✅' if dd_ok else '❌'} ({metrics['max_drawdown_pct']:.1%})")
    print("=" * 72 + "\n")


# =============================================================================
# MAIN
# =============================================================================
def main():
    print("\n" + "#" * 72)
    print("  🤖 BTC ADAPTIVE TRADING AGENT + FILTRO ML")
    print("#" * 72)
    print(f"  Símbolo: {SYMBOL}")
    print(f"  Período: {START_DATE} → {END_DATE}")
    print(f"  Capital inicial: ${INITIAL_EQUITY:,.2f}")
    print(f"  Riesgo por operación: {RISK_PER_TRADE:.1%}")
    print(f"  Filtro ML: {'ACTIVO' if ML_FALLBACK_ENABLED or os.path.exists(ML_MODEL_PATH) else 'DESACTIVADO'}")
    print(f"  Modelo: {ML_MODEL_PATH} ({'existe' if os.path.exists(ML_MODEL_PATH) else 'no existe, usando respaldo heurístico'})")
    print(f"  Umbral ML: {ML_THRESHOLD}")
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
    print("\n🔄 Ejecutando backtest adaptativo CON filtro ML...")
    backtester_ml = AdaptiveBacktester(use_ml_filter=True)
    trades_ml, regime_summary = backtester_ml.run(data)
    start_dt = datetime.strptime(START_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(END_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    period_days = (end_dt - start_dt).days
    metrics_ml = compute_metrics(trades_ml, period_days)
    print("\n🔄 Ejecutando backtest adaptativo SIN filtro ML (referencia)...")
    backtester_no_ml = AdaptiveBacktester(use_ml_filter=False)
    trades_no_ml, _ = backtester_no_ml.run(data)
    metrics_no_ml = compute_metrics(trades_no_ml, period_days)
    print("\n" + "=" * 72)
    print("  📊 REPORTE COMPARATIVO: CON vs SIN FILTRO ML")
    print("=" * 72)
    print(f"\n  {'Métrica':<25} {'SIN ML':>15} {'CON ML':>15} {'Δ':>10}")
    print(f"  {'-'*67}")
    print(f"  {'Total operaciones':<25} {metrics_no_ml['num_trades']:>15} {metrics_ml['num_trades']:>15} {metrics_ml['num_trades']-metrics_no_ml['num_trades']:>+10}")
    print(f"  {'Operaciones/mes':<25} {metrics_no_ml['trades_per_month']:>15.2f} {metrics_ml['trades_per_month']:>15.2f} {metrics_ml['trades_per_month']-metrics_no_ml['trades_per_month']:>+10.2f}")
    print(f"  {'Profit Factor':<25} {metrics_no_ml['profit_factor']:>15.3f} {metrics_ml['profit_factor']:>15.3f} {metrics_ml['profit_factor']-metrics_no_ml['profit_factor']:>+10.3f}")
    print(f"  {'Win Rate':<25} {metrics_no_ml['win_rate']:>15.1%} {metrics_ml['win_rate']:>15.1%} {metrics_ml['win_rate']-metrics_no_ml['win_rate']:>+10.1%}")
    print(f"  {'Máximo Drawdown':<25} {metrics_no_ml['max_drawdown_pct']:>15.1%} {metrics_ml['max_drawdown_pct']:>15.1%} {metrics_ml['max_drawdown_pct']-metrics_no_ml['max_drawdown_pct']:>+10.1%}")
    print(f"  {'PnL total':<25} ${metrics_no_ml['total_pnl']:>14,.2f} ${metrics_ml['total_pnl']:>14,.2f} ${metrics_ml['total_pnl']-metrics_no_ml['total_pnl']:>+9,.2f}")
    print(f"  {'Capital final':<25} ${metrics_no_ml['final_equity']:>14,.2f} ${metrics_ml['final_equity']:>14,.2f}")
    if backtester_ml.ml_classifier:
        ml_stats = backtester_ml.ml_classifier.stats()
        print(f"\n  📊 ESTADÍSTICAS DEL FILTRO ML:")
        print(f"    Modo: {'FALLBACK heurístico' if ml_stats['uses_fallback'] else 'Modelo Random Forest'}")
        print(f"    Umbral: {ml_stats['threshold']}")
        print(f"    Señales evaluadas: {ml_stats['total_evaluated']}")
        print(f"    Aceptadas: {ml_stats['accepted']} ({ml_stats['acceptance_rate']:.1%})")
        print(f"    Rechazadas: {ml_stats['rejected']} ({1-ml_stats['acceptance_rate']:.1%})")
    print("\n" + "=" * 72)
    print("  📊 REPORTE DETALLADO CON FILTRO ML")
    print("=" * 72)
    print_report(metrics_ml, trades_ml, regime_summary)
    if trades_ml:
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
            for t in trades_ml
        ])
        csv_path = "/home/z/my-project/discovery_lab/btc_adaptive_ml_trades.csv"
        trades_df.to_csv(csv_path, index=False)
        print(f"💾 Operaciones guardadas en: {csv_path}")
    return metrics_ml, trades_ml


if __name__ == "__main__":
    main()
