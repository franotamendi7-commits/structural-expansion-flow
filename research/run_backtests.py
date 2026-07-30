#!/usr/bin/env python3
"""
Comprehensive Backtest Runner
Runs all strategies across all symbols and timeframes.
"""

import sys
import os
import json
import time
import traceback
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# Add parent directory to path so we can import strategies
sys.path.insert(0, str(Path(__file__).parent))
from strategies import get_all_strategies, BacktestResult, Trade, PositionSide

# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/data')
BACKTEST_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/backtests')
REPORT_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/reports')
INITIAL_CAPITAL = 10000.0

# Cost model
COMMISSION_RATE = 0.0004  # 0.04% per trade (crypto taker)
SLIPPAGE_BPS = 5  # 5 basis points slippage

# Max candles for slow strategies (Supertrend, Parabolic SAR, Ichimoku)
SLOW_STRATEGY_MAX_CANDLES = 5000


def load_data(symbol_dir):
    """Load all parquet files for a symbol"""
    data = {}
    symbol_path = DATA_DIR / symbol_dir
    if not symbol_path.is_dir():
        return data
    
    for f in symbol_path.glob('*.parquet'):
        # Extract timeframe from filename (e.g., BTCUSDT_1h.parquet -> 1h)
        parts = f.stem.split('_')
        timeframe = parts[-1]
        try:
            df = pd.read_parquet(f)
            # Ensure required columns exist
            required = ['open', 'high', 'low', 'close', 'volume']
            if all(col in df.columns for col in required):
                data[timeframe] = df
        except Exception as e:
            print(f"  Error loading {f}: {e}")
    
    return data


