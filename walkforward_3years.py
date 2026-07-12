import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
import pickle

print("🚀 Validación Walk-Forward con 3 años de datos (1498 trades)\n")

df = pd.read_csv('backtest_3years.csv', parse_dates=['entry_time'])
df = df.sort_values('entry_time').reset_index(drop=True)

# Preprocesamiento común
phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
df_full = pd.concat([df, phase_dummies, mom_dummies], axis=1)
df_full.drop(['phase_h1','mom_direction'], axis=1, inplace=True)

cols_a_evitar = ['symbol','entry_time','direction','entry_signal',
                 'entry_real','sl_original','tp1','contracts','pnl_neto',
                 'exit_events','exit_type','explanation','score']
feature_cols = [c for c in df_full.columns if c not in cols_a_evitar]
X = df_full[feature_cols].fillna(df_full[feature_cols].median())
y = (df_full['pnl_neto'] > 0).astype(int)

# Crear trimestres desde Q2 2023 hasta Q2 2026
df_full['quarter'] = df_full['entry_time'].dt.to_period('Q')
quarters = sorted(df_full['quarter'].unique())

# Vamos a usar solo los trimestres con al menos 10 trades
test_quarters = [q for q in quarters if (df_full['quarter'] == q).sum() >= 10]

# Acumuladores de trades filtrados y resultados
all_trades_test = []
umbral = 0.55

print(f"Evaluando con umbral fijo {umbral:.2f}")
print("=" * 60)

for i, test_q in enumerate(test_quarters):
    # Train: todos los trimestres anteriores al test_q
    train_mask = df_full['quarter'] < test_q
    test_mask  = df_full['quarter'] == test_q
    
    X_train = X[train_mask]
    y_train = y[train_mask]
    X_test  = X[test_mask]
    y_test  = y[test_mask]
    df_test = df_full[test_mask].copy()
    
    if len(X_train) < 100:
        continue  # necesita suficiente historia
    
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    model.fit(X_train, y_train)
    
    probs = model.predict_proba(X_test)[:, 1]
    df_test['prob'] = probs
    filtro = probs > umbral
    
    trades_filt = df_test[filtro]
    n_filt = len(trades_filt)
    n_total = len(df_test)
    
    if n_filt > 0:
        wr = (trades_filt['pnl_neto'] > 0).mean()
        g = trades_filt[trades_filt['pnl_neto'] > 0]['pnl_neto'].sum()
        p = abs(trades_filt[trades_filt['pnl_neto'] <= 0]['pnl_neto'].sum())
        pf = g / p if p != 0 else float('inf')
        all_trades_test.append(trades_filt[['pnl_neto', 'prob']])
        print(f"  {test_q}: {n_filt:3d}/{n_total:3d} filtrados | WR {wr:.2%} | PF {pf:.2f}")
    else:
        print(f"  {test_q}: 0/{n_total} filtrados")

# Resultado acumulado
if all_trades_test:
    final_trades = pd.concat(all_trades_test)
    total_trades = len(final_trades)
    wr_total = (final_trades['pnl_neto'] > 0).mean()
    g_total = final_trades[final_trades['pnl_neto'] > 0]['pnl_neto'].sum()
    p_total = abs(final_trades[final_trades['pnl_neto'] <= 0]['pnl_neto'].sum())
    pf_total = g_total / p_total if p_total != 0 else float('inf')
    
    print("\n" + "=" * 60)
    print(f"✅ RESULTADO WALK-FORWARD ACUMULADO:")
    print(f"  Trades filtrados totales: {total_trades}")
    print(f"  Win Rate global: {wr_total:.2%}")
    print(f"  Profit Factor global: {pf_total:.2f}")
else:
    print("\n❌ No se generaron trades filtrados en ningún trimestre.")

# Entrenar modelo final con todos los datos y guardar
model_final = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
model_final.fit(X, y)
with open('ml_model_3years.pkl', 'wb') as f:
    pickle.dump(model_final, f)

print(f"\n✅ Modelo final guardado como ml_model_3years.pkl (umbral={umbral:.2f})")
