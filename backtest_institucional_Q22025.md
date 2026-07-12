# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 16:26 UTC
- **Período testeado:** 2025-04-01 → 2025-06-30
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
- **Trades totales:** 163
- **Win Rate:** 37.4%
- **PnL neto total:** $19.29
- **Drawdown máximo:** $23.31 (17.3% del pico)
- **Capital final:** $119.29
- **Profit Factor:** 1.20

### Desglose de salidas
- TP2 alcanzado: 39
- TP1 parcial + SL: 22
- SL completo: 102

### Por par
- BTCUSDT: 49 trades | WR 38.8% | PnL $3.74
- ETHUSDT: 38 trades | WR 18.4% | PnL $-17.70
- SOLUSDT: 19 trades | WR 52.6% | PnL $15.15
- XRPUSDT: 22 trades | WR 40.9% | PnL $7.61
- BNBUSDT: 35 trades | WR 45.7% | PnL $10.50

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
