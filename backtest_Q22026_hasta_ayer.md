# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 17:22 UTC
- **Período testeado:** 2026-04-01 → 2026-06-21
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
- **Win Rate:** 48.0%
- **PnL neto total:** $71.14
- **Drawdown máximo:** $10.92 (6.2% del pico)
- **Capital final:** $171.14
- **Profit Factor:** 2.22

### Desglose de salidas
- TP2 alcanzado: 27
- TP1 parcial + SL: 32
- SL completo: 64

### Por par
- BTCUSDT: 27 trades | WR 51.9% | PnL $11.84
- ETHUSDT: 28 trades | WR 46.4% | PnL $16.58
- SOLUSDT: 12 trades | WR 33.3% | PnL $6.08
- XRPUSDT: 25 trades | WR 60.0% | PnL $19.89
- BNBUSDT: 31 trades | WR 41.9% | PnL $16.74

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
