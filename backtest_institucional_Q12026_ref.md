# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-19 15:45 UTC
- **Período testeado:** 2026-03-19 → 2026-06-17
- **Pares:** BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT
- **Capital inicial:** $100.00
- **Riesgo fijo por operación:** 1.0%
- **Costos modelados:**
  - Comisión taker: 0.050%
  - Spread fijo: 0.020%
  - Slippage: 0.1 × ATR(5m, 14) en entrada y stops
- **Look-ahead:** Se usa precio de cierre de vela 1h (mejorable a precio de apertura siguiente)
- **Gestión de salida:** TP1 parcial 60%, breakeven al 50% del TP1, TP2 restante

## Resultados
- **Trades totales:** 130
- **Win Rate:** 50.8%
- **PnL neto total:** $105.91
- **Drawdown máximo:** $10.69 (5.2% del pico)
- **Capital final:** $205.91
- **Profit Factor:** 2.92

### Desglose de salidas
- TP2 alcanzado: 31
- TP1 parcial + SL: 32
- SL completo: 67

### Por par
- BTCUSDT: 23 trades | WR 47.8% | PnL $9.26
- ETHUSDT: 36 trades | WR 41.7% | PnL $16.52
- SOLUSDT: 15 trades | WR 40.0% | PnL $12.85
- XRPUSDT: 24 trades | WR 62.5% | PnL $23.40
- BNBUSDT: 32 trades | WR 59.4% | PnL $43.88

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
