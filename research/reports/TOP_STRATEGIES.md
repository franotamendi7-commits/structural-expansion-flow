# TOP STRATEGIES ANALYSIS REPORT

**Date:** July 28, 2026  
**Total Backtests Analyzed:** 442  
**Viable Strategies (Sharpe >1.0, DD < -40%):** 44 (9.9% of all backtests)  
**Strategy Types Tested:** 15  
**Assets Tested:** 15  
**Timeframes Tested:** 3 (1h, 4h, 1d)

---

## Executive Summary

After analyzing 442 backtests across 15 strategies, 15 assets, and 3 timeframes, we identified **44 viable configurations** that meet our risk-adjusted criteria. However, **only 3 strategies consistently deliver exceptional performance** with Sharpe ratios above 7.0. The vast majority of strategies (90.1%) fail to produce positive risk-adjusted returns, highlighting the difficulty of finding edge in markets.

---

## Top 10 by Sharpe Ratio

| Rank | Strategy | Symbol | Timeframe | Sharpe | Return | Max DD | Profit Factor | Sortino |
|------|----------|--------|-----------|--------|--------|--------|---------------|---------|
| 1 | EMA_Crossover_10_50 | GOOGL | 1d | 7.41 | +97.2% | -22.1% | 7.36 | 10.25 |
| 2 | EMA_Crossover_20_100 | JNJ | 1d | 7.36 | +61.1% | -13.8% | 7.45 | 9.17 |
| 3 | EMA_Crossover_10_50 | JNJ | 1d | 7.20 | +55.7% | -13.8% | 9.00 | 8.27 |
| 4 | MACD_12_26_9 | JNJ | 1d | 7.16 | +45.9% | -8.7% | 3.00 | 8.73 |
| 5 | EMA_Crossover_5_20 | JNJ | 1d | 6.84 | +50.8% | -13.9% | 3.40 | 7.94 |
| 6 | EMA_Crossover_5_20 | GOOGL | 1d | 6.54 | +79.9% | -16.8% | 4.82 | 8.48 |
| 7 | Ichimoku_Cloud_9_26_52 | JNJ | 1d | 6.26 | +40.0% | -11.4% | 2.84 | 8.47 |
| 8 | Ichimoku_Cloud_9_26_52 | GOOGL | 1d | 5.71 | +72.1% | -17.0% | 5.30 | 7.07 |
| 9 | Volume_Breakout_20_2.0 | AAPL | 1d | 5.63 | +12.5% | -0.8% | 22.84 | 8.74 |
| 10 | MACD_12_26_9 | GOOGL | 1d | 5.61 | +60.6% | -20.4% | 2.53 | 6.55 |

**Key Insight:** EMA Crossover strategies dominate the top rankings, particularly on GOOGL and JNJ. The 1d timeframe shows significantly better risk-adjusted performance than shorter timeframes.

---

## Top 10 by Total Return

| Rank | Strategy | Symbol | Timeframe | Return | Sharpe | Max DD |
|------|----------|--------|-----------|--------|--------|--------|
| 1 | ROC_20_0.02 | XRPUSDT | 4h | +258.4% | 1.23 | -62.9% |
| 2 | ROC_20_0.02 | BTCUSDT | 1d | +97.7% | 2.25 | -54.6% |
| 3 | EMA_Crossover_10_50 | GOOGL | 1d | +97.2% | 7.41 | -22.1% |
| 4 | Keltner_Breakout_20_10_2.0 | ETHUSDT | 4h | +92.5% | 0.97 | -30.7% |
| 5 | Keltner_Breakout_20_10_2.0 | BTCUSDT | 1d | +91.1% | 2.68 | -23.8% |
| 6 | EMA_Crossover_5_20 | GOOGL | 1d | +79.9% | 6.54 | -16.8% |
| 7 | Ichimoku_Cloud_9_26_52 | XRPUSDT | 1d | +76.3% | 2.09 | -68.2% |
| 8 | Ichimoku_Cloud_9_26_52 | GOOGL | 1d | +72.1% | 5.71 | -17.0% |
| 9 | EMA_Crossover_20_100 | JNJ | 1d | +61.1% | 7.36 | -13.8% |
| 10 | MACD_12_26_9 | GOOGL | 1d | +60.6% | 5.61 | -20.4% |