def run_backtest(strategy, data, symbol_name, timeframe, initial_capital=INITIAL_CAPITAL):
    """Run a single backtest with realistic costs"""
    
    if len(data) < 50:
        return None
    
    # Generate signals
    try:
        positions = strategy.generate_signals(data)
    except Exception as e:
        return None
    
    # Calculate returns
    returns = data['close'].pct_change().fillna(0)
    
    # Strategy returns (position * market return)
    # Position is shifted by 1 to avoid look-ahead bias
    strategy_returns = positions.shift(1).fillna(0) * returns
    
    # Calculate trade changes (when position changes)
    trade_changes = positions.diff().fillna(0)
    
    # Count trades (each change from 0 to +/-1 or flip is a trade)
    trades_mask = trade_changes != 0
    num_trades = int(trades_mask.sum())
    
    if num_trades < 5:
        return None
    
    # Calculate costs
    # Each trade has entry + exit = 2x costs
    trade_cost_per_bar = abs(trade_changes) * (COMMISSION_RATE + SLIPPAGE_BPS / 10000)
    total_costs = trade_cost_per_bar.sum()
    
    # Net strategy returns
    net_returns = strategy_returns - trade_cost_per_bar
    
    # Equity curve
    equity = initial_capital * (1 + net_returns).cumprod()
    
    # Performance metrics
    total_return = (equity.iloc[-1] / initial_capital - 1) * 100
    
    # Annualized return
    n_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
    if n_days > 0:
        annual_return = ((1 + total_return / 100) ** (365 / n_days) - 1) * 100
    else:
        annual_return = 0
    
    # Sharpe ratio (annualized)
    if net_returns.std() != 0:
        sharpe = (net_returns.mean() / net_returns.std()) * np.sqrt(252 * 24)  # Assuming hourly
    else:
        sharpe = 0
    
    # Sortino ratio
    downside_returns = net_returns[net_returns < 0]
    if len(downside_returns) > 0 and downside_returns.std() != 0:
        sortino = (net_returns.mean() / downside_returns.std()) * np.sqrt(252 * 24)
    else:
        sortino = 0
    
    # Max drawdown
    rolling_max = equity.expanding().max()
    drawdown = equity / rolling_max - 1
    max_drawdown = drawdown.min() * 100
    
    # Win rate
    trade_returns = []
    in_trade = False
    trade_start = None
    trade_pnl = 0
    
    for i in range(len(positions)):
        if positions.iloc[i] != 0 and not in_trade:
            in_trade = True
            trade_start = i
            trade_pnl = 0
        elif in_trade:
            trade_pnl += net_returns.iloc[i]
            if positions.iloc[i] == 0 or i == len(positions) - 1:
                in_trade = False
                trade_returns.append(trade_pnl)
    
    if len(trade_returns) == 0:
        return None
    
    winning_trades = sum(1 for r in trade_returns if r > 0)
    losing_trades = sum(1 for r in trade_returns if r <= 0)
    win_rate = (winning_trades / len(trade_returns)) * 100
    
    # Profit factor
    gross_profit = sum(r for r in trade_returns if r > 0)
    gross_loss = abs(sum(r for r in trade_returns if r <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # Avg win/loss
    avg_win = np.mean([r for r in trade_returns if r > 0]) * 100 if winning_trades > 0 else 0
    avg_loss = np.mean([r for r in trade_returns if r <= 0]) * 100 if losing_trades > 0 else 0
    
    # Trade duration
    avg_duration = n_days / len(trade_returns) * 24 if len(trade_returns) > 0 else 0
    
    # Trades per year
    trades_per_year = len(trade_returns) / (n_days / 365) if n_days > 0 else 0
    
    result = {
        'strategy_name': strategy.name,
        'strategy_params': strategy.get_params_str(),
        'symbol': symbol_name,
        'timeframe': timeframe,
        'start_date': str(data.index[0]),
        'end_date': str(data.index[-1]),
        'initial_capital': initial_capital,
        'final_capital': float(equity.iloc[-1]),
        'total_return': round(total_return, 2),
        'annual_return': round(annual_return, 2),
        'sharpe_ratio': round(sharpe, 2),
        'sortino_ratio': round(sortino, 2),
        'max_drawdown': round(max_drawdown, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'total_trades': len(trade_returns),
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'avg_trade_duration_hours': round(avg_duration, 1),
        'trades_per_year': round(trades_per_year, 1),
        'total_costs_pct': round(total_costs * 100, 2),
    }
    
    return result


def main():
    print("=" * 70)
    print("COMPREHENSIVE TRADING STRATEGY BACKTEST")
    print("=" * 70)
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Initial Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"Commission: {COMMISSION_RATE*100:.2f}%")
    print(f"Slippage: {SLIPPAGE_BPS} bps")
    print("=" * 70)
    
    # Create directories
    BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Get all strategies
    strategies = get_all_strategies()
    print(f"\nLoaded {len(strategies)} strategies")
    
    # Get all data files
    all_results = []
    total_combos = 0
    completed = 0
    errors = 0
    
    # Count total combinations first
    for symbol_dir in sorted(DATA_DIR.iterdir()):
        if symbol_dir.is_dir():
            data = load_data(symbol_dir.name)
            for tf in data:
                total_combos += len(strategies)
    
    print(f"Total backtest combinations: {total_combos}")
    print("=" * 70)
    
    # Run backtests
    for symbol_dir in sorted(DATA_DIR.iterdir()):
        if not symbol_dir.is_dir():
            continue
        
        symbol_name = symbol_dir.name
        data = load_data(symbol_name)
        
        if not data:
            print(f"\nSkipping {symbol_name}: no data")
            continue
        
        for timeframe, df in sorted(data.items()):
            print(f"\n--- {symbol_name} {timeframe} ({len(df)} bars) ---")
            
            for strategy in strategies:
                try:
                    result = run_backtest(strategy, df, symbol_name, timeframe)
                    
                    if result:
                        all_results.append(result)
                        completed += 1
                        
                        # Save individual backtest
                        filename = f"{strategy.name}_{symbol_name}_{timeframe}.md"
                        filepath = BACKTEST_DIR / filename
                        
                        md = f"""# {strategy.name} ({strategy.get_params_str()}) — {symbol_name} {timeframe}

## Performance Summary

| Metric | Value |
|--------|-------|
| Period | {result['start_date'][:10]} → {result['end_date'][:10]} |
| Initial Capital | ${result['initial_capital']:,.2f} |
| Final Capital | ${result['final_capital']:,.2f} |
| Total Return | {result['total_return']:+.2f}% |
| Annual Return | {result['annual_return']:+.2f}% |
| Sharpe Ratio | {result['sharpe_ratio']:.2f} |
| Sortino Ratio | {result['sortino_ratio']:.2f} |
| Max Drawdown | {result['max_drawdown']:.2f}% |
| Win Rate | {result['win_rate']:.1f}% |
| Profit Factor | {result['profit_factor']:.2f} |
| Total Trades | {result['total_trades']} |
| Win/Loss | {result['winning_trades']}/{result['losing_trades']} |
| Avg Win | {result['avg_win']:.2f}% |
| Avg Loss | {result['avg_loss']:.2f}% |
| Avg Duration | {result['avg_trade_duration_hours']:.1f}h |
| Trades/Year | {result['trades_per_year']:.1f} |
| Total Costs | {result['total_costs_pct']:.2f}% |

## Verdict
"""
                        if result['sharpe_ratio'] > 1.0 and result['max_drawdown'] > -30:
                            md += "✅ **VIABLE** — Strong risk-adjusted returns\n"
                        elif result['sharpe_ratio'] > 0.5:
                            md += "⚠️ **MARGINAL** — Positive but needs improvement\n"
                        else:
                            md += "❌ **NOT VIABLE** — Poor risk-adjusted returns\n"
                        
                        with open(filepath, 'w') as f:
                            f.write(md)
                        
                        # Print progress
                        marker = "✅" if result['sharpe_ratio'] > 1.0 else ("⚠️" if result['sharpe_ratio'] > 0.5 else "❌")
                        print(f"  {marker} {strategy.name}: Return={result['total_return']:+.1f}% Sharpe={result['sharpe_ratio']:.2f} WR={result['win_rate']:.0f}% PF={result['profit_factor']:.2f}")
                    else:
                        errors += 1
                        
                except Exception as e:
                    errors += 1
                    print(f"  ❌ {strategy.name}: ERROR - {str(e)[:60]}")
    
    # Save summary JSON
    summary_path = BACKTEST_DIR / 'summary.json'
    with open(summary_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    
    # Print final summary
    print("\n" + "=" * 70)
    print("BACKTEST COMPLETE")
    print("=" * 70)
    print(f"Completed: {completed} backtests")
    print(f"Errors: {errors}")
    print(f"Results saved to: {BACKTEST_DIR}")
    
    # Top strategies
    if all_results:
        sorted_by_sharpe = sorted(all_results, key=lambda x: x['sharpe_ratio'], reverse=True)
        
        print("\n\nTOP 10 BY SHARPE RATIO:")
        print("-" * 70)
        for i, r in enumerate(sorted_by_sharpe[:10], 1):
            print(f"{i:2d}. {r['strategy_name']:20s} {r['symbol']:10s} {r['timeframe']:3s} | Sharpe={r['sharpe_ratio']:6.2f} Return={r['total_return']:+7.1f}% WR={r['win_rate']:4.0f}% PF={r['profit_factor']:5.2f} DD={r['max_drawdown']:6.1f}%")
        
        sorted_by_return = sorted(all_results, key=lambda x: x['total_return'], reverse=True)
        print("\n\nTOP 10 BY TOTAL RETURN:")
        print("-" * 70)
        for i, r in enumerate(sorted_by_return[:10], 1):
            print(f"{i:2d}. {r['strategy_name']:20s} {r['symbol']:10s} {r['timeframe']:3s} | Return={r['total_return']:+7.1f}% Sharpe={r['sharpe_ratio']:6.2f} WR={r['win_rate']:4.0f}% PF={r['profit_factor']:5.2f}")
        
        sorted_by_pf = sorted(all_results, key=lambda x: x['profit_factor'], reverse=True)
        print("\n\nTOP 10 BY PROFIT FACTOR:")
        print("-" * 70)
        for i, r in enumerate(sorted_by_pf[:10], 1):
            print(f"{i:2d}. {r['strategy_name']:20s} {r['symbol']:10s} {r['timeframe']:3s} | PF={r['profit_factor']:6.2f} Return={r['total_return']:+7.1f}% Sharpe={r['sharpe_ratio']:6.2f} WR={r['win_rate']:4.0f}%")
    
    print("\n" + "=" * 70)
    print(f"Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)


if __name__ == '__main__':
    main()
