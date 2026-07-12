# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 17:18 UTC
- **Período testeado:** 2026-01-01 → 2026-03-31
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
- **Trades totales:** 115
- **Win Rate:** 47.0%
- **PnL neto total:** $128.70
- **Drawdown máximo:** $10.41 (4.6% del pico)
- **Capital final:** $228.70
- **Profit Factor:** 3.36

### Desglose de salidas
- TP2 alcanzado: 38
- TP1 parcial + SL: 16
- SL completo: 61

### Por par
- BTCUSDT: 36 trades | WR 36.1% | PnL $8.14
- ETHUSDT: 16 trades | WR 43.8% | PnL $3.82
- SOLUSDT: 17 trades | WR 58.8% | PnL $28.90
- XRPUSDT: 16 trades | WR 56.2% | PnL $37.94
- BNBUSDT: 30 trades | WR 50.0% | PnL $49.90

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