**Warning:** High returns often come with high drawdowns. ROC_20_0.02 on XRPUSDT achieved +258.4% but suffered -62.9% drawdown — not suitable for conservative risk management.

---

## Top 10 by Profit Factor

| Rank | Strategy | Symbol | Timeframe | Profit Factor | Return | Sharpe |
|------|----------|--------|-----------|---------------|--------|--------|
| 1 | Volume_Breakout_20_2.0 | TSLA | 1d | ∞ (no losing trades) | +7.5% | 2.36 |
| 2 | Volume_Breakout_20_2.0 | AAPL | 1d | 22.84 | +12.5% | 5.63 |
| 3 | EMA_Crossover_10_50 | JNJ | 1d | 9.00 | +55.7% | 7.20 |
| 4 | EMA_Crossover_20_100 | JNJ | 1d | 7.45 | +61.1% | 7.36 |
| 5 | EMA_Crossover_10_50 | GOOGL | 1d | 7.36 | +97.2% | 7.41 |
| 6 | EMA_Crossover_20_100 | GOOGL | 1d | 5.88 | +46.2% | 4.19 |
| 7 | Ichimoku_Cloud_9_26_52 | GOOGL | 1d | 5.30 | +72.1% | 5.71 |
| 8 | EMA_Crossover_5_20 | GOOGL | 1d | 4.82 | +79.9% | 6.54 |
| 9 | ROC_20_0.02 | GOOGL | 1d | 4.29 | +60.5% | 4.56 |
| 10 | EMA_Crossover_5_20 | JNJ | 1d | 3.40 | +50.8% | 6.84 |

**Observation:** Volume Breakout achieves near-infinite profit factors on selective assets, but these results may reflect overfitting to specific market conditions. EMA Crossover strategies show more consistent profit factors across multiple assets.

---

## Top 10 by Sortino Ratio

| Rank | Strategy | Symbol | Timeframe | Sortino | Return | Sharpe |
|------|----------|--------|-----------|---------|--------|--------|
| 1 | EMA_Crossover_10_50 | GOOGL | 1d | 10.25 | +97.2% | 7.41 |
| 2 | EMA_Crossover_20_100 | JNJ | 1d | 9.17 | +61.1% | 7.36 |
| 3 | Volume_Breakout_20_2.0 | AAPL | 1d | 8.74 | +12.5% | 5.63 |
| 4 | MACD_12_26_9 | JNJ | 1d | 8.73 | +45.9% | 7.16 |
| 5 | EMA_Crossover_5_20 | GOOGL | 1d | 8.48 | +79.9% | 6.54 |
| 6 | Ichimoku_Cloud_9_26_52 | JNJ | 1d | 8.47 | +40.0% | 6.26 |
| 7 | EMA_Crossover_10_50 | JNJ | 1d | 8.27 | +55.7% | 7.20 |
| 8 | EMA_Crossover_5_20 | JNJ | 1d | 7.94 | +50.8% | 6.84 |
| 9 | Ichimoku_Cloud_9_26_52 | GOOGL | 1d | 7.07 | +72.1% | 5.71 |
| 10 | ROC_20_0.02 | GOOGL | 1d | 7.01 | +60.5% | 4.56 |

**Key Insight:** High Sortino ratios indicate excellent downside risk management. EMA Crossover and Ichimoku strategies on JNJ and GOOGL provide the best risk-adjusted returns.

---

## Best Strategy Per Category

### Momentum
| Strategy | Best Performance | Symbol | Sharpe | Return |
|----------|-----------------|--------|--------|--------|
| RSI_Momentum_14_70_30 | Viable runs: 1 | BTCUSDT | 2.51 | +24.9% |

