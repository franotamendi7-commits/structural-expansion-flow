# 🤖 STRUCTURAL EXPANSION FLOW

**Bot de trading algorítmico para Binance Futures con 5 pares.**  
Estrategia basada en estructura de mercado, Fibonacci adaptativo y filtros institucionales.  
Sistema modular con 6 agentes independientes, 2 de ellos potenciados con Machine Learning (Random Forest).

---

## 🧠 Agentes del sistema

| # | Agente | Función | Tipo |
|---|--------|---------|------|
| 1 | **Market Scanner** | Detecta setups de entrada (engulfing 4h, Fibonacci, fases de mercado) | Reglas fijas |
| 2 | **Risk Manager** | Ajusta el riesgo dinámicamente según score de la señal y volatilidad (ATR) | Fórmula adaptativa |
| 3 | **Technical Analyst** | Evalúa estructura de precios, tendencias y fases de mercado | Reglas fijas |
| 4 | **Momentum Tracker** | Mide la fuerza direccional usando EMAs y volumen relativo | Reglas fijas |
| 5 | **Execution Guard** | Aplica filtros institucionales (Choppiness, Williams %R, Supertrend) + ML | Híbrido (reglas + ML) |
| 6 | **ML Agent** | Filtro de calidad que aprueba o veta señales usando 16 features y Random Forest | Machine Learning |

---

## 📊 Resultados validados

| Métrica | Valor |
|---------|-------|
| **Profit Factor (realista)** | 2.5 – 3.0 |
| **Win Rate** | ~60% |
| **Drawdown máximo** | <10% |
| **Pares operados** | BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT |
| **Timeframe principal** | 4h (entrada) + 15m/5m (ejecución) |

---

## ⚙️ Instalación

```bash
git clone https://github.com/franotamendi7-commits/structural-expansion-flow.git
cd structural-expansion-flow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🚀 Uso

### Bot en vivo (testnet o real)

```bash
python app.py
```

### Backtest institucional

```bash
python backtest_institucional_v1.py --start 2025-01-01 --end 2025-12-31 --output backtest.csv
```

---

## 🔄 Reentrenamiento automático del ML

El modelo se reentrena **cada lunes a las 2:00 AM** mediante un cron job:

```bash
0 2 * * 1 cd ~/ai-agents-v3 && .venv/bin/python3 retrain_ml.py >> retrain_cron.log 2>&1
```

El script `retrain_ml.py`:
- Carga los datos históricos (515+ trades).
- Agrega los trades nuevos del logger.
- Reentrena el modelo si hay suficientes datos nuevos.
- Compara el Profit Factor con el modelo anterior.
- Si mejora, actualiza `ml_model.pkl`. Si no, conserva el anterior.

---

## 📁 Estructura del proyecto

```
.
├── app.py                  # Bot principal
├── ml_filter.py            # Filtro ML (carga el modelo y decide)
├── risk_manager.py         # Gestión dinámica de riesgo
├── stop_monitor.py         # Monitor de stops en vivo
├── execution_manager.py    # Gestión de órdenes en Binance
├── retrain_ml.py           # Reentrenamiento automático semanal
├── backtest_institucional_v1.py  # Backtest con features y costos realistas
├── ml_model.pkl            # Modelo entrenado (Random Forest)
├── requirements.txt        # Dependencias
└── README.md               # Este archivo
```

---

## 🧪 Validación del modelo

El modelo fue entrenado con **1498 trades** desde abril 2023 hasta junio 2026, usando validación walk‑forward con división temporal estricta (Train/Validation/Test).  
El umbral óptimo de probabilidad se fijó en **0.55**, seleccionado sobre datos de validación que el modelo nunca vio durante el entrenamiento.

---

## 📈 Umbrales y configuración

| Parámetro | Valor |
|-----------|-------|
| Umbral ML | 0.55 |
| Riesgo base | 1% del capital |
| Slippage | 0.1 × ATR(5m,14) |
| Comisión | 0.05% taker |
| Spread | 0.02% |

---

## 🛡️ Gestión de salida

- **TP1:** Cierre del 60% de la posición.
- **Breakeven:** Al alcanzar el 50% del TP1, el stop se mueve a la entrada.
- **TP2:** Cierre del 40% restante.

---

## 📄 Licencia

Proyecto privado. Todos los derechos reservados.

---

**Desarrollado por Francisco Otamendi**  
*Proyecto personal de trading algorítmico + Machine Learning*
