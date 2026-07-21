"""
ML CONDICIONAL POR RÉGIMEN — solo filtra en mercados laterales/choppy
En tendencias, deja pasar todo sin filtro.
"""
import json, warnings
import numpy as np
import pandas as pd
from pathlib import Path
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
try:
    import xgboost as xgb
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

CAP = 100.0
SYM = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
RESULTS_PATH = "experimento_ml_condicional_results.json"

CSVS = [
    ("backtest_institucional_Q12023.csv", "2023-Q1"),
    ("backtest_institucional_Q32024.csv", "2024-Q3"),
    ("backtest_institucional_Q42025.csv", "2025-Q4"),
    ("backtest_institucional_Q12026_ref.csv", "2026-Q1"),
    ("backtest_Q12024.csv", "2024-Q1"),
    ("backtest_Q22024.csv", "2024-Q2"),
    ("backtest_Q12025.csv", "2025-Q1"),
    ("backtest_Q32025.csv", "2025-Q3"),
    ("backtest_Q12026.csv", "2026-Q1v2"),
]

FEATURE_COLS = [
    "fib_low_key", "fib_high_key", "fib_width", "ci_value",
    "wr_5m", "wr_15m", "st_aligned", "st_bias_bullish", "st_bias_bearish",
    "mom_score", "vol_ratio_5m", "body_ratio_4h", "hour_of_day", "direction_long",
]
CAT_COLS = ["phase_h1", "mom_direction"]

# Regimes where ML filter is applied (choppy/lateral)
ML_REGIMES = {"ranging", "neutral", "compressing"}
# Regimes where engine runs free (trending)
FREE_REGIMES = {"trending", "expanding"}

def load_data():
    dfs = []
    for fpath, label in CSVS:
        if not Path(fpath).exists(): continue
        df = pd.read_csv(fpath, parse_dates=["entry_time"])
        df["_period"] = label
        if "entry_time" in df.columns and df["entry_time"].notna().any():
            df["_ts"] = df["entry_time"].astype("int64") // 10**6
        else:
            df["_ts"] = 0
        dfs.append(df)
    full = pd.concat(dfs, ignore_index=True)
    full = full.dropna(subset=["pnl_neto"])
    # Fill missing phase_h1
    full["phase_h1"] = full["phase_h1"].fillna("neutral")
    return full

def prepare_features(df):
    X = pd.DataFrame()
    for col in FEATURE_COLS:
        X[col] = pd.to_numeric(df[col], errors="coerce").fillna(0) if col in df.columns else 0.0
    for col in CAT_COLS:
        if col in df.columns:
            dummies = pd.get_dummies(df[col], prefix=col[:4])
            for c in dummies.columns:
                X[c] = dummies[c].astype(float)
    return X.fillna(0)

