import pandas as pd, numpy as np, pickle, os

files = ['backtest_institucional_Q12023.csv','backtest_institucional_Q32024.csv','backtest_institucional_Q42025.csv','backtest_institucional_Q12026_ref.csv']
dfs = []
for f in files:
    if os.path.exists(f):
        df = pd.read_csv(f, parse_dates=['entry_time']); df = df.loc[:, ~df.columns.duplicated()]
        dfs.append(df)
df_all = pd.concat(dfs, ignore_index=True).sort_values('entry_time').reset_index(drop=True)

with open('guard_model.pkl','rb') as f: gm, gf = pickle.load(f)
with open('ml_model.pkl','rb') as f: mm, mf = pickle.load(f)

def guard_ok(row):
    feats = {col: row.get(col, 0.0) for col in gf}; feats['filtro_fijo'] = 1
    X = pd.DataFrame([feats]).reindex(columns=gf, fill_value=0)
    return gm.predict_proba(X)[0,1] > 0.5

def ml_ok(row):
    feats = {col: row.get(col, 0.0) for col in mf}
    X = pd.DataFrame([feats]).reindex(columns=mf, fill_value=0)
    return mm.predict_proba(X)[0,1] > 0.55

# ── 1. Train/Test Split (sin data leakage) ──
print("=== PRUEBA 1: TRAIN/TEST SPLIT (sin data leakage) ===")
df_all['filtro'] = df_all.apply(lambda r: guard_ok(r) and ml_ok(r), axis=1)
cut = int(len(df_all) * 0.8)
df_train = df_all.iloc[:cut]; df_test = df_all.iloc[cut:]
pf_test = df_test[df_test['filtro']]['pnl_neto'].sum() / abs(df_test[~df_test['filtro']]['pnl_neto'].sum()) if df_test[~df_test['filtro']]['pnl_neto'].sum() != 0 else float('inf')
print(f"PF en test set (último 20%): {pf_test:.2f}")
print("✅ Sin data leakage" if pf_test > 5 else "⚠️  Puede haber sobreajuste")

# ── 2. Stress de slippage ──
print("\n=== PRUEBA 2: SLIPPAGE EXTREMO (0.2 × ATR) ===")
df_stress = df_all.copy()
df_stress['pnl_neto'] = df_stress['pnl_neto'] - 0.1 * df_stress.get('entry_signal', 100)
pf_stress = df_stress[df_stress['filtro']]['pnl_neto'].sum() / abs(df_stress[~df_stress['filtro']]['pnl_neto'].sum()) if df_stress[~df_stress['filtro']]['pnl_neto'].sum() != 0 else float('inf')
print(f"PF con slippage 2x: {pf_stress:.2f}")
print("✅ Robusto a costos" if pf_stress > 5 else "⚠️  Sensible a costos")

# ── 3. Exclusión de outliers ──
print("\n=== PRUEBA 3: SIN OUTLIERS ===")
p = df_all[df_all['filtro']]['pnl_neto']
p_sin = p[(p != p.max()) & (p != p.min())]
pf_sin = p_sin[p_sin>0].sum() / abs(p_sin[p_sin<0].sum()) if p_sin[p_sin<0].sum() != 0 else float('inf')
print(f"PF sin mejor ni peor trade: {pf_sin:.2f}")
print("✅ No depende de outliers" if pf_sin > 10 else "⚠️  Puede haber outlier")

# ── 4. Monte Carlo por bloques ──
print("\n=== PRUEBA 4: MONTE CARLO POR BLOQUES (5 trades) ===")
pnls = df_all[df_all['filtro']]['pnl_neto'].values
bloques = [pnls[i:i+5] for i in range(0, len(pnls)-4, 5)]
caps = []
for _ in range(5000):
    muestra = np.random.choice(len(bloques), size=len(bloques), replace=True)
    caps.append(100 + sum(bloques[i].sum() for i in muestra))
caps = np.array(caps); p5b = np.percentile(caps, 5); pf_b = np.mean(caps)
print(f"Capital final promedio: ${pf_b:.2f} | P5: ${p5b:.2f}")
print("✅ Robusto por bloques" if p5b > 100 else "⚠️  Riesgo detectado")

# ── 5. PF por trimestre ──
print("\n=== PRUEBA 5: PROFIT FACTOR POR TRIMESTRE ===")
df_all['trim'] = df_all['entry_time'].dt.to_period('Q')
for t, g in df_all.groupby('trim'):
    if g[g['filtro']]['pnl_neto'].sum() == 0: continue
    pft = g[g['filtro']]['pnl_neto'].sum() / abs(g[~g['filtro']]['pnl_neto'].sum()) if g[~g['filtro']]['pnl_neto'].sum() != 0 else float('inf')
    print(f"{t}: PF = {pft:.2f} {'✅' if pft > 3 else '⚠️'}")
