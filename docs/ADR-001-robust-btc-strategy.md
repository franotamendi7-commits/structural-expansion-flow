# ADR-001: Estrategia Robusta BTC/USDT — "Adaptive VWAP Engine"

## Fecha: 29 Jul 2026

## Contexto

Francisco tiene un bot VWAP Breakout que generó +58.5% en backtest pero -9.73% en real. El motor institucional genera +11.40% en 6 meses pero es modesto. El ML filter daña la rentabilidad. Se necesita una estrategia que:

1. **No sea overfitteada** — que funcione en múltiples condiciones de mercado
2. **Tenga DD controlado** — máximo 10%
3. **Sea autogobernada** — que se auto-ajuste sin intervención humana
4. **Sea robusta en real** — que los backtests se traduzcan a ganancias

## Problemas Identificados en el Código Actual

### 1. Overfitting Temporal
- VWAP params específicos por régimen (ALCISTA: vwap_n=12, BAJISTA: vwap_n=15)
- Estos números fueron optimizados para el periodo ene-jun 2026
- Cuando el mercado cambió, el bot colapsó

### 2. Look-Ahead Bias
- `detect_regime()` usa `ema20_y` de daily data — pero el cálculo de EMA puede incluir el cierre de hoy
- El código descarta la barra en progreso pero el cálculo de régimen puede estar mirando hacia adelante

### 3. Sizing Demasiado Agresivo
- 20x leverage con $100/bot = exposición de $2,000
- Un movimiento del 0.5% liquidaría la posición
- El max SL del 1.8% con 20x = riesgo real del 36% por trade

### 4. Falta de Regime Filter Real
- El regime actual usa solo EMA20 daily + close 4h
- No detecta: volatilidad, chop, tendencia fuerte vs débil

## Decisión

Crear una estrategia **"Adaptive VWAP Engine"** que sea:

1. **Multi-indicador** con confirmación cruzada (no un solo indicador)
2. **Regime-aware** con filtro real de condiciones de mercado
3. **Dynamic sizing** basado en volatilidad real (ATR)
4. **Self-tuning** con rotación de parámetros según performance reciente
5. **DD-gated** con reducción progresiva de exposición

## Arquitectura Detallada

