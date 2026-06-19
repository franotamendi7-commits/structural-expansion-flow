import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

print("🚀 Iniciando entrenamiento del Agente de ML...")

# 1. Cargar y ordenar los datos
df = pd.read_csv('backtest_institucional_Q42025.csv', parse_dates=['entry_time'])
df = df.sort_values('entry_time').reset_index(drop=True)

# 2. Preprocesamiento de Features
phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
df.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True)

# 3. Definir Features (X) y Target (y)
cols_a_evitar = ['symbol', 'entry_time', 'direction', 'entry_signal',
                 'entry_real', 'sl_original', 'tp1', 'contracts', 'pnl_neto',
                 'exit_events', 'exit_type', 'explanation', 'score']
feature_cols = [c for c in df.columns if c not in cols_a_evitar]
X = df[feature_cols].copy()
y = (df['pnl_neto'] > 0).astype(int)

# 4. Manejo de valores nulos
X.fillna(X.median(), inplace=True)

# 5. Validación Cruzada Temporal
tscv = TimeSeriesSplit(n_splits=5)
all_importances = []

print("\n🔍 Evaluando rendimiento por cada pliegue temporal...")

for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
    X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
    y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    
    # PURGING
    test_times = df.iloc[test_idx]['entry_time']
    train_times = df.iloc[train_idx]['entry_time']
    cutoff = test_times.min()
    valid_train_idx = train_idx[train_times < cutoff]
    
    if len(valid_train_idx) == 0:
        print(f"Fold {fold+1}: Sin datos de entrenamiento válidos tras purging. Saltando...")
        continue
        
    X_train_purged = X.loc[valid_train_idx]
    y_train_purged = y.loc[valid_train_idx]
    
    # 6. Entrenar Modelo
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, class_weight='balanced')
    model.fit(X_train_purged, y_train_purged)
    all_importances.append(model.feature_importances_)
    
    # 7. Evaluar Filtro
    y_prob = model.predict_proba(X_test)[:, 1]
    umbral = 0.55
    filtro = y_prob > umbral
    
    if filtro.sum() > 0:
        wr_filtrado = y_test[filtro].mean()
        # Profit Factor clásico (Ganancia Total / Pérdida Total)
        trades_filtrados = df.iloc[test_idx][filtro]
        ganancia = trades_filtrados[trades_filtrados['pnl_neto'] > 0]['pnl_neto'].sum()
        perdida = abs(trades_filtrados[trades_filtrados['pnl_neto'] <= 0]['pnl_neto'].sum())
        pf_filtrado_clasico = ganancia / perdida if perdida != 0 else float('inf')
    else:
        wr_filtrado = 0
        pf_filtrado_clasico = 0
        
    print(f"Fold {fold+1}: Trades originales {len(y_test)}, filtrados {filtro.sum()} | WR original {y_test.mean():.2%}, WR filtrado {wr_filtrado:.2%}, PF filtrado {pf_filtrado_clasico:.2f}")

# 8. Importancia Promedio de Features (todos los folds)
if all_importances:
    avg_importances = np.mean(all_importances, axis=0)
    importance_series = pd.Series(avg_importances, index=feature_cols).sort_values(ascending=False)
    print("\n📊 Importancia promedio de features (todos los folds):")
    print(importance_series.head(10))
else:
    print("\n⚠️ No se pudo calcular la importancia de features. No se ejecutó ningún fold válido.")
