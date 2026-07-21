#!/usr/bin/env python3
"""
Backtest 6 meses (Ene–Jun 2026) – LÓGICA REAL DEL MOTOR + ML AGENT
Usa los datos ya descargados en backtest_cache/.
No depende del motor en vivo ni de get_ticker.
"""
import os, sys, json
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from scipy.signal import argrelextrema
from scipy.stats import linregress

# ─── CONFIG ───
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 22, 23, 59, tzinfo=timezone.utc)
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01
COMMISSION = 0.0004
SPREAD = 0.0002
CACHE_DIR = "backtest_cache"

# ─── ML AGENT REAL (usa el umbral que tengas en ml_filter.py) ───
import ml_filter

# ─── CARGAR DATOS DEL CACHÉ ───
def load_csv(sym, tf):
    fname = os.path.join(CACHE_DIR, f"{sym}_{tf}.csv")
    if not os.path.exists(fname):
        return []
    df = pd.read_csv(fname, parse_dates=['timestamp'])
    df = df[(df.timestamp >= START_DATE - timedelta(days=120)) & (df.timestamp <= END_DATE)]
    return df.to_dict(orient='records')

print("Cargando caché...")
data = {}
for s in SYMBOLS:
    data[s] = {}
    for tf in ['1d','4h','1h','15m','5m']:
        data[s][tf] = sorted(load_csv(s, tf), key=lambda x: x['timestamp'])
    print(f" {s}: 4h={len(data[s]['4h'])} velas")

# ─── INDICADORES (copia exacta del motor) ───
def ema(series, period):
    if len(series) < period: return None
    k = 2/(period+1)
    e = sum(series[:period])/period
    for x in series[period:]:
        e = (x - e) * k + e
    return e

def atr(highs, lows, closes, period=14):
    if len(highs) < period+1: return None
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(highs))]
    return np.mean(tr[-period:])

def bollinger_bands(closes, period=20, nbdev=2):
    if len(closes) < period: return None, None, None
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return sma + nbdev*std, sma - nbdev*std, std

def choppiness_index(highs, lows, closes, period=14):
    if len(highs) < period+1: return 50.0
    tr = np.maximum(highs[-period:] - lows[-period:],
           np.maximum(np.abs(highs[-period:] - np.roll(closes[-period:], 1)),
                      np.abs(lows[-period:] - np.roll(closes[-period:], 1))))
    tr[0] = highs[-period] - lows[-period]
    atr_sum = np.sum(tr)
    highest = np.max(highs[-period:])
    lowest = np.min(lows[-period:])
    total_range = highest - lowest
    if total_range == 0: return 50.0
    ci = 100 * np.log10(atr_sum / total_range) / np.log10(period)
    return np.clip(ci, 0, 100)

def williams_r(highs, lows, closes, period=14):
    if len(highs) < period: return None
    hh = np.max(highs[-period:])
    ll = np.min(lows[-period:])
    if hh == ll: return 0.0
    return np.clip(-100 * (hh - closes[-1]) / (hh - ll), -100, 0)

