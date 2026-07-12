# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 16:38 UTC
- **Período testeado:** 2023-04-01 → 2023-06-30
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
- **Trades totales:** 159
- **Win Rate:** 43.4%
- **PnL neto total:** $67.85
- **Drawdown máximo:** $12.07 (7.0% del pico)
- **Capital final:** $167.85
- **Profit Factor:** 1.87

### Desglose de salidas
- TP2 alcanzado: 29
- TP1 parcial + SL: 40
- SL completo: 90

### Por par
- BTCUSDT: 36 trades | WR 30.6% | PnL $-8.59
- ETHUSDT: 36 trades | WR 52.8% | PnL $27.22
- SOLUSDT: 35 trades | WR 42.9% | PnL $17.48
- XRPUSDT: 15 trades | WR 46.7% | PnL $10.65
- BNBUSDT: 37 trades | WR 45.9% | PnL $21.09

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
