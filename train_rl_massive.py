#!/usr/bin/env python3
"""
Entrenamiento masivo del agente PPO con datos de los CSVs multi‑período.
Usa las mismas reglas de entrada que el backtest institucional (Fase 5).
"""
import numpy as np
import pandas as pd
import pickle
import os
from datetime import datetime, timezone, timedelta
from rl_environment import ExitEnv
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

# ─── 1. CARGAR DATOS DE LOS CSVs MULTI‑PERÍODO ─────────────────
print("📂 Cargando CSVs multi‑período...")
TRAIN_FILES = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv"
]

dfs = []
for f in TRAIN_FILES:
    if os.path.exists(f):
        df = pd.read_csv(f, parse_dates=["entry_time"])
        df = df.loc[:, ~df.columns.duplicated()]
        dfs.append(df)
        print(f"  ✔ {f}: {len(df)} trades")
    else:
        print(f"  ⚠️  {f} no encontrado")

if not dfs:
    print("❌ No se encontraron CSVs.")
    exit()

df_all = pd.concat(dfs, ignore_index=True)
print(f"🔹 Total trades en CSVs: {len(df_all)}")

# ─── 2. GENERAR EPISODIOS A PARTIR DE CADA TRADE ──────────────
print("🧠 Generando episodios...")

def create_episode_from_trade(row):
    """
    Convierte un trade del CSV en un diccionario de señal y velas.
    Como no tenemos las velas reales de 5m en el CSV, generamos
    velas sintéticas basadas en la entrada y el PnL del trade.
    Esto es una aproximación; en producción usaríamos velas reales.
    """
    direction = row.get('direction', 'LONG').lower()
    if direction == 'long':
        direction = 'bullish'
    elif direction == 'short':
        direction = 'bearish'

    entry_price = row.get('entry_signal', row.get('entry_real', 0))
    sl = row.get('sl_original', entry_price * 0.98 if direction == 'bullish' else entry_price * 1.02)
    tp1 = row.get('tp1', entry_price * 1.04 if direction == 'bullish' else entry_price * 0.96)
    pnl = row.get('pnl_neto', 0)
    contracts = row.get('contracts', 1.0)

    # Generar velas sintéticas que simulan un camino de precios
    # basado en el resultado final (ganancia o pérdida)
    np.random.seed(hash(row.get('entry_time', str(datetime.now()))) % 2**32)
    num_candles = 100
    if direction == 'bullish':
        # Si PnL positivo, el precio sube; si negativo, baja
        if pnl > 0:
            target = entry_price + (pnl / contracts) * 3  # exageramos para darle recorrido
        else:
            target = entry_price - abs(pnl / contracts) * 3
    else:
        if pnl > 0:
            target = entry_price - (pnl / contracts) * 3
        else:
            target = entry_price + abs(pnl / contracts) * 3

    prices = [entry_price]
    for i in range(1, num_candles):
        # Movimiento con reversión al target
        remaining = num_candles - i
        if remaining > 0:
            step = (target - prices[-1]) / remaining
            noise = np.random.normal(0, abs(step) * 0.5)
            prices.append(prices[-1] + step + noise)
        else:
            prices.append(prices[-1])

    candles = []
    for p in prices:
        high = p + abs(np.random.normal(0, p * 0.005))
        low  = p - abs(np.random.normal(0, p * 0.005))
        candles.append({'high': high, 'low': low, 'close': p})

    return {
        'direction': direction,
        'entry_price': entry_price,
        'sl': sl,
        'tp1': tp1,
        'contracts': contracts,
        'candles': candles
    }

# Crear lista de señales (una por trade en los CSVs)
signals = []
for idx, row in df_all.iterrows():
    sig = create_episode_from_trade(row)
    if sig['entry_price'] > 0:
        signals.append(sig)

print(f"✔ {len(signals)} episodios generados.")

# ─── 3. CREAR ENTORNOS Y ENTRENAR ─────────────────────────────
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

print("🚀 Entrenando PPO masivo...")
envs = DummyVecEnv([make_env(s) for s in signals])
model = PPO("MlpPolicy", envs, verbose=1, learning_rate=0.0003, n_steps=4096, batch_size=256)
model.learn(total_timesteps=200000)  # más pasos para aprender mejor
model.save("rl_exit_massive")
print("✅ Modelo masivo guardado como rl_exit_massive.zip")
