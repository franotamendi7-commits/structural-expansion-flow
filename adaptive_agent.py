"""
Agente Adaptativo Multi‑Par para STRUCTURAL EXPANSION FLOW
===========================================================
CORRECCIONES FINALES:
- SL en _range_signal: swing ± 1.5*ATR
- TP en _range_signal: 3.0×rango (Variante G)
- TP en _trend_signal: 3×ATR (Variante G)
"""

import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone

# ------------------------- INDICADORES ----------------------------
def _ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def _atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()

def _adx(df, period=14):
    high, low, close = df['high'], df['low'], df['close']
    prev_close = close.shift(1)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index
    )
    # True Range completo (3 términos, alineado por índice)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr_w.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    return dx.ewm(alpha=1/period, min_periods=2*period, adjust=False).mean()

def _bollinger_width(df, period=20, num_std=2.0):
    middle = df['close'].rolling(period, min_periods=period).mean()
    std = df['close'].rolling(period, min_periods=period).std()
    return 2 * num_std * std / middle

def _volume_ratio(df, period=20):
    vol_ma = df['volume'].rolling(period, min_periods=period).mean()
    return df['volume'] / vol_ma.replace(0, np.nan)

# ---------------------- DETECTOR DE RÉGIMEN -----------------------
def _detect_regime(df_1h):
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
    """RANGO: SL = swing ± 1.5*ATR, TP = 3.0×rango."""
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
    # LONG
    if abs(low - swing_l) < tol and close > swing_l:
        sl = swing_l - 1.5 * atr_val
        tp = close + 3.0 * rango
        return {'direction': 'LONG', 'entry': close, 'sl': sl, 'tp1': tp}
    # SHORT
    if abs(high - swing_h) < tol and close < swing_h:
        sl = swing_h + 1.5 * atr_val
        tp = close - 3.0 * rango
        return {'direction': 'SHORT', 'entry': close, 'sl': sl, 'tp1': tp}
    return None

def _trend_signal(df_1h, regime, atr_val):
    """TENDENCIA: SL en swing contra, TP = 3×ATR."""
    if len(df_1h) < 20 or atr_val == 0:
        return None
    close = df_1h['close'].iloc[-1]
    if regime == 'TENDENCIA_ALCISTA':
        swing_against = df_1h['low'].iloc[-10:-1].min()
        recent_high = df_1h['high'].iloc[-20:-1].max()
        pullback = (recent_high - close) / atr_val
        if 0.2 <= pullback <= 0.3:
            return {'direction': 'LONG', 'entry': close, 'sl': swing_against, 'tp1': close + 3 * atr_val}
    else:
        swing_against = df_1h['high'].iloc[-10:-1].max()
        recent_low = df_1h['low'].iloc[-20:-1].min()
        pullback = (close - recent_low) / atr_val
        if 0.2 <= pullback <= 0.3:
            return {'direction': 'SHORT', 'entry': close, 'sl': swing_against, 'tp1': close - 3 * atr_val}
    return None

def _breakout_signal(df_1h, regime, atr_val, vol_ratio):
    """RUPTURA: SL en centro del rango, TP = 1.5×rango."""
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
    def __init__(self, symbol, capital=100.0, risk_pct=0.01):
        self.symbol = symbol
        self.capital = capital
        self.risk_pct = risk_pct

    def _fetch_klines(self, interval, limit=100):
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
        klines_1h = self._fetch_klines('1h', 120)
        if not klines_1h:
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': 'Sin datos'}

        df_1h = pd.DataFrame(klines_1h)
        df_1h['datetime'] = pd.to_datetime(df_1h['timestamp'], unit='ms', utc=True)
        df_1h = df_1h.set_index('datetime').sort_index()

        regime = _detect_regime(df_1h)
        if regime == 'INDEFINIDO':
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': 'Régimen indefinido'}

        atr_val = _atr(df_1h).iloc[-1]
        vol_ratio = _volume_ratio(df_1h).iloc[-1] if len(df_1h) >= 20 else 1.0

        trade = None
        if regime == 'RANGO':
            trade = _range_signal(df_1h, regime, atr_val)
        elif regime in ('TENDENCIA_ALCISTA', 'TENDENCIA_BAJISTA'):
            trade = _trend_signal(df_1h, regime, atr_val)
        elif regime == 'RUPTURA':
            trade = _breakout_signal(df_1h, regime, atr_val, vol_ratio)

        if trade is None:
            return {'signal': 'WAIT', 'setup_state': 'FORMING', 'explanation': f'Régimen {regime} sin señal válida'}

        entry = trade['entry']
        sl = trade['sl']
        risk_distance = abs(entry - sl)
        if risk_distance < 1e-9:
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': f'Régimen {regime}: SL coincide con entry'}

        direction = trade['direction']
        if direction == 'LONG' and sl >= entry:
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': f'Régimen {regime}: SL inválido'}
        if direction == 'SHORT' and sl <= entry:
            return {'signal': 'WAIT', 'setup_state': 'INVALID', 'explanation': f'Régimen {regime}: SL inválido'}

        contracts = (self.capital * self.risk_pct) / risk_distance

        return {
            'signal': direction,
            'direction': direction.lower(),
            'setup_state': 'EXECUTE',
            'score': 85,
            'trade': {
                'entry': entry,
                'sl': sl,
                'tp1': trade['tp1'],
                'tp2': None,
                'risk_usd': self.capital * self.risk_pct,
                'contracts': contracts
            },
            'explanation': f"Régimen: {regime} | Entrada adaptativa",
            'market_phase': regime.lower(),
            'trend_h4': regime.lower(),
            'agent_scores': {'scanner': 85, 'risk': 80, 'technical': 90, 'momentum': 85, 'guard': 80}
        }