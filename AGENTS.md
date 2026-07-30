# AGENTS.md — Sistema Multi-Bot + ML Filter

## Estado actual (Jul 2026)

### Dashboard
- Puerto: 8501, PID: 75291
- Balance: $4,383.35 (LIVE, testnet Binance Futures)
- 5 bots: BTC, ETH, SOL, XRP, BNB
- API key en `.streamlit/secrets.toml` (funcionando)

### Backtest Results Summary

#### 1. VWAP Breakout (multi_bot.py) — ⚠️ OVERFITTING CONFIRMADO
- Backtest ene–jun 2026: +58.5%, Max DD -2.24%
- **REAL: -9.73% (pérdida confirmada)**
- **6 configuraciones de R:R testeadas en ene–jul 2026: TODAS pierden dinero**
- Mejor PF alcanzado: 0.89 (necesario >1.0)
- **Conclusión: VWAP no es predictivo en BTC 15m**

#### 2. Institutional Engine (engulfing 4h + TP1/TP2 exits) — ene–jul 2026
| Config | Trades | WR | Retorno |
|---|---|---|---|
| Sin ML | 294 | 40.8% | **+11.40%** |
| RF th=0.55 | 36 | 38.9% | +3.19% |
- **El motor solo es rentable. El RF filter lo empeora.**

#### 3. Simplified Exits (all-or-nothing TP/SL) — ene–jul 2026
| Config | Trades | WR | Retorno |
|---|---|---|---|
| Sin ML | 298 | 27.9% | -43.20% |
| RF th=0.4 | 233 | 30.5% | -30.12% |
| RF th=0.5 | 85 | 25.9% | -13.63% |
- Sin partial exits / breakeven, todo pierde.
- RF th=0.4 da mejor WR (+2.6pp) pero no alcanza.

#### 4. Walk-Forward Retrain (RF nuevo Q1→Q2)
- Train: 298 trades (83 wins, 27.9%) — acc 92.3%
- Test Q2: 0 señales de engulfing (no se pudo evaluar)
- Top features: ci_value 14%, wr_15m 13%, body_ratio_4h 13%, vol_ratio_5m 10%, mom_score 10%

### Conclusiones clave
1. **El ML filter actual daña la rentabilidad** — rechaza ~88% de trades buenos
2. **El modelo de salidas (TP1 parcial + breakeven) es más importante que el ML**
3. **El motor institucional solo ya es rentable** (+11.40% en 6m)
4. **⚠️ VWAP breakout era OVERFITTING** — pierde dinero en real y en backtest extendido

#### 5. Adaptive VWAP Engine (Jul 2026) — `engine/adaptive_vwap_v2.py`
- **6 configuraciones de R:R testeadas** en BTCUSDT 15m (ene–jul 2026)
- **TODAS pierden dinero** (PF máximo: 0.89)
- **Mejor configuración**: SL=1.5, TP=3.5 → 99 trades, WR 60.6%, PF 0.89, -$2.54
- **Código generado**: `engine/adaptive_vwap_engine.py`, `engine/adaptive_vwap_v2.py`, `backtest_adaptive_vwap.py`
- **Reporte completo**: `docs/REPORT_ADAPTIVE_VWAP.md`

#### 6. Análisis de Por Qué VWAP No Funciona
- **Las desviaciones de VWAP no son predictivas** en BTC 15m
- **El R:R nunca supera 1.0** — pérdidas siempre más grandes que ganancias
- **64% WR no salva un R:R de 0.45** — necesitarías 80%+ WR
- **El mercado BTC muestra tendencias fuertes** — mean reversion falla

### Resultados Optimización Completa (Jul 2026) — `opt_completa.py`
- Periodo: Oct 2025 → Jul 2026 | Train 67% / Test 33%
- **Total señales:** 518 (424 train, 94 test)

| Config | Trades | WR | Retorno | vs Baseline |
|---|---|---|---|---|
| Sin ML (baseline) | 94 | 27.7% | **+0.09%** | — |
| RF gate th=0.6 | 15 | 40.0% | +1.40% | +15.6× |
| **XGBoost gate th=0.55** | **41** | **39.0%** | **+2.20%** | **+24.4×** |
| XGBoost sizing | 94 | 27.7% | +1.06% | +11.8× |

### Resultados Backtest Histórico Profundo (Jul 2026) — `historical_deep_backtest.py`
- **1148 trades** de 9 periodos (2023-Q1 → 2026-Q1)
- Walk-forward: train en N periodos, test en el siguiente
- **8 folds walk-forward**

