#!/usr/bin/env python3
"""
Entrena un detector de régimen de mercado (K-Means) usando los CSVs históricos.
Features: CI, ATR, ancho de Bollinger, volatilidad relativa, volumen, pendiente.
"""
import pandas as pd
import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import pickle
import os

print("🧠 Entrenando Regime Detector (K-Means, 3 clusters)...")

# ─── 1. Cargar datos históricos ─────────────────────────────────
files = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv"
]
dfs = []
for f in files:
    if not os.path.exists(f):
        print(f"⚠️  {f} no encontrado, se omite.")
        continue
    df = pd.read_csv(f, parse_dates=["entry_time"])
    df = df.loc[:, ~df.columns.duplicated()]
    dfs.append(df)
    print(f"  ✔ {f}: {len(df)} trades")

if not dfs:
    print("❌ No se encontraron CSVs.")
    exit()

df_all = pd.concat(dfs, ignore_index=True)
print(f"🔹 Total muestras: {len(df_all)}")

# ─── 2. Construir features de régimen ──────────────────────────
# Usamos las columnas que ya existen en los CSVs y derivamos otras
feature_cols = []

# Choppiness Index (ya está como 'ci_value')
if 'ci_value' in df_all.columns:
    feature_cols.append('ci_value')
else:
    df_all['ci_value'] = 50.0
    feature_cols.append('ci_value')

# Volatilidad relativa (vol_ratio_5m)
if 'vol_ratio_5m' in df_all.columns:
    feature_cols.append('vol_ratio_5m')
else:
    df_all['vol_ratio_5m'] = 1.0
    feature_cols.append('vol_ratio_5m')

# Ancho de las Bandas de Bollinger (bb_width) – si no existe, lo estimamos con ATR/precio
if 'bb_width' not in df_all.columns:
    # Estimación: ATR/precio (tenemos entry_price como proxy)
    if 'entry_signal' in df_all.columns:
        df_all['atr_est'] = df_all['entry_signal'] * 0.02
        df_all['bb_width'] = df_all['atr_est'] / df_all['entry_signal']
    else:
        df_all['bb_width'] = 0.02
feature_cols.append('bb_width')

# Volumen relativo (body_ratio_4h)
if 'body_ratio_4h' in df_all.columns:
    feature_cols.append('body_ratio_4h')
else:
    df_all['body_ratio_4h'] = 0.5
    feature_cols.append('body_ratio_4h')

# Pendiente de tendencia (si tenemos 'mom_score')
if 'mom_score' in df_all.columns:
    feature_cols.append('mom_score')
else:
    df_all['mom_score'] = 0.0
    feature_cols.append('mom_score')

# Hora del día (para capturar diferencias entre sesiones)
if 'hour_of_day' in df_all.columns:
    feature_cols.append('hour_of_day')
else:
    df_all['hour_of_day'] = 12
    feature_cols.append('hour_of_day')

# Rellenar NaN con 0
for col in feature_cols:
    if df_all[col].isna().any():
        df_all[col] = df_all[col].fillna(0)

X = df_all[feature_cols].values

# ─── 3. Escalar features ───────────────────────────────────────
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# ─── 4. Aplicar K-Means (3 clusters) ───────────────────────────
kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
kmeans.fit(X_scaled)
df_all['cluster'] = kmeans.labels_

# ─── 5. Analizar los clusters ──────────────────────────────────
print("\n🔍 Análisis de clusters (centroides escalados):")
centroids = pd.DataFrame(kmeans.cluster_centers_, columns=feature_cols)
print(centroids)

print("\n📊 Estadísticas por cluster:")
for c in range(3):
    cluster_data = df_all[df_all['cluster'] == c]
    win_rate = (cluster_data['pnl_neto'] > 0).mean() * 100
    avg_pnl = cluster_data['pnl_neto'].mean()
    count = len(cluster_data)
    # Interpretación básica: CI alto = rango, CI bajo = tendencia, vol extremo = caos
    ci_mean = cluster_data['ci_value'].mean()
    vol_mean = cluster_data['vol_ratio_5m'].mean()
    if ci_mean > 65 and vol_mean < 1.2:
        label = "RANGO"
    elif ci_mean < 45 and vol_mean > 1.0:
        label = "TENDENCIA"
    else:
        label = "CAOS"
    print(f"  Cluster {c}: {count} trades, WR {win_rate:.1f}%, PnL medio ${avg_pnl:.2f}, CI medio {ci_mean:.1f}, Vol medio {vol_mean:.2f} → {label}")

# ─── 6. Guardar modelo y scaler ───────────────────────────────
with open("regime_model.pkl", "wb") as f:
    pickle.dump((kmeans, scaler, feature_cols), f)
print("\n✅ Modelo guardado como regime_model.pkl")
