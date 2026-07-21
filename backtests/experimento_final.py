import os, sys, shutil, subprocess, textwrap, pickle, pandas as pd, numpy as np

print("🚀 Experimento final de fase con ML\n")

src = "backtest_institucional_v1.py"
tmp = "_backtest_temp.py"

if not os.path.exists(src):
    sys.exit(f"❌ No se encontró {src}")

# ── 1. Crear copia temporal ──
shutil.copyfile(src, tmp)

# ── 2. Leer líneas ──
with open(tmp, 'r') as f:
    lineas = f.readlines()

# ── 3. Insertar argumento --phase_mode antes de --output ──
idx_output = None
for i, linea in enumerate(lineas):
    if "parser.add_argument('--output'" in linea:
        idx_output = i
        break

if idx_output is None:
    os.remove(tmp)
    sys.exit("❌ No se encontró la línea de --output")

# Obtener indentación exacta
indent = lineas[idx_output][:len(lineas[idx_output]) - len(lineas[idx_output].lstrip())]
nueva_linea = f"{indent}parser.add_argument('--phase_mode', type=str, default='strict', choices=['strict','flexible','free'])\n"
lineas.insert(idx_output, nueva_linea)

# ── 4. Reemplazar lógica de fase ──
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

# Buscar la línea del filtro y obtener su indentación
idx_filtro = None
for i, linea in enumerate(lineas):
    if filtro_original in linea:
        idx_filtro = i
        break

if idx_filtro is not None:
    indent_f = lineas[idx_filtro][:len(lineas[idx_filtro]) - len(lineas[idx_filtro].lstrip())]
    # Reconstruir el bloque con la misma indentación
    bloque = []
    for l in filtro_nuevo.splitlines():
        if l.strip():
            bloque.append(indent_f + l)
        else:
            bloque.append(l)
    lineas[idx_filtro] = "\n".join(bloque) + "\n"
    # Si había más líneas después (como 'else:'), las eliminamos porque ya no las necesitamos
    # Pero el código original podría tener una continuación. Para simplificar, borramos las líneas adyacentes que pertenecían al mismo bloque.
    # El original es solo una línea, así que reemplazamos esa línea por el bloque nuevo.
    # Pueden quedar restos si había 'else:', pero en tu código original no hay else después de esa línea, solo un 'if signal...'.
    # Así que es seguro.

# ── 5. Guardar archivo temporal ──
with open(tmp, 'w') as f:
    f.writelines(lineas)

print("✅ Script temporal listo.\n")

# ── 6. Ejecutar los tres backtests ──
for modo in ['strict', 'flexible', 'free']:
    salida = f"backtest_{modo}.csv"
    print(f"🔬 Generando {modo}...")
    cmd = f"python3 {tmp} --start 2023-04-01 --end 2026-06-23 --output {salida} --phase_mode {modo}"
    ret = subprocess.run(cmd, shell=True)
    if ret.returncode != 0:
        print(f"   ⚠ Falló {modo} (código {ret.returncode})")
    else:
        print(f"   ✔ {salida} listo.\n")

# ── 7. Cargar modelo ML ──
print("🧠 Aplicando filtro ML...\n")
with open('ml_model.pkl', 'rb') as f:
    model = pickle.load(f)
features_ok = model.feature_names_in_

for modo in ['strict', 'flexible', 'free']:
    archivo = f"backtest_{modo}.csv"
    if not os.path.exists(archivo):
        print(f"{modo}: archivo no encontrado")
        continue
    df = pd.read_csv(archivo)
    if df.empty:
        print(f"{modo}: sin trades")
        continue
    if 'phase_h1' not in df.columns:
        print(f"{modo}: no tiene features, se omite ML")
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
    prob = model.predict_proba(X)[:,1]
    filtro = prob > 0.55
    df_filtrado = df[filtro]
    if len(df_filtrado) == 0:
        print(f"{modo}: ML vetó todos")
        continue
    wr = (df_filtrado['pnl_neto'] > 0).mean()
    pnl = df_filtrado['pnl_neto'].sum()
    dd = ((100 + df_filtrado['pnl_neto'].cumsum()).cummax() - (100 + df_filtrado['pnl_neto'].cumsum())).max()
    print(f"{modo}: Trades={len(df_filtrado)}/{len(df)} | WR={wr:.2%} | PnL=${pnl:.2f} | DD=${dd:.2f}")

# ── 8. Limpiar ──
os.remove(tmp)
print("\n✅ Experimento finalizado.")
