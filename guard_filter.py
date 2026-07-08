import pickle
import pandas as pd

_model = None
_features_cols = None

def cargar_modelo():
    global _model, _features_cols
    with open("guard_model.pkl", "rb") as f:
        _model = pickle.load(f)                 # ✅ solo el modelo
        _features_cols = _model.feature_names_in_  # ✅ features desde el modelo

def debe_revivir(features, filtro_fijo_aprobo):
    global _model, _features_cols
    if _model is None:
        cargar_modelo()

    # Agregar la decisión del filtro fijo como feature
    features['filtro_fijo'] = 1 if filtro_fijo_aprobo else 0

    X = pd.DataFrame([features])
    for col in _features_cols:
        if col not in X.columns:
            X[col] = 0.0
    X = X[_features_cols].fillna(0)

    prob = _model.predict_proba(X)[0, 1]
    # Si el Guard Agent tiene alta confianza (> 0.55), revierte la decisión del filtro fijo
    if prob > 0.55:
        return not filtro_fijo_aprobo  # si el fijo aprobó, guard veta; si el fijo vetó, guard revive
    else:
        return filtro_fijo_aprobo  # mantener decisión del filtro fijo