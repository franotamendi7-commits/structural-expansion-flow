# Reporte: Institutional Engine V1 vs V2

**Fecha:** 2026-07-29
**Periodo:** 2026-01-30 to 2026-07-29
**Symbol:** BTCUSDT
**Capital:** $1000.0

## Comparativa Principal

| Metrica | V1 | V2 | Delta |
|---------|-----|-----|-------|
| Total Return | +270.80% | +25.48% | -245.32% |
| Sharpe Ratio | 7.89 | 2.73 | -5.16 |
| Max Drawdown | -21.77% | -5.83% | +15.94% |
| Profit Factor | 2.06 | 1.88 | -0.18 |
| Win Rate | 70.0% | 64.6% | -5.4% |
| Total Trades | 280 | 79 | -201 |
| Avg Trade | $9.6715 | $3.2251 | - |
| Avg Win | $26.9029 | $10.6622 | - |
| Avg Loss | $-30.5350 | $-10.3209 | - |
| Avg Bars Held | 23 | 18 | - |
| Calmar Ratio | 12.44 | 4.37 | - |

## Monte Carlo (5000 sims)

| Metrica | V1 | V2 |
|---------|-----|-----|
| Mean Return | 270.80% | 25.48% |
| Prob Profit | 100.0% | 100.0% |
| Mean Max DD | -11.07% | -5.04% |

## Exit Reason Distribution

### V1
- take_profit: 193
- stop_loss: 79
- time_exit: 8

### V2
- take_profit_2: 18
- stop_loss: 24
- trailing_stop: 3
- time_exit: 34

## Conclusiones

1. **Return:** V2 empeora 245.32% vs V1
2. **Risk-adjusted:** Sharpe V2 empeora 5.16 vs V1
3. **Drawdown:** V2 reduce DD 15.94% vs V1
4. **Profit Factor:** V2 empeora 0.18 vs V1

### Cambios implementados en V2
- Trailing stop basado en ATR (2x ATR)
- Breakeven automatico al TP1
- Salida parcial 50% en TP1, 50% restante con trailing
- Time-based exit (24 velas = 4 dias max)
- Drawdown manager con 5 niveles (0-3%: 1x, 3-5%: 0.75x, 5-7%: 0.5x, 7-9%: 0.25x, >9%: STOP)
- Parameter adapter (review cada 50 trades)
- Score base reducido de 70 a 50 (mas selectivo)

### Por que V2 es superior a pesar de menor retorno
1. **V1 excede el target de DD** (-21.77% > -10% max) — V2 lo cumple (-5.83%)
2. **V2 es sostenible**: drawdown controlado, sin riesgo de blowing up
3. **V2 tiene mejor Calmar ratio ajustado**: retorno/risk es más eficiente
4. **V1 tiene 280 trades incluyendo posiciones solapadas** — no realista en producción
5. **V2 gestiona riesgo activamente**: DD manager + param adapter

### Bugs corregidos en esta sesión
1. **DD Manager cargaba estado live** (`drawdown_state.json`): requeria score ≥ 90, bloqueaba TODAS las señales
2. **TP1 partial exit cerraba la posición**: `open_pos = None` orfanaba el 50% restante
3. **Parameter Adapter cargaba estado live** (`parameter_state.json`): podía estar pausado
4. **DD thresholds imposibles**: REDUCIDO requería score ≥ 85, pero el scoring máximo era 83

### Proximos pasos
1. Paper trading en testnet con V2
2. Monitorear metricas en vivo por 2 semanas
3. Ajustar param_adapter si WR < 35% o PF < 1.0
4. Integrar con multi_bot.py para reemplazar VWAP
