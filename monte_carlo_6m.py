"""
Monte Carlo Simulation - 6 meses riesgo fijo.
Uso:
    python3 monte_carlo_6m.py [ruta_csv] [--plot]
"""
import sys, os
import numpy as np
import pandas as pd

def run_monte_carlo(csv_path, n_sim=1000, show_plot=False):
    df = pd.read_csv(csv_path)
    if 'pnl' not in df.columns:
        raise ValueError("El CSV debe contener la columna 'pnl'.")

    pnl_array = df['pnl'].values
    n_trades = len(pnl_array)
    print(f"Trades históricos cargados: {n_trades}")
    print(f"Simulaciones: {n_sim}  |  Tamaño de muestra: {n_trades}")

    np.random.seed(42)
    final_pnl = np.zeros(n_sim)
    max_dd = np.zeros(n_sim)
    win_rates = np.zeros(n_sim)

    for i in range(n_sim):
        sample = np.random.choice(pnl_array, size=n_trades, replace=True)
        curve = np.cumsum(sample)
        final_pnl[i] = curve[-1]
        running_max = np.maximum.accumulate(curve)
        max_dd[i] = np.max(running_max - curve)
        win_rates[i] = np.sum(sample > 0) / n_trades

    pcts = [5, 50, 95]
    pnl_perc = np.percentile(final_pnl, pcts)
    dd_perc = np.percentile(max_dd, pcts)
    wr_perc = np.percentile(win_rates, pcts)
    prob_profit = np.mean(final_pnl > 0) * 100

    print("\n" + "=" * 65)
    print("RESULTADOS MONTE CARLO (1 000 simulaciones)")
    print("=" * 65)
    print(f"{'Métrica':<25} {'P5':>12} {'P50':>12} {'P95':>12}")
    print("-" * 65)
    print(f"{'PnL Final ($)':<25} {pnl_perc[0]:>12.2f} {pnl_perc[1]:>12.2f} {pnl_perc[2]:>12.2f}")
    print(f"{'Drawdown Máximo ($)':<25} {dd_perc[0]:>12.2f} {dd_perc[1]:>12.2f} {dd_perc[2]:>12.2f}")
    print(f"{'Win Rate (%)':<25} {wr_perc[0]*100:>11.1f}% {wr_perc[1]*100:>11.1f}% {wr_perc[2]*100:>11.1f}%")
    print("-" * 65)
    print(f"Probabilidad de terminar en ganancia: {prob_profit:.1f}%")

    results = pd.DataFrame({
        'run': np.arange(1, n_sim+1),
        'pnl_final': final_pnl,
        'max_drawdown': max_dd,
        'win_rate': win_rates
    })
    output_csv = 'monte_carlo_results.csv'
    results.to_csv(output_csv, index=False)
    print(f"\n📁 Resultados detallados guardados en: {output_csv}")

    if show_plot:
        try:
            import matplotlib.pyplot as plt
            plt.figure(figsize=(10,5))
            plt.hist(final_pnl, bins=50, color='steelblue', edgecolor='white', alpha=0.85)
            plt.axvline(np.median(final_pnl), color='red', linestyle='--',
                        label=f'Mediana = ${np.median(final_pnl):.2f}')
            plt.title('Distribución del PnL Final - Monte Carlo')
            plt.xlabel('PnL Final ($)')
            plt.ylabel('Frecuencia')
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig('pnl_histogram.png', dpi=150)
            plt.show()
        except ImportError:
            print("\n⚠️  matplotlib no instalado. Omitiendo histograma.")

if __name__ == "__main__":
    csv_file = 'backtest_6months_fixed_risk.csv'
    show_plot = False
    if len(sys.argv) > 1:
        if sys.argv[1] in ['--plot', '-p']:
            show_plot = True
        else:
            csv_file = sys.argv[1]
        if len(sys.argv) > 2 and sys.argv[2] in ['--plot', '-p']:
            show_plot = True

    if not os.path.exists(csv_file):
        print(f"Error: no se encontró '{csv_file}'")
        sys.exit(1)

    run_monte_carlo(csv_file, show_plot=show_plot)
