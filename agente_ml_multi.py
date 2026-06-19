import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

print("🚀 ML Multi-Período con umbrales 0.55 y 0.70\n")

archivos = [
    'backtest_institucional_Q12023.csv',
    'backtest_institucional_Q32024.csv',
    'backtest_institucional_Q42025.csv',
    'backtest_institucional_Q12026_ref.csv'
]

dfs = []
for arch in archivos:
    try:
        df_temp = pd.read_csv(arch, parse_dates=['entry_time'])
        dfs.append(df_temp)
        print(f"✔ {arch}: {len(df_temp)} trades")
    except FileNotFoundError:
        print(f"⚠ No se encontró {arch}, se omite.")

if not dfs:
    raise SystemExit("Ningún CSV encontrado. Ejecutá primero generar_periodos.py")

df = pd.concat(dfs, ignore_index=True)
df = df.sort_values('entry_time').reset_index(drop=True)
print(f"\n🔹 Dataset combinado: {len(df)} trades")

phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
df.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True)

cols_a_evitar = ['symbol', 'entry_time', 'direction', 'entry_signal',
                 'entry_real', 'sl_original', 'tp1', 'contracts', 'pnl_neto',
                 'exit_events', 'exit_type', 'explanation', 'score']
feature_cols = [c for c in df.columns if c not in cols_a_evitar]
X = df[feature_cols].fillna(df[feature_cols].median())
y = (df['pnl_neto'] > 0).astype(int)

umbrales = [0.55, 0.70]
tscv = TimeSeriesSplit(n_splits=6)

for umbral in umbrales:
    print(f"\n{'='*40}\n=== UMBRAL {umbral} ===\n{'='*40}")
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

        test_times = df.iloc[test_idx]['entry_time']
        train_times = df.iloc[train_idx]['entry_time']
        cutoff = test_times.min()
        valid_train_idx = train_idx[train_times < cutoff]

        if len(valid_train_idx) == 0:
            print(f"Fold {fold+1}: sin train válido, saltando")
            continue

        X_train_purged = X.loc[valid_train_idx]
        y_train_purged = y.loc[valid_train_idx]

        model = RandomForestClassifier(n_estimators=100, max_depth=5,
                                       random_state=42, class_weight='balanced')
        model.fit(X_train_purged, y_train_purged)
        y_prob = model.predict_proba(X_test)[:, 1]

        filtro = y_prob > umbral
        n_filtrados = filtro.sum()
        if n_filtrados == 0:
            print(f"Fold {fold+1}: 0 trades filtrados")
            continue

        trades_filt = df.iloc[test_idx][filtro]
        ganancia = trades_filt[trades_filt['pnl_neto'] > 0]['pnl_neto'].sum()
        perdida = abs(trades_filt[trades_filt['pnl_neto'] <= 0]['pnl_neto'].sum())
        pf = ganancia / perdida if perdida != 0 else float('inf')
        wr = y_test[filtro].mean()
        print(f"Fold {fold+1}: {n_filtrados} trades | WR {wr:.2%} | PF {pf:.2f}")
