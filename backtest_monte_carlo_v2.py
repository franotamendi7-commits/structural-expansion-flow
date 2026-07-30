"""
Monte Carlo Exhaustive Simulation V2 - 10,000+ simulations (Post-Only + Vol Targeting)
========================================================================================

Uses real trades from V2 backtest (Jan-Jul 2026, BTCUSDT) with:
- Post-Only orders: commission reduced from 0.05% to 0.018% per side
- Volatility Targeting: position size adjusted inversely to volatility

Scenarios:
1. Base: trades as-is
2. Worst case: reduce WR by 10pp (65% → 55%)
3. Extreme worst case: reduce WR by 20pp (65% → 45%)
4. High volatility: increase trade sizes by 50%
5. Low volatility: reduce trade sizes by 50%

Success criteria:
- Prob profit > 85% in ALL scenarios
- Prob DD > 10% < 15%
- Prob blowing up (>50% DD) < 5%
- Mean Sharpe > 1.5 in all scenarios
"""

import sys
import json
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent))

from backtest_institutional_v2 import (
    BinanceDataDownloader, BacktestEngine, Trade, SYMBOL, INITIAL_CAPITAL
)

OUTPUT_DIR = Path(__file__).parent / "backtest_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class ScenarioConfig:
    """Configuration for a Monte Carlo scenario."""
    name: str
    description: str
    wr_adjustment: float = 0.0  # pp to adjust win rate
    size_multiplier: float = 1.0  # multiplier for trade sizes


SCENARIOS = [
    ScenarioConfig("base", "Trades as-is"),
    ScenarioConfig("worst_case_10pp", "Reduce WR by 10pp", wr_adjustment=-10.0),
    ScenarioConfig("extreme_worst_20pp", "Reduce WR by 20pp", wr_adjustment=-20.0),
    ScenarioConfig("high_volatility", "Increase trade sizes 50%", size_multiplier=1.5),
    ScenarioConfig("low_volatility", "Reduce trade sizes 50%", size_multiplier=0.5),
]


