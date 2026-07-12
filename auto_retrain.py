#!/usr/bin/env python3
"""
Auto-retrain ML model from live testnet trades (trade_history.json).
Runs on demand or via cron. Only retrains if we have >= 15 new trades
since the last retrain.
"""
import json, os, pickle, sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier

HISTORY_FILE = Path(__file__).parent / "trade_history.json"
MODEL_FILE = Path(__file__).parent / "ml_model.pkl"
GUARD_FILE = Path(__file__).parent / "guard_model.pkl"
RETRAIN_LOG = Path(__file__).parent / "retrain_log.json"
MIN_NEW_TRADES = 15

# Base training CSVs
TRAIN_FILES = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv",
]

FEATURES = [
    'fib_low_key', 'fib_high_key', 'fib_width', 'ci_value',
    'wr_5m', 'wr_15m', 'st_aligned', 'st_bias_bullish', 'st_bias_bearish',
    'mom_score', 'vol_ratio_5m', 'body_ratio_4h', 'hour_of_day',
    'direction_long', 'phase_compressing', 'phase_expanding', 'phase_trending',
    'mom_bullish', 'mom_bearish', 'mom_neutral',
]

def load_historical_csvs():
    dfs = []
    for f in TRAIN_FILES:
        path = Path(__file__).parent / f
        if path.exists():
            df = pd.read_csv(path, parse_dates=["entry_time"])
            df = df.loc[:, ~df.columns.duplicated()]
            dfs.append(df)
    if not dfs:
        raise FileNotFoundError("No training CSVs found")
    df = pd.concat(dfs, ignore_index=True)
    df = df.loc[:, ~df.columns.duplicated()]
    return df

def load_live_trades():
    if not HISTORY_FILE.exists():
        return None
    with open(HISTORY_FILE) as f:
        trades = json.load(f)
    if len(trades) < MIN_NEW_TRADES:
        return None
    rows = []
    for t in trades:
        net = t.get("net", 0)
        direction = t.get("dir", 1)
        row = {
            "direction_long": 1 if direction == 1 else 0,
            "hour_of_day": datetime.fromisoformat(t.get("exit_time", datetime.now().isoformat())).hour,
            "pnl_neto": net,
        }
        for feat in FEATURES:
            if feat not in row:
                row[feat] = 0.0
        rows.append(row)
    return pd.DataFrame(rows)

def train_model(df_hist, df_live):
    combined = pd.concat([df_hist, df_live], ignore_index=True)

    cols_avoid = ["symbol", "entry_time", "direction", "entry_signal",
                  "entry_real", "sl_original", "tp1", "contracts",
                  "pnl_neto", "exit_events", "exit_type", "explanation", "score"]
    feature_cols = [c for c in combined.columns if c not in cols_avoid and c in FEATURES]

    X = combined[feature_cols].fillna(0)
    y = (combined["pnl_neto"] > 0).astype(int) if "pnl_neto" in combined.columns else None

    if y is None or len(y) < 20:
        return None

    split_idx = int(len(combined) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    model = RandomForestClassifier(n_estimators=100, max_depth=5,
                                   random_state=42, class_weight="balanced")
    model.fit(X_train, y_train)

    prob = model.predict_proba(X_test)[:, 1]
    filtro = prob > 0.55
    df_test = combined.iloc[split_idx:].reset_index(drop=True)
    trades_filt = df_test[filtro]
    pf_new = (
        trades_filt[trades_filt["pnl_neto"] > 0]["pnl_neto"].sum() /
        max(abs(trades_filt[trades_filt["pnl_neto"] <= 0]["pnl_neto"].sum()), 0.001)
    ) if len(trades_filt) > 0 else 0.0

    return {"model": model, "pf": pf_new, "n_trades": len(combined),
            "n_new": len(df_live), "features": feature_cols}

def main():
    df_hist = load_historical_csvs()
    df_live = load_live_trades()
    if df_live is None:
        print("❌ No hay suficientes trades nuevos. Se conserva el modelo actual.")
        return

    result = train_model(df_hist, df_live)
    if result is None:
        return

    pf_new = result["pf"]
    pf_old = 0.0

    if MODEL_FILE.exists():
        with open(MODEL_FILE, "rb") as f:
            old_model, _ = pickle.load(f)
        pf_old_data = load_trade_pf_from_model(old_model, df_live)
        pf_old = pf_old_data

    print(f"📊 PF actual: {pf_old:.4f}, PF nuevo: {pf_new:.4f}")
    if pf_new > pf_old:
        with open(MODEL_FILE, "wb") as f:
            pickle.dump((result["model"], result["features"]), f)
        print(f"✅ Modelo actualizado ({result['n_new']} nuevos trades, total {result['n_trades']})")
    else:
        print("⛔ Nuevo modelo no mejora. Conservando actual.")

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "n_new": result["n_new"],
        "n_total": result["n_trades"],
        "pf_old": round(pf_old, 4),
        "pf_new": round(pf_new, 4),
        "updated": pf_new > pf_old,
    }
    with open(RETRAIN_LOG, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

if __name__ == "__main__":
    main()