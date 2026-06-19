# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-19 15:40 UTC
- **Período testeado:** 2025-10-01 → 2025-12-31
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
- **Trades totales:** 127
- **Win Rate:** 40.9%
- **PnL neto total:** $34.84
- **Drawdown máximo:** $11.89 (8.8% del pico)
- **Capital final:** $134.84
- **Profit Factor:** 1.49

### Desglose de salidas
- TP2 alcanzado: 32
- TP1 parcial + SL: 20
- SL completo: 75

### Por par
- BTCUSDT: 17 trades | WR 41.2% | PnL $2.18
- ETHUSDT: 35 trades | WR 48.6% | PnL $16.90
- SOLUSDT: 25 trades | WR 24.0% | PnL $-4.02
- XRPUSDT: 17 trades | WR 29.4% | PnL $2.02
- BNBUSDT: 33 trades | WR 51.5% | PnL $17.76

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