def simulate_single_scenario(
    pnls: np.ndarray,
    initial_capital: float,
    n_sims: int,
    wr_adjustment: float = 0.0,
    size_multiplier: float = 1.0,
    n_trades_override: int = None,
    rng: np.random.Generator = None,
) -> Dict[str, Any]:
    """
    Run Monte Carlo simulation for a single scenario.
    
    Args:
        pnls: Array of PnL values from real trades
        initial_capital: Starting capital
        n_sims: Number of simulations
        wr_adjustment: Win rate adjustment in percentage points
        size_multiplier: Multiplier for trade sizes
        n_trades_override: Override number of trades (if None, use len(pnls))
        rng: Random number generator for reproducibility
    
    Returns:
        Dictionary with scenario results
    """
    if rng is None:
        rng = np.random.default_rng(42)
    
    n_trades = n_trades_override or len(pnls)
    
    # Separate wins and losses
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    
    # Apply win rate adjustment
    if wr_adjustment != 0.0 and len(wins) > 0 and len(losses) > 0:
        current_wr = len(wins) / len(pnls) * 100
        target_wr = current_wr + wr_adjustment
        target_wr = max(10, min(90, target_wr))  # Clamp between 10-90%
        
        # Recalculate number of wins and losses
        n_wins = int(n_trades * target_wr / 100)
        n_losses = n_trades - n_wins
        
        # Sample wins and losses to match target WR
        if n_wins <= len(wins):
            sampled_wins = rng.choice(wins, size=n_wins, replace=True)
        else:
            sampled_wins = rng.choice(wins, size=n_wins, replace=True)
        
        if n_losses <= len(losses):
            sampled_losses = rng.choice(losses, size=n_losses, replace=True)
        else:
            sampled_losses = rng.choice(losses, size=n_losses, replace=True)
        
        # Combine and shuffle
        adjusted_pnls = np.concatenate([sampled_wins, sampled_losses])
        rng.shuffle(adjusted_pnls)
    else:
        adjusted_pnls = pnls.copy()
        # Apply size multiplier
        if size_multiplier != 1.0:
            adjusted_pnls = adjusted_pnls * size_multiplier
    
    # Apply size multiplier if not already applied
    if wr_adjustment == 0.0 and size_multiplier != 1.0:
        adjusted_pnls = adjusted_pnls * size_multiplier
    
    # Run simulations
    final_equities = np.zeros(n_sims)
    max_dds = np.zeros(n_sims)
    sharpe_ratios = np.zeros(n_sims)
    dd_exceeds_10 = np.zeros(n_sims, dtype=bool)
    dd_exceeds_15 = np.zeros(n_sims, dtype=bool)
    dd_exceeds_50 = np.zeros(n_sims, dtype=bool)
    
    for sim in range(n_sims):
        # Shuffle trades
        shuffled = rng.permutation(adjusted_pnls)
        
        equity = initial_capital
        peak = equity
        max_dd = 0.0
        
        # Track equity for Sharpe calculation
        equity_history = [equity]
        
        for pnl in shuffled:
            equity += pnl
            equity_history.append(equity)
            peak = max(peak, equity)
            dd = (equity - peak) / peak * 100
            max_dd = min(max_dd, dd)
        
        final_equities[sim] = equity
        max_dds[sim] = max_dd
        
        # Calculate annualized Sharpe
        eq_arr = np.array(equity_history)
        returns = np.diff(eq_arr) / eq_arr[:-1]
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe_ratios[sim] = np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 4)
        else:
            sharpe_ratios[sim] = 0.0
        
        # Check DD thresholds
        dd_exceeds_10[sim] = max_dd < -10.0
        dd_exceeds_15[sim] = max_dd < -15.0
        dd_exceeds_50[sim] = max_dd < -50.0
    
    # Calculate statistics
    returns_pct = (final_equities - initial_capital) / initial_capital * 100
    
    return {
        "mean_return": round(float(np.mean(returns_pct)), 2),
        "median_return": round(float(np.median(returns_pct)), 2),
        "std_return": round(float(np.std(returns_pct)), 2),
        "percentiles": {
            "p5": round(float(np.percentile(returns_pct, 5)), 2),
            "p25": round(float(np.percentile(returns_pct, 25)), 2),
            "p50": round(float(np.percentile(returns_pct, 50)), 2),
            "p75": round(float(np.percentile(returns_pct, 75)), 2),
            "p95": round(float(np.percentile(returns_pct, 95)), 2),
        },
        "max_dd": {
            "mean": round(float(np.mean(max_dds)), 2),
            "worst": round(float(np.min(max_dds)), 2),
            "std": round(float(np.std(max_dds)), 2),
        },
        "prob_profit": round(float(np.mean(final_equities > initial_capital)), 4),
        "prob_dd_exceeds_10": round(float(np.mean(dd_exceeds_10)), 4),
        "prob_dd_exceeds_15": round(float(np.mean(dd_exceeds_15)), 4),
        "prob_blowing_up": round(float(np.mean(dd_exceeds_50)), 4),
        "sharpe": {
            "mean": round(float(np.mean(sharpe_ratios)), 2),
            "median": round(float(np.median(sharpe_ratios)), 2),
            "worst": round(float(np.percentile(sharpe_ratios, 5)), 2),
            "std": round(float(np.std(sharpe_ratios)), 2),
        },
        "final_equity": {
            "mean": round(float(np.mean(final_equities)), 2),
            "median": round(float(np.median(final_equities)), 2),
            "std": round(float(np.std(final_equities)), 2),
        },
    }


