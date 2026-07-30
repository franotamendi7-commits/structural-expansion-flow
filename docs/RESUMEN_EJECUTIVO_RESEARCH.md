# Resumen Ejecutivo: Research Estrategias Robustas

**Para:** Francisco (via Manager)  
**De:** Institutional Trading Engine Specialist  
**Fecha:** 29 Julio 2026  
**Prioridad:** ALTA

---

## Hallazgo Principal

**Los 3 problemas del Engine V2 tienen soluciones probadas por la industria.** No necesitamos reinventar nada — solo implementar lo que ya funciona en hedge funds institucionales.

---

## Top 3 Soluciones (Impacto Máximo)

### 1. 🔴 POST-ONLY ORDERS — Reducir Costos 84%
**Problema:** Costos 0.22%  
**Solución:** Usar solo órdenes maker (Post-Only)  
**Costo resultante:** 0.036% (maker + BNB discount)  
**Tiempo:** 1 día  
**Riesgo:** Muy bajo  

**Cómo:** Cambiar `timeInForce='GTX'` en todas las órdenes de `execution_manager.py`

### 2. 🟡 VOLATILITY TARGETING — Reducir DD de >10% a <7%
**Problema:** DD >10% en 42.5% de sims de alta volatilidad  
**Solución:** Escalar posición inversamente a la volatilidad  
**Fórmula:** `position_scale = baseline_vol / current_vol`  
**Tiempo:** 3-5 días  
**Riesgo:** Medio  

**Cómo:** Modificar `compute_size()` en `position_manager.py` para que use ATR actual

### 3. 🟢 REGIME DETECTION — Adaptar Estrategia al Mercado
**Problema:** WR cae en escenarios extremos  
**Solución:** Hidden Markov Model para detectar bull/bear/calm/high-vol  
**Beneficio:** En high-vol: reducir 60% el tamaño. En bull: mantener posiciones más tiempo  
**Tiempo:** 1-2 semanas  
**Riesgo:** Medio-alto  

**Cómo:** Entrenar HMM con 4 estados, integrar con `institutional_engine_v2.py`

---

## Números Clave

| Métrica | Actual | Target Post-Implementación |
|---------|--------|---------------------------|
| Costos | 0.22% | 0.126% (-43%) |
| Max DD alta vol | >10% | <7% |
| WR escenario extremo | 44% (64.6-20) | >60% |
| Sharpe | 2.73 | >3.0 |

---

## Plan de Acción Recomendado

### Inmediato (Esta semana)
1. **Lunes:** Implementar Post-Only orders
2. **Martes:** Habilitar BNB discount
3. **Miércoles:** Agregar vol-adjusted sizing
4. **Jueves:** Backtest con nuevos costos
5. **Viernes:** Paper trading en testnet

### Corto plazo (Próximas 2 semanas)
1. Entrenar HMM regime detection
2. Implementar Triple Barrier labeling
3. Reemplazar sizing fijo con Fractional Kelly
4. Walk-forward validation completa

---

## Lo que NO Implementar

- ❌ VWAP Breakout (overfitting confirmado)
- ❌ ML Filter (daña rentabilidad)
- ❌ Adaptive VWAP (pierde dinero)

---

## Papers de Referencia

1. **Triple Barrier Method** — Lopez de Prado (el estándar de la industria)
2. **Regime Detection HMM** — Wiley 2020, Preprints 2026
3. **Volatility Targeting** — Keel.io (usado por hedge funds)
4. **Fractional Kelly** — Hyper-Quant (reduce DD 58% → 21%)

---

**Decisión necesaria:** ¿Proceder con Fase 1 (Post-Only) esta semana? Es la de mayor impacto con menor riesgo.

---

*Documento completo: `docs/RESEARCH_ROBUST_STRATEGIES.md`*
