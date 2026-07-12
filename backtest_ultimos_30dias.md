# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 18:06 UTC
- **Período testeado:** 2026-05-22 → 2026-06-22
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
- **Trades totales:** 43
- **Win Rate:** 46.5%
- **PnL neto total:** $8.91
- **Drawdown máximo:** $12.94 (11.6% del pico)
- **Capital final:** $108.91
- **Profit Factor:** 1.44

### Desglose de salidas
- TP2 alcanzado: 8
- TP1 parcial + SL: 12
- SL completo: 23

### Por par
- BTCUSDT: 7 trades | WR 71.4% | PnL $2.98
- ETHUSDT: 7 trades | WR 14.3% | PnL $-7.10
- SOLUSDT: 8 trades | WR 25.0% | PnL $-2.12
- XRPUSDT: 5 trades | WR 60.0% | PnL $-0.35
- BNBUSDT: 16 trades | WR 56.2% | PnL $15.50

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