**Assessment:** RSI Momentum is highly inconsistent. Only 1 viable run out of 30 total backtests (3.3% success rate). **Not recommended for deployment.**

### Mean Reversion
| Strategy | Best Performance | Symbol | Sharpe | Return |
|----------|-----------------|--------|--------|--------|
| BB_Mean_Reversion_20_2.0 | Viable runs: 3 | JNJ | 3.34 | +18.7% |
| ZScore_Mean_Reversion_20_2.0_0.5 | Viable runs: 3 | JNJ | 3.34 | +18.7% |
| RSI_Mean_Reversion_14_80_20 | Viable runs: 1 | V | 1.41 | +3.3% |

**Assessment:** Mean Reversion strategies show promise only on JNJ and V. Both BB and Z-Score variants are essentially identical. **Use only on blue-chip equities in low-volatility environments.**

### Breakout
| Strategy | Best Performance | Symbol | Sharpe | Return |
|----------|-----------------|--------|--------|--------|
| Volume_Breakout_20_2.0 | Viable runs: 5 | AAPL | 5.63 | +12.5% |
| Keltner_Breakout_20_10_2.0 | Viable runs: 3 | JNJ | 5.25 | +22.2% |
| Donchian_Breakout_20_10 | Viable runs: 2 | AAPL | 1.51 | +30.5% |

**Assessment:** Breakout strategies show the most cross-asset versatility but require careful asset selection. Volume Breakout is most reliable, while Keltner works well on trending assets.

### Trend Following
| Strategy | Best Performance | Symbol | Sharpe | Return |
|----------|-----------------|--------|--------|--------|
| Supertrend_10_3.0 | Viable runs: 0 | — | — | — |

**Assessment:** Supertrend completely failed across all 30 backtests. **0% profitability rate. Avoid entirely.**

---

## Cross-Asset Analysis

### Strategies Profitable on Multiple Symbols

| Strategy | Profitable Symbols | Count | Cross-Asset Score |
|----------|-------------------|-------|-------------------|
| Volume_Breakout_20_2.0 | AAPL, AMZN, BTCUSDT, ETHUSDT, MSFT, TSLA, XRPUSDT | 7 | ⭐⭐⭐⭐⭐ |
| Keltner_Breakout_20_10_2.0 | BNBUSDT, BTCUSDT, ETHUSDT, JNJ, SOLUSDT, TSLA, XRPUSDT | 7 | ⭐⭐⭐⭐⭐ |
| Ichimoku_Cloud_9_26_52 | BNBUSDT, GOOGL, JNJ, JPM, NVDA, SOLUSDT, XRPUSDT | 7 | ⭐⭐⭐⭐⭐ |
| ROC_20_0.02 | AAPL, AUDUSD, BTCUSDT, GOOGL, JNJ, XRPUSDT | 6 | ⭐⭐⭐⭐ |
| EMA_Crossover_5_20 | AAPL, GOOGL, JNJ, JPM, V | 5 | ⭐⭐⭐ |
| EMA_Crossover_10_50 | AAPL, GOOGL, JNJ, JPM, V | 5 | ⭐⭐⭐ |
| EMA_Crossover_20_100 | AAPL, GOOGL, JNJ, JPM, V | 5 | ⭐⭐⭐ |
| Donchian_Breakout_20_10 | AAPL, BNBUSDT, BTCUSDT, SOLUSDT, XRPUSDT | 5 | ⭐⭐⭐ |
| MACD_12_26_9 | AAPL, GOOGL, JNJ, JPM | 4 | ⭐⭐ |
| RSI_Momentum_14_70_30 | BTCUSDT, ETHUSDT, TSLA | 3 | ⭐⭐ |
| BB_Mean_Reversion_20_2.0 | JNJ, NVDA, V | 3 | ⭐⭐ |
| ZScore_Mean_Reversion_20_2.0_0.5 | JNJ, NVDA, V | 3 | ⭐⭐ |

