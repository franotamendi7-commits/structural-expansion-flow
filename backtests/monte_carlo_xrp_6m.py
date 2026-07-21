"""
Monte Carlo para XRPUSDT - 6 meses.
Uso: python3 monte_carlo_xrp_6m.py [--plot]
"""
import sys, os
import numpy as np
import pandas as pd

def run_monte_carlo(csv_path, n_sim=1000, show_plot=False):
    df = pd.read_csv(csv_path)
    if 'pnl' not in df.columns:
        raise ValueError("Falta la columna 'pnl'")
    pnl = df['pnl'].values
    n_trades = len(pnl)
    print(f"Trades cargados: {n_trades}")
    np.random.seed(42)

    final_pnl = np.zeros(n_sim)
    max_dd = np.zeros(n_sim)
    win_rates = np.zeros(n_sim)

    for i in range(n_sim):
        sample = np.random.choice(pnl, size=n_trades, replace=True)
        curve = np.cumsum(sample)
        final_pnl[i] = curve[-1]
        running_max = np.maximum.accumulate(curve)
        max_dd[i] = (running_max - curve).max()
        win_rates[i] = (sample > 0).mean()

    pnl_perc = np.percentile(final_pnl, [5, 50, 95])
    dd_perc = np.percentile(max_dd, [5, 50, 95])
    wr_perc = np.percentile(win_rates, [5, 50, 95])
    prob_profit = np.mean(final_pnl > 0) * 100

    print("\n" + "="*65)
    print("MONTE CARLO - XRPUSDT (1 000 simulaciones)")
    print("="*65)
    print(f"{'Métrica':<25} {'P5':>12} {'P50':>12} {'P95':>12}")
    print("-"*65)
    print(f"{'PnL Final ($)':<25} {pnl_perc[0]:>12.2f} {pnl_perc[1]:>12.2f} {pnl_perc[2]:>12.2f}")
    print(f"{'Drawdown Máx ($)':<25} {dd_perc[0]:>12.2f} {dd_perc[1]:>12.2f} {dd_perc[2]:>12.2f}")
    print(f"{'Win Rate (%)':<25} {wr_perc[0]*100:>11.1f}% {wr_perc[1]*100:>11.1f}% {wr_perc[2]*100:>11.1f}%")
    print("-"*65)
    print(f"Probabilidad de ganancia: {prob_profit:.1f}%")

    # guardar resultados completos
    res_df = pd.DataFrame({
        'run': np.arange(1, n_sim+1),
        'pnl_final': final_pnl,
        'max_drawdown': max_dd,
        'win_rate': win_rates
    })
    res_df.to_csv("monte_carlo_xrp_6m.csv", index=False)
    print("\n📁 Resultados detallados guardados en monte_carlo_xrp_6m.csv")

    if show_plot:
        try:
            import matplotlib.pyplot as plt
            plt.figure(figsize=(10,5))
            plt.hist(final_pnl, bins=50, color='darkorange', edgecolor='white', alpha=0.8)
            plt.axvline(np.median(final_pnl), color='red', linestyle='--', label=f'Mediana = ${np.median(final_pnl):.2f}')
            plt.title('Distribución del PnL Final - XRPUSDT 6 meses')
            plt.xlabel('PnL ($)')
            plt.ylabel('Frecuencia')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig('xrp_hist.png', dpi=150)
            plt.show()
        except ImportError:
            print("matplotlib no instalado, omitiendo gráfico.")

if __name__ == "__main__":
    csv = "backtest_6m_xrp_fixed.csv"
    show = '--plot' in sys.argv
    if not os.path.exists(csv):
        print(f"No se encontró {csv}. Ejecutá primero el backtest de XRP.")
        sys.exit(1)
    run_monte_carlo(csv, show_plot=show)