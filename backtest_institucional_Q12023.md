# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-19 15:28 UTC
- **Período testeado:** 2023-01-01 → 2023-03-31
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
- **Trades totales:** 135
- **Win Rate:** 41.5%
- **PnL neto total:** $43.75
- **Drawdown máximo:** $12.03 (8.4% del pico)
- **Capital final:** $143.74
- **Profit Factor:** 1.70

### Desglose de salidas
- TP2 alcanzado: 25
- TP1 parcial + SL: 31
- SL completo: 79

### Por par
- BTCUSDT: 43 trades | WR 37.2% | PnL $2.54
- ETHUSDT: 28 trades | WR 35.7% | PnL $4.57
- SOLUSDT: 15 trades | WR 66.7% | PnL $13.39
- XRPUSDT: 22 trades | WR 31.8% | PnL $8.18
- BNBUSDT: 27 trades | WR 48.1% | PnL $15.06

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
