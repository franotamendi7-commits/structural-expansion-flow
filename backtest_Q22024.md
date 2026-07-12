# Auditoría Backtest Institucional V1
- **Fecha de ejecución:** 2026-06-22 17:01 UTC
- **Período testeado:** 2024-04-01 → 2024-06-30
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
- **Trades totales:** 114
- **Win Rate:** 43.9%
- **PnL neto total:** $73.40
- **Drawdown máximo:** $24.68 (14.2% del pico)
- **Capital final:** $173.40
- **Profit Factor:** 2.36

### Desglose de salidas
- TP2 alcanzado: 26
- TP1 parcial + SL: 24
- SL completo: 64

### Por par
- BTCUSDT: 26 trades | WR 53.8% | PnL $11.35
- ETHUSDT: 29 trades | WR 31.0% | PnL $25.03
- SOLUSDT: 35 trades | WR 22.9% | PnL $-10.56
- XRPUSDT: 9 trades | WR 88.9% | PnL $20.68
- BNBUSDT: 15 trades | WR 73.3% | PnL $26.90

## Features registradas
- fib_low_key, fib_high_key, fib_width, phase_h1, ci_value, wr_5m, wr_15m, st_aligned, st_bias_bullish, st_bias_bearish, mom_score, mom_direction, vol_ratio_5m, body_ratio_4h, hour_of_day, direction_long

## Conclusión preliminar
_(Completar después del análisis out‑of‑sample)_
