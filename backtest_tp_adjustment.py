"""
Quick backtest to verify tp_ratio adjustment from 1.5 to 1.3.
Runs V2 backtest with new tp_ratio and saves results.
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from backtest_institutional_v2 import BinanceDataDownloader, BacktestEngine, SYMBOL, INITIAL_CAPITAL

def run_tp_adjustment_test():
    print("=" * 60)
    print(" TP RATIO ADJUSTMENT TEST: 1.5 → 1.3")
    print("=" * 60)

    OUTPUT_DIR = Path(__file__).parent / "backtest_results"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Download data
    print("\n[1/2] Downloading data...")
    dl = BinanceDataDownloader()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=180)  # 6 months

    df_15m = dl.download_klines(SYMBOL, "15m", start_date, end_date)
    df_1h = dl.download_klines(SYMBOL, "1h", start_date, end_date)
    df_4h = dl.download_klines(SYMBOL, "4h", start_date, end_date)
    df_1d = dl.download_klines(SYMBOL, "1d", start_date, end_date)

    print(f"  Period: {df_15m.index[0]} to {df_15m.index[-1]}")
    print(f"  15m bars: {len(df_15m)}")

    # Run V2 backtest with new tp_ratio (1.3 is now default in engine config)
    print("\n[2/2] Running V2 backtest with tp_ratio=1.3...")
    bt = BacktestEngine()
    result = bt.run_v2(df_15m, df_1h, df_4h, df_1d)
    m = result["metrics"]

    print(f"\n{'='*60}")
    print(f" RESULTS with tp_ratio=1.3")
    print(f"{'='*60}")
    print(f"  Trades: {result['total_trades']}")
    print(f"  Win Rate: {m['win_rate']}%")
    print(f"  Profit Factor: {m['profit_factor']}")
    print(f"  Total Return: {m['total_return_pct']}%")
    print(f"  Max Drawdown: {m['max_dd_pct']}%")
    print(f"  Sharpe: {m['sharpe']}")
    print(f"  Sortino: {m['sortino']}")
    print(f"  Final Equity: ${m['final_equity']}")
    print(f"{'='*60}")

    # Save result
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "tp_ratio": 1.3,
        "period": f"{start_date.date()} to {end_date.date()}",
        "initial_capital": INITIAL_CAPITAL,
        "total_trades": result["total_trades"],
        "metrics": m,
        "exit_reasons": m.get("exit_reasons", {}),
    }

    outfile = OUTPUT_DIR / "tp_adjustment_result.json"
    with open(outfile, "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\nResults saved to: {outfile}")
    return output

if __name__ == "__main__":
    run_tp_adjustment_test()
