#!/usr/bin/env python3
"""
Reentrenamiento automático del modelo ML para STRUCTURAL EXPANSION FLOW.
- Carga los 4 CSV multi‑período base.
- Lee nuevas señales reales (features desde ml_veto_log.json, PnL desde audit_log.csv).
- Concatena todo y reentrena con validación temporal.
- Compara Profit Factor en un fold de test ciego.
- Sobrescribe ml_model.pkl solo si el nuevo modelo mejora el PF.
"""

import pandas as pd
import numpy as np
import json
import pickle
import os
from datetime import datetime, timezone
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

# ─── CONFIGURACIÓN ───────────────────────────────────────────────
MIN_NEW_TRADES = 15          # mínimo de trades nuevos para reentrenar
VETO_FILE = "ml_veto_log.json"
AUDIT_FILE = "audit_log.csv"
MODEL_FILE = "ml_model.pkl"
TRAIN_FILES = [
    "backtest_institucional_Q12023.csv",
    "backtest_institucional_Q32024.csv",
    "backtest_institucional_Q42025.csv",
    "backtest_institucional_Q12026_ref.csv"
]

# ─── FUNCIONES AUXILIARES ─────────────────────────────────────────
def cargar_datos_historicos():
    """Carga y une los 4 CSV base."""
    dfs = []
    for f in TRAIN_FILES:
        if not os.path.exists(f):
            print(f"⚠️  {f} no encontrado, se omite.")
            continue
        df = pd.read_csv(f, parse_dates=["entry_time"])
        df = df.loc[:, ~df.columns.duplicated()]
        dfs.append(df)
    if not dfs:
        raise FileNotFoundError("Ningún CSV de entrenamiento encontrado.")
    df = pd.concat(dfs, ignore_index=True)
    df = df.loc[:, ~df.columns.duplicated()]
    print(f"✔ Datos históricos cargados: {len(df)} trades.")
    return df

def cargar_nuevos_trades():
    """
    Extrae señales ejecutadas (veto==False) de ml_veto_log.json
    y busca su PnL en audit_log.csv usando trade_id.
    Retorna DataFrame con features y pnl_neto, o None si no hay suficientes.
    """
    if not os.path.exists(VETO_FILE):
        print("⚠️  ml_veto_log.json no existe.")
        return None
    with open(VETO_FILE, "r") as f:
        lines = f.readlines()
    if not lines:
        print("⚠️  ml_veto_log.json vacío.")
        return None

    # Parsear señales aprobadas
    signals = []
    for line in lines:
        try:
            entry = json.loads(line)
            if not entry.get("veto", True):  # solo las ejecutadas
                signals.append(entry)
        except:
            continue
    if len(signals) < MIN_NEW_TRADES:
        print(f"⚠️  Solo {len(signals)} señales nuevas (mínimo {MIN_NEW_TRADES}). No se reentrenará.")
        return None

    # Cargar auditoría para obtener PnL
    if not os.path.exists(AUDIT_FILE):
        print("⚠️  audit_log.csv no encontrado.")
        return None
    audit = pd.read_csv(AUDIT_FILE)

    # Cruzar por trade_id
    rows = []
    for sig in signals:
        trade_id = sig.get("trade_id")
        if not trade_id:
            continue
        trade_row = audit[audit["trade_id"] == trade_id]
        if trade_row.empty:
            continue
        pnl = trade_row.iloc[0]["pnl_final"]
        features = sig.get("features", {})
        if not features:
            continue
        features["pnl_neto"] = float(pnl) if pd.notna(pnl) else 0.0
        rows.append(features)

    if len(rows) < MIN_NEW_TRADES:
        print(f"⚠️  Solo {len(rows)} trades nuevos con PnL válido. No se reentrenará.")
        return None

    df_new = pd.DataFrame(rows)
    print(f"✔ Trades nuevos con PnL: {len(df_new)}.")
    return df_new