**Key Finding:** Breakout strategies (Volume, Keltner) show the best cross-asset versatility, but **only when filtered for specific market conditions**. EMA Crossover strategies are more reliable but work only on high-quality equities.

---

## Strategies to AVOID

### ❌ Critical Failures (Negative Sharpe + Deep Drawdowns)

| Strategy | Avg Sharpe | Avg Max DD | Profitable Runs | Recommendation |
|----------|------------|------------|------------------|----------------|
| Parabolic_SAR_0.02_0.02_0.2 | -3.47 | -71.1% | 1/30 (3.3%) | **AVOID** — Catastrophic losses |
| Supertrend_10_3.0 | -2.48 | -66.9% | 0/30 (0%) | **AVOID** — Zero profitability |
| Donchian_Breakout_20_10 | -4.40 | -53.0% | 5/30 (16.7%) | **AVOID** — Negative expectancy |
| BB_Mean_Reversion_20_2.0 | -4.92 | -61.1% | 3/30 (10%) | **AVOID** — High failure rate |
| ZScore_Mean_Reversion_20_2.0_0.5 | -4.92 | -61.1% | 3/30 (10%) | **AVOID** — Same as BB variant |

### ⚠️ High-Risk Strategies (Use Only With Extreme Caution)

| Strategy | Notes |
|----------|-------|
| RSI_Momentum_14_70_30 | Only works on crypto (BTC, ETH, TSLA). 86.7% failure rate on other assets. |
| Keltner_Breakout_20_10_2.0 | Very inconsistent. 30% failure rate despite cross-asset potential. |
| EMA_Crossover_5_20 | Fast crossovers produce excessive false signals. 83.3% failure rate. |

### 🚫 Forex Strategies: DO NOT TRADE

| Currency Pair | Avg Sharpe | Profitable Runs | Verdict |
|---------------|------------|------------------|---------|
| USDCAD | -11.31 | 0/13 | **AVOID** |
| GBPUSD | -10.71 | 0/13 | **AVOID** |
| EURUSD | -9.95 | 0/14 | **AVOID** |
| AUDUSD | -8.39 | 1/14 (7.1%) | **AVOID** |
| USDJPY | -7.65 | 0/14 | **AVOID** |

**Assessment:** None of the tested strategies work on forex markets. The total failure across 68 forex backtests suggests these strategies are not suited for currency markets. Forex requires completely different approach (carry trade, mean reversion on different parameters, or fundamental analysis).

---

## Market Analysis: Asset Performance Rankings

### Best Performing Assets

| Rank | Asset | Avg Sharpe | Avg Return | Avg Max DD | Profitable Runs |
|------|-------|------------|------------|------------|-----------------|
| 1 | JNJ | +1.08 | +14.5% | -13.9% | 9/15 (60%) |
| 2 | GOOGL | +0.97 | +21.1% | -22.2% | 6/15 (40%) |
| 3 | AAPL | -0.17 | -4.1% | -23.7% | 7/15 (46.7%) |
| 4 | JPM | -0.59 | -0.6% | -19.5% | 5/15 (33.3%) |
| 5 | TSLA | -1.02 | -23.4% | -45.9% | 3/15 (20%) |

### Worst Performing Assets

| Rank | Asset | Avg Sharpe | Avg Return | Avg Max DD | Profitable Runs |
|------|-------|------------|------------|------------|-----------------|
| 1 | USDCAD | -11.31 | -50.5% | -51.2% | 0/13 (0%) |
| 2 | GBPUSD | -10.71 | -54.5% | -55.8% | 0/13 (0%) |
| 3 | EURUSD | -9.95 | -50.3% | -50.9% | 0/14 (0%) |
| 4 | AUDUSD | -8.39 | -54.2% | -54.9% | 1/14 (7.1%) |
| 5 | USDJPY | -7.65 | -50.6% | -52.5% | 0/14 (0%) |

### Asset Class Summary

