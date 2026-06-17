"""
Monte Carlo sobre Fase 4 – 10,000 simulaciones
"""
import pandas as pd
import numpy as np
import sys

CSV = 'backtest_3m_FASE4.csv'
N_SIM = 10_000
INITIAL_CAPITAL = 100.0

df = pd.read_csv(CSV)
pnls = df['pnl_neto'].values
n_trades = len(pnls)
print(f"Trades cargados: {n_trades}")

np.random.seed(42)

final_pnls = np.zeros(N_SIM)
max_drawdowns = np.zeros(N_SIM)
win_rates = np.zeros(N_SIM)

for i in range(N_SIM):
    sample = np.random.choice(pnls, size=n_trades, replace=True)
    curve = INITIAL_CAPITAL + np.cumsum(sample)
    final_pnls[i] = curve[-1] - INITIAL_CAPITAL
    # Drawdown máximo
    running_max = np.maximum.accumulate(curve)
    dd = running_max - curve
    max_drawdowns[i] = dd.max()
    win_rates[i] = (sample > 0).mean() * 100

prob_profit = (final_pnls > 0).mean() * 100

print("\n========== MONTE CARLO FASE 4 (10,000 simulaciones) ==========")
print(f"Probabilidad de ganancia: {prob_profit:.1f}%")
print(f"PnL final percentil 5:   ${np.percentile(final_pnls, 5):.2f}")
print(f"PnL final percentil 95:  ${np.percentile(final_pnls, 95):.2f}")
print(f"PnL final promedio:      ${final_pnls.mean():.2f}")
print(f"Drawdown promedio:       ${max_drawdowns.mean():.2f}")
print(f"Drawdown máximo (P5):    ${np.percentile(max_drawdowns, 5):.2f}")
print(f"Drawdown máximo (P95):   ${np.percentile(max_drawdowns, 95):.2f}")
print(f"Win rate promedio:       {win_rates.mean():.1f}%")
print(f"Win rate rango (P5-P95): {np.percentile(win_rates, 5):.1f}% – {np.percentile(win_rates, 95):.1f}%")

# Guardar resultados completos
resultados = pd.DataFrame({
    'simulacion': np.arange(N_SIM),
    'pnl_final': final_pnls,
    'max_drawdown': max_drawdowns,
    'win_rate': win_rates
})
resultados.to_csv('monte_carlo_fase4_results.csv', index=False)
print("\n📁 Resultados detallados guardados en monte_carlo_fase4_results.csv")

# Histograma opcional
try:
    import matplotlib.pyplot as plt
    plt.figure(figsize=(10,5))
    plt.hist(final_pnls, bins=50, color='steelblue', edgecolor='white', alpha=0.85)
    plt.axvline(final_pnls.mean(), color='red', linestyle='--', label=f'Promedio = ${final_pnls.mean():.2f}')
    plt.title('Distribución del PnL Final – Monte Carlo Fase 4')
    plt.xlabel('PnL Final ($)')
    plt.ylabel('Frecuencia')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('monte_carlo_fase4_hist.png', dpi=150)
    print("📊 Histograma guardado como monte_carlo_fase4_hist.png")
except ImportError:
    print("⚠️  matplotlib no instalado, histograma omitido.")
