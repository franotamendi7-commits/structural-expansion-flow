# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 17:12 UTC
- **Período testeado:** 2025-07-01 → 2025-09-30
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
- **Trades totales:** 102
- **Win Rate:** 46.1%
- **PnL neto total:** $89.37
- **Drawdown máximo:** $11.76 (6.0% del pico)
- **Capital final:** $189.37
- **Profit Factor:** 2.18

### Desglose de salidas
- TP2 alcanzado: 36
- TP1 parcial + SL: 11
- SL completo: 55

### Por par
- BTCUSDT: 23 trades | WR 56.5% | PnL $13.76
- ETHUSDT: 21 trades | WR 47.6% | PnL $16.26
- SOLUSDT: 21 trades | WR 38.1% | PnL $16.86
- XRPUSDT: 17 trades | WR 47.1% | PnL $31.60
- BNBUSDT: 20 trades | WR 40.0% | PnL $10.89

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