| Config | Mejora avg | Mejora max | Veces mejor |
|---|---|---|---|
| RF gate vs No ML | +0.04pp | +1.09pp | 3/8 |
| XGB gate vs No ML | -2.65pp | +0.49pp | 1/8 |

#### Conclusión clave
**El ML filter no mejora consistentemente el motor institucional en 3 años de datos.** El motor solo ya es robusto en la mayoría de los períodos. El ML solo ayuda marginalmente cuando el baseline ya es débil (ej. Q4 2025 con solo +6.97%).

#### ML Condicional por Régimen (`experimento_ml_condicional.py`)
- Filtro ML solo en mercados laterales/choppy (764t), engine libre en tendencias (384t)
- Resultado: **No ML gana: +23.41% avg vs RF condicional +18.34% vs RF always +17.04%**
- El conditional es menos malo que always-on, pero ambos pierden frente a no usar ML

**Conclusión final sobre ML: no usar. El motor institucional solo es la estrategia óptima.**

### Archivos nuevos
- `opt_completa.py` — Optimización completa (RF sizing + XGBoost + features)
- `opt_completa_results.json` — Resultados de optimización ene–jul 2026
- `xgb_model.json` / `xgb_features.json` — XGBoost entrenado (ene–jul 2026)
- `historical_deep_backtest.py` — Backtest histórico 2023-2026 walk-forward
- `historical_deep_results.json` — Resultados 2023-2026
- `xgb_historical.json` / `xgb_historical_features.json` — XGBoost entrenado con 3 años

## Estado Actual (Jul 12 — Post-Sesión Activa)
### Infraestructura Pública
- **Túnel Cloudflare activo**: Dashboard en `https://chelsea-alien-arrangement-fax.trycloudflare.com`
- **API REST pública**: `https://medicaid-glossary-totals-personals.trycloudflare.com`
  - `GET /api/last_signal` — última señal
  - `GET /api/track_record` — track record completo
  - `GET /api/bot_status` — estado del bot
- **API server**: `api_server.py` corriendo en :8585
- **Landing page**: `docs/index.html` con equity chart vivo + track record

### Mejoras Implementadas
- **Anti-fail risk**: drawdown progresivo (−3% → 0.75×, −6% → 0.5×, −10% → STOP total)
- **SHA256 chain**: cada señal en `signal_log.json` tiene hash encadenado (inmutable)
- **Daemon reiniciado** con PID 26895 (nuevo código anti-fail + hash chain)

### Últimos cambios (commit 32f4534)
- **On-chain audit**: `chain_audit.py` — firma Ethereum de cada trade en signal_log.json
- **Pine Script publication-ready**: inputs claros, performance table, equity curve, descripción
- **X poster integrado**: thread daemon en `run_bots_background.py`, solo se activa con API keys
- **Logs removidos del repo**: `.gitignore` actualizado, logs ya no se trackean
- **Daemon reiniciado**: PID 28644 con nuevo código

### On-chain Audit Wallet
- **Address público**: `0xBEB7E29B235068F2CD455621db2373e5fc4De79b`
- Cada trade en `signal_log.json` queda firmado con esta wallet
- Verificable en Etherscan o con `python chain_audit.py`

### Pendiente manual
- **GitHub Pages**: ir a Settings → Pages → Source: master branch /docs folder (o esperar al Actions workflow)
- **TradingView**: copiar `vwap_strategy.pine` → Pine Editor → Publicar en comunidad
- **X/Twitter**: crear proyecto en developer.twitter.com, agregar claves a `.streamlit/secrets.toml`

### Archivos clave
- `multi_bot.py` — Sistema VWAP breakout (profitable)
- `walkforward_institucional_rf.py` — Backtest institucional + RF
- `analisis_completo.py` — Análisis completo (thresholds + walkforward)
- `analisis_completo.json` — Resultados guardados
- `opt_completa.py` — Optimización completa (RF sizing + XGBoost + features)
- `opt_completa_results.json` — Resultados de la optimización completa
- `ml_filter.py` — RF filter actual
- `ml_model.pkl` — Random Forest (max_depth=5, balanced)
- `.streamlit/secrets.toml` — API key testnet
- `backtest_institucional_v1.py` — Motor original institucional

## Institutional Engine V2 (Jul 29, 2026)

### Estado: Completado (backtest, pendiente paper trading)

### Resultados V1 vs V2 (ene-jul 2026)
| Métrica | V1 | V2 | Target |
|---------|-----|-----|--------|
| Return | +270.8% | +25.48% | — |
| Max DD | -21.77% ❌ | -5.83% ✅ | <10% |
| PF | 2.06 | 1.88 | >1.3 |
| Sharpe | 7.89 | 2.73 | >1.0 |
| Win Rate | 70.0% | 64.6% | >45% |
| Trades | 280 | 79 | >50 |

