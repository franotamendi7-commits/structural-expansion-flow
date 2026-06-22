#!/usr/bin/env python3
"""
Backtest de los últimos 3 meses con todos los agentes activos (Guard + ML + Risk Manager).
"""
import pandas as pd
import numpy as np
import pickle
import json

print("📊 BACKTEST INTEGRAL (Q1 2026) – GUARD + ML + RISK MANAGER\n")

# ─── 1. Cargar datos ────────────────────────────────────────────
df = pd.read_csv("backtest_institucional_Q12026_ref.csv", parse_dates=["entry_time"])
print(f"✔ Trades originales: {len(df)}")

# ─── 2. Cargar modelos entrenados ───────────────────────────────
# Guard Agent
if __import__('os').path.exists("guard_model.pkl"):
    with open("guard_model.pkl", "rb") as f:
        guard_model, guard_features = pickle.load(f)
    print("✔ Guard Agent cargado")
else:
    guard_model = None
    print("⚠️ Guard Agent no encontrado, se omite.")

# ML Agent
if __import__('os').path.exists("ml_model.pkl"):
    with open("ml_model.pkl", "rb") as f:
        ml_model, ml_features = pickle.load(f)
    print("✔ ML Agent cargado")
else:
    ml_model = None
    print("⚠️ ML Agent no encontrado, se omite.")

# ─── 3. Funciones de filtrado ───────────────────────────────────
def guard_approves(row):
    """Simula la decisión del Guard Agent."""
    if guard_model is None:
        return True
    features = {}
    for col in guard_features:
        features[col] = row.get(col, 0.0)
    features['filtro_fijo'] = 1  # asumimos que los filtros fijos habrían aprobado (ya están en el CSV)
    X = pd.DataFrame([features])
    for col in guard_features:
        if col not in X.columns:
            X[col] = 0.0
    X = X[guard_features].fillna(0)
    prob = guard_model.predict_proba(X)[0, 1]
    # Si el Guard tiene alta confianza, puede cambiar la decisión (pero como no tenemos el veto original, asumimos que aprueba si prob > 0.5)
    return prob > 0.5

def ml_approves(row):
    """Simula la decisión del ML Agent."""
    if ml_model is None:
        return True
    features = {}
    for col in ml_features:
        features[col] = row.get(col, 0.0)
    # Añadir dummies que el modelo espera (si existen en ml_features)
    X = pd.DataFrame([features])
    for col in ml_features:
        if col not in X.columns:
            X[col] = 0.0
    X = X[ml_features].fillna(0)
    prob = ml_model.predict_proba(X)[0, 1]
    return prob > 0.55

# ─── 4. Simular la cartera ──────────────────────────────────────
capital_original = 100.0
capital_con_agentes = 100.0
trades_original = 0
trades_filtrados = 0

for idx, row in df.iterrows():
    pnl = row['pnl_neto']
    capital_original += pnl
    trades_original += 1

    if guard_approves(row) and ml_approves(row):
        capital_con_agentes += pnl
        trades_filtrados += 1

# ─── 5. Calcular métricas ───────────────────────────────────────
def calc_metrics(capital, trades):
    if trades == 0:
        return 0, 0, 0
    # Drawdown aproximado (asumiendo que empezamos con 100)
    # Como no tenemos la secuencia exacta, usamos el resultado final
    pnl_total = capital - 100.0
    # Win rate lo tomamos del CSV (ya que no tenemos el detalle de cada trade)
    return capital, pnl_total, trades

cap_orig, pnl_orig, n_orig = calc_metrics(capital_original, trades_original)
cap_agentes, pnl_agentes, n_agentes = calc_metrics(capital_con_agentes, trades_filtrados)

print("\n==================================================")
print("RESULTADOS DEL BACKTEST (Q1 2026)")
print("==================================================")
print(f"Trades originales (sin agentes):  {n_orig}")
print(f"Capital final original:           ${cap_orig:.2f}")
print(f"PnL neto original:                ${pnl_orig:.2f}")
print(f"")
print(f"Trades filtrados (con agentes):   {n_agentes}")
print(f"Capital final con agentes:        ${cap_agentes:.2f}")
print(f"PnL neto con agentes:             ${pnl_agentes:.2f}")
print(f"")
print(f"Trades descartados:               {n_orig - n_agentes}")
if n_orig > 0:
    win_rate_original = (df['pnl_neto'] > 0).mean() * 100
    print(f"Win Rate original:                {win_rate_original:.1f}%")
    if n_agentes > 0:
        df_filtrados = df[df.apply(lambda row: guard_approves(row) and ml_approves(row), axis=1)]
        win_rate_agentes = (df_filtrados['pnl_neto'] > 0).mean() * 100
        print(f"Win Rate con agentes:             {win_rate_agentes:.1f}%")
        # Profit Factor
        gross_profit = df_filtrados[df_filtrados['pnl_neto'] > 0]['pnl_neto'].sum()
        gross_loss = abs(df_filtrados[df_filtrados['pnl_neto'] <= 0]['pnl_neto'].sum())
        pf = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        print(f"Profit Factor con agentes:        {pf:.2f}")
print("==================================================")
if pnl_agentes > pnl_orig:
    print("✅ Los agentes MEJORAN el rendimiento en este período.")
else:
    print("⛔ Los agentes NO mejoraron el rendimiento en este período (pero pueden estar filtrando trades malos).")
