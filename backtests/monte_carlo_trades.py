import pandas as pd
import numpy as np
import sys, os

def run_monte_carlo(csv_path, n_sim=1000, sample_size=None, seed=42):
    df = pd.read_csv(csv_path)
    if 'pnl' not in df.columns:
        raise ValueError("El CSV debe contener la columna 'pnl'.")
    pnl_array = df['pnl'].values
    n_real_trades = len(pnl_array)
    if sample_size is None:
        sample_size = n_real_trades
    print(f"Trades reales cargados: {n_real_trades}")
    print(f"Tamaño de muestra por simulación: {sample_size}")
    print(f"Número de simulaciones: {n_sim}")
    np.random.seed(seed)
    final_pnl = np.zeros(n_sim)
    max_dd = np.zeros(n_sim)
    win_rates = np.zeros(n_sim)
    for i in range(n_sim):
        sample = np.random.choice(pnl_array, size=sample_size, replace=True)
        curve = np.cumsum(sample)
        final_pnl[i] = curve[-1] if len(curve) > 0 else 0.0
        running_max = np.maximum.accumulate(curve)
        dd = running_max - curve
        max_dd[i] = np.max(dd) if len(dd) > 0 else 0.0
        win_rates[i] = np.sum(sample > 0) / len(sample)
    percentiles = [5, 50, 95]
    pnl_perc = np.percentile(final_pnl, percentiles)
    dd_perc = np.percentile(max_dd, percentiles)
    wr_perc = np.percentile(win_rates, percentiles)
    prob_profit = np.mean(final_pnl > 0) * 100
    print("\n" + "="*60)
    print("RESULTADOS MONTE CARLO")
    print("="*60)
    print(f"{'Métrica':<25} {'P5':>10} {'P50':>10} {'P95':>10}")
    print("-"*60)
    print(f"{'PnL Final ($)':<25} {pnl_perc[0]:>10.2f} {pnl_perc[1]:>10.2f} {pnl_perc[2]:>10.2f}")
    print(f"{'Drawdown Máximo ($)':<25} {dd_perc[0]:>10.2f} {dd_perc[1]:>10.2f} {dd_perc[2]:>10.2f}")
    print(f"{'Win Rate (%)':<25} {wr_perc[0]*100:>10.1f} {wr_perc[1]*100:>10.1f} {wr_perc[2]*100:>10.1f}")
    print("-"*60)
    print(f"Probabilidad de terminar en ganancia: {prob_profit:.1f}%")
    pd.DataFrame({'sim_id': np.arange(1, n_sim+1), 'final_pnl': final_pnl, 'max_drawdown': max_dd, 'win_rate': win_rates}).to_csv('mc_results.csv', index=False)
    print("\nResultados completos guardados en: mc_results.csv")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python monte_carlo_trades.py <ruta_csv>")
        sys.exit(1)
    csv_file = sys.argv[1]
    n_sim = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    sample_size = int(sys.argv[3]) if len(sys.argv) > 3 else None
    if not os.path.exists(csv_file):
        print(f"Error: no se encontró el archivo {csv_file}")
        sys.exit(1)
    run_monte_carlo(csv_file, n_sim=n_sim, sample_size=sample_size)
