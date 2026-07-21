import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from datetime import timezone
import pickle

print("🚀 Reentrenando modelo ML con 515 trades...")

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

if len(dfs) < 4:
    raise SystemExit("Faltan archivos CSV. Asegurate de tener los 4 períodos.")

df = pd.concat(dfs, ignore_index=True)
df = df.sort_values('entry_time').reset_index(drop=True)

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

model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
model.fit(X, y)

with open('ml_model.pkl', 'wb') as f:
    pickle.dump(model, f)

print(f"✅ Modelo reentrenado con {len(df)} trades y guardado en ml_model.pkl")