def preprocesar(df):
    """Aplica el mismo preprocesamiento que usamos en el entrenamiento original."""
    df = df.copy()
    # Dummies para phase_h1 y mom_direction (si existen)
    for col, prefix in [("phase_h1", "phase"), ("mom_direction", "mom")]:
        if col in df.columns:
            dummies = pd.get_dummies(df[col], prefix=prefix)
            df = pd.concat([df, dummies], axis=1)
            df.drop(col, axis=1, inplace=True)
    return df

def calcular_pf(df):
    """Calcula Profit Factor clásico."""
    ganancia = df[df["pnl_neto"] > 0]["pnl_neto"].sum()
    perdida = abs(df[df["pnl_neto"] <= 0]["pnl_neto"].sum())
    return ganancia / perdida if perdida != 0 else 0.0

# ─── PIPELINE PRINCIPAL ──────────────────────────────────────────
def main():
    print("=" * 60)
    print("🔄 REENTRENAMIENTO AUTOMÁTICO DEL MODELO ML")
    print("=" * 60)

    # 1. Cargar datos históricos
    df_hist = cargar_datos_historicos()

    # 2. Cargar nuevos trades
    df_new = cargar_nuevos_trades()
    if df_new is None:
        print("❌ No hay suficientes trades nuevos. Se conserva el modelo actual.")
        return

    # 3. Concatenar y preprocesar
    df_all = pd.concat([df_hist, df_new], ignore_index=True)
    df_all = preprocesar(df_all)

    # Definir features y target
    cols_a_evitar = ["symbol", "entry_time", "direction", "entry_signal",
                     "entry_real", "sl_original", "tp1", "contracts", "pnl_neto",
                     "exit_events", "exit_type", "explanation", "score"]
    feature_cols = [c for c in df_all.columns if c not in cols_a_evitar]
    X = df_all[feature_cols].fillna(df_all[feature_cols].median())
    y = (df_all["pnl_neto"] > 0).astype(int)

    # 4. Validación temporal con un fold de test (los últimos 20% de trades)
    split_idx = int(len(df_all) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    # 5. Entrenar nuevo modelo
    model = RandomForestClassifier(n_estimators=100, max_depth=5,
                                   random_state=42, class_weight="balanced")
    model.fit(X_train, y_train)

    # 6. Evaluar Profit Factor en el fold de test
    prob = model.predict_proba(X_test)[:, 1]
    filtro = prob > 0.55
    df_test = df_all.iloc[split_idx:].reset_index(drop=True)
    trades_filtrados = df_test[filtro]
    pf_nuevo = calcular_pf(trades_filtrados) if len(trades_filtrados) > 0 else 0.0

    # 7. Cargar modelo actual y evaluar mismo fold
    if os.path.exists(MODEL_FILE):
        with open(MODEL_FILE, "rb") as f:
            old_model, _ = pickle.load(f)
        old_prob = old_model.predict_proba(X_test)[:, 1]
        old_filtro = old_prob > 0.55
        trades_old = df_test[old_filtro]
        pf_actual = calcular_pf(trades_old) if len(trades_old) > 0 else 0.0
    else:
        pf_actual = 0.0

    print(f"📊 Profit Factor actual: {pf_actual:.4f}")
    print(f"📊 Profit Factor nuevo:  {pf_nuevo:.4f}")

    # 8. Decidir si actualizar
    if pf_nuevo > pf_actual:
        with open(MODEL_FILE, "wb") as f:
            pickle.dump((model, feature_cols), f)
        print("✅ Modelo ACTUALIZADO. El nuevo modelo mejora el Profit Factor.")
    else:
        print("⛔ El nuevo modelo NO mejora el actual. Se conserva el modelo existente.")

    # Guardar log de la operación
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trades_historicos": len(df_hist),
        "trades_nuevos": len(df_new),
        "pf_actual": round(pf_actual, 4),
        "pf_nuevo": round(pf_nuevo, 4),
        "actualizado": pf_nuevo > pf_actual
    }
    with open("retrain_log.json", "a") as log_f:
        log_f.write(json.dumps(log_entry) + "\n")
    print("📝 Log guardado en retrain_log.json.")

if __name__ == "__main__":
    main()