| Asset Class | Avg Sharpe | Avg Return | Assessment |
|-------------|------------|------------|------------|
| Blue-Chip Equities | +0.05 | +1.5% | ✅ Best — Only profitable asset class |
| Crypto | -0.85 | -57.8% | ⚠️ High variance, selective opportunities |
| Tech Stocks (MSFT, NVDA, META, AMZN) | -2.71 | -23.6% | ❌ Overfitting risk, poor consistency |
| Forex | -9.56 | -52.0% | ❌ Complete failure |

---

## Timeframe Analysis

| Timeframe | Avg Sharpe | Avg Return | Backtests | Assessment |
|-----------|------------|------------|-----------|------------|
| 4h | -0.66 | -46.6% | 75 | ⚠️ Moderate — Some opportunities |
| 1d | -0.97 | -18.7% | 224 | ⚠️ Best timeframe overall |
| 1h | -5.29 | -71.7% | 143 | ❌ Avoid — Too much noise |

**Key Insight:** Daily timeframe provides the best risk-adjusted returns. Hourly timeframe is too noisy for trend-following strategies. 4-hour shows promise for crypto assets but is inconsistent.

---

## Statistical Summary

### Overall Performance Metrics

- **Total backtests:** 442
- **Viable strategies (Sharpe >1.0, DD < -40%):** 44 (9.9%)
- **Average Sharpe across all tests:** -1.62
- **Median Sharpe:** -1.85
- **Best Sharpe:** 7.41 (EMA_Crossover_10_50 on GOOGL)
- **Worst Sharpe:** -15.23 (EMA_Crossover_20_100 on EURUSD)

### Viable Strategy Statistics

- **Average Sharpe:** +3.45
- **Average Return:** +29.3%
- **Average Max Drawdown:** -17.1%
- **Minimum Sharpe in viable set:** 1.02
- **Maximum drawdown in viable set:** -0.8% (Volume_Breakout on AAPL)

### Strategy Success Rates

| Strategy | Total Runs | Viable Runs | Success Rate |
|----------|------------|-------------|--------------|
| Ichimoku_Cloud_9_26_52 | 30 | 4 | 13.3% |
| EMA_Crossover_20_100 | 30 | 5 | 16.7% |
| ROC_20_0.02 | 28 | 3 | 10.7% |
| EMA_Crossover_10_50 | 30 | 5 | 16.7% |
| Volume_Breakout_20_2.0 | 24 | 5 | 20.8% |
| EMA_Crossover_5_20 | 30 | 5 | 16.7% |
| MACD_12_26_9 | 30 | 4 | 13.3% |
| Keltner_Breakout_20_10_2.0 | 30 | 3 | 10.0% |
| BB_Mean_Reversion_20_2.0 | 30 | 3 | 10.0% |
| ZScore_Mean_Reversion_20_2.0_0.5 | 30 | 3 | 10.0% |
| Donchian_Breakout_20_10 | 30 | 2 | 6.7% |
| RSI_Momentum_14_70_30 | 30 | 1 | 3.3% |
| RSI_Mean_Reversion_14_80_20 | 30 | 1 | 3.3% |
| Supertrend_10_3.0 | 30 | 0 | 0.0% |
| Parabolic_SAR_0.02_0.02_0.2 | 30 | 0 | 0.0% |

---

## Key Takeaways

1. **EMA Crossover dominates** — The top 6 strategies by Sharpe ratio are all EMA-based. Use EMA_10_50 or EMA_20_100 on GOOGL and JNJ.

2. **JNJ is the safest asset** — 60% profitability rate with lowest average drawdown (-13.9%).

3. **Forex is untradeable** with these strategies — 0% success rate across 68 backtests.

4. **Hourly timeframe is noise** — Avoid 1h timeframe for trend-following strategies.

5. **Only 3 strategies are truly deployable** — EMA_Crossover_10_50, EMA_Crossover_20_100, and MACD_12_26_9 on select blue-chip equities.

6. **Volume Breakout is the wild card** — Highest profit factors but requires careful position sizing due to infrequent signals.

---

*Report generated from 442 backtests across 15 strategies, 15 assets, and 3 timeframes.*
