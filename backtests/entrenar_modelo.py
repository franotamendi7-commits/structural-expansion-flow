import pandas as pd
from sklearn.ensemble import RandomForestClassifier
import pickle

print("🧠 Entrenando modelo ML definitivo...")
train_files = [
    'backtest_institucional_Q12023.csv',
    'backtest_institucional_Q32024.csv',
    'backtest_institucional_Q42025.csv',
    'backtest_institucional_Q12026_ref.csv'
]
dfs = []
for f in train_files:
    df_temp = pd.read_csv(f, parse_dates=['entry_time'])
    df_temp = df_temp.loc[:, ~df_temp.columns.duplicated()]
    dfs.append(df_temp)
train = pd.concat(dfs, ignore_index=True)
train = train.loc[:, ~train.columns.duplicated()]

phase_dummies = pd.get_dummies(train['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(train['mom_direction'], prefix='mom')
train = pd.concat([train, phase_dummies, mom_dummies], axis=1)
train.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True, errors='ignore')

cols_a_evitar = ['symbol', 'entry_time', 'direction', 'entry_signal',
                 'entry_real', 'sl_original', 'tp1', 'contracts', 'pnl_neto',
                 'exit_events', 'exit_type', 'explanation', 'score']
features_cols = [c for c in train.columns if c not in cols_a_evitar]

X = train[features_cols].fillna(train[features_cols].median())
y = (train['pnl_neto'] > 0).astype(int)

model = RandomForestClassifier(n_estimators=100, max_depth=5,
                               random_state=42, class_weight='balanced')
model.fit(X, y)
with open('ml_model.pkl', 'wb') as f:
    pickle.dump((model, features_cols), f)
print(f"✅ Modelo guardado (ml_model.pkl) con {len(features_cols)} features.")
