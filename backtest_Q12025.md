# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 17:07 UTC
- **Período testeado:** 2025-01-01 → 2025-03-31
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
- **Trades totales:** 147
- **Win Rate:** 44.2%
- **PnL neto total:** $181.46
- **Drawdown máximo:** $10.97 (3.9% del pico)
- **Capital final:** $281.46
- **Profit Factor:** 3.51

### Desglose de salidas
- TP2 alcanzado: 40
- TP1 parcial + SL: 25
- SL completo: 82

### Por par
- BTCUSDT: 36 trades | WR 36.1% | PnL $13.46
- ETHUSDT: 36 trades | WR 52.8% | PnL $52.56
- SOLUSDT: 29 trades | WR 37.9% | PnL $27.66
- XRPUSDT: 28 trades | WR 39.3% | PnL $41.37
- BNBUSDT: 18 trades | WR 61.1% | PnL $46.42

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
