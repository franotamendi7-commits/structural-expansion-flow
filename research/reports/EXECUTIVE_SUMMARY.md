# EXECUTIVE SUMMARY: BACKTEST ANALYSIS

**Date:** July 28, 2026  
**Analyst:** Francisco Otamendi  
**Scope:** 442 backtests | 15 strategies | 15 assets | 3 timeframes

---

## Key Findings

### What Worked
- **EMA Crossover strategies** are the clear winners — top 6 Sharpe ratios are all EMA-based
- **Daily timeframe** significantly outperforms hourly and 4-hour for trend following
- **Blue-chip equities (JNJ, GOOGL)** are the only consistently profitable asset class
- **44 out of 442 backtests** (9.9%) met viability criteria (Sharpe >1.0, Max DD < -40%)

### What Failed
- **Forex markets** — Complete failure across 68 backtests (0% profitability on 4 of 5 pairs)
- **Hourly timeframe** — Too much noise, average Sharpe of -5.29
- **Parabolic SAR & Supertrend** — 0% viability rate, catastrophic losses
- **Mean Reversion strategies** — Only work in extremely specific conditions (JNJ only)

### The Harsh Reality
- **90.1% of tested strategies fail** to produce positive risk-adjusted returns
- Only **3 strategies are truly reliable** across multiple market conditions
- Most "profitable" backtests are likely **overfitting artifacts** — proceed with caution

---

## Top 3 Recommended Strategies

### 1. EMA_Crossover_10_50 on GOOGL (Daily)
**Status:** DEPLOY WITH CAUTION

| Metric | Value |
|--------|-------|
| Sharpe Ratio | 7.41 |
| Total Return | +97.2% |
| Max Drawdown | -22.1% |
| Profit Factor | 7.36 |
| Sortino Ratio | 10.25 |
| Win Rate | 50.0% |

**Parameters:**
- Fast EMA: 10 periods
- Slow EMA: 50 periods
- Timeframe: Daily (1d)
- Entry: Fast EMA crosses above Slow EMA
- Exit: Fast EMA crosses below Slow EMA

**Risk Management:**
- Position size: Max 2% of portfolio
- Stop loss: -15% from entry
- Take profit: +40% from entry (2.67:1 R/R)

### 2. EMA_Crossover_20_100 on JNJ (Daily)
**Status:** DEPLOY — LOWEST RISK

| Metric | Value |
|--------|-------|
| Sharpe Ratio | 7.36 |
| Total Return | +61.1% |
| Max Drawdown | -13.8% |
| Profit Factor | 7.45 |
| Sortino Ratio | 9.17 |
| Win Rate | 20.0% |

**Parameters:**
- Fast EMA: 20 periods
- Slow EMA: 100 periods
- Timeframe: Daily (1d)
- Entry: Fast EMA crosses above Slow EMA
- Exit: Fast EMA crosses below Slow EMA

**Risk Management:**
- Position size: Max 3% of portfolio (lower drawdown allows larger size)
- Stop loss: -10% from entry
- Take profit: +30% from entry (3:1 R/R)

### 3. MACD_12_26_9 on JNJ (Daily)
**Status:** DEPLOY — HIGHEST CONSISTENCY

| Metric | Value |
|--------|-------|
| Sharpe Ratio | 7.16 |
| Total Return | +45.9% |
| Max Drawdown | -8.7% |
| Profit Factor | 3.00 |
| Sortino Ratio | 8.73 |

**Parameters:**
- MACD Fast: 12 periods
- MACD Slow: 26 periods
- Signal: 9 periods
- Timeframe: Daily (1d)
- Entry: MACD crosses above Signal line
- Exit: MACD crosses below Signal line

**Risk Management:**
- Position size: Max 4% of portfolio (lowest drawdown)
- Stop loss: -8% from entry
- Take profit: +24% from entry (3:1 R/R)

---

## Risk Warnings & Caveats

### ⚠️ Critical Disclaimers

1. **Overfitting Risk:** These results are based on historical data. Past performance does NOT guarantee future results. The strategies may be curve-fitted to specific market conditions.

2. **Survivorship Bias:** GOOGL and JNJ are survivors. We didn't test delisted or failed stocks, which inflates apparent strategy performance.

3. **Transaction Costs:** These backtests may not fully account for slippage, commissions, and market impact. Real returns will be lower.

4. **Regime Change:** Strategies optimized for bull markets may fail in bear markets or sideways conditions.

5. **Sample Size:** Most strategies have only 30 backtests. Statistical significance is limited.

