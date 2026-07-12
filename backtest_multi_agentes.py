#!/usr/bin/env python3
"""
Backtest multi‑período con todos los CSVs disponibles (Guard + ML Agent).
"""
import pandas as pd
import numpy as np
import pickle
import os

print("📊 BACKTEST MULTI‑PERÍODO (4 CSVs) – GUARD + ML AGENT\n")

# ─── 1. Cargar datos ────────────────────────────────────────────
files = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv"
]
dfs = []
for f in files:
    if os.path.exists(f):
        df = pd.read_csv(f, parse_dates=["entry_time"])
        df = df.loc[:, ~df.columns.duplicated()]
        dfs.append(df)
        print(f"  ✔ {f}: {len(df)} trades")
    else:
        print(f"  ⚠️  {f} no encontrado, se omite.")

if not dfs:
    print("❌ No se encontraron CSVs.")
    exit()

df_all = pd.concat(dfs, ignore_index=True).sort_values('entry_time').reset_index(drop=True)
print(f"\n🔹 Trades originales totales: {len(df_all)}")

# ─── 2. Cargar modelos ──────────────────────────────────────────
guard_model, guard_features = None, None
ml_model, ml_features = None, None

if os.path.exists("guard_model.pkl"):
    with open("guard_model.pkl", "rb") as f:
        guard_model, guard_features = pickle.load(f)
    print("✔ Guard Agent cargado")
else:
    print("⚠️ Guard Agent no encontrado, se omite.")

if os.path.exists("ml_model.pkl"):
    with open("ml_model.pkl", "rb") as f:
        ml_model = pickle.load(f)
        ml_features = ml_model.feature_names_in_
    print("✔ ML Agent cargado")
else:
    print("⚠️ ML Agent no encontrado, se omite.")

# ─── 3. Funciones de filtrado ───────────────────────────────────
def guard_approves(row):
    if guard_model is None:
        return True
    feats = {col: row.get(col, 0.0) for col in guard_features}
    feats['filtro_fijo'] = 1   # asumimos que los filtros fijos habrían aprobado
    X = pd.DataFrame([feats])
    for col in guard_features:
        if col not in X.columns:
            X[col] = 0.0
    X = X[guard_features].fillna(0)
    prob = guard_model.predict_proba(X)[0, 1]
    return prob > 0.5

def ml_approves(row):
    if ml_model is None:
        return True
    feats = {col: row.get(col, 0.0) for col in ml_features}
    X = pd.DataFrame([feats])
    for col in ml_features:
        if col not in X.columns:
            X[col] = 0.0
    X = X[ml_features].fillna(0)
    prob = ml_model.predict_proba(X)[0, 1]
    return prob > 0.55

# ─── 4. Simular ─────────────────────────────────────────────────
capital_original = 100.0
capital_filtrado = 100.0
trades_original = 0
trades_filtrados = 0

df_filtrados_list = []

for idx, row in df_all.iterrows():
    pnl = row['pnl_neto']
    capital_original += pnl
    trades_original += 1

    if guard_approves(row) and ml_approves(row):
        capital_filtrado += pnl
        trades_filtrados += 1
        df_filtrados_list.append(row)

# ─── 5. Métricas ────────────────────────────────────────────────
print("\n==================================================")
print("RESULTADOS MULTI‑PERÍODO (4 CSVs)")
print("==================================================")
print(f"Trades originales:               {trades_original}")
print(f"Capital final original:          ${capital_original:.2f}")
print(f"PnL neto original:               ${capital_original - 100:.2f}")

win_rate_original = (df_all['pnl_neto'] > 0).mean() * 100
print(f"Win Rate original:               {win_rate_original:.1f}%")

print("")
print(f"Trades filtrados (Guard+ML):     {trades_filtrados}")
print(f"Capital final filtrado:          ${capital_filtrado:.2f}")
print(f"PnL neto filtrado:               ${capital_filtrado - 100:.2f}")

if trades_filtrados > 0:
    df_filt = pd.DataFrame(df_filtrados_list)
    win_rate_filt = (df_filt['pnl_neto'] > 0).mean() * 100
    gross_profit = df_filt[df_filt['pnl_neto'] > 0]['pnl_neto'].sum()
    gross_loss   = abs(df_filt[df_filt['pnl_neto'] <= 0]['pnl_neto'].sum())
    pf = gross_profit / gross_loss if gross_loss != 0 else float('inf')
    print(f"Win Rate filtrado:               {win_rate_filt:.1f}%")
    print(f"Profit Factor filtrado:          {pf:.2f}")
else:
    print("Win Rate filtrado:               N/A (0 trades)")
    print("Profit Factor filtrado:          N/A")

print("==================================================")
print(f"Trades descartados por agentes:  {trades_original - trades_filtrados}")
