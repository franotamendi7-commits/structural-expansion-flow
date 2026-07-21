#!/usr/bin/env python3
"""
Validación del agente PPO masivo vs reglas fijas en Q2 2025 (OUT‑OF‑SAMPLE).
"""
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from rl_environment import ExitEnv
from stable_baselines3 import PPO

# ─── 1. DESCARGA DE DATOS (Q2 2025) ────────────────────────────
print("📥 Descargando velas de BTCUSDT para Q2 2025...")
BASE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"

def fetch_klines(symbol, interval, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    all_klines = []
    while start_ms < end_ms:
        params = {'symbol':symbol,'interval':interval,'limit':1000,
                  'startTime':start_ms,'endTime':end_ms}
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"Error API: {resp.text}"); break
        data = resp.json()
        if not data: break
        for k in data:
            all_klines.append({
                'timestamp': int(k[0]),
                'open': float(k[1]), 'high': float(k[2]),
                'low': float(k[3]), 'close': float(k[4]),
                'volume': float(k[5])
            })
        start_ms = int(data[-1][0]) + 1
    return [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]

end_date = datetime(2025, 6, 30, 23, 59, tzinfo=timezone.utc)
start_date = datetime(2025, 4, 1, 0, 0, tzinfo=timezone.utc)
klines_5m  = fetch_klines(SYMBOL, '5m', start_date, end_date)
klines_4h  = fetch_klines(SYMBOL, '4h', start_date - timedelta(days=30), end_date)
print(f"✔ {len(klines_5m)} velas de 5m, {len(klines_4h)} velas de 4h.")

# ─── 2. DETECCIÓN DE SEÑALES (Fase 5 simplificada) ─────────────
def is_bullish_engulfing(candles):
    if len(candles) < 2: return False
    prev, curr = candles[-2], candles[-1]
    return (prev['close'] < prev['open'] and curr['close'] > curr['open'] and
            curr['open'] < prev['close'] and curr['close'] > prev['open'])

def is_bearish_engulfing(candles):
    if len(candles) < 2: return False
    prev, curr = candles[-2], candles[-1]
    return (prev['close'] > prev['open'] and curr['close'] < curr['open'] and
            curr['open'] > prev['close'] and curr['close'] < prev['open'])

