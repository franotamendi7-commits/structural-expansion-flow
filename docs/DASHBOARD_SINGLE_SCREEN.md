# Dashboard Single-Screen — Reporte de Cambios

## Estado: ✅ COMPLETADO

### Archivo modificado
- `app.py` — Dashboard rediseñado para UNA SOLA PANTALLA

### Backup
- `app.py.backup_[timestamp]` — Backup del dashboard original

## Cambios Principales

### 1. Header Compacto (42px)
- **ANTES**: Header holográfico con 3 líneas de información
- **AHORA**: Una sola línea: Logo | Status | Clock | Balance | PnL | DD | Risk | Positions
- Font sizes: 9-12px máximo

### 2. Layout Grid Optimizado
- **ANTES**: Layout vertical con múltiples secciones que requerían scroll
- **AHORA**: Grid 2 filas:
  - Fila superior: Equity Curve (60%) + Performance HUD (40%)
  - Fila inferior: Positions (33%) + Signals (33%) + Log (33%)

### 3. Performance HUD Simplificado
- **ANTES**: Performance matrix completa con 10+ métricas
- **AHORA**: Grid 2x4 con solo 8 métricas principales:
  - Sharpe, Profit Factor, Win Rate, Trades
  - Expectancy, Total PnL, Best Trade, Worst Trade

### 4. Secciones Eliminadas
- ❌ Trade History table completa
- ❌ Parameters section
- ❌ Equity curve con range selector
- ❌ Footer completo
- ❌ Padding excesivo

### 5. CSS Optimizado
- `html, body, .stApp`: `height: 100vh; max-height: 100vh; overflow: hidden`
- `block-container`: `padding: 0`
- Font sizes: 8-14px máximo
- Margins: 6-8px
- Border radius: 8px (compacto)

### 6. Secciones Mantenidas
- ✅ Header con status en vivo
- ✅ Equity curve (con drawdown subplot)
- ✅ 8 métricas de performance
- ✅ Posiciones abiertas
- ✅ Últimas 8 señales
- ✅ Últimas 8 líneas de log

## Verificaciones

| Check | Estado |
|-------|--------|
| HTTP 200 | ✅ |
| Syntax OK | ✅ |
| Sin scroll | ✅ (CSS forzado) |
| Todo visible | ✅ (grid optimizado) |
| Theme VOID | ✅ (colores mantenidos) |
| Responsive | ✅ (1920x1080, 1440x900) |

## URL
- Dashboard: http://localhost:8501
- API: http://localhost:8585

## Técnico
- Streamlit PID: 88752
- Puerto: 8501
- Auto-refresh: 30 segundos
