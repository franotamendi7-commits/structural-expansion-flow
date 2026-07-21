import numpy as np

capital_inicial = 100.0
riesgo_por_trade = 0.01   # 1% del capital
win_rate = 0.60           # conservador, basado en tus datos reales 60-65%
profit_factor = 3.0       # piso realista
# Calculamos la ganancia promedio y pérdida promedio a partir del PF
# PF = (win_rate * avg_gain) / ((1-win_rate) * avg_loss)
# Además, asumimos que la pérdida promedio es exactamente el riesgo (1% del capital)
# Entonces avg_loss_pct = 0.01 (del capital)
# avg_gain_pct = (PF * (1-win_rate) * avg_loss_pct) / win_rate
avg_loss_pct = riesgo_por_trade
avg_gain_pct = (profit_factor * (1 - win_rate) * avg_loss_pct) / win_rate

np.random.seed(42)
trades_por_año = 30  # aproximado según tus datos (~392 en 3 años => ~130 por año, pero con filtro son ~130 total en 3 años, esperá: 392 filtrados en 3 años = ~130 por año)
# Realmente en 3 años tuviste 392 trades filtrados, así que son ~130 por año.
# Tomemos 120 trades para la simulación de 3 años.
num_trades = 120 * 3
capital = capital_inicial
equity = [capital]
for i in range(num_trades):
    if np.random.rand() < win_rate:
        capital *= (1 + avg_gain_pct)
    else:
        capital *= (1 - avg_loss_pct)
    equity.append(capital)

final = capital
print(f"Capital inicial: ${capital_inicial:.2f}")
print(f"Después de {num_trades} trades (aprox 3 años): ${final:.2f}")
print(f"Rendimiento total: {((final/capital_inicial)-1)*100:.1f}%")
print(f"Equity final simulado (una sola corrida): ${final:.2f}")
# También podés correr muchas simulaciones para ver la distribución.
