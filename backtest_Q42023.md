# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 16:50 UTC
- **Período testeado:** 2023-10-01 → 2023-12-31
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
- **Trades totales:** 145
- **Win Rate:** 48.3%
- **PnL neto total:** $144.43
- **Drawdown máximo:** $9.54 (3.9% del pico)
- **Capital final:** $244.43
- **Profit Factor:** 2.92

### Desglose de salidas
- TP2 alcanzado: 42
- TP1 parcial + SL: 28
- SL completo: 75

### Por par
- BTCUSDT: 30 trades | WR 43.3% | PnL $11.47
- ETHUSDT: 25 trades | WR 52.0% | PnL $21.32
- SOLUSDT: 51 trades | WR 43.1% | PnL $56.65
- XRPUSDT: 16 trades | WR 43.8% | PnL $11.72
- BNBUSDT: 23 trades | WR 65.2% | PnL $43.27

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
