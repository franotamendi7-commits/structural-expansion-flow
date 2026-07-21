import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

print("🔬 Validación Forward en Q2 2025 (período nunca visto)\n")

# ------------------------------------------------------------
# 1. Cargar y unir los 4 CSV de entrenamiento (eliminando duplicados de raíz)
# ------------------------------------------------------------
train_files = [
    'backtest_institucional_Q12023.csv',
    'backtest_institucional_Q32024.csv',
    'backtest_institucional_Q42025.csv',
    'backtest_institucional_Q12026_ref.csv'
]

train_parts = []
for f in train_files:
    try:
        df_temp = pd.read_csv(f, parse_dates=['entry_time'])
        # Eliminar columnas duplicadas que puedan venir en el CSV
        df_temp = df_temp.loc[:, ~df_temp.columns.duplicated()]
        train_parts.append(df_temp)
        print(f"✔ Entrenamiento: {f} ({len(df_temp)} trades)")
    except FileNotFoundError:
        print(f"⚠ No se encontró {f}, se omite.")

if not train_parts:
    raise SystemExit("No se encontraron archivos de entrenamiento.")

train = pd.concat(train_parts, ignore_index=True)
# Por si acaso, volver a eliminar cualquier columna duplicada
train = train.loc[:, ~train.columns.duplicated()]
print(f"🔹 Total entrenamiento: {len(train)} trades\n")

# ------------------------------------------------------------
# 2. Preprocesar datos de entrenamiento
# ------------------------------------------------------------
def preprocesar(df):
    df = df.copy()
    phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
    mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
    df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
    df.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True, errors='ignore')
    return df

train = preprocesar(train)

# ------------------------------------------------------------
# 3. Definir las columnas de features dinámicamente (evitando errores manuales)
# ------------------------------------------------------------
cols_a_evitar = ['symbol', 'entry_time', 'direction', 'entry_signal',
                 'entry_real', 'sl_original', 'tp1', 'contracts', 'pnl_neto',
                 'exit_events', 'exit_type', 'explanation', 'score']
features_cols = [c for c in train.columns if c not in cols_a_evitar]

X_train = train[features_cols].fillna(train[features_cols].median())
y_train = (train['pnl_neto'] > 0).astype(int)

# ------------------------------------------------------------
# 4. Entrenar modelo con TODOS los datos históricos
# ------------------------------------------------------------
model = RandomForestClassifier(n_estimators=100, max_depth=5,
                               random_state=42, class_weight='balanced')
model.fit(X_train, y_train)
print("✅ Modelo entrenado con todos los datos históricos.\n")

# ------------------------------------------------------------
# 5. Cargar y preprocesar el período nuevo (Q2 2025)
# ------------------------------------------------------------
try:
    nuevo = pd.read_csv('backtest_institucional_Q22025.csv', parse_dates=['entry_time'])
except FileNotFoundError:
    raise SystemExit("❌ No se encontró backtest_institucional_Q22025.csv. Ejecutá primero el Paso 1.")

nuevo = preprocesar(nuevo)

# Asegurar que tenga exactamente las mismas columnas que el train
for col in features_cols:
    if col not in nuevo.columns:
        nuevo[col] = 0
X_nuevo = nuevo[features_cols].fillna(nuevo[features_cols].median())

# ------------------------------------------------------------
# 6. Predecir y evaluar
# ------------------------------------------------------------
nuevo['prob_exito'] = model.predict_proba(X_nuevo)[:, 1]
filtro = nuevo['prob_exito'] > 0.55
trades_filtrados = nuevo[filtro]
todos = nuevo

def calcular_pf(df):
    ganancia = df[df['pnl_neto'] > 0]['pnl_neto'].sum()
    perdida = abs(df[df['pnl_neto'] <= 0]['pnl_neto'].sum())
    return ganancia / perdida if perdida != 0 else float('inf')

pf_original = calcular_pf(todos)
pf_filtrado = calcular_pf(trades_filtrados)
wr_original = (todos['pnl_neto'] > 0).mean() * 100
wr_filtrado = (trades_filtrados['pnl_neto'] > 0).mean() * 100 if len(trades_filtrados) > 0 else 0.0

print("="*50)
print("RESULTADOS DE LA VALIDACIÓN FORWARD")
print("="*50)
print(f"Trades totales en Q2 2025:        {len(todos)}")
print(f"Trades después del filtro (0.55): {len(trades_filtrados)}")
print(f"Win Rate original:                {wr_original:.1f}%")
print(f"Win Rate filtrado:                {wr_filtrado:.1f}%")
print(f"Profit Factor original:           {pf_original:.2f}")
print(f"Profit Factor filtrado:           {pf_filtrado:.2f}")
print("="*50)

if pf_filtrado > pf_original:
    print("✅ El filtro MEJORA el rendimiento en datos nunca vistos.")
else:
    print("❌ El filtro NO mejoró el rendimiento en este período.")
