import pickle
import pandas as pd
import numpy as np

_model = None
_features_cols = None

def cargar_modelo():
    global _model, _features_cols
    with open('ml_model.pkl', 'rb') as f:
        _model = pickle.load(f)                 # ✅ Solo cargamos el modelo
        _features_cols = _model.feature_names_in_  # ✅ Las features las da el modelo

def debe_ejecutar(signal_features):
    global _model, _features_cols
    if _model is None:
        cargar_modelo()
    X = pd.DataFrame([signal_features])
    for col in _features_cols:
        if col not in X.columns:
            X[col] = 0.0
    X = X[_features_cols].fillna(0)
    prob = _model.predict_proba(X)[0, 1]
    ejecutar = prob > 0.55
    return ejecutar, prob