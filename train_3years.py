import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import pickle
import sys

print("🚀 Entrenando modelo con 3 años de datos (división temporal)")

# Cargar datos
df = pd.read_csv('backtest_3years.csv', parse_dates=['entry_time'])
df = df.sort_values('entry_time')

# Definir puntos de corte
train_end = '2025-05-31'
val_end   = '2025-08-31'

mask_train = df['entry_time'] <= train_end
mask_val   = (df['entry_time'] > train_end) & (df['entry_time'] <= val_end)
mask_test  = df['entry_time'] > val_end

n_train = mask_train.sum()
n_val   = mask_val.sum()
n_test  = mask_test.sum()

print(f"Train: {n_train} trades | Val: {n_val} | Test: {n_test}")

if n_train < 100:
    sys.exit("❌ Muy pocos trades en Train. Revisá las fechas de corte.")

# Preprocesamiento
phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
df.drop(['phase_h1','mom_direction'], axis=1, inplace=True)

cols_a_evitar = ['symbol','entry_time','direction','entry_signal',
                 'entry_real','sl_original','tp1','contracts','pnl_neto',
                 'exit_events','exit_type','explanation','score']
feature_cols = [c for c in df.columns if c not in cols_a_evitar]
X = df[feature_cols].fillna(df[feature_cols].median())
y = (df['pnl_neto'] > 0).astype(int)

X_train, y_train = X[mask_train], y[mask_train]
X_val,   y_val   = X[mask_val],   y[mask_val]
X_test,  y_test  = X[mask_test],  y[mask_test]

# Entrenar modelo
model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
model.fit(X_train, y_train)

# Buscar mejor umbral en Validación
probs_val = model.predict_proba(X_val)[:,1]
mejor_pf = 0
mejor_umbral = 0.5
print("\n🔍 Evaluando umbrales en Validación:")
for umb in np.arange(0.45, 0.75, 0.05):
    filtro = probs_val > umb
    n = filtro.sum()
    if n < 10:
        print(f"  Umbral {umb:.2f}: {n} trades (insuficientes)")
        continue
    trades_filt = df[mask_val].iloc[filtro]
    g = trades_filt[trades_filt['pnl_neto']>0]['pnl_neto'].sum()
    p = abs(trades_filt[trades_filt['pnl_neto']<=0]['pnl_neto'].sum())
    pf = g/p if p != 0 else float('inf')
    wr = (trades_filt['pnl_neto']>0).mean()
    print(f"  Umbral {umb:.2f}: {n} trades | WR {wr:.2%} | PF {pf:.2f}")
    if pf > mejor_pf:
        mejor_pf = pf
        mejor_umbral = umb

print(f"\n✅ Mejor umbral en Validación: {mejor_umbral:.2f} (PF={mejor_pf:.2f})")

# Evaluar en TEST (intocable)
probs_test = model.predict_proba(X_test)[:,1]
filtro_test = probs_test > mejor_umbral
n_test_filt = filtro_test.sum()
if n_test_filt == 0:
    print("❌ Ningún trade superó el umbral en Test.")
else:
    trades_test = df[mask_test].iloc[filtro_test]
    g_test = trades_test[trades_test['pnl_neto']>0]['pnl_neto'].sum()
    p_test = abs(trades_test[trades_test['pnl_neto']<=0]['pnl_neto'].sum())
    pf_test = g_test/p_test if p_test != 0 else float('inf')
    wr_test = (trades_test['pnl_neto']>0).mean()
    print(f"\n🔮 Rendimiento en TEST (nunca visto):")
    print(f"  Trades filtrados: {n_test_filt} de {n_test}")
    print(f"  Win Rate: {wr_test:.2%}")
    print(f"  Profit Factor: {pf_test:.2f}")

# Entrenar modelo final (Train + Validation) y guardar
model_final = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
X_trainval = pd.concat([X_train, X_val])
y_trainval = pd.concat([y_train, y_val])
model_final.fit(X_trainval, y_trainval)

with open('ml_model_3years.pkl', 'wb') as f:
    pickle.dump(model_final, f)

print(f"\n✅ Modelo final guardado como ml_model_3years.pkl (umbral={mejor_umbral:.2f})")