### Módulos implementados
1. **institutional_engine_v2.py** — Motor con gestión de posiciones + autogobernanza
2. **position_manager.py** — Trailing stop ATR, breakeven, partial exit, time-based exit
3. **drawdown_manager.py** — 5 niveles de reducción de riesgo, stop total >9%
4. **parameter_adapter.py** — Auto-ajuste cada 50 trades, detección de cambio de régimen
5. **backtest_institutional_v2.py** — Comparativo V1 vs V2 + Monte Carlo 5000 sims

### Bugs corregidos en V2
1. DD Manager cargaba estado live → requería score ≥ 90, bloqueaba TODAS las señales
2. TP1 partial exit cerraba posición completa → orfanaba 50% restante
3. Parameter Adapter cargaba estado live → podía estar pausado
4. DD thresholds imposibles → REDUCIDO requería score ≥ 85, pero max score era 83

### Próximos pasos
1. Paper trading en testnet con V2 (2 semanas)
2. Monitorear métricas en vivo
3. Ajustar param_adapter si WR < 35% o PF < 1.0
4. Integrar con multi_bot.py para reemplazar VWAP

### Archivos nuevos
- `engine/institutional_engine_v2.py`
- `engine/position_manager.py`
- `engine/drawdown_manager.py`
- `engine/parameter_adapter.py`
- `backtest_institutional_v2.py`
- `docs/REPORT_INSTITUTIONAL_V2.md`

## Research: Cross-Asset Strategy Discovery (Jul 2026)

### Overview
Comprehensive backtest of 15 strategies across 15 assets (crypto, forex, stocks) and 3 timeframes.
- **Total backtests:** 442
- **Viable (Sharpe >1.0):** 44 (9.9%)
- **Best Sharpe:** 7.41 (EMA_Crossover_10_50 on GOOGL)
- **Forex viability:** 0% (avoid entirely)

### Top 3 Strategies
1. **EMA_Crossover_10_50 on GOOGL (1d)** — Sharpe 7.41, +97.2% return, -22.1% DD
2. **EMA_Crossover_20_100 on JNJ (1d)** — Sharpe 7.36, +61.1% return, -13.8% DD
3. **MACD_12_26_9 on JNJ (1d)** — Sharpe 7.16, +45.9% return, -8.7% DD

### Key Findings
- EMA Crossover dominates (top 6 Sharpe ratios)
- Daily timeframe >> 4h >> 1h (hourly is noise)
- JNJ most reliable asset (60% profitability across strategies)
- Forex completely untradeable (0/68 backtests profitable)
- Mean Reversion only works on JNJ (defensive stock)

### Reports
- `research/reports/TOP_STRATEGIES.md` — Full rankings
- `research/reports/EXECUTIVE_SUMMARY.md` — Executive summary
- `research/backtests/summary.json` — All 442 results
- `research/run_backtests.py` — Backtest runner script

## Recomendaciones (Jul 29, 2026)

### Estrategia Actual: Motor Institucional (+11.40% en 6m)
El motor institucional (engulfing 4h + TP1/TP2) es la ÚNICA estrategia demostrada rentable. Mantener como base.

### NO Implementar
- ❌ **VWAP Breakout**: Overfitting confirmado, pierde dinero en real
- ❌ **Adaptive VWAP Engine**: 6 configuraciones testeadas, todas pierden
- ❌ **ML Filter**: Daña rentabilidad, rechaza trades buenos

### Próximos Pasos Recomendados
1. **Optimizar Motor Institucional**: Ajustar parámetros para mejorar +11.40%
2. **Explorar Trend Following**: EMA Crossover en 4H (evidencia fuerte del research)
3. **Reducir pares**: Enfocarse en BTC + 1-2 pares más rentables
4. **Paper Trading**: Validar cualquier cambio en testnet antes de real

### Archivos Nuevos (Jul 29)
- `engine/adaptive_vwap_engine.py` — Engine VWAP V1
- `engine/adaptive_vwap_v2.py` — Engine VWAP V2 (simplificado)
- `backtest_adaptive_vwap.py` — Framework de backtesting
- `run_full_backtest.py` — Runner principal
- `docs/REPORT_ADAPTIVE_VWAP.md` — Reporte completo de resultados
- `docs/ADR-001-robust-btc-strategy.md` — Architecture Decision Record
- `backtest_results/vwap_strategy_results.json` — Resultados guardados