signals = []
for i in range(1, len(klines_4h)):
    if is_bullish_engulfing(klines_4h[i-1:i+1]):
        entry_ts = klines_4h[i]['timestamp']
        future_5m = [k for k in klines_5m if k['timestamp'] >= entry_ts]
        if len(future_5m) >= 10:
            entry_price = klines_4h[i]['close']
            sl = entry_price * 0.98
            tp1 = entry_price * 1.04
            signals.append({
                'direction': 'bullish',
                'entry_price': entry_price,
                'sl': sl,
                'tp1': tp1,
                'contracts': 1.0,
                'candles': future_5m[:100]
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

print(f"✔ {len(signals)} señales detectadas en Q2 2025.")
if len(signals) == 0:
    print("No se encontraron señales.")
    exit()

# ─── 3. SIMULACIÓN CON REGLAS FIJAS (idéntica a validate_rl_agent.py) ──
def simulate_fixed(signal):
    direction = signal['direction']; entry = signal['entry_price']
    sl = signal['sl']; tp1 = signal['tp1']
    contracts = signal['contracts']; candles = signal['candles']

    if direction == 'bullish':
        tp_distance = tp1 - entry; tp2 = entry + 2 * tp_distance
        half_target = entry + 0.5 * tp_distance
    else:
        tp_distance = entry - tp1; tp2 = entry - 2 * tp_distance
        half_target = entry - 0.5 * tp_distance

    remaining = contracts; sl_active = sl
    breakeven_moved = False; partial_done = False
    total_pnl = 0.0; commission = 0.0005

    for j in range(len(candles)):
        high = candles[j]['high']; low = candles[j]['low']
        close = candles[j]['close']
        if direction == 'bullish':
            if not breakeven_moved and high >= half_target:
                sl_active = entry; breakeven_moved = True
            if not partial_done and high >= tp1:
                close_contracts = contracts * 0.6
                pnl = (tp1 - entry) * close_contracts
                comm = tp1 * close_contracts * commission
                total_pnl += pnl - comm
                remaining = contracts * 0.4; sl_active = entry
                breakeven_moved = True; partial_done = True
            if partial_done and high >= tp2:
                pnl = (tp2 - entry) * remaining
                comm = tp2 * remaining * commission
                total_pnl += pnl - comm; break
            if low <= sl_active:
                exit_price = sl_active - 0.001 * entry
                if not partial_done:
                    pnl = (exit_price - entry) * contracts
                    comm = exit_price * contracts * commission
                else:
                    pnl = (exit_price - entry) * remaining
                    comm = exit_price * remaining * commission
                total_pnl += pnl - comm; break
        else:
            if not breakeven_moved and low <= half_target:
                sl_active = entry; breakeven_moved = True
            if not partial_done and low <= tp1:
                close_contracts = contracts * 0.6
                pnl = (entry - tp1) * close_contracts
                comm = tp1 * close_contracts * commission
                total_pnl += pnl - comm
                remaining = contracts * 0.4; sl_active = entry
                breakeven_moved = True; partial_done = True
            if partial_done and low <= tp2:
                pnl = (entry - tp2) * remaining
                comm = tp2 * remaining * commission
                total_pnl += pnl - comm; break
            if high >= sl_active:
                exit_price = sl_active + 0.001 * entry
                if not partial_done:
                    pnl = (entry - exit_price) * contracts
                    comm = exit_price * contracts * commission
                else:
                    pnl = (entry - exit_price) * remaining
                    comm = exit_price * remaining * commission
                total_pnl += pnl - comm; break
        if j == len(candles) - 1 and remaining > 0:
            if direction == 'bullish': pnl = (close - entry) * remaining
            else: pnl = (entry - close) * remaining
            comm = close * remaining * commission
            total_pnl += pnl - comm
    return total_pnl

# ─── 4. SIMULACIÓN CON PPO MASIVO ─────────────────────────────
model = PPO.load("rl_exit_massive")
print("✅ Modelo masivo cargado.")

def simulate_rl(signal):
    env = ExitEnv(
        candles=signal['candles'],
        direction=signal['direction'],
        entry_price=signal['entry_price'],
        sl_original=signal['sl'],
        tp1=signal['tp1'],
        contracts=signal['contracts']
    )
    obs, _ = env.reset(); done = False; total_pnl = 0.0
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, _ = env.step(action)
        total_pnl += reward
        done = terminated or truncated
    return total_pnl

# ─── 5. COMPARACIÓN ───────────────────────────────────────────
print("\n🔄 Simulando y comparando...")
pnl_fixed = []; pnl_rl = []
for i, sig in enumerate(signals):
    pnl_f = simulate_fixed(sig)
    pnl_r = simulate_rl(sig)
    pnl_fixed.append(pnl_f); pnl_rl.append(pnl_r)

df = pd.DataFrame({'fixed': pnl_fixed, 'rl': pnl_rl})
print("\n" + "="*50)
print("RESULTADOS DE VALIDACIÓN MASIVA (Q2 2025)")
print("="*50)
print(f"Trades evaluados:          {len(signals)}")
print(f"PnL total (reglas fijas):  ${df['fixed'].sum():.2f}")
print(f"PnL total (agente PPO):    ${df['rl'].sum():.2f}")
win_fixed = (df['fixed'] > 0).mean() * 100
win_rl    = (df['rl'] > 0).mean() * 100
print(f"Win Rate fijo:             {win_fixed:.1f}%")
print(f"Win Rate PPO:              {win_rl:.1f}%")
pf_fixed = df[df['fixed']>0]['fixed'].sum() / abs(df[df['fixed']<=0]['fixed'].sum()) if df[df['fixed']<=0]['fixed'].sum() != 0 else float('inf')
pf_rl    = df[df['rl']>0]['rl'].sum() / abs(df[df['rl']<=0]['rl'].sum()) if df[df['rl']<=0]['rl'].sum() != 0 else float('inf')
print(f"Profit Factor fijo:        {pf_fixed:.2f}")
print(f"Profit Factor PPO:         {pf_rl:.2f}")
print("="*50)
if df['rl'].sum() > df['fixed'].sum():
    print("✅ El agente PPO masivo SUPERA a las reglas fijas en Q2 2025.")
else:
    print("⛔ Las reglas fijas siguen siendo mejores.")