```
┌─────────────────────────────────────────────────────────────┐
│                    ADAPTIVE VWAP ENGINE                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  REGIME       │    │  SIGNAL      │    │  RISK        │  │
│  │  DETECTOR     │───▶│  GENERATOR   │───▶│  MANAGER     │  │
│  │              │    │              │    │              │  │
│  │  - ADX(14)   │    │  - VWAP dev  │    │  - ATR size  │  │
│  │  - BB Width  │    │  - RSI conf  │    │  - DD gate   │  │
│  │  - ATR ratio │    │  - Volume    │    │  - Cooldown  │  │
│  │  - EMA slope │    │  - Structure │    │  - Leverage  │  │
│  └──────────────┘    └──────────────┘    └──────────────┘  │
│         │                   │                   │            │
│         ▼                   ▼                   ▼            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │                 POSITION MANAGER                      │  │
│  │  - TP1 (60% @ 1.5×ATR)                              │  │
│  │  - Move to breakeven                                 │  │
│  │  - Trailing stop (2×ATR)                             │  │
│  │  - TP2 (remaining @ 3×ATR)                           │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              SELF-TUNING MODULE                       │  │
│  │  - Rolling 7-day performance tracking                │  │
│  │  - Parameter rotation (3 presets)                    │  │
│  │  - Auto-reduction on losing streaks                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Parámetros Específicos

### 1. Regime Detector (Multi-Indicador)

```python
REGIME_CONFIG = {
    # Indicador 1: ADX para tendencia
    'adx_period': 14,
    'adx_trending': 25,      # ADX > 25 = tendencia
    'adx_ranging': 20,       # ADX < 20 = rango
    
    # Indicador 2: Bollinger Width para volatilidad
    'bb_period': 20,
    'bb_std': 2.0,
    'bb_squeeze': 0.03,      # Width < 3% = compresión
    'bb_expand': 0.06,       # Width > 6% = expansión
    
    # Indicador 3: ATR ratio (ATR actual vs histórico)
    'atr_period': 14,
    'atr_lookback': 50,      # Comparar contra 50 barras
    'atr_high_vol': 1.5,     # ATR > 1.5x = alta vol
    'atr_low_vol': 0.7,      # ATR < 0.7x = baja vol
    
    # Indicador 4: EMA slope para dirección
    'ema_fast': 20,
    'ema_slow': 50,
    'ema_slope_bars': 5,     # Pendiente de 5 barras
}
```

**Clasificación de régimen:**
- **TRENDING_BULL**: ADX > 25 AND EMA20 > EMA50 AND slope > 0
- **TRENDING_BEAR**: ADX > 25 AND EMA20 < EMA50 AND slope < 0
- **RANGING_LOW_VOL**: ADX < 20 AND BB width < 3% AND ATR ratio < 0.7
- **RANGING_HIGH_VOL**: ADX < 20 AND BB width > 6% AND ATR ratio > 1.5
- **TRANSITION**: Todo lo demás (no operar)

### 2. Signal Generator (VWAP + Confirmación)

```python
SIGNAL_CONFIG = {
    # VWAP params (dinámicos por régimen)
    'vwap_base_period': 20,
    'vwap_dev_threshold': 1.0,  # % deviation para señal
    
    # Confirmación: RSI
    'rsi_period': 14,
    'rsi_oversold': 30,
    'rsi_overbought': 70,
    
    # Confirmación: Volume
    'volume_ma_period': 20,
    'volume_threshold': 1.5,  # 1.5x promedio para confirmar
    
    # Confirmación: Structure (swing points)
    'swing_lookback': 10,
    
    # Filtro de sesión (evitar baja liquidez)
    'session_utc_offset': 0,  # UTC
}
```

**Lógica de señal:**
1. **VWAP Deviation**: Precio se desvía > 1% del VWAP
2. **RSI Confirmation**: RSI < 30 para long, RSI > 70 para short
3. **Volume Confirmation**: Volumen > 1.5x promedio de 20 barras
4. **Structure Confirmation**: Precio en zona de soporte/resistencia (swing points)
5. **Regime Confirmation**: Régimen debe ser favorable (no TRANSITION)

### 3. Risk Manager (Dynamic)

```python
RISK_CONFIG = {
    # Position sizing
    'base_risk_pct': 0.005,     # 0.5% por trade (conservador)
    'max_risk_pct': 0.01,       # 1% máximo
    'atr_sl_multiplier': 2.0,   # SL = 2×ATR
    'atr_tp1_multiplier': 1.5,  # TP1 = 1.5×ATR
    'atr_tp2_multiplier': 3.0,  # TP2 = 3×ATR
    
    # Leverage
    'max_leverage': 10,         # Reducido de 20x
    'leverage_by_regime': {
        'TRENDING_BULL': 8,
        'TRENDING_BEAR': 8,
        'RANGING_LOW_VOL': 5,
        'RANGING_HIGH_VOL': 3,
        'TRANSITION': 0,
    },
    
    # Drawdown gates
    'dd_warning': -0.03,        # -3%: reducir a 50% size
    'dd_critical': -0.06,       # -6%: reducir a 25% size
    'dd_stop': -0.10,           # -10%: parar trading
    
    # Cooldown
    'cooldown_losses': 3,       # 3 pérdidas consecutivas
    'cooldown_minutes': 60,     # 1 hora de pausa
}
```

### 4. Self-Tuning Module

```python
SELF_TUNING_CONFIG = {
    # Performance tracking
    'rolling_window': 7,        # 7 días de performance
    'min_trades_for_tuning': 10,
    
    # Parameter presets (3 configuraciones)
    'presets': {
        'AGGRESSIVE': {
            'vwap_dev': 0.8,
            'rsi_oversold': 35,
            'rsi_overbought': 65,
            'volume_threshold': 1.3,
        },
        'BALANCED': {
            'vwap_dev': 1.0,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'volume_threshold': 1.5,
        },
        'CONSERVATIVE': {
            'vwap_dev': 1.5,
            'rsi_oversold': 25,
            'rsi_overbought': 75,
            'volume_threshold': 2.0,
        },
    },
    
    # Rotation logic
    'performance_threshold': 0.02,  # 2% return = bueno
    'rotation_cooldown_days': 3,     # No rotar más de 1 vez cada 3 días
}
```

**Lógica de rotación:**
1. Cada 7 días, evaluar performance del preset actual
2. Si return < -1% en 7 días → rotar a preset más conservador
3. Si return > 3% en 7 días → puede rotar a preset más agresivo
4. Si return entre -1% y 3% → mantener preset actual

## Métricas de Éxito

### Objetivos Cuantitativos
| Métrica | Target | Mínimo Aceptable |
|---------|--------|------------------|
| Sharpe Ratio | > 1.5 | > 1.0 |
| Sortino Ratio | > 2.0 | > 1.5 |
| Max Drawdown | < -8% | < -10% |
| Win Rate | > 45% | > 40% |
| Profit Factor | > 1.8 | > 1.5 |
| Retorno Anual | > 30% | > 15% |
| Trades/Mes | 15-25 | 10-30 |

### Métricas de Robustez
- **Walk-Forward Sharpe**: > 1.0 en cada fold
- **Monte Carlo DD p95**: < -12%
- **Consistencia**: Profitable en > 60% de los meses
- **Regime performance**: No perder dinero en ningún régimen por más de 1 mes

## Plan de Implementación

### Fase 1: Core Engine (1-2 días)
1. Crear `engine/adaptive_vwap_engine.py`
2. Implementar regime detector (multi-indicador)
3. Implementar signal generator (VWAP + RSI + Volume + Structure)
4. Integrar con risk manager existente
5. Unit tests para cada componente

### Fase 2: Backtesting (2-3 días)
1. Crear `backtest_adaptive_vwap.py`
2. Backtest en 3 periodos:
   - **In-sample**: Ene-Jun 2026 (optimización)
   - **Out-of-sample**: Jul 2026 (validación)
   - **Stress test**: Oct 2025 - Jul 2026 (completo)
3. Walk-forward validation (6 folds)
4. Monte Carlo simulation (5000 sims)

### Fase 3: Paper Trading (1-2 semanas)
1. Deploy en testnet Binance
2. Monitorear performance vs backtest
3. Comparar régimen detectado vs real
4. Ajustar parámetros si es necesario

### Fase 4: Self-Tuning (1 semana)
1. Implementar rolling performance tracker
2. Implementar parameter rotation
3. Test en paper trading
4. Validar que la rotación mejora performance

### Fase 5: Deploy Real (solo si Fase 3-4 pasan)
1. Capital inicial: $500
2. Leverage: 3-8x (según régimen)
3. Monitoreo intensivo primeras 2 semanas
4. Reporte diario a Telegram

## Riesgos y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Overfitting | Alta | Alto | Walk-forward + Monte Carlo |
| Look-ahead bias | Media | Alto | Usar solo barras cerradas |
| Drawdown > 10% | Baja | Alto | DD gates progresivos |
| Baja liquidez | Media | Medio | Session filter + volume filter |
| API errors | Baja | Medio | Circuit breaker + retry |

## Archivos a Crear/Modificar

### Nuevos
- `engine/adaptive_vwap_engine.py` — Core engine
- `backtest_adaptive_vwap.py` — Backtesting framework
- `engine/regime_detector.py` — Detector de régimen multi-indicador
- `engine/signal_generator.py` — Generador de señales VWAP + confirmación
- `engine/self_tuner.py` — Módulo de auto-ajuste

### Modificar
- `multi_bot.py` — Integrar nuevo engine como bot alternativo
- `risk_manager.py` — Agregar DD gates progresivos
- `AGENTS.md` — Documentar nueva estrategia

## Decisiones Futuras

1. **¿Agregar ML como filtro secundario?** → Solo si el engine base demuestra consistencia
2. **¿Expandir a otros pares?** → Solo después de 3 meses profitable en BTC
3. **¿Aumentar capital?** → Solo después de 6 meses con Sharpe > 1.5

---

**Autor**: Trading Specialist Agent  
**Estado**: Propuesto  
**Revisión**: Pendiente de aprobación de Francisco