6. **No Risk Management:** These are raw strategy returns. Without proper position sizing and stop losses, drawdowns will be significantly worse.

### 🚨 Position Sizing Rules

- **Maximum risk per trade:** 2% of portfolio
- **Maximum portfolio exposure:** 20% in any single strategy
- **Maximum drawdown tolerance:** -25% before strategy review
- **Correlation check:** Don't deploy correlated strategies on same assets simultaneously

---

## Asset-Specific Recommendations

### ✅ DEPLOY ON
| Asset | Strategy | Rationale |
|-------|----------|-----------|
| GOOGL | EMA_Crossover_10_50 | Highest Sharpe (7.41), consistent performance |
| JNJ | EMA_Crossover_20_100 | Lowest drawdown (-13.8%), defensive stock |
| JNJ | MACD_12_26_9 | Lowest max DD (-8.7%), highest consistency |
| AAPL | Volume_Breakout_20_2.0 | Infinite profit factor on TSLA, moderate on AAPL |

### ⚠️ SELECTIVE DEPLOYMENT
| Asset | Strategy | Condition |
|-------|----------|-----------|
| GOOGL | Ichimoku_Cloud_9_26_52 | Only in trending markets |
| BTCUSDT | ROC_20_0.02 | High risk, allocate max 1% |
| XRPUSDT | ROC_20_0.02 | Highest return but -62.9% DD |

### ❌ DO NOT TRADE
| Asset | Reason |
|-------|--------|
| EURUSD | Avg Sharpe -9.95, 0% profitability |
| GBPUSD | Avg Sharpe -10.71, 0% profitability |
| USDCAD | Avg Sharpe -11.31, 0% profitability |
| USDJPY | Avg Sharpe -7.65, 0% profitability |
| AUDUSD | Avg Sharpe -8.39, 7% profitability |
| META | Avg Sharpe -3.37, 0% profitability |
| AMZN | Avg Sharpe -3.03, 13% profitability |

---

## Next Steps for Francisco

### Phase 1: Validation (Week 1-2)
1. **Run out-of-sample tests** on top 3 strategies using data from 2024-2025
2. **Paper trade** for 2 weeks to validate real-time execution
3. **Stress test** strategies on 2020 COVID crash and 2022 bear market

### Phase 2: Risk Framework (Week 3)
1. **Build position sizing model** using Kelly Criterion
2. **Implement correlation monitoring** between deployed strategies
3. **Create drawdown circuit breaker** at -20% portfolio level

### Phase 3: Deployment (Week 4+)
1. **Start with 25% allocation** to top strategy (EMA_Crossover_10_50 on GOOGL)
2. **Scale gradually** over 4 weeks to full allocation
3. **Monitor daily** for regime changes or strategy degradation

### Phase 4: Monitoring (Ongoing)
1. **Weekly performance review** against backtest expectations
2. **Monthly strategy revalidation** with rolling Sharpe calculations
3. **Quarterly strategy rotation** if Sharpe drops below 2.0

---

## Portfolio Allocation Recommendation

### Conservative (60% fixed income, 40% strategies)
| Strategy | Allocation | Asset |
|----------|------------|-------|
| EMA_Crossover_20_100 | 15% | JNJ |
| MACD_12_26_9 | 10% | JNJ |
| EMA_Crossover_10_50 | 10% | GOOGL |
| Cash Buffer | 5% | — |

### Aggressive (100% strategies)
| Strategy | Allocation | Asset |
|----------|------------|-------|
| EMA_Crossover_10_50 | 30% | GOOGL |
| EMA_Crossover_20_100 | 25% | JNJ |
| MACD_12_26_9 | 20% | JNJ |
| Volume_Breakout_20_2.0 | 15% | AAPL |
| Cash Buffer | 10% | — |

---

## Key Metrics Summary

| Metric | Value |
|--------|-------|
| Total Backtests | 442 |
| Viable Strategies | 44 (9.9%) |
| Best Sharpe | 7.41 |
| Best Return | +258.4% (high risk) |
| Lowest Max DD | -0.8% (Volume_Breakout on AAPL) |
| Most Reliable Asset | JNJ (60% profitability) |
| Most Reliable Strategy | EMA_Crossover_10_50 (16.7% viability) |
| Forex Viability | 0% (0 of 68 backtests) |

---

**Bottom Line:** Only 3 strategies are truly deployable. Focus on EMA Crossover on GOOGL and JNJ, with MACD on JNJ as a defensive complement. Avoid forex entirely. Start with paper trading before live deployment.

---

*This report is for educational and research purposes only. Not financial advice. Past performance does not guarantee future results.*
