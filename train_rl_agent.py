#!/usr/bin/env python3
"""
Entrenamiento de un agente PPO para gestionar salidas.
Usa datos históricos de BTCUSDT y el entorno ExitEnv de rl_environment.py.
"""
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from rl_environment import ExitEnv
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

# ─── 1. DESCARGA DE DATOS HISTÓRICOS (BTCUSDT, 5m) ───────────────
print("📥 Descargando velas de 5m de BTCUSDT...")
BASE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
LIMIT = 1000

# Últimos 60 días (para tener suficientes señales)
end_date = datetime.now(timezone.utc)
start_date = end_date - timedelta(days=60)

def fetch_klines(symbol, interval, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    all_klines = []
    while start_ms < end_ms:
        params = {'symbol':symbol, 'interval':interval, 'limit':LIMIT,
                  'startTime':start_ms, 'endTime':end_ms}
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"Error API: {resp.text}")
            break
        data = resp.json()
        if not data:
            break
        for k in data:
            all_klines.append({
                'timestamp': int(k[0]),
                'open': float(k[1]), 'high': float(k[2]),
                'low': float(k[3]), 'close': float(k[4]),
                'volume': float(k[5])
            })
        start_ms = int(data[-1][0]) + 1
    return [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]

klines_5m = fetch_klines(SYMBOL, INTERVAL, start_date, end_date)
print(f"✔ {len(klines_5m)} velas descargadas.")

# ─── 2. DETECCIÓN DE SEÑALES (Fase 5 simplificada) ─────────────
print("🔍 Detectando señales de entrada...")

# Para simplificar, usaremos una detección básica de engulfing en velas de 4h.
# También necesitamos velas de 4h para los engulfing.
klines_4h = fetch_klines(SYMBOL, '4h', start_date - timedelta(days=30), end_date)
print(f"✔ {len(klines_4h)} velas de 4h descargadas.")

def is_bullish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev = candles[-2]
    curr = candles[-1]
    return (prev['close'] < prev['open'] and curr['close'] > curr['open'] and
            curr['open'] < prev['close'] and curr['close'] > prev['open'])

def is_bearish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev = candles[-2]
    curr = candles[-1]
    return (prev['close'] > prev['open'] and curr['close'] < curr['open'] and
            curr['open'] > prev['close'] and curr['close'] < prev['open'])

# Para cada vela de 4h, vemos si hay engulfing y buscamos la vela de 5m inmediata como entrada
signals = []
for i in range(1, len(klines_4h)):
    if is_bullish_engulfing(klines_4h[i-1:i+1]):
        # Encontrar la primera vela de 5m después de la vela de 4h
        entry_ts = klines_4h[i]['timestamp']
        # Buscar velas de 5m posteriores a esa entrada
        future_5m = [k for k in klines_5m if k['timestamp'] >= entry_ts]
        if len(future_5m) >= 10:   # necesitamos al menos 10 velas para simular
            entry_price = klines_4h[i]['close']
            sl = entry_price * 0.98
            tp1 = entry_price * 1.04
            signals.append({
                'direction': 'bullish',
                'entry_price': entry_price,
                'sl': sl,
                'tp1': tp1,
                'contracts': 1.0,
                'candles': future_5m[:100]   # máx 100 velas (500 min)
            })
    elif is_bearish_engulfing(klines_4h[i-1:i+1]):
        entry_ts = klines_4h[i]['timestamp']
        future_5m = [k for k in klines_5m if k['timestamp'] >= entry_ts]
        if len(future_5m) >= 10:
            entry_price = klines_4h[i]['close']
            sl = entry_price * 1.02
            tp1 = entry_price * 0.96
            signals.append({
                'direction': 'bearish',
                'entry_price': entry_price,
                'sl': sl,
                'tp1': tp1,
                'contracts': 1.0,
                'candles': future_5m[:100]
            })

print(f"✔ {len(signals)} señales detectadas.")

if len(signals) == 0:
    print("❌ No se encontraron señales. Probá con un período más largo.")
    exit()

# ─── 3. CREAR EPISODIOS Y ENTRENAR ─────────────────────────────
print("🧠 Creando episodios y entrenando agente PPO...")

# Creamos una función que genere un entorno por cada señal
def make_env(signal):
    def _init():
        return ExitEnv(
            candles=signal['candles'],
            direction=signal['direction'],
            entry_price=signal['entry_price'],
            sl_original=signal['sl'],
            tp1=signal['tp1'],
            contracts=signal['contracts']
        )
    return _init

# Vectorizamos los entornos (PPO espera un entorno vectorizado)
envs = DummyVecEnv([make_env(s) for s in signals[:50]])  # usamos hasta 50 señales para no saturar

# Entrenar PPO
model = PPO("MlpPolicy", envs, verbose=1, learning_rate=0.0003, n_steps=2048)
model.learn(total_timesteps=50000)

# Guardar modelo
model.save("rl_exit_model")
print("✅ Modelo PPO guardado como rl_exit_model.zip")