def supertrend(highs, lows, closes, period=14, multiplier=3.0):
    if len(highs) < period+2: return None, 0.0, 'neutral'
    hl2 = (highs + lows) / 2
    tr = np.maximum(highs - lows,
           np.maximum(np.abs(highs - np.roll(closes, 1)),
                      np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    atr_vals = pd.Series(tr).ewm(span=period, adjust=False).mean().values
    upper = hl2 + multiplier * atr_vals
    lower = hl2 - multiplier * atr_vals
    direction = np.ones(len(highs), dtype=int)
    for i in range(1, len(highs)):
        if closes[i-1] <= upper[i-1]:
            upper[i] = min(upper[i], upper[i-1])
        if closes[i-1] >= lower[i-1]:
            lower[i] = max(lower[i], lower[i-1])
        if closes[i] > upper[i-1]:
            direction[i] = 1
        elif closes[i] < lower[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
    last_dir = direction[-1]
    label = 'bullish' if last_dir == 1 else 'bearish'
    return lower[-1] if last_dir == 1 else upper[-1], last_dir, label

def market_phase(highs, lows, closes, volumes):
    if len(closes) < 30: return 'unknown'
    bb_u, bb_l, bb_s = bollinger_bands(closes)
    if bb_u is None: return 'unknown'
    bb_w = (bb_u - bb_l) / closes[-1]
    atr14 = atr(highs, lows, closes)
    if atr14 is None: return 'unknown'
    atr_r = atr14 / closes[-1]
    vol_r = volumes[-1] / np.mean(volumes[-20:]) if len(volumes)>=20 else 1.0
    slope = linregress(np.arange(20), closes[-20:])[0] if len(closes)>=20 else 0
    if bb_w < 0.03 and atr_r < 0.01 and vol_r < 0.8: return 'compressing'
    if bb_w > 0.06 and vol_r > 1.3 and abs(slope) > 0.005: return 'expanding'
    if atr_r > 0.015 and abs(slope) > 0.01: return 'trending'
    if atr_r < 0.008 and abs(slope) < 0.003: return 'ranging'
    return 'neutral'

def is_bullish_engulfing(klines):
    if len(klines) < 2: return False
    prev, curr = klines[-2], klines[-1]
    return (prev['close'] < prev['open'] and curr['close'] > curr['open'] and
            curr['open'] < prev['close'] and curr['close'] > prev['open'])

def is_bearish_engulfing(klines):
    if len(klines) < 2: return False
    prev, curr = klines[-2], klines[-1]
    return (prev['close'] > prev['open'] and curr['close'] < curr['open'] and
            curr['open'] > prev['close'] and curr['close'] < prev['open'])

def get_fib_block(klines_1d, price, days=30):
    if len(klines_1d) < 2: return None
    recent = klines_1d[-min(days, len(klines_1d)):]
    highs = [k['high'] for k in recent]
    lows = [k['low'] for k in recent]
    fib_high = max(highs)
    fib_low = min(lows)
    if fib_high <= fib_low: return None
    rng = fib_high - fib_low
    levels = {
        0.0: fib_low, 0.25: fib_low+rng*0.25, 0.5: fib_low+rng*0.5,
        0.75: fib_low+rng*0.75, 1.0: fib_high,
        1.25: fib_high+rng*0.25, 1.5: fib_high+rng*0.5,
        1.75: fib_high+rng*0.75, 2.0: fib_high+rng
    }
    sorted_levels = sorted(levels.items(), key=lambda x: x[1])
    for i in range(len(sorted_levels)-1):
        low_key, low_price = sorted_levels[i]
        high_key, high_price = sorted_levels[i+1]
        if low_price <= price <= high_price:
            return (low_key, low_price, high_key, high_price)
    return None

# ─── BACKTEST ───
all_trades = []
ml_approved = ml_rejected = 0
capital = INITIAL_CAPITAL
equity = [capital]

for sym in SYMBOLS:
    print(f"\nProcesando {sym}...")
    k4h = [k for k in data[sym]['4h'] if START_DATE.timestamp()*1000 <= k['timestamp'] <= END_DATE.timestamp()*1000]
    if len(k4h) < 30: continue

    for i in range(30, len(k4h)):
        ts = k4h[i]['timestamp']
        price = k4h[i]['close']

        # Datos truncados hasta ts
        trunc = {}
        for tf in ['5m','15m','1h','4h','1d']:
            trunc[tf] = [k for k in data[sym][tf] if k['timestamp'] <= ts]

        # Arrays para indicadores
        c_5m  = np.array([k['close'] for k in trunc['5m']])
        h_5m  = np.array([k['high'] for k in trunc['5m']])
        l_5m  = np.array([k['low'] for k in trunc['5m']])
        v_5m  = np.array([k['volume'] for k in trunc['5m']])
        c_15m = np.array([k['close'] for k in trunc['15m']])
        h_15m = np.array([k['high'] for k in trunc['15m']])
        l_15m = np.array([k['low'] for k in trunc['15m']])
        c_1h  = np.array([k['close'] for k in trunc['1h']])
        h_1h  = np.array([k['high'] for k in trunc['1h']])
        l_1h  = np.array([k['low'] for k in trunc['1h']])
        c_4h  = np.array([k['close'] for k in trunc['4h']])
        h_4h  = np.array([k['high'] for k in trunc['4h']])
        l_4h  = np.array([k['low'] for k in trunc['4h']])

        if len(c_5m) < 30: continue

        # Detección de engulfing en 4h
        signal = 'WAIT'
        direction = 'neutral'
        if is_bearish_engulfing(trunc['4h']):
            signal = 'SHORT'; direction = 'bearish'
        elif is_bullish_engulfing(trunc['4h']):
            signal = 'LONG'; direction = 'bullish'

        if signal == 'WAIT': continue

        # Fase de mercado
        phase = market_phase(h_1h, l_1h, c_1h, v_5m)
        if phase in ('ranging', 'neutral', 'unknown'):
            signal = 'WAIT'
            continue

        # Filtros fijos
        ci_val = choppiness_index(h_15m, l_15m, c_15m)
        wr5_val = williams_r(h_5m, l_5m, c_5m, 14)
        wr15_val = williams_r(h_15m, l_15m, c_15m, 14)
        if wr5_val is None or wr15_val is None: continue
        st_val, st_dir, st_label = supertrend(h_5m, l_5m, c_5m, 10, 3.0)
        _, st_dir15, st_label15 = supertrend(h_15m, l_15m, c_15m, 10, 3.0)
        _, st_dir1h, st_label1h = supertrend(h_1h, l_1h, c_1h, 14, 3.0)
        st_aligned = (st_label == st_label15 == st_label1h)
        st_bias = st_label if st_aligned else 'mixed'

        # Veto de filtros (umbrales iguales al motor)
        if ci_val > 70.0:
            continue
        if signal == 'LONG' and wr5_val > -20:
            continue
        if signal == 'SHORT' and wr5_val < -80:
            continue
        if signal == 'LONG' and st_bias == 'bearish' and st_aligned:
            continue
        if signal == 'SHORT' and st_bias == 'bullish' and st_aligned:
            continue

        # Fibonacci
        fib_block = get_fib_block(trunc['1d'], price)
        if fib_block is None:
            continue

        # ML Agent (usa el umbral actual de ml_filter.py)
        fib_low_key = fib_block[0]
        fib_high_key = fib_block[2]
        fib_width = (fib_block[3]-fib_block[1])/fib_block[1] if fib_block[1] else 0.0
        try:
            features = {
                'fib_low_key': fib_low_key,
                'fib_high_key': fib_high_key,
                'fib_width': fib_width,
                'ci_value': ci_val,
                'wr_5m': wr5_val,
                'wr_15m': wr15_val,
                'st_aligned': 1 if st_aligned else 0,
                'st_bias_bullish': 1 if st_bias=='bullish' else 0,
                'st_bias_bearish': 1 if st_bias=='bearish' else 0,
                'mom_score': 50.0,
                'vol_ratio_5m': v_5m[-1]/np.mean(v_5m[-20:]) if len(v_5m)>=20 else 1.0,
                'body_ratio_4h': 0.0,
                'hour_of_day': datetime.fromtimestamp(ts/1000, tz=timezone.utc).hour,
                'direction_long': 1 if signal=='LONG' else 0,
                'phase_compressing': 1 if phase=='compressing' else 0,
                'phase_expanding': 1 if phase=='expanding' else 0,
                'phase_trending': 1 if phase=='trending' else 0,
                'mom_bullish': 1 if direction=='bullish' else 0,
                'mom_bearish': 1 if direction=='bearish' else 0,
                'mom_neutral': 1 if direction=='neutral' else 0
            }
            ejecutar, prob = ml_filter.debe_ejecutar(features)
            if ejecutar:
                ml_approved += 1
            else:
                ml_rejected += 1
        except:
            continue

        # Registrar trade (solo conteo)
        all_trades.append({
            'timestamp': datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat(),
            'symbol': sym,
            'signal': signal,
            'prob': prob
        })

# ─── REPORTE ───
print("\n" + "="*60)
print("BACKTEST 6 MESES CON MOTOR REAL (LÓGICA) + ML AGENT")
print("="*60)
print(f"Período: {START_DATE.date()} → {END_DATE.date()}")
print(f"Trades totales encontrados por el motor: {len(all_trades)}")
print(f"ML APROBADOS: {ml_approved}")
print(f"ML RECHAZADOS: {ml_rejected}")
if len(all_trades) > 0:
    print(f"Tasa de aprobación ML: {ml_approved/len(all_trades)*100:.1f}%")
    print(f"Promedio mensual de operaciones: {ml_approved/6:.1f}")
else:
    print("El motor no encontró ningún setup válido en los 6 meses.")
