# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 16:55 UTC
- **Período testeado:** 2024-01-01 → 2024-03-31
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
- **Trades totales:** 155
- **Win Rate:** 48.4%
- **PnL neto total:** $193.68
- **Drawdown máximo:** $13.07 (4.4% del pico)
- **Capital final:** $293.68
- **Profit Factor:** 3.03

### Desglose de salidas
- TP2 alcanzado: 46
- TP1 parcial + SL: 29
- SL completo: 80

### Por par
- BTCUSDT: 37 trades | WR 43.2% | PnL $7.81
- ETHUSDT: 20 trades | WR 50.0% | PnL $23.74
- SOLUSDT: 76 trades | WR 50.0% | PnL $120.56
- XRPUSDT: 8 trades | WR 50.0% | PnL $16.58
- BNBUSDT: 14 trades | WR 50.0% | PnL $24.99

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
