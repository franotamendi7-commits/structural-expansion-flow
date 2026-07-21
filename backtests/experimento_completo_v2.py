import os, sys, shutil, subprocess, textwrap

print("🚀 Experimento completo de fase con ML (v2)\n")

src = "backtest_institucional_v1.py"
tmp = "_backtest_temp.py"

if not os.path.exists(src):
    sys.exit(f"❌ No se encontró {src}")

# Leer el script original
with open(src, 'r') as f:
    lineas = f.readlines()

# Buscar el número de línea donde está parser.add_argument('--output'
indice_output = None
for i, linea in enumerate(lineas):
    if "parser.add_argument('--output'" in linea:
        indice_output = i
        break

if indice_output is None:
    sys.exit("❌ No se encontró la línea de --output")

# Obtener la indentación de esa línea (espacios al principio)
indent = lineas[indice_output][:len(lineas[indice_output]) - len(lineas[indice_output].lstrip())]

# Línea nueva a insertar justo antes de la línea de --output
nueva_linea = f"{indent}parser.add_argument('--phase_mode', type=str, default='strict', choices=['strict','flexible','free'])\n"

# Insertar la nueva línea antes de la línea de --output
lineas.insert(indice_output, nueva_linea)

# Reemplazar la lógica del filtro de fase
filtro_original = "if phase_h1 in ('ranging', 'neutral'): signal = 'WAIT'; setup_state = 'INVALID'"
filtro_nuevo = textwrap.dedent("""\
            # Filtro de fase variable
            phase_mode = getattr(args, 'phase_mode', 'strict')
            if phase_mode == 'strict':
                if phase_h1 in ('ranging', 'neutral'):
                    signal = 'WAIT'; setup_state = 'INVALID'
            elif phase_mode == 'flexible':
                ci_val = enhanced.get('ci', {}).get('value', 50)
                if phase_h1 in ('ranging', 'neutral') and (ci_val is None or ci_val >= 60):
                    signal = 'WAIT'; setup_state = 'INVALID'
            # phase_mode == 'free' no restringe nada""")

for i, linea in enumerate(lineas):
    if filtro_original in linea:
        # Conservar la indentación original
        indent_filtro = linea[:len(linea) - len(linea.lstrip())]
        # Reemplazar manteniendo la indentación
        nuevo_filtro = "\n".join(
            (indent_filtro + l if l.strip() else l) for l in filtro_nuevo.splitlines()
        )
        lineas[i] = nuevo_filtro + "\n"
        break

# Guardar el archivo temporal
with open(tmp, 'w') as f:
    f.writelines(lineas)

print("✅ Script temporal preparado. Ejecutando backtests...\n")

# Ejecutar los tres modos
for modo in ['strict', 'flexible', 'free']:
    salida = f"backtest_{modo}.csv"
    print(f"🔬 Modo {modo}...")
    cmd = f"python3 {tmp} --start 2023-04-01 --end 2026-06-23 --output {salida} --phase_mode {modo}"
    resultado = subprocess.run(cmd, shell=True)
    if resultado.returncode != 0:
        print(f"   ⚠ Falló el modo {modo} (código {resultado.returncode})")
    else:
        print(f"   -> {salida} generado.\n")

# Aplicar filtro ML
print("🧠 Aplicando filtro ML...\n")
import pickle, pandas as pd, numpy as np

try:
    with open('ml_model.pkl', 'rb') as f:
        model = pickle.load(f)
    features_ok = model.feature_names_in_
except FileNotFoundError:
    sys.exit("❌ No se encontró ml_model.pkl")

for modo in ['strict', 'flexible', 'free']:
    archivo = f"backtest_{modo}.csv"
    if not os.path.exists(archivo):
        print(f"{modo}: no se encontró {archivo}")
        continue
    df = pd.read_csv(archivo)
    if df.empty:
        print(f"{modo}: sin trades")
        continue

    # Preprocesar
    if 'phase_h1' not in df.columns:
        print(f"{modo}: falta columna phase_h1")
        continue
    phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
    mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
    df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
    df.drop(['phase_h1','mom_direction'], axis=1, inplace=True)

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

# Limpiar
os.remove(tmp)
print("\n✅ Experimento completado. Archivos temporales eliminados.")
