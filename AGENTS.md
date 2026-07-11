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

### Próximos pasos (pendientes)
- [ ] A: RF como sizing (no gate) — proba ajusta tamaño de posición
- [ ] B: Re-entrenar RF sobre trades del institucional Q1, test Q2
- [ ] C: XGBoost en vez de Random Forest
- [ ] D: Feature engineering (volatilidad, correlación, regime)
- [ ] E: Implementar todo junto

### Archivos clave
- `multi_bot.py` — Sistema VWAP breakout (profitable)
- `walkforward_institucional_rf.py` — Backtest institucional + RF
- `analisis_completo.py` — Análisis completo (thresholds + walkforward)
- `analisis_completo.json` — Resultados guardados
- `ml_filter.py` — RF filter actual
- `ml_model.pkl` — Random Forest (max_depth=5, balanced)
- `.streamlit/secrets.toml` — API key testnet
- `backtest_institucional_v1.py` — Motor original institucional
