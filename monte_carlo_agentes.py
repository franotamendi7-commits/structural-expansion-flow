#!/usr/bin/env python3
"""Monte Carlo sobre los trades filtrados por Guard + ML."""
import pandas as pd
import numpy as np
import pickle
import os

print("📊 MONTE CARLO – TRADES FILTRADOS (GUARD + ML)\n")

# ─── 1. Cargar CSVs y aplicar filtros ──────────────────────────
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
df_all = pd.concat(dfs, ignore_index=True).sort_values('entry_time')

# Cargar modelos
with open("guard_model.pkl", "rb") as f:
    guard_model, guard_features = pickle.load(f)
with open("ml_model.pkl", "rb") as f:
    ml_model, ml_features = pickle.load(f)

def guard_approves(row):
    feats = {col: row.get(col, 0.0) for col in guard_features}
    feats['filtro_fijo'] = 1
    X = pd.DataFrame([feats]).reindex(columns=guard_features, fill_value=0)
    prob = guard_model.predict_proba(X)[0, 1]
    return prob > 0.5

def ml_approves(row):
    feats = {col: row.get(col, 0.0) for col in ml_features}
    X = pd.DataFrame([feats]).reindex(columns=ml_features, fill_value=0)
    prob = ml_model.predict_proba(X)[0, 1]
    return prob > 0.55

# Obtener lista de P&L de los trades filtrados
pnl_list = []
for idx, row in df_all.iterrows():
    if guard_approves(row) and ml_approves(row):
        pnl_list.append(row['pnl_neto'])

print(f"✔ Trades filtrados encontrados: {len(pnl_list)}")
if len(pnl_list) == 0:
    print("❌ No hay trades filtrados. Abortando.")
    exit()

# ─── 2. Monte Carlo ────────────────────────────────────────────
n_sims = 10_000
capital_inicial = 100.0
final_caps = []

np.random.seed(42)
for _ in range(n_sims):
    # Remuestrear con reemplazo la misma cantidad de trades que tenemos (158)
    sample = np.random.choice(pnl_list, size=len(pnl_list), replace=True)
    capital = capital_inicial + sample.sum()
    final_caps.append(capital)

final_caps = np.array(final_caps)
p5 = np.percentile(final_caps, 5)
mean = np.mean(final_caps)
p95 = np.percentile(final_caps, 95)
prob_ganancia = (final_caps > 100).mean() * 100

print(f"\n📈 Resultados de {n_sims} simulaciones (remuestreo de {len(pnl_list)} trades):")
print(f"Capital inicial: ${capital_inicial:.2f}")
print(f"Capital final promedio: ${mean:.2f}")
print(f"Peor escenario (P5):   ${p5:.2f}")
print(f"Mejor escenario (P95): ${p95:.2f}")
print(f"Probabilidad de ganancia: {prob_ganancia:.1f}%")
