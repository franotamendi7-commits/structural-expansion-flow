#!/usr/bin/env python3
"""
Entrenamiento del Guard Agent (filtro inteligente para CI, WR, ST).
Usa los CSVs multi‑período para aprender cuándo los filtros fijos se equivocan.
"""
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
import pickle

print("🧠 Entrenando Guard Agent...")

# 1. Cargar datos históricos
files = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv"
]
dfs = []
for f in files:
    df = pd.read_csv(f, parse_dates=["entry_time"])
    df = df.loc[:, ~df.columns.duplicated()]
    dfs.append(df)
    print(f"  ✔ {f}: {len(df)} trades")
df_all = pd.concat(dfs, ignore_index=True)
print(f"🔹 Total trades: {len(df_all)}")

# 2. Crear columna: ¿qué habrían hecho los filtros fijos?
# Tomamos los thresholds actuales de la Fase 5 por par.
thresholds = {
    'BTCUSDT': {'ci': 74.0, 'wr_long': -80, 'wr_short': -20, 'st_bias': 'bullish'},
    'ETHUSDT': {'ci': 72.0, 'wr_long': -80, 'wr_short': -20, 'st_bias': 'bullish'},
    'SOLUSDT': {'ci': 70.0, 'wr_long': -80, 'wr_short': -20, 'st_bias': 'bullish'},
    'XRPUSDT': {'ci': 68.0, 'wr_long': -80, 'wr_short': -20, 'st_bias': 'bullish'},
    'BNBUSDT': {'ci': 68.0, 'wr_long': -80, 'wr_short': -20, 'st_bias': 'bullish'},
}

def filtro_fijo_aprueba(row):
    sym = row['symbol']
    if sym not in thresholds:
        return True  # si no hay config, aprobar
    cfg = thresholds[sym]
    # Choppiness: rechazar si CI > threshold
    if row.get('ci_value', 0) > cfg['ci']:
        return False
    # WilliamsR: para LONG, WR debe estar en oversold (< -80); para SHORT, en overbought (> -20)
    direction = row.get('direction', 'LONG')
    wr5 = row.get('wr_5m', -50)
    if direction == 'LONG' and wr5 < cfg['wr_long']:
        return False
    if direction == 'SHORT' and wr5 > cfg['wr_short']:
        return False
    # Supertrend: rechazar si bias no coincide con dirección
    st_bias = row.get('st_bias_bullish', 1)  # 1 si bullish, 0 si bearish
    if direction == 'LONG' and st_bias == 0:
        return False
    if direction == 'SHORT' and st_bias == 1:
        return False
    return True

df_all['filtro_fijo'] = df_all.apply(filtro_fijo_aprueba, axis=1)

# 3. Features y target
feature_cols = [
    'ci_value', 'wr_5m', 'wr_15m', 'st_aligned', 'st_bias_bullish', 'st_bias_bearish',
    'mom_score', 'vol_ratio_5m', 'body_ratio_4h', 'hour_of_day', 'direction_long',
    'fib_width', 'filtro_fijo'  # <-- incluimos la decisión del filtro fijo como feature
]
# Asegurar que existen
for col in feature_cols:
    if col not in df_all.columns:
        df_all[col] = 0.0

X = df_all[feature_cols].fillna(0)
y = (df_all['pnl_neto'] > 0).astype(int)

# 4. Validación temporal con 5 folds
tscv = TimeSeriesSplit(n_splits=5)
pfs = []
for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

    # Purging básico
    test_times = df_all.iloc[test_idx]['entry_time']
    train_times = df_all.iloc[train_idx]['entry_time']
    cutoff = test_times.min()
    valid_train = train_idx[train_times < cutoff]
    if len(valid_train) == 0:
        continue
    X_train = X.loc[valid_train]
    y_train = y.loc[valid_train]

    model = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight='balanced', random_state=42)
    model.fit(X_train, y_train)
    prob = model.predict_proba(X_test)[:, 1]

    # Simular: Guard Agent revierte la decisión del filtro fijo si su probabilidad > 0.55
    filtro_fijo_test = X_test['filtro_fijo'].values
    guard_aprueba = (prob > 0.55) | filtro_fijo_test  # aprueba si el filtro fijo ya aprobaba O si el guard la revive
    # Para evaluar, tomamos solo las señales que el sistema habría ejecutado (filtro fijo + guard)
    y_pred = y_test[guard_aprueba]
    if len(y_pred) > 0:
        wr = y_pred.mean()
        # PF simulado
        trades_test = df_all.iloc[test_idx][guard_aprueba]
        gross_profit = trades_test[trades_test['pnl_neto'] > 0]['pnl_neto'].sum()
        gross_loss = abs(trades_test[trades_test['pnl_neto'] <= 0]['pnl_neto'].sum())
        pf = gross_profit / gross_loss if gross_loss != 0 else 10.0
        pfs.append(pf)
        print(f"Fold {fold+1}: WR {wr:.2%}, PF {pf:.2f}, trades ejecutados {len(y_pred)}")
    else:
        print(f"Fold {fold+1}: sin trades")

if pfs:
    avg_pf = np.mean(pfs)
    print(f"\n📊 Profit Factor promedio en validación: {avg_pf:.2f}")
    if avg_pf > 1.8:
        print("✅ Guard Agent mejora el rendimiento. Se guarda el modelo.")
        with open("guard_model.pkl", "wb") as f:
            pickle.dump((model, feature_cols), f)
    else:
        print("⛔ Guard Agent no mejora lo suficiente. No se guarda.")
else:
    print("❌ No se pudo validar el modelo.")
