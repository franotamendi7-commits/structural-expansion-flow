# ROADMAP — VWAP Breakout Signal SaaS

Estado: `julio 12, 2026`

---

## ✅ FASE 1 — NUCLEO (COMPLETADO)
- [x] Bot VWAP Breakout con trailing stop + TP1 parcial
- [x] Anti-fail risk (drawdown progresivo)
- [x] Daemon 24/7 independiente del dashboard
- [x] Telegram alerts privado + canal público `t.me/vwapsignals`
- [x] Signal log con SHA256 chain (inmutable)
- [x] On-chain audit con wallet Ethereum
- [x] Validación: +58.5% en 6m, DD -2.24%, Sharpe 12.75

## 🟡 FASE 2 — INFRAESTRUCTURA PUBLICA (EN PROGRESO)
- [x] API REST pública (last_signal, track_record, bot_status)
- [x] Cloudflare Tunnel (dashboard + API públicos)
- [x] Landing page con chart vivo (`docs/index.html`)
- [x] GitHub Actions para deploy automático a Pages
- [ ] Activar GitHub Pages en Settings → Pages → Source: master branch /docs
- [ ] Publicar Pine Script en comunidad TradingView (`vwap_strategy.pine`)

## 🔵 FASE 3 — DISTRIBUCION VIRAL
- [ ] Canal Telegram funcionando automáticamente ✅ (solo esperar señales)
- [ ] X/Twitter — crear developer account + API keys
- [ ] TradingView — copiar/pegar `vwap_strategy.pine` en Pine Editor y publicar
- [ ] Landing page en GitHub Pages activa (link público fijo)

## 🟣 FASE 4 — MONETIZACION
- [ ] Crear wallet USDC (sin KYC)
- [ ] Señales premium vs gratuitas (delay 1h gratis, instantáneo premium)
- [ ] Copy Trade API para suscriptores
- [ ] Pagina de precios simple en landing page

## ⚪ FASE 5 — EXPANSION
- [ ] Evaluar añadir ETH (si BTC no da suficientes señales)
- [ ] Dashboard con login para suscriptores
- [ ] Estadísticas semanales automáticas

---

## HOY: ¿Qué hacemos cuando volvemos?

### Prioridad 1 — GitHub Pages (2 minutos)
1. Ir a https://github.com/franotamendi7-commits/structural-expansion-flow/settings/pages
2. Source: `master branch /docs folder`
3. Listo → `https://franotamendi7-commits.github.io/structural-expansion-flow/`

### Prioridad 2 — Esperar primera señal real
- El bot ya está corriendo. Cuando aparezca una señal de entrada/salida real:
  - Se publica automáticamente en Telegram
  - Se firma con SHA256 + Ethereum
  - Aparece en la API
  - Se puede verificar en Etherscan

### Prioridad 3 — TradingView (10 minutos)
- Abrir TradingView → Pine Editor
- Pegar contenido de `vwap_strategy.pine`
- Click "Añadir a gráfico" → verificar que funciona
- Click "Publicar" → comunidad

### Prioridad 4 — Si querés monetizar
- Crear wallet USDC (ej: WalletConnect, Phantom)
- Definir precio: ej. $29/mes por señales instantáneas
- Configurar delay 1h en canal gratis

---

## LINKS PERMANENTES
| Recurso | Link |
|---|---|
| Canal Telegram | https://t.me/vwapsignals |
| Dashboard (túnel) | https://chelsea-alien-arrangement-fax.trycloudflare.com |
| API | https://medicaid-glossary-totals-personals.trycloudflare.com |
| Repo GitHub | https://github.com/franotamendi7-commits/structural-expansion-flow |
| GitHub Pages | (pendiente activar) |
| Wallet on-chain | 0xBEB7E29B235068F2CD455621db2373e5fc4De79b |
