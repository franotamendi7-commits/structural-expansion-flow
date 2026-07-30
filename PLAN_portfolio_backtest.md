# Plan: Portfolio Backtest 5 Pairs

## Context
The existing backtests (V1 vs V2) only test BTCUSDT individually. We need to validate that the Institutional Engine V2 works as a **portfolio** across all 5 pairs simultaneously, since the live bot (`multi_bot_v2.py`) runs 5 bots in parallel. The portfolio-level drawdown, correlation, and Sharpe are critical for risk management.

## Data Available
- **Cache files** in `cache_15m/`: 15m OHLCV for all 5 pairs (18,230 bars each, Jan 1 → Jul 9, 2026)
- **BTC 1h**: cached in `cache_15m/BTCUSDT_1h.npy`
- **Missing**: 4h, 1d for all pairs; 1h for ETH, SOL, XRP, BNB; 5m for all pairs
- **Existing backtest uses**: `BinanceDataDownloader` with cache dir `backtest_results/cache/` (Parquet format)

## Per-Pair Configs (from multiasset_results.json — optimized per-pair)

| Pair | ST Mult | CI Threshold | TP Ratio | Fib Days | Min Score |
|------|---------|--------------|----------|----------|-----------|
| BTC  | 2.8     | 74.0         | 1.5      | 30       | 50        |
| ETH  | 2.6     | 72.0         | 1.8      | 30       | 50        |
| SOL  | 2.3     | 70.0         | 2.0      | 90       | 50        |
| XRP  | 2.3     | 68.0         | 1.8      | 60       | 50        |
| BNB  | 2.8     | 68.0         | 1.6      | 30       | 50        |

## Implementation Plan

### Step 1: Create `backtest_portfolio_5pairs.py`

**Architecture:**
- Reuse `BinanceDataDownloader` from existing backtest for downloading missing timeframes
- For each pair, download 15m + 1h + 4h + 1d (15m already cached as .npy; convert or re-download)
- Run a simplified V2 backtest per pair (same logic as `BacktestEngine.run_v2()`)
- Use per-pair configs from `multiasset_results.json`
- $4,450 total → $890 per pair

**Portfolio Simulation:**
- Synchronize all 5 equity curves by timestamp (15m bars)
- Portfolio equity at bar t = sum of all 5 pair equities at bar t
- Portfolio DD calculated from synchronized equity curve
- Per-pair contribution = individual PnL / total PnL

**Key metrics to compute:**
1. Portfolio-level: total return, max DD, Sharpe, Sortino, Calmar, PF, win rate
2. Per-pair: return contribution, DD contribution, trade count
3. Correlation matrix between pair equity curves
4. Maximum simultaneous DD (how many pairs in DD at once)
5. Monthly breakdown of returns

**Fees (matching live bot):**
- Maker: 0.018% per side (with BNB discount)
- Taker fallback: 0.05% per side
- Spread: 0.02%
- Slippage: 0.1 × ATR(14) × 0.5 (maker reduction)

### Step 2: Data Download Strategy

The existing BinanceDataDownloader uses a Parquet cache in `backtest_results/cache/`. The cached .npy files in `cache_15m/` are in a different format. Two options:

**Option A (simpler):** Download fresh data using BinanceDataDownloader for all 5 pairs × 4 timeframes. This reuses existing code exactly. ~20 API calls total (5 pairs × 4 timeframes, data already cached as Parquet from prior runs).

**Option B:** Load 15m from .npy cache, download only 1h/4h/1d. Faster but requires format conversion.

**Decision: Option A** — simpler, reuses existing infra, data will be cached in Parquet.

### Step 3: Backtest Loop (per pair)

For each pair:
1. Download 15m, 1h, 4h, 1d data
2. Initialize fresh `DrawdownManager` and `ParameterAdapter` (reset state)
3. Run V2 logic bar-by-bar on 15m data:
   - Check for open position management (trailing, breakeven, partial, time exit)
   - Check for new engulfing 4H signal
   - Apply CI filter, phase filter, score filter
   - Simulate entry/exit with fees
4. Record equity curve (timestamp-indexed, 15m resolution)
5. Record all trades

### Step 4: Portfolio Aggregation

After running all 5 pairs:
1. Align equity curves by timestamp (union of all timestamps)
2. Portfolio equity[t] = sum(pair_equity[t] for each pair)
3. Compute portfolio DD from aligned curve
4. Compute correlation matrix (5×5) from per-pair returns
5. Compute maximum simultaneous DD count
6. Monthly breakdown: group returns by calendar month

### Step 5: Output

Save to `backtest_results/portfolio_5pairs_results.json`:
```json
{
  "portfolio": {
    "initial_capital": 4450,
    "final_equity": ...,
    "total_return_pct": ...,
    "max_dd_pct": ...,
    "sharpe": ...,
    "sortino": ...,
    "profit_factor": ...,
    "win_rate": ...,
    "total_trades": ...,
    "calmar": ...
  },
  "per_pair": { ... },
  "correlation_matrix": [...],
  "max_simultaneous_dd": ...,
  "monthly_breakdown": { ... },
  "equity_curve": [...]
}
```

### Step 6: Run & Analyze

```bash
cd /Users/franciscootamendi/ai-agents-v3
.venv/bin/python3 backtest_portfolio_5pairs.py
```

Display results with formatted tables.

## Files to Create/Modify
- **Create**: `backtest_portfolio_5pairs.py` (new, ~500 lines)

## Risk/Bugs to Watch
1. **DD Manager state leakage**: Must call `dd_mgr.reset()` before each pair (existing backtest had this bug)
2. **Parameter Adapter state leakage**: Must call `param_adapter.reset()` similarly
3. **Different price scales**: BTC ~$100k vs XRP ~$2 — position sizing handles this via `risk_pct` and `sl_pct`
4. **Missing data for non-BTC pairs**: 1h/4h/1d must be downloaded for each pair
5. **Time alignment**: All pairs may not have identical timestamps (exchange downtime) — need forward-fill

## Verification
1. Each pair's individual results should match `multiasset_results.json` (within tolerance for state differences)
2. Portfolio return should equal sum of per-pair returns (since capital is split, no compounding across pairs)
3. Portfolio DD should be ≤ max(individual DDs) due to diversification
4. All trades should have realistic fees applied
