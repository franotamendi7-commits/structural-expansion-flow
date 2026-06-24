"""
Agente Adaptativo Multi‑Par para STRUCTURAL EXPANSION FLOW
===========================================================
Reemplaza al motor Fase 5 original y al viejo ML Agent.
Usa el detector de régimen y las micro‑estrategias desarrolladas
por Z.ia, empaquetadas como un módulo reutilizable.
"""

import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone

# ----------------------------- CONFIG -----------------------------
SYMBOL_CONFIG = {
    'BTCUSDT': {'supertrend_multiplier': 2.8, 'choppiness_neutral_threshold': 74.0, 'fibonacci_days': 30, 'tp_ratio': 1.5},
    'ETHUSDT': {'supertrend_multiplier': 2.6, 'choppiness_neutral_threshold': 72.0, 'fibonacci_days': 30, 'tp_ratio': 1.8},
    'SOLUSDT': {'supertrend_multiplier': 2.3, 'choppiness_neutral_threshold': 70.0, 'fibonacci_days': 90, 'tp_ratio': 2.0},
    'XRPUSDT': {'supertrend_multiplier': 2.3, 'choppiness_neutral_threshold': 68.0, 'fibonacci_days': 60, 'tp_ratio': 1.8},
    'BNBUSDT': {'supertrend_multiplier': 2.8, 'choppiness_neutral_threshold': 68.0, 'fibonacci_days': 30, 'tp_ratio': 1.6},
}

# ------------------------- INDICADORES ----------------------------
def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def _atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()

def _adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = np.maximum(high - low, np.abs(high - close.shift(1)))
    atr_w = pd.Series(tr).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm).ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
    return pd.Series(dx).ewm(alpha=1/period, min_periods=2*period, adjust=False).mean()

def _bollinger_width(df, period=20, num_std=2.0):
    middle = df['close'].rolling(period, min_periods=period).mean()
    std = df['close'].rolling(period, min_periods=period).std()
    return 2 * num_std * std / middle

def _volume_ratio(df, period=20):
    vol_ma = df['volume'].rolling(period, min_periods=period).mean()
    return df['volume'] / vol_ma.replace(0, np.nan)

# ---------------------- DETECTOR DE RÉGIMEN -----------------------
def _detect_regime(df_1h):
    """Clasifica el régimen actual en RANGO, TENDENCIA_ALCISTA, TENDENCIA_BAJISTA o RUPTURA."""
    if len(df_1h) < 50:
        return 'INDEFINIDO'
    adx_val = _adx(df_1h).iloc[-1]
    bb_w = _bollinger_width(df_1h).iloc[-1]
    vol_r = _volume_ratio(df_1h).iloc[-1]
    ema20 = _ema(df_1h['close'], 20)
    slope = ema20.diff(3).iloc[-1]

    if bb_w > 0.06 and vol_r > 1.5:
        return 'RUPTURA'
    if adx_val > 25:
        return 'TENDENCIA_ALCISTA' if slope > 0 else 'TENDENCIA_BAJISTA'
    if adx_val < 20 and bb_w < 0.04:
        return 'RANGO'
    return 'INDEFINIDO'

# --------------------- MICRO‑ESTRATEGIAS --------------------------
def _range_signal(df_1h, regime, atr_val):
    """RANGO: rebote en swings recientes."""
    if len(df_1h) < 7:
        return None
    close = df_1h['close'].iloc[-1]
    high = df_1h['high'].iloc[-1]
    low = df_1h['low'].iloc[-1]
    swing_h = df_1h['high'].iloc[-6:-1].max()
    swing_l = df_1h['low'].iloc[-6:-1].min()
    if swing_h == swing_l:
        return None
    tol = 0.002 * close
    rango = swing_h - swing_l
    if abs(low - swing_l) < tol and close > swing_l:
        return {'direction': 'LONG', 'entry': close, 'sl': swing_h, 'tp1': close + 0.5 * rango}
    if abs(high - swing_h) < tol and close < swing_h:
        return {'direction': 'SHORT', 'entry': close, 'sl': swing_l, 'tp1': close - 0.5 * rango}
    return None

