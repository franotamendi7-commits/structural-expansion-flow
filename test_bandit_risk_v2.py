import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from bandit_risk import LinUCBRisk
from risk_manager import RiskManager

# ─── 1. Cargar datos históricos ─────────────────────────────────
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
df_all = pd.concat(dfs, ignore_index=True).sort_values('entry_time').reset_index(drop=True)

feature_cols = [
    'fib_low_key', 'fib_high_key', 'fib_width',
    'ci_value', 'wr_5m', 'wr_15m',
    'st_aligned', 'st_bias_bullish', 'st_bias_bearish',
    'mom_score', 'vol_ratio_5m', 'body_ratio_4h',
    'hour_of_day', 'direction_long',
    'phase_compressing', 'phase_expanding', 'phase_trending', 'phase_ranging', 'phase_neutral',
    'mom_bullish', 'mom_bearish', 'mom_neutral'
]
for col in feature_cols:
    if col not in df_all.columns:
        df_all[col] = 0.0
df_all[feature_cols] = df_all[feature_cols].fillna(0)

# ─── 2. Escalar features (mejora clave) ────────────────────────
scaler = StandardScaler()
df_all[feature_cols] = scaler.fit_transform(df_all[feature_cols])

risk_levels_A = [0.005, 0.01, 0.015]
risk_levels_B = [0.005, 0.01, 0.015, 0.02]

def run_backtest(risk_levels, alpha=0.5):    # <-- alpha reducido a 0.5
    n_arms = len(risk_levels)
    bandit = LinUCBRisk(n_arms=n_arms, alpha=alpha, feature_dim=len(feature_cols))
    det_rm = RiskManager(base_risk=0.01, max_risk=0.015, min_risk=0.005)
    capital_det = 100.0
    capital_ban = 100.0

    for idx, row in df_all.iterrows():
        features = row[feature_cols].values.astype(float).reshape(-1, 1)
        pnl = row['pnl_neto']
        entry = row.get('entry_signal', row.get('entry_real', 0))
        if entry <= 0:
            continue

        atr_est = entry * 0.02
        score_est = row.get('score', 70)
        risk_det = det_rm.get_risk_pct(score_est, atr_est, entry)
        notional_det = capital_det * risk_det / (entry * 0.018) if entry > 0 else 0
        contracts_det = notional_det / entry if entry > 0 else 0
        pnl_det = pnl * (contracts_det / row['contracts']) if row['contracts'] > 0 else 0
        capital_det += pnl_det

        arm = bandit.select_arm(features)
        risk_ban = risk_levels[arm]
        notional_ban = capital_ban * risk_ban / (entry * 0.018) if entry > 0 else 0
        contracts_ban = notional_ban / entry if entry > 0 else 0
        pnl_ban = pnl * (contracts_ban / row['contracts']) if row['contracts'] > 0 else 0
        capital_ban += pnl_ban

        reward = pnl_ban / capital_ban if capital_ban > 0 else 0
        bandit.update(arm, features, reward)

    return capital_det, capital_ban, bandit

print("=== Variante A (hasta 1.5%) ===")
cap_det_A, cap_ban_A, bandit_A = run_backtest(risk_levels_A)
print(f"Capital final Risk Manager actual: ${cap_det_A:.2f}")
print(f"Capital final Bandido:            ${cap_ban_A:.2f}")

print("\n=== Variante B (hasta 2.0%) ===")
cap_det_B, cap_ban_B, bandit_B = run_backtest(risk_levels_B)
print(f"Capital final Risk Manager actual: ${cap_det_B:.2f}")
print(f"Capital final Bandido:            ${cap_ban_B:.2f}")

if cap_ban_A > cap_det_A or cap_ban_B > cap_det_B:
    print("\n✅ El bandido supera al Risk Manager actual con las mejoras aplicadas.")
else:
    print("\n⛔ El bandido aún no supera al Risk Manager actual. Se descarta para producción.")
