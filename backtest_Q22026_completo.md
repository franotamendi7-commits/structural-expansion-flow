# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-07-16 16:03 UTC
- **Período testeado:** 2026-04-01 → 2026-07-16
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
- **Trades totales:** 151
- **Win Rate:** 47.0%
- **PnL neto total:** $93.31
- **Drawdown máximo:** $16.06 (8.3% del pico)
- **Capital final:** $193.31
- **Profit Factor:** 2.17

### Desglose de salidas
- TP2 alcanzado: 35
- TP1 parcial + SL: 36
- SL completo: 80

### Por par
- BTCUSDT: 36 trades | WR 50.0% | PnL $19.43
- ETHUSDT: 37 trades | WR 45.9% | PnL $12.99
- SOLUSDT: 15 trades | WR 26.7% | PnL $6.04
- XRPUSDT: 28 trades | WR 60.7% | PnL $27.53
- BNBUSDT: 35 trades | WR 42.9% | PnL $27.33

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
