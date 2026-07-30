"""
Backtest Completo — Adaptive VWAP Engine
==========================================
Ejecuta backtest con datos históricos reales de Binance:
1. Descarga datos de BTCUSDT 15m (últimos 6 meses)
2. Ejecuta backtest con costos realistas
3. Walk-forward validation
4. Monte Carlo simulation
5. Análisis por régimen
6. Genera reporte completo
"""

import sys
import json
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Agregar directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent))

from engine.adaptive_vwap_engine import AdaptiveVWAPEngine
from backtest_adaptive_vwap import BacktestEngine, WalkForwardValidator, MonteCarloSimulator, RegimeAnalyzer


# ============================================================
# CONFIGURACIÓN
# ============================================================

SYMBOL = "BTCUSDT"
INTERVAL = "15m"
MONTHS_BACK = 6  # 6 meses de datos
OUTPUT_DIR = Path(__file__).parent / "backtest_results"


# ============================================================
# DATA DOWNLOADER
# ============================================================

class BinanceDataDownloader:
    """Descarga datos históricos de Binance."""
    
    BASE_URLS = [
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api3.binance.com",
    ]
    
    def __init__(self):
        self.cache_dir = OUTPUT_DIR / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def download_klines(self, symbol: str, interval: str, 
                        start_date: datetime, end_date: datetime) -> pd.DataFrame:
        """
        Descarga klines históricos.
        
        Args:
            symbol: Par de trading (ej: BTCUSDT)
            interval: Intervalo de tiempo (ej: 15m)
            start_date: Fecha inicial
            end_date: Fecha final
        
        Returns:
            DataFrame con OHLCV data
        """
        cache_file = self.cache_dir / f"{symbol}_{interval}_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.parquet"
        
        if cache_file.exists():
            print(f"Loading cached data from {cache_file}")
            return pd.read_parquet(cache_file)
        
        print(f"Downloading {symbol} {interval} from {start_date} to {end_date}...")
        
        all_klines = []
        current_start = start_date
        
        while current_start < end_date:
            # Binance limit: 1000 klines per request
            klines = self._fetch_klines(symbol, interval, current_start, min(current_start + timedelta(days=7), end_date))
            
            if klines is None or len(klines) == 0:
                break
            
            all_klines.extend(klines)
            
            # Avanzar
            last_ts = klines[-1]['timestamp']
            current_start = datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc) + timedelta(minutes=15)
            
            # Rate limit
            import time
            time.sleep(0.1)
        
        if not all_klines:
            raise ValueError(f"No data downloaded for {symbol}")
        
        # Convertir a DataFrame
        df = pd.DataFrame(all_klines)
        df['open_time'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        df = df.set_index('open_time')
        df = df[['open', 'high', 'low', 'close', 'volume']]
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='first')]
        
        # Guardar en cache
        df.to_parquet(cache_file)
        print(f"Downloaded {len(df)} bars, cached to {cache_file}")
        
        return df
    
    def _fetch_klines(self, symbol: str, interval: str, 
                      start: datetime, end: datetime) -> list:
        """Fetch klines from Binance API."""
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        
        endpoint = f"/api/v3/klines?symbol={symbol}&interval={interval}&startTime={start_ms}&endTime={end_ms}&limit=1000"
        
        for base in self.BASE_URLS:
            try:
                resp = requests.get(base + endpoint, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    klines = []
                    for k in data:
                        klines.append({
                            'timestamp': int(k[0]),
                            'open': float(k[1]),
                            'high': float(k[2]),
                            'low': float(k[3]),
                            'close': float(k[4]),
                            'volume': float(k[5]),
                        })
                    return klines
            except Exception:
                continue
        
        return None


# ============================================================
# MAIN BACKTEST
# ============================================================

def run_full_backtest():
    """Ejecuta backtest completo."""
    print("=" * 60)
    print("ADAPTIVE VWAP ENGINE — FULL BACKTEST")
    print("=" * 60)
    
    # Crear directorio de output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. Descargar datos
    print("\n[1/5] Downloading historical data...")
    downloader = BinanceDataDownloader()
    
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=MONTHS_BACK * 30)
    
    df = downloader.download_klines(SYMBOL, INTERVAL, start_date, end_date)
    print(f"Data: {len(df)} bars from {df.index[0]} to {df.index[-1]}")
    
    # 2. Run backtest
    print("\n[2/5] Running backtest...")
    engine = AdaptiveVWAPEngine()
    engine.update_equity(100.0)  # $100 initial capital
    
    backtest = BacktestEngine()
    result = backtest.run(df, engine)
    
    metrics = result['metrics']
    print(f"\n=== Backtest Results ===")
    print(f"Total trades: {metrics['total_trades']}")
    print(f"Win rate: {metrics['win_rate']:.1%}")
    print(f"Profit factor: {metrics['profit_factor']:.2f}")
    print(f"Sharpe ratio: {metrics['sharpe']:.2f}")
    print(f"Sortino ratio: {metrics['sortino']:.2f}")
    print(f"Max drawdown: {metrics['max_dd_pct']:.2%}")
    print(f"Total return: ${metrics['total_return']:.2f} ({metrics['total_return_pct']:.2f}%)")
    print(f"Avg trade: ${metrics['avg_trade']:.4f}")
    print(f"Avg win: ${metrics['avg_win']:.4f}")
    print(f"Avg loss: ${metrics['avg_loss']:.4f}")
    
    # 3. Monte Carlo
    print("\n[3/5] Running Monte Carlo simulation...")
    mc = MonteCarloSimulator()
    mc_result = mc.simulate(result['trades'])
    
    print(f"\n=== Monte Carlo Results (5000 sims) ===")
    print(f"Mean return: {mc_result['metrics']['mean_return']:.2f}%")
    print(f"Median return: {mc_result['metrics']['median_return']:.2f}%")
    print(f"Prob profit: {mc_result['metrics']['prob_profit']:.1%}")
    print(f"P95 return: {mc_result['metrics']['p95_return']:.2f}%")
    print(f"P5 return: {mc_result['metrics']['p5_return']:.2f}%")
    print(f"Mean max DD: {mc_result['metrics']['mean_max_dd']:.2f}%")
    print(f"P95 max DD: {mc_result['metrics']['p95_max_dd']:.2f}%")
    
    # 4. Regime analysis
    print("\n[4/5] Analyzing performance by regime...")
    regime_analyzer = RegimeAnalyzer()
    regime_result = regime_analyzer.analyze(result['trades'])
    
    print(f"\n=== Regime Analysis ===")
    for regime, stats in regime_result.items():
        print(f"{regime}: {stats['trades']} trades | WR {stats['win_rate']:.1%} | "
              f"Avg ${stats['avg_trade']:.4f} | PnL ${stats['total_pnl']:.2f}")
    
    # 5. Walk-forward (simplified)
    print("\n[5/5] Walk-forward validation...")
    wf_validator = WalkForwardValidator()
    wf_result = wf_validator.validate(df)
    
    print(f"\n=== Walk-Forward Results ===")
    print(f"Folds: {wf_result['summary']['n_folds']}")
    print(f"Profitable folds: {wf_result['summary']['profitable_folds']}/{wf_result['summary']['n_folds']}")
    print(f"Consistency: {wf_result['summary']['consistency']:.1%}")
    print(f"Avg Sharpe: {wf_result['summary']['avg_sharpe']:.2f}")
    print(f"Avg Return: {wf_result['summary']['avg_return']:.2f}%")
    print(f"Min Sharpe: {wf_result['summary']['min_sharpe']:.2f}")
    print(f"Min Return: {wf_result['summary']['min_return']:.2f}%")
    
    # Save results
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'symbol': SYMBOL,
        'interval': INTERVAL,
        'period': {
            'start': df.index[0].isoformat(),
            'end': df.index[-1].isoformat(),
            'bars': len(df),
        },
        'backtest': metrics,
        'monte_carlo': mc_result['metrics'],
        'regime_analysis': regime_result,
        'walk_forward': wf_result['summary'],
    }
    
    report_file = OUTPUT_DIR / f"backtest_{SYMBOL}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n{'=' * 60}")
    print(f"Report saved to: {report_file}")
    print(f"{'=' * 60}")
    
    return report


if __name__ == "__main__":
    run_full_backtest()
