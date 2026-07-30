# Session Summary — Institutional Engine V2 (Jul 29, 2026)

## What We Did
Fixed 4 critical bugs in the V2 backtest that caused 0→10→79 trades.

## Bugs Fixed

### 1. Drawdown Manager loads stale live state
**File**: `engine/drawdown_manager.py`
**Problem**: `__init__()` loads `drawdown_state.json` from live trading (5.47% DD), requiring score ≥ 90. V2 scores max at 83 → ALL signals blocked.
**Fix**: Call `dd_mgr.reset(equity)` in `run_v2()` after construction.

### 2. TP1 partial exit orphans remaining position
**File**: `backtest_institutional_v2.py` (line ~674-703)
**Problem**: `_manage_position_v2()` returned a result dict when TP1 hit → `open_pos = None` → new position opens immediately, orphaning the remaining 50%.
**Fix**: TP1 partial now modifies position in-place (tp1_hit=True, breakeven, trailing, reduce size) and returns None. Partial PnL accumulates in `total_realized`.

### 3. Parameter Adapter loads stale live state
**File**: `engine/parameter_adapter.py`
**Problem**: `__init__()` loads `parameter_state.json` from live trading.
**Fix**: Call `param_adapter.reset()` in `run_v2()` after construction.

### 4. DD thresholds impossible to achieve
**File**: `engine/drawdown_manager.py`
**Problem**: REDUCIDO mode required score ≥ 85, but V2 scoring system maxed at 83. Death spiral: once DD hit 3%, could never trade again.
**Fix**: Lowered thresholds: REDUCIDO=65, MÍNIMO=75, SUPERVIVENCIA=85.

## Final Results

### V1 vs V2 (Jan-Jul 2026, BTCUSDT)

| Metric | V1 | V2 | Winner |
|--------|-----|-----|--------|
| Trades | 280 | 79 | V2 (selective) |
| WR | 70.0% | 64.6% | V1 |
| PF | 2.06 | 1.88 | V1 |
| Return | +270.8% | +25.48% | V1 |
| **Max DD** | **-21.77%** | **-5.83%** | **V2** ✅ |
| Sharpe | 7.89 | 2.73 | V1 |
| Calmar | 12.44 | 4.37 | V1 |
| Monte Carlo Mean DD | -11.07% | -5.04% | V2 |

### Key Insight
V1 has higher returns but FAILS the DD < 10% requirement (-21.77%). V2 MEETS it (-5.83%) while still being profitable (+25.48%).

## Files Modified
- `backtest_institutional_v2.py` — TP1 fix, DD/adapter reset, score preservation
- `engine/drawdown_manager.py` — Reset instructions, lowered DD thresholds
- `docs/REPORT_INSTITUTIONAL_V2.md` — Updated with real results

## Next Steps
1. Paper trading on testnet with V2
2. Monitor metrics live for 2 weeks
3. Integrate with multi_bot.py to replace VWAP
4. Consider increasing V2 position count (currently max 1 at a time)
