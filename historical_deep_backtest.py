"""
HISTORICAL DEEP BACKTEST — 2023-2026
Entrena XGBoost/RF sobre 3 años de datos institucionales y evalúa walk-forward por trimestres.
"""
import json, pickle, warnings
import numpy as np
import pandas as pd
from datetime import datetime, timezone
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
RESULTS_PATH = "historical_deep_results.json"

INSTITUCIONAL_CSVS = [
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

def load_and_prepare():
    all_dfs = []
    for fpath, label in INSTITUCIONAL_CSVS:
        if not Path(fpath).exists():
            print(f"  [!] {fpath} no encontrado, omitiendo")
            continue
        df = pd.read_csv(fpath, parse_dates=["entry_time"])
        df["_period_label"] = label
        # Derive period from entry_time
        if "entry_time" in df.columns and df["entry_time"].notna().any():
            df["_period_ts"] = df["entry_time"].astype("int64") // 10**6
        else:
            df["_period_ts"] = 0
        all_dfs.append(df)
        print(f"  [+] {fpath}: {len(df)} trades ({label})")

    if not all_dfs:
        raise ValueError("No CSV files found!")
    full = pd.concat(all_dfs, ignore_index=True)
    full = full.dropna(subset=["pnl_neto"])
    print(f"\n  Total: {len(full)} trades cargados")
    print(f"  Periodos: {full['_period_label'].value_counts().to_dict()}")
    return full

def prepare_features(df):
    X = pd.DataFrame()
    for col in FEATURE_COLS:
        if col in df.columns:
            X[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        else:
            X[col] = 0.0

    # One-hot encode categoricals
    for col in CAT_COLS:
        if col in df.columns:
            dummies = pd.get_dummies(df[col], prefix=col[:4])
            for c in dummies.columns:
                X[c] = dummies[c].astype(float)
        else:
            # Create zero columns if missing
            pass

    X = X.fillna(0)
    return X

def train_and_evaluate(train_df, test_df, label=""):
    if len(train_df) < 10 or len(test_df) < 2:
        return None

    train_X = prepare_features(train_df)
    train_y = (train_df["pnl_neto"] > 0).astype(int).values
    test_X = prepare_features(test_df)
    test_y = (test_df["pnl_neto"] > 0).astype(int).values

    # Ensure same columns
    all_cols = list(set(train_X.columns) | set(test_X.columns))
    for c in all_cols:
        if c not in train_X.columns: train_X[c] = 0.0
        if c not in test_X.columns: test_X[c] = 0.0
    train_X = train_X[all_cols].fillna(0)
    test_X = test_X[all_cols].fillna(0)

    results = {}

    # Baseline
    test_baseline_pnl = test_df["pnl_neto"].sum()
    test_baseline_wr = (test_y.mean()) * 100
    test_baseline_ret = (test_baseline_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
    results["no_ml"] = {
        "trades": len(test_df),
        "wr": round(test_baseline_wr, 1),
        "pnl": round(test_baseline_pnl, 2),
        "ret": round(test_baseline_ret * 100, 2)
    }

    # RF
    rf = RandomForestClassifier(n_estimators=100, max_depth=5, class_weight="balanced", random_state=42)
    rf.fit(train_X, train_y)
    rf_test_probs = rf.predict_proba(test_X)[:, 1]

    results["rf"] = {"train_acc": round(accuracy_score(train_y, rf.predict(train_X)) * 100, 1)}
    for th in [0.3, 0.4, 0.5, 0.55, 0.6, 0.7]:
        mask = rf_test_probs >= th
        if mask.sum() == 0: continue
        sel = test_df[mask]
        pnl = sel["pnl_neto"].sum()
        wr = (sel["pnl_neto"] > 0).mean() * 100
        ret = (pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
        results["rf"][f"gate_th{th}"] = {
            "trades": int(mask.sum()), "wr": round(wr, 1),
            "pnl": round(pnl, 2), "ret": round(ret * 100, 2)
        }

    # RF sizing
    sizing_pnl = sum(test_df["pnl_neto"].values[i] * max(0.05, rf_test_probs[i]) / 1.0 for i in range(len(test_df)))
    results["rf"]["sizing"] = {
        "trades": len(test_df),
        "pnl": round(sizing_pnl, 2),
        "ret": round(((sizing_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1) * 100, 2)
    }

    # XGBoost
    if HAS_XGB:
        pos_ratio = (train_y.sum()) / len(train_y) if train_y.sum() > 0 else 0.5
        xgb_model = xgb.XGBClassifier(
            n_estimators=100, max_depth=5, learning_rate=0.1,
            scale_pos_weight=(1 - pos_ratio) / pos_ratio if pos_ratio > 0 and pos_ratio < 1 else 1,
            random_state=42, eval_metric="logloss"
        )
        xgb_model.fit(train_X, train_y)
        xgb_test_probs = xgb_model.predict_proba(test_X)[:, 1]

        results["xgb"] = {"train_acc": round(accuracy_score(train_y, xgb_model.predict(train_X)) * 100, 1)}
        for th in [0.3, 0.4, 0.5, 0.55, 0.6, 0.7]:
            mask = xgb_test_probs >= th
            if mask.sum() == 0: continue
            sel = test_df[mask]
            pnl = sel["pnl_neto"].sum()
            wr = (sel["pnl_neto"] > 0).mean() * 100
            ret = (pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1
            results["xgb"][f"gate_th{th}"] = {
                "trades": int(mask.sum()), "wr": round(wr, 1),
                "pnl": round(pnl, 2), "ret": round(ret * 100, 2)
            }

        # XGB sizing
        xgb_sizing_pnl = sum(test_df["pnl_neto"].values[i] * max(0.05, xgb_test_probs[i]) / 1.0 for i in range(len(test_df)))
        results["xgb"]["sizing"] = {
            "trades": len(test_df),
            "pnl": round(xgb_sizing_pnl, 2),
            "ret": round(((xgb_sizing_pnl + CAP * len(SYM)) / (CAP * len(SYM)) - 1) * 100, 2)
        }

        # Save feature importance
        fi = xgb_model.feature_importances_
        top5 = np.argsort(fi)[-5:][::-1]
        results["xgb"]["top_features"] = {test_X.columns[j]: round(float(fi[j] * 100), 1) for j in top5}

    return results

def run():
    print("=" * 70)
    print("  HISTORICAL DEEP BACKTEST — 2023-2026")
    print("  Walk-forward por trimestres · XGBoost + RF")
    print("=" * 70)

    df = load_and_prepare()
    df = df.sort_values("_period_ts").reset_index(drop=True)

    # Walk-forward: train on first N periods, test on next period
    periods = df["_period_label"].unique()
    print(f"\n  Periodos disponibles: {list(periods)}")

    all_folds = []
    for i in range(1, len(periods)):
        train_periods = periods[:i]
        test_period = periods[i]
        train_df = df[df["_period_label"].isin(train_periods)].copy()
        test_df = df[df["_period_label"] == test_period].copy()

        if len(train_df) < 20 or len(test_df) < 5:
            print(f"  [SKIP] Fold {test_period}: train={len(train_df)}, test={len(test_df)}")
            continue

        print(f"\n  {'─' * 70}")
        print(f"  Fold: train {'+'.join(train_periods)} ({len(train_df)}t) → test {test_period} ({len(test_df)}t)")
        result = train_and_evaluate(train_df, test_df, label=test_period)
        if result:
            all_folds.append({"train": "+".join(train_periods), "test": test_period, "results": result})
            # Print fold summary
            r = result
            no_ml = r["no_ml"]
            rf_best = max([v for k, v in r.get("rf", {}).items() if k.startswith("gate")], key=lambda x: x["ret"]) if any(k.startswith("gate") for k in r.get("rf", {})) else None
            xgb_best = max([v for k, v in r.get("xgb", {}).items() if k.startswith("gate")], key=lambda x: x["ret"]) if HAS_XGB and any(k.startswith("gate") for k in r.get("xgb", {})) else None

            print(f"    No ML:     {no_ml['trades']:3d}t  WR {no_ml['wr']:5.1f}%  Ret {no_ml['ret']:+6.2f}%")
            if rf_best: print(f"    RF gate:   {rf_best['trades']:3d}t  WR {rf_best['wr']:5.1f}%  Ret {rf_best['ret']:+6.2f}%")
            if xgb_best: print(f"    XGB gate:  {xgb_best['trades']:3d}t  WR {xgb_best['wr']:5.1f}%  Ret {xgb_best['ret']:+6.2f}%")
            sizing_rf = r.get("rf", {}).get("sizing", {})
            if sizing_rf: print(f"    RF sizing: {sizing_rf['trades']:3d}t  Ret {sizing_rf.get('ret',0):+6.2f}%")
            sizing_xgb = r.get("xgb", {}).get("sizing", {})
            if sizing_xgb: print(f"    XGB sizing:{sizing_xgb['trades']:3d}t  Ret {sizing_xgb.get('ret',0):+6.2f}%")

    # ─── FINAL: train on ALL data, save best XGBoost model ───
    print(f"\n{'=' * 70}")
    print(f"  ENTRENAMIENTO FINAL (todo el histórico)")
    print(f"{'=' * 70}")

    all_X = prepare_features(df)
    all_y = (df["pnl_neto"] > 0).astype(int).values

    if HAS_XGB and len(all_X) >= 50:
        pos_ratio = all_y.mean()
        xgb_final = xgb.XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.1,
            scale_pos_weight=(1 - pos_ratio) / pos_ratio if 0 < pos_ratio < 1 else 1,
            random_state=42, eval_metric="logloss"
        )
        xgb_final.fit(all_X, all_y)
        xgb_final.save_model("xgb_historical.json")
        with open("xgb_historical_features.json", "w") as f:
            json.dump(list(all_X.columns), f)

        final_acc = accuracy_score(all_y, xgb_final.predict(all_X))
        fi = xgb_final.feature_importances_
        top5 = np.argsort(fi)[-5:][::-1]
        print(f"  ✓ XGBoost entrenado: {len(all_X)} trades, acc {final_acc * 100:.1f}%")
        print(f"  ✓ Guardado: xgb_historical.json + xgb_historical_features.json")
        print(f"  Top features:")
        for j in top5:
            print(f"    {all_X.columns[j]}: {fi[j] * 100:.1f}%")

    # Save comprehensive results
    final = {
        "total_trades": len(df),
        "periods_used": list(periods),
        "walkforward_folds": all_folds,
        "summary": {}
    }

    # Aggregate summary across folds
    no_ml_trades = sum(f["results"]["no_ml"]["trades"] for f in all_folds if f["results"])
    no_ml_pnl = sum(f["results"]["no_ml"]["pnl"] for f in all_folds if f["results"])

    # Best gate per fold
    rf_improvements = []
    xgb_improvements = []
    for f in all_folds:
        if not f["results"]: continue
        r = f["results"]
        no_ret = r["no_ml"]["ret"]
        rf_gates = {k: v for k, v in r.get("rf", {}).items() if k.startswith("gate")}
        xgb_gates = {k: v for k, v in r.get("xgb", {}).items() if k.startswith("gate")}
        if rf_gates:
            best_rf = max(rf_gates.values(), key=lambda x: x["ret"])
            rf_improvements.append(best_rf["ret"] - no_ret)
        if xgb_gates:
            best_xgb = max(xgb_gates.values(), key=lambda x: x["ret"])
            xgb_improvements.append(best_xgb["ret"] - no_ret)

    final["summary"] = {
        "total_trades_all_periods": len(df),
        "walkforward_folds": len(all_folds),
        "avg_rf_gate_improvement_pp": round(np.mean(rf_improvements), 2) if rf_improvements else 0,
        "avg_xgb_gate_improvement_pp": round(np.mean(xgb_improvements), 2) if xgb_improvements else 0,
        "max_rf_gate_improvement_pp": round(max(rf_improvements), 2) if rf_improvements else 0,
        "max_xgb_gate_improvement_pp": round(max(xgb_improvements), 2) if xgb_improvements else 0,
        "rf_better_count": sum(1 for i in rf_improvements if i > 0),
        "xgb_better_count": sum(1 for i in xgb_improvements if i > 0),
    }

    Path(RESULTS_PATH).write_text(json.dumps(final, indent=2))
    print(f"\n  ✅ Resultados guardados en {RESULTS_PATH}")
    print(f"\n  {'=' * 70}")
    print(f"  SUMMARY ({len(all_folds)} folds walk-forward)")
    print(f"  {'=' * 70}")
    print(f"  {'':30s} {'Mejora avg':>10s} {'Mejora max':>10s} {'Veces >0':>9s}")
    s = final["summary"]
    print(f"  {'RF gate vs No ML':30s} {s['avg_rf_gate_improvement_pp']:+9.2f}pp {s['max_rf_gate_improvement_pp']:+9.2f}pp {s['rf_better_count']:>4d}/{len(all_folds)}")
    print(f"  {'XGB gate vs No ML':30s} {s['avg_xgb_gate_improvement_pp']:+9.2f}pp {s['max_xgb_gate_improvement_pp']:+9.2f}pp {s['xgb_better_count']:>4d}/{len(all_folds)}")
    print(f"  {'─' * 70}")
    print(f"  Total datos: {len(df)} trades en {len(periods)} periodos (2023-2026)")
    print(f"  Modelo final: xgb_historical.json (XGBoost, {len(all_X.columns)} features)")

if __name__ == "__main__":
    run()
