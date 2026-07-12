# AGENTS.md — Sistema Multi-Bot + ML Filter

## Estado actual (Jul 2026)

### Dashboard
- Puerto: 8501, PID: 75291
- Balance: $4,383.35 (LIVE, testnet Binance Futures)
- 5 bots: BTC, ETH, SOL, XRP, BNB
- API key en `.streamlit/secrets.toml` (funcionando)

### Backtest Results Summary

#### 1. VWAP Breakout (multi_bot.py) — Validado ene–jun 2026
- Portfolio: $500 → +58.5%, Max DD -2.24%
- Probabilidad de ganar (5000 MC sims): 99.94–100%
- **Rentable sin ML filter**

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
4. **El VWAP breakout (multi_bot.py) es aún más rentable** (+58.5% en 6m)

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