def run_monte_carlo_exhaustive():
    """Run exhaustive Monte Carlo simulation."""
    print("=" * 70)
    print(" MONTE CARLO EXHAUSTIVE SIMULATION - 10,000+ sims")
    print("=" * 70)
    
    # 1. Get real trades from V2 backtest
    print("\n[1/3] Running V2 backtest to get real trades...")
    
    from datetime import timedelta
    dl = BinanceDataDownloader()
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=180)
    
    df_15m = dl.download_klines(SYMBOL, "15m", start_date, end_date)
    df_1h = dl.download_klines(SYMBOL, "1h", start_date, end_date)
    df_4h = dl.download_klines(SYMBOL, "4h", start_date, end_date)
    df_1d = dl.download_klines(SYMBOL, "1d", start_date, end_date)
    
    bt = BacktestEngine()
    result = bt.run_v2(df_15m, df_1h, df_4h, df_1d)
    
    trades = result.get("trades", [])
    if not trades:
        print("ERROR: No trades from backtest")
        return None
    
    pnls = np.array([t["pnl"] for t in trades])
    print(f"  Total trades: {len(trades)}")
    print(f"  Win rate: {len(pnls[pnls > 0]) / len(pnls) * 100:.1f}%")
    print(f"  Total PnL: ${np.sum(pnls):.2f}")
    print(f"  Avg PnL: ${np.mean(pnls):.4f}")
    
    # 2. Run Monte Carlo for each scenario
    print(f"\n[2/3] Running Monte Carlo for {len(SCENARIOS)} scenarios...")
    
    N_SIMS = 10000
    results = {}
    
    for scenario in SCENARIOS:
        print(f"\n  Scenario: {scenario.name} - {scenario.description}")
        
        # Use different seed for each scenario for variety
        seed = hash(scenario.name) % (2**31)
        rng = np.random.default_rng(seed)
        
        sim_result = simulate_single_scenario(
            pnls=pnls,
            initial_capital=INITIAL_CAPITAL,
            n_sims=N_SIMS,
            wr_adjustment=scenario.wr_adjustment,
            size_multiplier=scenario.size_multiplier,
            rng=rng,
        )
        
        results[scenario.name] = {
            "description": scenario.description,
            "wr_adjustment": scenario.wr_adjustment,
            "size_multiplier": scenario.size_multiplier,
            "n_sims": N_SIMS,
            "n_trades": len(trades),
            "results": sim_result,
        }
        
        # Print summary
        r = sim_result
        print(f"    Mean Return: {r['mean_return']:+.2f}%")
        print(f"    Prob Profit: {r['prob_profit']:.1%}")
        print(f"    Mean DD: {r['max_dd']['mean']:.2f}%")
        print(f"    Prob DD > 10%: {r['prob_dd_exceeds_10']:.1%}")
        print(f"    Prob Blow Up: {r['prob_blowing_up']:.1%}")
        print(f"    Mean Sharpe: {r['sharpe']['mean']:.2f}")
    
    # 3. Evaluate success criteria
    print(f"\n[3/3] Evaluating success criteria...")
    
    criteria = {
        "prob_profit_above_85": {},
        "prob_dd_10_below_15": {},
        "prob_blowup_below_5": {},
        "sharpe_above_1_5": {},
    }
    
    all_pass = True
    
    for scenario_name, scenario_result in results.items():
        r = scenario_result["results"]
        
        # Criterion 1: Prob profit > 85%
        pass_prob_profit = r["prob_profit"] > 0.85
        criteria["prob_profit_above_85"][scenario_name] = {
            "value": r["prob_profit"],
            "threshold": 0.85,
            "pass": pass_prob_profit,
        }
        if not pass_prob_profit:
            all_pass = False
        
        # Criterion 2: Prob DD > 10% < 15%
        pass_dd_10 = r["prob_dd_exceeds_10"] < 0.15
        criteria["prob_dd_10_below_15"][scenario_name] = {
            "value": r["prob_dd_exceeds_10"],
            "threshold": 0.15,
            "pass": pass_dd_10,
        }
        if not pass_dd_10:
            all_pass = False
        
        # Criterion 3: Prob blowing up < 5%
        pass_blowup = r["prob_blowing_up"] < 0.05
        criteria["prob_blowup_below_5"][scenario_name] = {
            "value": r["prob_blowing_up"],
            "threshold": 0.05,
            "pass": pass_blowup,
        }
        if not pass_blowup:
            all_pass = False
        
        # Criterion 4: Sharpe > 1.5
        pass_sharpe = r["sharpe"]["mean"] > 1.5
        criteria["sharpe_above_1_5"][scenario_name] = {
            "value": r["sharpe"]["mean"],
            "threshold": 1.5,
            "pass": pass_sharpe,
        }
        if not pass_sharpe:
            all_pass = False
        
        status = "PASS" if all([
            pass_prob_profit, pass_dd_10, pass_blowup, pass_sharpe
        ]) else "FAIL"
        print(f"  {scenario_name}: {status}")
    
    # Summary
    print(f"\n{'='*70}")
    print(f" OVERALL RESULT: {'ALL CRITERIA PASSED' if all_pass else 'SOME CRITERIA FAILED'}")
    print(f"{'='*70}")
    
    # Save results
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "period": f"{start_date.date()} to {end_date.date()}",
        "initial_capital": INITIAL_CAPITAL,
        "n_sims_per_scenario": N_SIMS,
        "total_trades": len(trades),
        "scenarios": results,
        "criteria": criteria,
        "overall_pass": all_pass,
    }
    
    outfile = OUTPUT_DIR / "monte_carlo_exhaustive_v2.json"
    with open(outfile, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f"\nResults saved to: {outfile}")
    
    return output


if __name__ == "__main__":
    run_monte_carlo_exhaustive()
