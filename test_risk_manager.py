import pandas as pd
import numpy as np
from risk_manager import RiskManager

# Cargar trades de un período conocido (Q3 2024, por ejemplo)
df = pd.read_csv('backtest_3m_FASE5_5pares_Q32024.csv')

# Necesitamos una estimación del ATR en el momento de cada trade.
# Como no lo tenemos guardado en este CSV, usaremos un ATR promedio por par
# basado en datos recientes. Para un test rápido, asumimos ATR = precio * 0.02 (2% vol)
# y un score promedio de 70 (típico del motor).
# Esto es una aproximación; en producción usaríamos el ATR real del motor.

rm = RiskManager(base_risk=0.01, max_risk=0.015, min_risk=0.005)

# Simular con riesgo fijo (1%) como referencia
capital_fijo = 100.0
capital_dinamico = 100.0

for idx, row in df.iterrows():
    pnl = row['pnl_neto']
    entry = row.get('entry_signal', row.get('entry_real', 0))
    # Estimar ATR como 2% del precio (aproximación)
    atr_estimado = entry * 0.02 if entry > 0 else 20.0
    score_estimado = row.get('score', 70)

    # Riesgo fijo
    risk_fijo = 0.01
    notional_fijo = capital_fijo * risk_fijo / (entry * 0.018)  # sl_pct ~ 1.8%
    contracts_fijo = notional_fijo / entry if entry > 0 else 0
    pnl_fijo = pnl * (contracts_fijo / row['contracts']) if row['contracts'] > 0 else 0
    capital_fijo += pnl_fijo

    # Riesgo dinámico
    risk_din = rm.get_risk_pct(score_estimado, atr_estimado, entry)
    notional_din = capital_dinamico * risk_din / (entry * 0.018) if entry > 0 else 0
    contracts_din = notional_din / entry if entry > 0 else 0
    pnl_din = pnl * (contracts_din / row['contracts']) if row['contracts'] > 0 else 0
    capital_dinamico += pnl_din

# Resultados
print("=== Comparación Riesgo Fijo vs Dinámico ===")
print(f"Capital final (fijo 1%):      ${capital_fijo:.2f}")
print(f"Capital final (dinámico):     ${capital_dinamico:.2f}")
print(f"Diferencia:                   ${capital_dinamico - capital_fijo:.2f}")
