import os, sys, shutil, subprocess

print("🚀 Experimento completo de fase con ML\n")

# 1. Copia temporal del script original
src = "backtest_institucional_v1.py"
tmp = "_backtest_temp.py"
if not os.path.exists(src):
    sys.exit(f"❌ No se encontró {src}")
shutil.copyfile(src, tmp)

# 2. Leer el contenido de la copia
with open(tmp, 'r') as f:
    codigo = f.read()

# 3. Insertar argumento --phase_mode justo después de '--output'
#    Buscamos la línea que contiene "parser.add_argument('--output'"
old_line = "parser.add_argument('--output'"
new_line = "parser.add_argument('--phase_mode', type=str, default='strict', choices=['strict','flexible','free'])\n    parser.add_argument('--output'"
codigo = codigo.replace(old_line, new_line)

# 4. Insertar lógica de fase variable en la función run()
#    Buscar la línea exacta del filtro fijo actual
filtro_actual = "if phase_h1 in ('ranging', 'neutral'): signal = 'WAIT'; setup_state = 'INVALID'"
filtro_nuevo = '''# Filtro de fase variable
        phase_mode = getattr(args, 'phase_mode', 'strict')
        if phase_mode == 'strict':
            if phase_h1 in ('ranging', 'neutral'):
                signal = 'WAIT'; setup_state = 'INVALID'
        elif phase_mode == 'flexible':
            ci_val = enhanced.get('ci', {}).get('value', 50)
            if phase_h1 in ('ranging', 'neutral') and (ci_val is None or ci_val >= 60):
                signal = 'WAIT'; setup_state = 'INVALID'
        # phase_mode == 'free' no restringe nada'''
codigo = codigo.replace(filtro_actual, filtro_nuevo)

# 5. Guardar la copia modificada
with open(tmp, 'w') as f:
    f.write(codigo)

print("✅ Script temporal preparado. Ejecutando backtests...\n")

# 6. Ejecutar los tres modos
for modo in ['strict', 'flexible', 'free']:
    salida = f"backtest_{modo}.csv"
    print(f"🔬 Modo {modo}...")
    cmd = f"python3 {tmp} --start 2023-04-01 --end 2026-06-23 --output {salida} --phase_mode {modo}"
    subprocess.run(cmd, shell=True, check=True)
    print(f"   -> {salida} generado.\n")

# 7. Aplicar filtro ML
print("🧠 Aplicando filtro ML...\n")
import pickle, pandas as pd, numpy as np

with open('ml_model.pkl', 'rb') as f:
    model = pickle.load(f)
features_ok = model.feature_names_in_

for modo in ['strict', 'flexible', 'free']:
    archivo = f"backtest_{modo}.csv"
    try:
        df = pd.read_csv(archivo)
    except FileNotFoundError:
        print(f"{modo}: no se encontró {archivo}")
        continue
    if df.empty:
        print(f"{modo}: sin trades")
        continue

    # Preprocesamiento
    phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
    mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
    df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
    df.drop(['phase_h1', 'mom_direction'], axis=1, inplace=True)

    cols_a_evitar = ['symbol','entry_time','direction','entry_signal',
                     'entry_real','sl_original','tp1','contracts','pnl_neto',
                     'exit_events','exit_type','explanation','score']
    feature_cols = [c for c in df.columns if c not in cols_a_evitar]
    X = df[feature_cols].fillna(0)
    for col in features_ok:
        if col not in X.columns:
            X[col] = 0.0
    X = X[features_ok]

    prob = model.predict_proba(X)[:, 1]
    filtro = prob > 0.55
    df_filtrado = df[filtro]

    if len(df_filtrado) == 0:
        print(f"{modo} con ML: ML vetó todos los trades")
        continue

    wr = (df_filtrado['pnl_neto'] > 0).mean()
    total = df_filtrado['pnl_neto'].sum()
    dd = ((100 + df_filtrado['pnl_neto'].cumsum()).cummax() - (100 + df_filtrado['pnl_neto'].cumsum())).max()

    print(f"{modo} con ML: Trades={len(df_filtrado)} (de {len(df)}), WR={wr:.2%}, PnL=${total:.2f}, DD=${dd:.2f}")

# 8. Limpiar el script temporal
os.remove(tmp)
print("\n✅ Experimento completado. Archivos temporales eliminados.")