def run():
    print("=" * 70)
    print("  EXPERIMENTO: ML CONDICIONAL POR RÉGIMEN")
    print("  Filtro XGBoost/RF solo en mercados laterales/choppy")
    print("  En tendencias: engine libre (sin ML)")
    print("=" * 70)

    df = load_data()
    df = df.sort_values("_ts").reset_index(drop=True)
    print(f"\n  Datos: {len(df)} trades")
    regime_dist = df["phase_h1"].value_counts().to_dict()
    print(f"  Regímenes: {regime_dist}")
    ml_count = df["phase_h1"].isin(ML_REGIMES).sum()
    free_count = df["phase_h1"].isin(FREE_REGIMES).sum()
    print(f"  → ML filter activo en {ml_count}t ({list(ML_REGIMES)})")
    print(f"  → Engine libre en {free_count}t ({list(FREE_REGIMES)})")

    periods = df["_period"].unique()
    results_per_fold = []

    for i in range(1, len(periods)):
        train_periods = periods[:i]
        test_period = periods[i]
        train_df = df[df["_period"].isin(train_periods)]
        test_df = df[df["_period"] == test_period]
        if len(train_df) < 20 or len(test_df) < 5: continue

        train_X = prepare_features(train_df)
        train_y = (train_df["pnl_neto"] > 0).astype(int).values
        test_X = prepare_features(test_df)
        test_y = (test_df["pnl_neto"] > 0).astype(int).values

        # Align columns
        all_cols = list(set(train_X.columns) | set(test_X.columns))
        for c in all_cols:
            if c not in train_X.columns: train_X[c] = 0.0
            if c not in test_X.columns: test_X[c] = 0.0
        train_X = train_X[all_cols].fillna(0)
        test_X = test_X[all_cols].fillna(0)

        fold = {"train": "+".join(train_periods), "test": test_period, "n_train": len(train_df), "n_test": len(test_df)}

        # Baseline
        base_pnl = test_df["pnl_neto"].sum()
        base_wr = test_y.mean() * 100
        base_ret = (base_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
        fold["no_ml"] = {"trades": len(test_df), "wr": round(base_wr, 1), "pnl": round(base_pnl, 2), "ret": round(base_ret * 100, 2)}

        # Train models
        rf = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight="balanced", random_state=42)
        rf.fit(train_X, train_y)
        rf_probs = rf.predict_proba(test_X)[:, 1]

        xgb_model = None
        xgb_probs = None
        if HAS_XGB:
            pos_ratio = train_y.mean()
            xgb_model = xgb.XGBClassifier(
                n_estimators=100, max_depth=5, learning_rate=0.1,
                scale_pos_weight=(1 - pos_ratio) / pos_ratio if 0 < pos_ratio < 1 else 1,
                random_state=42, eval_metric="logloss"
            )
            xgb_model.fit(train_X, train_y)
            xgb_probs = xgb_model.predict_proba(test_X)[:, 1]

        # ─── CONDITIONAL ML ───
        for model_name, probs in [("RF", rf_probs), ("XGB", xgb_probs)]:
            if probs is None: continue
            for th in [0.3, 0.4, 0.5, 0.55, 0.6]:
                cond_pnl = 0.0
                cond_trades = 0
                cond_wins = 0
                for j in range(len(test_df)):
                    regime = str(test_df.iloc[j]["phase_h1"])
                    if regime in FREE_REGIMES:
                        # Free pass: no filter
                        cond_pnl += test_df.iloc[j]["pnl_neto"]
                        cond_trades += 1
                        if test_df.iloc[j]["pnl_neto"] > 0: cond_wins += 1
                    elif regime in ML_REGIMES:
                        # Apply ML filter
                        if probs[j] >= th:
                            cond_pnl += test_df.iloc[j]["pnl_neto"]
                            cond_trades += 1
                            if test_df.iloc[j]["pnl_neto"] > 0: cond_wins += 1
                    else:
                        # Unknown regime: free pass
                        cond_pnl += test_df.iloc[j]["pnl_neto"]
                        cond_trades += 1
                        if test_df.iloc[j]["pnl_neto"] > 0: cond_wins += 1

                cond_wr = cond_wins / cond_trades * 100 if cond_trades > 0 else 0
                cond_ret = (cond_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
                fold[f"{model_name}_cond_th{th}"] = {
                    "trades": cond_trades, "wr": round(cond_wr, 1),
                    "pnl": round(cond_pnl, 2), "ret": round(cond_ret * 100, 2),
                    "ml_filtered": int(len(test_df) - cond_trades),
                }

        # Also test: ALWAYS apply ML (for comparison)
        for model_name, probs in [("RF", rf_probs), ("XGB", xgb_probs)]:
            if probs is None: continue
            for th in [0.3, 0.4, 0.5, 0.55, 0.6]:
                mask = probs >= th
                if mask.sum() == 0: continue
                sel = test_df[mask]
                pnl = sel["pnl_neto"].sum()
                wr = (sel["pnl_neto"] > 0).mean() * 100
                ret = (pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
                fold[f"{model_name}_always_th{th}"] = {
                    "trades": int(mask.sum()), "wr": round(wr, 1),
                    "pnl": round(pnl, 2), "ret": round(ret * 100, 2),
                }

        results_per_fold.append(fold)

        # Print fold
        print(f"\n  {'─' * 60}")
        print(f"  Fold: {'+'.join(train_periods)} ({len(train_df)}t) → {test_period} ({len(test_df)}t)")
        b = fold["no_ml"]
        print(f"    No ML:          {b['trades']:3d}t  WR {b['wr']:5.1f}%  Ret {b['ret']:+6.2f}%")

        for mn in ["RF", "XGB"]:
            cond_key = f"{mn}_cond_th0.5"
            always_key = f"{mn}_always_th0.5"
            if cond_key in fold:
                c = fold[cond_key]
                a = fold.get(always_key, {})
                print(f"    {mn} cond th=0.5: {c['trades']:3d}t  WR {c['wr']:5.1f}%  Ret {c['ret']:+6.2f}%  (filtered {c['ml_filtered']})")
                if a:
                    print(f"    {mn} always th=0.5:{a['trades']:3d}t  WR {a['wr']:5.1f}%  Ret {a['ret']:+6.2f}%")

    # ─── SUMMARY ───
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY — ML Condicional por Régimen ({len(results_per_fold)} folds)")
    print(f"{'=' * 70}")

    strategies = ["no_ml"]
    for mn in ["RF", "XGB"]:
        for th in [0.5, 0.55, 0.6]:
            strategies.append(f"{mn}_cond_th{th}")
            strategies.append(f"{mn}_always_th{th}")

    for strat in strategies:
        vals = []
        for f in results_per_fold:
            if strat in f:
                vals.append(f[strat]["ret"])
        if vals:
            avg = np.mean(vals)
            wins = sum(1 for v in vals if v > 0)
            print(f"  {strat:30s}: avg ret {avg:+6.2f}%  (mejor que no_ml en {wins}/{len(vals)} folds)")

    # Save
    Path(RESULTS_PATH).write_text(json.dumps(results_per_fold, indent=2))
    print(f"\n  ✅ Resultados: {RESULTS_PATH}")

if __name__ == "__main__":
    run()