def _trend_signal(df_1h, regime, atr_val):
    """TENDENCIA: corrección del 20‑30% del ATR del último impulso."""
    if len(df_1h) < 20 or atr_val == 0:
        return None
    close = df_1h['close'].iloc[-1]
    if regime == 'TENDENCIA_ALCISTA':
        swing_against = df_1h['low'].iloc[-10:-1].min()
        recent_high = df_1h['high'].iloc[-20:-1].max()
        pullback = (recent_high - close) / atr_val
        if 0.2 <= pullback <= 0.3:
            return {'direction': 'LONG', 'entry': close, 'sl': swing_against, 'tp1': close + 2 * atr_val}
    else:
        swing_against = df_1h['high'].iloc[-10:-1].max()
        recent_low = df_1h['low'].iloc[-20:-1].min()
        pullback = (close - recent_low) / atr_val
        if 0.2 <= pullback <= 0.3:
            return {'direction': 'SHORT', 'entry': close, 'sl': swing_against, 'tp1': close - 2 * atr_val}
    return None

def _breakout_signal(df_1h, regime, atr_val, vol_ratio):
    """RUPTURA: cierre fuera del rango de 6 velas con volumen > 1.5×."""
    if len(df_1h) < 7 or vol_ratio < 1.5:
        return None
    close = df_1h['close'].iloc[-1]
    range_high = df_1h['high'].iloc[-7:-1].max()
    range_low = df_1h['low'].iloc[-7:-1].min()
    if range_high == range_low:
        return None
    center = (range_high + range_low) / 2
    rango = range_high - range_low
    if close > range_high:
        return {'direction': 'LONG', 'entry': close, 'sl': center, 'tp1': close + 1.5 * rango}
    if close < range_low:
        return {'direction': 'SHORT', 'entry': close, 'sl': center, 'tp1': close - 1.5 * rango}
    return None

# ------------------------- AGENTE PRINCIPAL -----------------------
class AdaptiveAgent:
    """
    Agente adaptativo que reemplaza a ScalpingEngine + ML Agent.
    Detecta el régimen y aplica la micro‑estrategia correcta.
    """
    def __init__(self, symbol, capital=100.0, risk_pct=0.01):
        self.symbol = symbol
        self.capital = capital
        self.risk_pct = risk_pct
        self.config = SYMBOL_CONFIG.get(symbol, SYMBOL_CONFIG['BTCUSDT'])
        self._df_1h = pd.DataFrame()
        self._df_15m = pd.DataFrame()

    def _fetch_klines(self, interval, limit=100):
        """Descarga velas recientes desde Binance."""
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": self.symbol, "interval": interval, "limit": limit}
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        return [{
            'timestamp': int(k[0]),
            'open': float(k[1]), 'high': float(k[2]),
            'low': float(k[3]), 'close': float(k[4]),
            'volume': float(k[5])
        } for k in data]

    def run(self):
        """Ejecuta el análisis y devuelve una señal (o None)."""
        # Obtener datos frescos
        klines_1h = self._fetch_klines('1h', 120)
        if not klines_1h:
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': 'Sin datos'}

        # Construir DataFrames
        df_1h = pd.DataFrame(klines_1h)
        df_1h['datetime'] = pd.to_datetime(df_1h['timestamp'], unit='ms', utc=True)
        df_1h = df_1h.set_index('datetime').sort_index()

        # Detectar régimen
        regime = _detect_regime(df_1h)
        if regime == 'INDEFINIDO':
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': 'Régimen indefinido'}

        # Calcular ATR en 1h para las estrategias
        atr_val = _atr(df_1h).iloc[-1]
        vol_ratio = _volume_ratio(df_1h).iloc[-1] if len(df_1h) >= 20 else 1.0

        # Generar señal según régimen
        trade = None
        if regime == 'RANGO':
            trade = _range_signal(df_1h, regime, atr_val)
        elif regime in ('TENDENCIA_ALCISTA', 'TENDENCIA_BAJISTA'):
            trade = _trend_signal(df_1h, regime, atr_val)
        elif regime == 'RUPTURA':
            trade = _breakout_signal(df_1h, regime, atr_val, vol_ratio)

        if trade is None:
            return {'signal': 'WAIT', 'setup_state': 'FORMING', 'explanation': f'Régimen {regime} sin señal válida'}

        # Construir respuesta compatible con el resto del bot
        return {
            'signal': trade['direction'],
            'direction': trade['direction'].lower(),
            'setup_state': 'EXECUTE',
            'score': 85,
            'trade': {
                'entry': trade['entry'],
                'sl': trade['sl'],
                'tp1': trade['tp1'],
                'tp2': None,
                'risk_usd': self.capital * self.risk_pct,
                'contracts': (self.capital * self.risk_pct) / abs(trade['entry'] - trade['sl'])
            },
            'explanation': f"Régimen: {regime} | Entrada adaptativa",
            'market_phase': regime.lower(),
            'trend_h4': regime.lower(),
            'agent_scores': {'scanner': 85, 'risk': 80, 'technical': 90, 'momentum': 85, 'guard': 80}
        }
