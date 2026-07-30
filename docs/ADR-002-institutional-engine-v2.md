# ADR-002: Institutional Engine V2

**Status:** Accepted
**Date:** 2026-07-29
**Deciders:** Francisco Otamendi, Trading System

## Context

The current institutional engine (V1) generates +11.40% return in 6 months with PF 2.17 on BTCUSDT. However, it has critical gaps:

1. **No position management**: V1 only generates entry signals, no trailing stop, breakeven, or partial exits
2. **All-or-nothing exits**: TP/SL only, leading to missed profits on winning trades
3. **Dead code**: ~150 lines of unused classes (PhaseTransitionDetector, SessionFilter)
4. **Overfitting risk**: 48 tunable parameters, score starts at 70 (almost everything passes)
5. **Hardcoded agent_scores**: Not dynamic

Failed experiments (DO NOT repeat):
- VWAP Breakout: Overfitting, loses money in real
- ML Filter: Damages profitability, rejects good trades
- Simplified Exits: -43.20%

## Decision

Create Institutional Engine V2 with:

### Position Management (NEW)
- **Trailing stop**: ATR-based (2x ATR), activates after price moves in favor
- **Breakeven**: Auto-move SL to entry + spread when TP1 is hit
- **Partial exits**: Close 50% at TP1, let 50% run with trailing
- **Time-based exit**: Close if no TP1 in 24 bars (4 days max)
- **Re-entry logic**: Evaluate re-entry after trailing closes remainder

### Self-Governance (NEW)
- **Parameter adaptation**: Rolling 50-trade analysis every 50 trades
  - WR < 35%: Increase filters (CI +5, score +10)
  - WR > 55%: Relax filters slightly (CI -3, score -5)
  - PF < 1.0 in 20 trades: Pause configuration
- **Strategy weight**: 3+ consecutive losses -> risk x 0.5
- **Volatility regime**: Detect ATR expansion/compression, adjust parameters

### Drawdown Manager (NEW)
- DD < 3%: Normal (1.0x risk)
- DD 3-5%: Reduced (0.75x, score > 85)
- DD 5-7%: Minimum (0.5x, score > 90)
- DD 7-9%: Survival (0.25x, score > 95)
- DD > 9%: TOTAL STOP (resume when DD < 5%)

### Cleanup
- Remove ~150 lines of dead code
- Reduce parameters from 48 to ~20
- Score base reduced from 70 to 50 (more selective)

## Architecture

```
institutional_engine_v2.py  (Signal generation + scoring)
        |
        v
position_manager.py  (Trailing, breakeven, partial exits, time exit)
        |
        v
drawdown_manager.py  (5-level DD protection)
        |
        v
parameter_adapter.py  (Self-governance, rolling optimization)
```

## Consequences

### Positive
- Expected improvement: +3-8% additional return (trailing captures more of winners)
- Max DD reduction: trailing + DD manager should keep DD < 7%
- Self-healing: parameter adaptation corrects degraded performance
- Production-ready: logging, persistence, error handling

### Negative
- More complex than V1 (4 modules vs 1)
- Parameter adaptation adds a layer of indirection
- Time-based exit may close trades prematurely in strong trends

### Risks
- Trailing stop too tight: premature exit on normal pullbacks
- Parameter adaptation over-optimizing: mitigated by gradual changes (small steps)
- DD manager too aggressive: may miss good trades during recovery

## Alternatives Considered

1. **Keep V1 as-is**: Already profitable, but leaves money on table
2. **ML filter**: Proven to damage profitability (rejected)
3. **VWAP integration**: Overfitting confirmed (rejected)
4. **Grid trading**: Not suitable for BTC trending behavior

## Validation

- Backtest period: Jan-Jul 2026 (6 months)
- Costs: 0.05% commission + 0.02% spread + 0.1x ATR slippage
- Monte Carlo: 5000 simulations for robustness verification
- No look-ahead bias: only closed bars used for decisions
