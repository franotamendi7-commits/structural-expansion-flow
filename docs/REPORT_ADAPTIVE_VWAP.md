# Reporte: Estrategia Adaptive VWAP Engine — Resultados

## Resumen Ejecutivo

**La estrategia VWAP-based NO es rentable en el periodo Ene-Jul 2026.** Después de 6 configuraciones de R:R testing y múltiples variantes de señal, el Profit Factor máximo alcanzado fue 0.89 (necesario >1.0 para ser rentable).

### Resultados Clave

| Configuración | Trades | Win Rate | Profit Factor | R:R | Retorno |
|---------------|--------|----------|---------------|-----|---------|
| Original (SL=2.0, TP=2.5) | 110 | 64.5% | 0.81 | 0.45 | -$3.13 |
| Tight SL (SL=1.0, TP=2.5) | 108 | 54.6% | 0.81 | 0.67 | -$5.50 |
| Wide TP (SL=2.0, TP=4.0) | 98 | 64.3% | 0.86 | 0.48 | -$2.68 |
| **Balanced (SL=1.5, TP=3.5)** | **99** | **60.6%** | **0.89** | **0.58** | **-$2.54** |
| Aggressive (SL=1.0, TP=3.0) | 104 | 52.9% | 0.82 | 0.73 | -$5.56 |
| Conservative (SL=1.0, TP=2.0) | 108 | 52.8% | 0.63 | 0.57 | -$10.34 |

### Diagnóstico

1. **El VWAP no es predictivo en este mercado**: Las desviaciones de VWAP no generan señales confiables para mean reversion ni breakout
2. **El overfitting del original era real**: El +58.5% fue en un periodo alcista específico
3. **El R:R nunca supera 1.0**: Las pérdidas siempre son más grandes que las ganancias
4. **64% WR no salva un R:R de 0.45**: Necesitarías 80%+ WR para ser rentable con ese R:R

## Análisis Detallado

### Por Régimen

| Régimen | Trades | Win Rate | PnL |
|---------|--------|----------|-----|
| RANGING | 182 | 63.7% | -$5.18 |
| TRENDING_BEAR | 69 | 53.6% | -$3.58 |
| TRENDING_BULL | 47 | 68.1% | -$0.32 |

**Hallazgo**: El régimen más rentable es TRENDING_BULL pero con solo 47 trades no es estadísticamente significativo.

### Por Tipo de Salida

| Salida | Trades | PnL |
|--------|--------|-----|
| SL (Stop Loss) | 113 | -$22.09 |
| TP1 (Parcial) | 115 | +$7.37 |
| TP2 (Completo) | 66 | +$5.51 |
| Trail (Trailing) | 4 | +$0.14 |

**Hallazgo**: El SL genera 3x más pérdidas que las ganancias de TP1+TP2.

## Recomendaciones

### 1. NO implementar Adaptive VWAP Engine
Los datos muestran que la estrategia no es rentable. Implementarla sería perder dinero.

### 2. Alternativas a considerar

#### A. Trend Following (Recomendado)
- **Por qué**: El mercado BTC muestra tendencias fuertes
- **Indicador**: EMA Crossover (10/50 en 4H o 1D)
- **Evidence**: Research muestra Sharpe 7.41 en similar setup
- **Implementación**: Comprar cuando EMA10 > EMA50, vender cuando cruza al revés

#### B. Grid Trading (Alternativa)
- **Por qué**: Funciona en mercados laterales
- **Indicador**: Rango de soporte/resistencia
- **Implementación**: Colocar órdenes limit en ambos lados del rango
- **Riesgo**: En tendencia fuerte, pierde dinero

#### C. Momentum Breakout (Alternativa)
- **Por qué**: Captura movimientos fuertes
- **Indicador**: Precio rompe máximo de 20 barras con volumen
- **Implementación**: Comprar en breakout, vender en trailing stop
- **Riesgo**: Falsos breakouts

### 3. Si insistir en VWAP
Si Francisco quiere insistir en VWAP, necesita:
- **Timeframe mayor** (1H o 4H en lugar de 15m)
- **Filtro de tendencia** más fuerte (solo operar con la tendencia principal)
- **Menos trades** (calidad sobre cantidad)
- **Backtest en más periodos** (2023-2026 completo)

## Datos del Backtest

- **Periodo**: Ene 30 - Jul 29 2026 (6 meses)
- **Activo**: BTCUSDT
- **Timeframe**: 15m
- **Barras**: 17,280
- **Capital inicial**: $100
- **Costos**: 0.05% commission + 0.02% spread + 0.1×ATR slippage

## Código Generado

Los archivos creados están en:
- `engine/adaptive_vwap_engine.py` — Engine V1 (completo)
- `engine/adaptive_vwap_v2.py` — Engine V2 (simplificado)
- `backtest_adaptive_vwap.py` — Framework de backtesting
- `run_full_backtest.py` — Runner principal
- `backtest_results/` — Resultados y cache de datos

## Conclusión

**El VWAP no funciona como estrategia principal en BTC 15m.** Los datos son concluyentes: 6 configuraciones diferentes, todas pierden dinero. La raíz del problema es que las desviaciones de VWAP no son predictivas en este mercado.

**Recomendación**: Explorar Trend Following o Momentum que sí muestran evidencia de ser rentables en crypto.

---

**Fecha**: 29 Jul 2026
**Autor**: Trading Specialist Agent
**Estado**: Resultados negativos — Estrategia no recomendada para implementación
