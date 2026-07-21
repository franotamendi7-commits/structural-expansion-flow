import pandas as pd
import numpy as np
import pickle
import sys

# Cargar modelo
with open('ml_model.pkl', 'rb') as f:
    model = pickle.load(f)

# Columnas que espera el modelo (en orden exacto)
expected_features = model.feature_names_in_

# Cargar datos out-of-sample
try:
    df = pd.read_csv('backtest_institucional_Q22025.csv', parse_dates=['entry_time'])
except FileNotFoundError:
    sys.exit("❌ No se encontró backtest_institucional_Q22025.csv. Generalo primero.")

df = df.sort_values('entry_time')

# Preprocesamiento
phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
df.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True)

cols_a_evitar = ['symbol', 'entry_time', 'direction', 'entry_signal',
                 'entry_real', 'sl_original', 'tp1', 'contracts', 'pnl_neto',
                 'exit_events', 'exit_type', 'explanation', 'score']
feature_cols = [c for c in df.columns if c not in cols_a_evitar]
X_new = df[feature_cols].fillna(df[feature_cols].median())

# --- Alinear columnas con el modelo ---
# Añadir columnas faltantes con 0
for col in expected_features:
    if col not in X_new.columns:
        X_new[col] = 0

# Eliminar columnas extra que el modelo no conoce
X_new = X_new[expected_features]

# Predecir
y_prob = model.predict_proba(X_new)[:, 1]

# Evaluar umbrales
print("🔍 Evaluación de umbrales en Q2 2025 (OUT-OF-SAMPLE)")
print("=" * 60)

umbrales = np.arange(0.45, 0.75, 0.05)
mejor_pf = 0
mejor_umbral = 0.5

for umbral in umbrales:
    filtro = y_prob > umbral
    n = filtro.sum()
    if n == 0:
        print(f"Umbral {umbral:.2f}: 0 trades filtrados")
        continue

    trades_filt = df.iloc[filtro]
    ganancia = trades_filt[trades_filt['pnl_neto'] > 0]['pnl_neto'].sum()
    perdida = abs(trades_filt[trades_filt['pnl_neto'] <= 0]['pnl_neto'].sum())
    pf = ganancia / perdida if perdida != 0 else float('inf')
    wr = (trades_filt['pnl_neto'] > 0).mean()

    print(f"Umbral {umbral:.2f}: {n:3d} trades | WR {wr:.2%} | PF {pf:.2f}")

    if pf > mejor_pf and n >= 10:
        mejor_pf = pf
        mejor_umbral = umbral

print(f"\n✅ Mejor umbral recomendado: {mejor_umbral:.2f} (PF={mejor_pf:.2f})")
print("Este umbral solo debe cambiarse si reentrenás el modelo con nuevos datos.")
