# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-19 15:34 UTC
- **Período testeado:** 2024-07-01 → 2024-09-30
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
- **Trades totales:** 123
- **Win Rate:** 49.6%
- **PnL neto total:** $128.94
- **Drawdown máximo:** $7.31 (3.2% del pico)
- **Capital final:** $228.94
- **Profit Factor:** 3.48

### Desglose de salidas
- TP2 alcanzado: 36
- TP1 parcial + SL: 25
- SL completo: 62

### Por par
- BTCUSDT: 16 trades | WR 62.5% | PnL $12.79
- ETHUSDT: 39 trades | WR 35.9% | PnL $16.24
- SOLUSDT: 30 trades | WR 50.0% | PnL $34.27
- XRPUSDT: 16 trades | WR 56.2% | PnL $21.51
- BNBUSDT: 22 trades | WR 59.1% | PnL $44.12

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
