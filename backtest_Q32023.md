# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 16:44 UTC
- **Período testeado:** 2023-07-01 → 2023-09-30
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
- **Trades totales:** 188
- **Win Rate:** 36.2%
- **PnL neto total:** $33.14
- **Drawdown máximo:** $20.43 (14.8% del pico)
- **Capital final:** $133.14
- **Profit Factor:** 1.34

### Desglose de salidas
- TP2 alcanzado: 38
- TP1 parcial + SL: 30
- SL completo: 120

### Por par
- BTCUSDT: 52 trades | WR 34.6% | PnL $3.02
- ETHUSDT: 47 trades | WR 21.3% | PnL $-11.17
- SOLUSDT: 30 trades | WR 46.7% | PnL $18.66
- XRPUSDT: 22 trades | WR 45.5% | PnL $16.17
- BNBUSDT: 37 trades | WR 43.2% | PnL $6.45

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
