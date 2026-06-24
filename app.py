import streamlit as st
import time, datetime, pandas as pd, json, requests, re, csv, os
import logging
from logging.handlers import RotatingFileHandler
import plotly.graph_objects as go
from adaptive_agent import AdaptiveAgent
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

# ============================================================
# CONFIGURACIÓN DE LOGGING ESTRUCTURADO (P0 — Mejora 1)
# ============================================================
os.makedirs("logs", exist_ok=True)
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    _formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _file_handler = RotatingFileHandler(
        "logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    _file_handler.setFormatter(_formatter)
    _file_handler.setLevel(logging.DEBUG)
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    _console_handler.setLevel(logging.INFO)
    _root_logger.addHandler(_file_handler)
    _root_logger.addHandler(_console_handler)
    _root_logger.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACIÓN GLOBAL
# ============================================================
ACTIVE_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
COOLDOWN_MINUTES = 15  # P0 — Mejora 2: cooldown por par después de cerrar posición
STOPMON_STATUS_FILE = "stop_monitor_status.json"

# ======================================================
# AUDITOR DE ENTRADA
# ======================================================
def _ensure_audit_file():
    AUDIT_FILE = "audit_log.csv"
    FIELDS = ["trade_id","timestamp_entry","symbol","side","score",
              "entry","sl","tp1","tp2","fib_label","phase",
              "ci","wr","st","veto","explanation_raw","exit_reason",
              "exit_price","pnl_final","duration_min"]
    if not os.path.exists(AUDIT_FILE):
        with open(AUDIT_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDS).writeheader()

def log_signal_taken(symbol, side, score, trade, explanation, fib_label, trade_id=None):
    _ensure_audit_file()
    if trade_id is None:
        trade_id = f"{symbol}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

    row = {
        "trade_id": trade_id,
        "timestamp_entry": datetime.datetime.now().isoformat(),
        "symbol": symbol,
        "side": side,
        "score": score,
        "entry": trade.get("entry"),
        "sl": trade.get("sl"),
        "tp1": trade.get("tp1"),
        "tp2": trade.get("tp2", ""),
        "fib_label": fib_label,
        "phase": "adaptive",
        "ci": "",
        "wr": "",
        "st": "",
        "veto": "",
        "explanation_raw": explanation,
        "exit_reason": "",
        "exit_price": "",
        "pnl_final": "",
        "duration_min": ""
    }
    with open("audit_log.csv", "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "trade_id","timestamp_entry","symbol","side","score",
            "entry","sl","tp1","tp2","fib_label","phase",
            "ci","wr","st","veto","explanation_raw","exit_reason",
            "exit_price","pnl_final","duration_min"
        ])
        writer.writerow(row)
    return trade_id

# ======================================================
# GESTIÓN DINÁMICA DE RIESGO
# ======================================================
class DynamicRiskManager:
    def __init__(self, base_risk_pct=0.01, max_daily_loss_pct=0.03, max_consecutive_losses=5):
        self.base_risk_pct = base_risk_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_pnl = 0.0
        self.last_day = None

    def get_adjusted_risk(self, capital, peak_capital, consecutive_losses):
        return 0.01

    def update_daily_pnl(self, pnl):
        today = datetime.date.today()
        if self.last_day != today:
            self.daily_pnl = 0.0
            self.last_day = today
        self.daily_pnl += pnl

def extract_fib_label(explanation):
    return "N/A"

def _extract_regime_from_explanation(explanation):
    if not isinstance(explanation, str):
        return None
    m = re.search(r'Régimen\s*1h:\s*(\w+)', explanation)
    if m:
        return m.group(1)
    m = re.search(r'Régimen:\s*(\w+)', explanation)
    if m:
        return m.group(1)
    return None

def _format_regime_display(regime_raw):
    if not regime_raw or regime_raw == 'unknown' or regime_raw == 'neutral':
        return "—"
    return regime_raw.upper().replace('_', ' ')

# ======================================================
# TELEGRAM
# ======================================================
def send_telegram_alert(token, chat_id, symbol, signal, trade, score, explanation):
    if not token or not chat_id:
        return
    contrarian = trade.get('contrarian', False)
    lines = []
    if contrarian:
        lines.append("⚠️ CONTRARIAN")
    lines.append("🚨 STRUCTURAL EXPANSION FLOW — SIGNAL")
    lines.append(f"Pair: {symbol}")
    lines.append(f"Signal: {signal}")
    lines.append(f"Entry: ${trade['entry']:.2f}")
    lines.append(f"Risk USD: ${trade.get('risk_usd', 0):.2f}")
    lines.append(f"SL: ${trade['sl']:.2f}")
    lines.append(f"TP1: ${trade['tp1']:.2f}")
    tp2 = trade.get('tp2')
    if tp2:
        lines.append(f"TP2: ${tp2:.2f}")
    lines.append(f"Score: {score}/100")
    lines.append(f"Explanation: {explanation}")
    message = "\n".join(lines)
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        logger.error(f"Telegram send error: {e}")

def send_telegram_message(token, chat_id, message):
    """Envía un mensaje libre a Telegram (para alertas inteligentes P0 — Mejora 3)."""
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        logger.error(f"Telegram message error: {e}")

def send_test_telegram(token, chat_id):
    if not token or not chat_id:
        st.error("❌ No se puede enviar prueba: Token o Chat ID vacíos")
        return False
    try:
        message = "🧪 TEST: Structural Expansion Flow - sistema operativo correctamente"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            st.success("✅ Mensaje de prueba enviado correctamente a Telegram")
            return True
        else:
            st.error(f"❌ Error al enviar: {r.text}")
            return False
    except Exception as e:
        st.error(f"❌ Excepción: {e}")
        return False

# ============================================================
# ALERTAS INTELIGENTES (P0 — Mejora 3)
# ============================================================
def _read_stopmon_status():
    """Lee stop_monitor_status.json para chequear errores consecutivos."""
    if not os.path.exists(STOPMON_STATUS_FILE):
        return None
    try:
        with open(STOPMON_STATUS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error leyendo stopmon status: {e}")
        return None

def _compute_regime_frequencies(audit_file="audit_log.csv", since_date=None):
    """Cuenta regímenes en audit_log.csv desde una fecha dada.
    Devuelve dict {'RANGO': 5, 'TENDENCIA_ALCISTA': 3, ...}
    """
    if not os.path.exists(audit_file):
        return {}
    try:
        df = pd.read_csv(audit_file)
        if df.empty or 'explanation_raw' not in df.columns:
            return {}
        if since_date is not None and 'timestamp_entry' in df.columns:
            df['timestamp_entry'] = pd.to_datetime(df['timestamp_entry'], errors='coerce')
            df = df[df['timestamp_entry'] >= since_date]
        regimes = df['explanation_raw'].apply(_extract_regime_from_explanation)
        regimes = regimes[regimes.notna()]
        return regimes.value_counts().to_dict()
    except Exception as e:
        logger.error(f"Error computando frecuencias de régimen: {e}")
        return {}

def _check_and_alert_status(trader, token, chat_id):
    """Ejecuta todos los checks de alerta inteligente (P0 — Mejora 3).
    Se llama desde el loop de auto-refresh. Throttling interno por tipo de alerta.
    """
    alert_state = st.session_state.get('alert_state')
    if alert_state is None:
        alert_state = {
            'last_dd_alert': None,            # None | '5pct' | '10pct'
            'last_silence_alert': None,       # timestamp o None
            'last_stopmon_error_alert': None, # timestamp o None
            'last_daily_summary_date': None,  # 'YYYY-MM-DD'
        }
        st.session_state['alert_state'] = alert_state

    now = datetime.datetime.now()
    now_ts = now.timestamp()

    # ── 1. Drawdown ──
    dd_pct = trader.get_drawdown_pct() * 100
    if dd_pct >= 10.0:
        if alert_state['last_dd_alert'] != '10pct':
            send_telegram_message(
                token, chat_id,
                f"🚨 URGENTE — Drawdown {dd_pct:.1f}% supera 10%. Trading pausado automáticamente. "
                f"Balance: ${trader.get_balance():.2f} | Peak: ${trader.get_peak_balance():.2f}"
            )
            trader.pause(f"Drawdown crítico {dd_pct:.1f}%")
            alert_state['last_dd_alert'] = '10pct'
            logger.warning(f"PAUSA automática por DD crítico: {dd_pct:.1f}%")
    elif dd_pct >= 5.0:
        if alert_state['last_dd_alert'] != '5pct':
            send_telegram_message(
                token, chat_id,
                f"⚠️ WARNING — Drawdown {dd_pct:.1f}% supera 5%. Balance: ${trader.get_balance():.2f}"
            )
            alert_state['last_dd_alert'] = '5pct'
            logger.warning(f"Alerta DD 5%: {dd_pct:.1f}%")
    else:
        # Si volvió a estar bajo, resetear para poder alertar de nuevo si sube
        if alert_state['last_dd_alert'] is not None:
            alert_state['last_dd_alert'] = None
            logger.info(f"DD volvió a estar bajo control: {dd_pct:.1f}%")

    # ── 2. Silencio > 4h sin señales ──
    last_signal_ts = st.session_state.get('last_signal_time')
    if last_signal_ts is not None:
        silence_hours = (now_ts - last_signal_ts) / 3600
        if silence_hours >= 4.0:
            # Throttle: no mandar más de 1 alerta de silencio cada 4h
            last_silence = alert_state['last_silence_alert']
            if last_silence is None or (now_ts - last_silence) >= 4 * 3600:
                send_telegram_message(
                    token, chat_id,
                    f"⚠️ Sin señales en {silence_hours:.1f}h. ¿Feed de Binance activo? "
                    f"Última señal: {datetime.datetime.fromtimestamp(last_signal_ts).strftime('%H:%M:%S')}"
                )
                alert_state['last_silence_alert'] = now_ts
                logger.warning(f"Alerta silencio: {silence_hours:.1f}h sin señales")

    # ── 3. Stop monitor: 3+ errores consecutivos ──
    stopmon = _read_stopmon_status()
    if stopmon and stopmon.get('errors_consecutive', 0) >= 3:
        last_stopmon_alert = alert_state['last_stopmon_error_alert']
        # Throttle: máximo 1 alerta cada 30 min
        if last_stopmon_alert is None or (now_ts - last_stopmon_alert) >= 1800:
            send_telegram_message(
                token, chat_id,
                f"🚨 Stop Monitor con {stopmon['errors_consecutive']} errores consecutivos. "
                f"Último error: {stopmon.get('last_error', 'desconocido')[:200]}"
            )
            alert_state['last_stopmon_error_alert'] = now_ts
            logger.error(f"Alerta stopmon: {stopmon['errors_consecutive']} errores consecutivos")

    # ── 4. Resumen diario a las 00:00 UTC ──
    today_utc = datetime.datetime.utcnow().date()
    yesterday_utc = today_utc - datetime.timedelta(days=1)
    if alert_state['last_daily_summary_date'] != str(today_utc):
        # Solo enviar si ya pasó medianoche UTC y hay trades del día anterior
        now_utc = datetime.datetime.utcnow()
        if now_utc.hour == 0 and now_utc.minute < 5:
            # Ventana de 5 min después de medianoche UTC para enviar el resumen
            start_of_yesterday = datetime.datetime.combine(yesterday_utc, datetime.time.min)
            trades_yesterday = [
                t for t in trader.get_closed_trades()
                if t.get('exit_time', '') >= start_of_yesterday.isoformat()
                and t.get('exit_time', '') < datetime.datetime.combine(today_utc, datetime.time.min).isoformat()
            ]
            if trades_yesterday:
                pnls = [t.get('pnl', 0) for t in trades_yesterday]
                wins = sum(1 for p in pnls if p > 0)
                wr = (wins / len(pnls) * 100) if pnls else 0
                pnl_total = sum(pnls)
                regime_freq = _compute_regime_frequencies(since_date=start_of_yesterday)
                regime_mas_frecuente = max(regime_freq, key=regime_freq.get) if regime_freq else "—"

                msg = (
                    f"📊 RESUMEN DIARIO ({yesterday_utc.isoformat()})\n"
                    f"Operaciones: {len(trades_yesterday)}\n"
                    f"Win Rate: {wr:.1f}% ({wins}W / {len(pnls)-wins}L)\n"
                    f"P&L del día: ${pnl_total:+.2f}\n"
                    f"Balance actual: ${trader.get_balance():.2f}\n"
                    f"Drawdown: {trader.get_drawdown_pct()*100:.1f}%\n"
                    f"Régimen más frecuente: {_format_regime_display(regime_mas_frecuente.lower())}\n"
                    f"Racha de pérdidas: {trader.consecutive_losses}"
                )
                send_telegram_message(token, chat_id, msg)
                alert_state['last_daily_summary_date'] = str(today_utc)
                logger.info(f"Resumen diario enviado: {len(trades_yesterday)} ops, P&L=${pnl_total:.2f}")

# ============================================================
# INICIALIZAR COMPONENTES
# ============================================================
if 'trader' not in st.session_state:
    st.session_state['trader'] = PaperTrader(initial_balance=100.0, state_file="paper_state.json")
    st.session_state['trader'].save_state()
trader = st.session_state['trader']

if 'risk_manager' not in st.session_state:
    st.session_state['risk_manager'] = DynamicRiskManager(base_risk_pct=0.01)
risk_manager = st.session_state['risk_manager']

if 'exec_mgr' not in st.session_state:
    st.session_state['exec_mgr'] = ExecutionManager(
        api_key="TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u",
        api_secret="DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo",
        testnet=True
    )
exec_mgr = st.session_state['exec_mgr']

if 'backend_price' not in st.session_state:
    st.session_state['backend_price'] = {}
if 'last_analysis' not in st.session_state:
    st.session_state['last_analysis'] = 0
if 'last_signal' not in st.session_state:
    st.session_state['last_signal'] = None
if 'last_signal_time' not in st.session_state:
    st.session_state['last_signal_time'] = None
if 'last_telegram_signal_id' not in st.session_state:
    st.session_state['last_telegram_signal_id'] = None
if 'previous_pair' not in st.session_state:
    st.session_state['previous_pair'] = None
if 'pair_states' not in st.session_state:
    st.session_state['pair_states'] = {pair: {
        'setup_state': 'INVALID',
        'signal': 'WAIT',
        'score': 0,
        'price': None,
        'agent_scores': {},
        'direction': 'neutral',
        'trade': None,
        'explanation': 'No analysis yet',
        'trend_h4': 'neutral',
        'market_phase': 'unknown',
        'fib_label': 'N/A'
    } for pair in ACTIVE_PAIRS}

# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="QNTFRY · Command Terminal", page_icon="⬡", layout="wide")

# ══════════════════════════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;900&family=Share+Tech+Mono&family=Rajdhani:wght@300;400;500;600;700&display=swap');

:root {
  --bg:          #020408;
  --bg2:         #040810;
  --glass:       rgba(6, 15, 30, 0.85);
  --glass2:      rgba(4, 10, 22, 0.92);
  --border:      rgba(0, 200, 255, 0.12);
  --border2:     rgba(0, 200, 255, 0.06);
  --neon-cyan:   #00d4ff;
  --neon-green:  #00ff88;
  --neon-red:    #ff2d6b;
  --neon-gold:   #ffb800;
  --neon-purple: #bf5fff;
  --neon-blue:   #4d7cff;
  --text:        #e2f0ff;
  --text-dim:    #5a7a99;
  --text-ghost:  #2a3d52;
  --font-hud:    'Orbitron', monospace;
  --font-mono:   'Share Tech Mono', monospace;
  --font-body:   'Rajdhani', sans-serif;
}

html, body, [data-testid="stApp"] {
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: var(--font-body) !important;
}

[data-testid="stApp"]::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(0,212,255,0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,212,255,0.03) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
  z-index: 0;
  animation: gridPan 60s linear infinite;
}
@keyframes gridPan {
  0%   { background-position: 0 0; }
  100% { background-position: 40px 40px; }
}

[data-testid="stApp"]::after {
  content: '';
  position: fixed;
  width: 600px; height: 600px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(0,212,255,0.04) 0%, transparent 70%);
  top: -200px; left: -200px;
  pointer-events: none;
  z-index: 0;
  animation: orbFloat 12s ease-in-out infinite alternate;
}
@keyframes orbFloat {
  from { transform: translate(0, 0); }
  to   { transform: translate(80px, 60px); }
}

.main .block-container {
  padding: 1.2rem 1.8rem 2rem !important;
  max-width: 100% !important;
  position: relative; z-index: 1;
}

::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--neon-cyan); border-radius: 3px; opacity: 0.3; }

[data-testid="stSidebar"] {
  background: var(--glass2) !important;
  border-right: 1px solid var(--border) !important;
  backdrop-filter: blur(20px) !important;
}
[data-testid="stSidebar"]::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg, transparent, var(--neon-cyan), transparent);
  animation: scanLine 3s ease-in-out infinite;
}
@keyframes scanLine {
  0%, 100% { opacity: 0.3; }
  50%       { opacity: 1; }
}
[data-testid="stSidebar"] * { color: var(--text) !important; }
[data-testid="stSidebar"] .stSelectbox > div > div {
  background: rgba(0,20,40,0.8) !important;
  border: 1px solid var(--border) !important;
  border-radius: 6px !important;
  font-family: var(--font-mono) !important;
}
section[data-testid="stSidebar"] [data-testid="stButton"] > button {
  background: rgba(0,212,255,0.06) !important;
  border: 1px solid rgba(0,212,255,0.3) !important;
  border-radius: 6px !important;
  color: var(--neon-cyan) !important;
  font-family: var(--font-mono) !important;
  font-size: 11px !important;
  letter-spacing: 0.15em !important;
  transition: all 0.3s !important;
  text-transform: uppercase !important;
}
section[data-testid="stSidebar"] [data-testid="stButton"] > button:hover {
  background: rgba(0,212,255,0.15) !important;
  box-shadow: 0 0 20px rgba(0,212,255,0.3) !important;
}

.stButton > button {
  background: rgba(0,212,255,0.06) !important;
  border: 1px solid rgba(0,212,255,0.35) !important;
  color: var(--neon-cyan) !important;
  border-radius: 6px !important;
  font-family: var(--font-mono) !important;
  font-size: 11px !important;
  letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  transition: all 0.3s ease !important;
}
.stButton > button:hover {
  background: rgba(0,212,255,0.14) !important;
  box-shadow: 0 0 25px rgba(0,212,255,0.35) !important;
  transform: translateY(-1px) !important;
}

div[data-testid="stExpander"] {
  background: var(--glass) !important;
  border: 1px solid var(--border) !important;
  border-radius: 10px !important;
  backdrop-filter: blur(12px) !important;
}
div[data-testid="stExpander"] summary {
  color: var(--text-dim) !important;
  font-family: var(--font-mono) !important;
  font-size: 11px !important;
  letter-spacing: 0.1em !important;
}

.master-header {
  position: relative;
  padding: 1.4rem 2rem;
  margin-bottom: 1.6rem;
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 14px;
  backdrop-filter: blur(24px);
  overflow: hidden;
  box-shadow:
    0 0 40px rgba(0,212,255,0.05),
    inset 0 1px 0 rgba(0,212,255,0.1);
}
.master-header::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg,
    transparent 0%, var(--neon-cyan) 30%,
    var(--neon-green) 70%, transparent 100%);
  animation: headerScan 4s ease-in-out infinite;
}
@keyframes headerScan {
  0%, 100% { opacity: 0.5; transform: scaleX(0.8); }
  50%       { opacity: 1;   transform: scaleX(1); }
}
.master-header::after {
  content: '⬡';
  position: absolute;
  right: 2rem; top: 50%;
  transform: translateY(-50%);
  font-size: 8rem;
  color: rgba(0,212,255,0.03);
  font-family: var(--font-hud);
  pointer-events: none;
}

.hdr-title {
  font-family: var(--font-hud);
  font-size: 1.7rem;
  font-weight: 900;
  background: linear-gradient(90deg, var(--neon-cyan), var(--neon-blue));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  letter-spacing: 0.06em;
}
.hdr-sub {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  letter-spacing: 0.18em;
  margin-top: 4px;
  text-transform: uppercase;
}
.hdr-live {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.live-dot-wrap {
  display: flex; align-items: center; gap: 8px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--neon-green);
  letter-spacing: 0.18em;
}
.live-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--neon-green);
  box-shadow: 0 0 12px var(--neon-green);
  animation: livePulse 1.5s ease-in-out infinite;
}
@keyframes livePulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.5; transform: scale(0.7); }
}
.clock {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text);
  letter-spacing: 0.1em;
}

.sec-title {
  font-family: var(--font-hud);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--neon-cyan);
  text-transform: uppercase;
  margin: 1.6rem 0 1rem;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
  position: relative;
}
.sec-title::after {
  content: '';
  position: absolute;
  bottom: -1px; left: 0;
  width: 60px; height: 1px;
  background: var(--neon-cyan);
  box-shadow: 0 0 8px var(--neon-cyan);
}

.agent-card {
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem;
  position: relative;
  overflow: hidden;
  backdrop-filter: blur(12px);
  --accent-color: var(--neon-cyan);
  --accent-glow: rgba(0,212,255,0.08);
  transition: all 0.3s;
  height: 100%;
}
.agent-card:hover {
  border-color: var(--accent-color);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.4), 0 0 20px var(--accent-glow);
}
.agent-accent {
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
}
.agent-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.agent-icon {
  width: 36px; height: 36px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.agent-name {
  font-family: var(--font-hud);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text);
  text-transform: uppercase;
}
.agent-role {
  font-family: var(--font-body);
  font-size: 10px;
  color: var(--text-dim);
  margin-top: 2px;
}
.status-pill {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 8px;
  padding: 3px 8px;
  border-radius: 20px;
  letter-spacing: 0.15em;
  text-transform: uppercase;
}
.agent-body { padding-top: 4px; }
.data-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0;
  border-bottom: 1px solid var(--border2);
  font-family: var(--font-mono);
  font-size: 11px;
}
.data-label { color: var(--text-dim); }
.data-value { color: var(--text); font-weight: 700; }
.info-box {
  margin-top: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--accent-color);
  background: var(--accent-glow);
  color: var(--accent-color);
  font-family: var(--font-mono);
  font-size: 10px;
  text-align: center;
}

.commander-wrap {
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 1.4rem;
  position: relative;
  overflow: hidden;
  backdrop-filter: blur(16px);
}
.commander-wrap::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--neon-blue), transparent);
  animation: cmdScan 3s ease-in-out infinite;
}
@keyframes cmdScan {
  0%,100% { opacity: 0.6; }
  50%     { opacity: 1; }
}
.commander-wrap::after {
  content: 'CMD';
  position: absolute;
  right: 1.5rem; bottom: 1rem;
  font-family: var(--font-hud);
  font-size: 5rem;
  font-weight: 900;
  color: rgba(77,124,255,0.04);
  letter-spacing: 0.1em;
  pointer-events: none;
  line-height: 1;
}

.cmd-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
}
.cmd-icon {
  width: 44px; height: 44px;
  background: rgba(77,124,255,0.12);
  border: 1px solid rgba(77,124,255,0.3);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
  box-shadow: 0 0 20px rgba(77,124,255,0.2);
}
.cmd-title {
  font-family: var(--font-hud);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--text);
}
.cmd-sub {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 2px;
}
.cmd-operational {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 4px 10px;
  border-radius: 20px;
  background: rgba(77,124,255,0.12);
  border: 1px solid rgba(77,124,255,0.35);
  color: #6d9fff;
  letter-spacing: 0.1em;
}

.cmd-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}
.cmd-stat-cell {
  background: rgba(0,5,15,0.7);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 10px 12px;
  position: relative;
  overflow: hidden;
  transition: border-color 0.3s;
}
.cmd-stat-cell:hover { border-color: rgba(77,124,255,0.35); }
.cmd-stat-cell::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(77,124,255,0.3), transparent);
}
.cmd-stat-label {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--text-ghost);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  margin-bottom: 4px;
}
.cmd-stat-value {
  font-family: var(--font-hud);
  font-size: 14px;
  font-weight: 700;
}

.signal-area {
  border-top: 1px solid var(--border2);
  padding-top: 14px;
}
.signal-tag {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-hud);
  font-size: 11px;
  font-weight: 700;
  padding: 5px 14px;
  border-radius: 20px;
  letter-spacing: 0.12em;
  margin-bottom: 12px;
  text-transform: uppercase;
}

.setup-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 7px;
  margin-bottom: 8px;
}
.setup-cell {
  background: rgba(0,5,15,0.8);
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 9px 10px;
  text-align: center;
  position: relative;
  overflow: hidden;
  transition: all 0.25s;
}
.setup-cell:hover {
  border-color: rgba(0,212,255,0.3);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.5);
}
.setup-cell::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(0,212,255,0.2), transparent);
}
.setup-label {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--text-ghost);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  margin-bottom: 4px;
}
.setup-value {
  font-family: var(--font-hud);
  font-size: 12px;
  font-weight: 700;
}

.consensus-row {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--border2);
  flex-wrap: wrap;
}
.consensus-node {
  display: flex;
  align-items: center;
  gap: 5px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-dim);
}
.c-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  animation: dotBlink 2s ease-in-out infinite;
}
@keyframes dotBlink {
  0%, 80%, 100% { opacity: 1; }
  40% { opacity: 0.4; }
}

.conf-bar-wrap { margin-top: 10px; }
.conf-bar-labels {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-dim);
  margin-bottom: 5px;
}
.conf-bar-track {
  height: 4px;
  background: var(--border2);
  border-radius: 4px;
  overflow: hidden;
  position: relative;
}
.conf-bar-fill {
  height: 100%;
  border-radius: 4px;
  position: relative;
  transition: width 1.2s cubic-bezier(.4,0,.2,1);
}
.conf-bar-fill::after {
  content: '';
  position: absolute;
  right: 0; top: 0; bottom: 0;
  width: 4px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 0 8px currentColor;
  filter: blur(1px);
}

.raw-json {
  background: rgba(0,5,10,0.95);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1.2rem;
  font-family: var(--font-mono);
  font-size: 11px;
  color: #00cc99;
  overflow-x: auto;
  white-space: pre;
  line-height: 1.7;
}

.sb-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border2);
  font-family: var(--font-mono);
  font-size: 11px;
}
.sb-row-label { color: var(--text-dim); }

.n-cyan   { color: var(--neon-cyan); }
.n-green  { color: var(--neon-green); }
.n-red    { color: var(--neon-red); }
.n-gold   { color: var(--neon-gold); }
.n-purple { color: var(--neon-purple); }
.n-blue   { color: var(--neon-blue); }
.n-dim    { color: var(--text-dim); }

.glow-green { text-shadow: 0 0 10px rgba(0,255,136,0.6), 0 0 30px rgba(0,255,136,0.3); }
.glow-red   { text-shadow: 0 0 10px rgba(255,45,107,0.6), 0 0 30px rgba(255,45,107,0.3); }
.glow-cyan  { text-shadow: 0 0 10px rgba(0,212,255,0.6), 0 0 30px rgba(0,212,255,0.3); }
.glow-gold  { text-shadow: 0 0 10px rgba(255,184,0,0.6), 0 0 30px rgba(255,184,0,0.3); }

.perf-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 1.2rem;
}
.perf-cell {
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  backdrop-filter: blur(12px);
  text-align: center;
}
.perf-label { font-family: var(--font-mono); font-size: 8px; color: var(--text-dim); letter-spacing: 0.15em; text-transform: uppercase; }
.perf-value { font-family: var(--font-hud); font-size: 18px; font-weight: 700; color: var(--text); margin-top: 4px; }
</style>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("""
    <div style="padding:.8rem 0 1.4rem;">
      <div style="font-family:'Share Tech Mono',monospace;font-size:9px;
                  letter-spacing:.28em;text-transform:uppercase;
                  color:#2a3d52;margin-bottom:5px;">System</div>
      <div style="font-family:'Orbitron',monospace;font-size:13px;font-weight:700;
                  background:linear-gradient(90deg,#00d4ff,#4d7cff);
                  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
                  background-clip:text;letter-spacing:.1em;">⬡ AGENT CONTROL</div>
    </div>
    """, unsafe_allow_html=True)

    pair = st.selectbox("TRADING PAIR", ACTIVE_PAIRS, index=0)
    if st.session_state['previous_pair'] != pair:
        st.session_state['last_telegram_signal_id'] = None
        st.session_state['last_signal'] = None
        st.session_state['previous_pair'] = pair

    risk_level = st.select_slider("BASE RISK %", options=[0.5, 1.0, 1.5], value=1.0)
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    auto = st.checkbox("AUTO REFRESH (60s)", value=False)
    run_btn = st.button("▶  ANALIZAR AHORA", use_container_width=True)
    if run_btn:
        st.session_state['last_telegram_signal_id'] = None

    st.markdown("---")
    st.markdown("### 📨 TELEGRAM ALERTS")
    telegram_token = st.text_input("Bot Token", type="password", value="8813532919:AAF4FcqNCMA5jfeiHDp71M-lbqLbBh3RuzY")
    telegram_chat_id = st.text_input("Chat ID", value="8026382563")
    if telegram_token and telegram_chat_id:
        st.success("✅ Alertas activas")
        if st.button("📤 Enviar mensaje de prueba"):
            send_test_telegram(telegram_token, telegram_chat_id)
    else:
        st.info("Completa ambos campos para recibir alertas")

    st.markdown("---")
    st.markdown("### 🟢 LIVE EXECUTION (Binance)")
    binance_api_key = st.text_input("API Key", type="password", value="TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u")
    binance_secret_key = st.text_input("Secret Key", type="password", value="DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo")
    use_testnet = st.checkbox("Usar Testnet", value=True)
    enable_live_trading = st.checkbox("Activar ejecución real (riesgo real)", value=False)
    use_real_balance = st.checkbox("Usar balance real (Testnet)", value=False)

    if enable_live_trading and (not binance_api_key or not binance_secret_key):
        st.warning("⚠️ Las credenciales de API son necesarias para activar ejecución real")
    elif enable_live_trading:
        st.success("✅ Live trading activado. Las órdenes se enviarán a Binance.")
    else:
        st.info("Modo paper trading (ejecución simulada)")

    # P0 — Mejora 2: cooldown visible en sidebar
    st.markdown("---")
    st.markdown(f"### ⏱ COOLDOWN: {COOLDOWN_MINUTES} min")
    active_cooldowns = {sym: trader.cooldown_remaining(sym, COOLDOWN_MINUTES)
                        for sym in ACTIVE_PAIRS if trader.is_in_cooldown(sym, COOLDOWN_MINUTES)}
    if active_cooldowns:
        for sym, remaining in active_cooldowns.items():
            st.markdown(f"🔒 **{sym}**: {remaining:.1f} min restantes")
    else:
        st.caption("Sin cooldowns activos")

    # P0 — Mejora 3: control de pausa manual
    st.markdown("---")
    if trader.manually_paused:
        st.error(f"🚫 Trading PAUSADO: {trader.pause_reason}")
        if st.button("▶ Reanudar trading"):
            trader.resume()
            st.rerun()
    else:
        st.success("✅ Trading activo")

# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════
now_str = datetime.datetime.now().strftime("%Y-%m-%d  |  %H:%M:%S UTC")
st.markdown(f"""
<div class="master-header">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;">
    <div>
      <div class="hdr-title">Quantfury Command Terminal</div>
      <div class="hdr-sub">Adaptive Agent · Régimen Detector · 5 Agent System · 20x Leverage · Fixed 1% Risk</div>
    </div>
    <div class="hdr-live">
      <div class="live-dot-wrap"><div class="live-dot"></div>LIVE FEED</div>
      <div class="clock">{now_str}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ---------- MÉTRICAS DE BALANCE ----------
if use_real_balance:
    try:
        balance = exec_mgr.get_balance("USDT")
    except Exception:
        balance = trader.get_balance()
else:
    balance = trader.get_balance()

posiciones_abiertas = len(trader.get_all_open_positions())
pnl_abierto_total = sum(pos.get('pnl', 0) for pos in trader.get_all_open_positions().values())
closed_trades = trader.get_closed_trades()
pnl_cerrado = sum(t['pnl'] for t in closed_trades) if closed_trades else 0.0

cols_metrics = st.columns(6)
cols_metrics[0].metric("BALANCE", f"${balance:,.2f}")
cols_metrics[1].metric("MARGIN 20x", f"${balance * 20:,.0f}")
cols_metrics[2].metric("RISK / OP", f"${balance * 0.01:,.2f} (1.0%)")
cols_metrics[3].metric("P&L ABIERTO", f"${pnl_abierto_total:,.2f}")
cols_metrics[4].metric("OPEN OPS", f"{posiciones_abiertas} / {len(ACTIVE_PAIRS)}")
cols_metrics[5].metric("P&L CERRADO", f"${pnl_cerrado:,.2f}")

# ========== PANEL DE ESTADO MULTI-PAR (CON P&L LIVE) ==========
pair_status_data = []
color_map = {'FORMING':'#ffb800', 'EXECUTE':'#00ff88', 'READY':'#4d7cff', 'INVALID':'#ff2d6b'}
for p in ACTIVE_PAIRS:
    open_pos = trader.get_open_position(p)
    state = st.session_state['pair_states'].get(p, {})

    if open_pos is not None:
        setup_state = 'EXECUTE'
        signal_val = open_pos['side']
        score_val = state.get('score', 0)
        price_val = state.get('price')
        pnl = open_pos.get('pnl', 0.0)
        current_price = open_pos.get('current_price', price_val)
        stop_loss = open_pos.get('stop_loss', 0)
        if current_price and stop_loss and current_price > 0:
            if signal_val == 'LONG':
                distance_pct = ((current_price - stop_loss) / current_price) * 100
            else:
                distance_pct = ((stop_loss - current_price) / current_price) * 100
            bar_pct = max(0, min(100, 100 - distance_pct * 10))
            color_bar = '#00ff88' if pnl >= 0 else '#ff2d6b'
            pnl_str = f"{'+' if pnl >= 0 else ''}{pnl:.2f}"
            pnl_color = '#00ff88' if pnl >= 0 else '#ff2d6b'
            live_html = f"""
            <div style="margin: 0;">
              <div style="height:4px; background: #2a3d52; border-radius:4px; overflow:hidden; margin-bottom:4px;">
                <div style="width:{bar_pct:.1f}%; height:100%; background:{color_bar}; border-radius:4px; transition:width 0.5s;"></div>
              </div>
              <div style="font-family:'Share Tech Mono', monospace; font-size:9px; color:{pnl_color}; letter-spacing:0.05em;">
                ${pnl_str} &nbsp;|&nbsp; <span style="color:#5a7a99;">SL a {distance_pct:.1f}%</span>
              </div>
            </div>
            """
        else:
            live_html = "—"
    else:
        setup_state = state.get('setup_state', 'INVALID')
        signal_val = state.get('signal', 'WAIT')
        score_val = state.get('score', 0)
        price_val = state.get('price')
        live_html = "—"

    price_display = f"${price_val:,.2f}" if price_val else "—"

    # Description: régimen real + estado
    regime_display = _format_regime_display(state.get('market_phase', 'unknown'))
    if setup_state == 'FORMING':
        status_msg = "Esperando entrada"
    elif setup_state == 'INVALID':
        status_msg = "Sin condiciones"
    elif setup_state == 'EXECUTE':
        status_msg = "Entrada ejecutada"
    elif setup_state == 'READY':
        status_msg = "Listo para ejecutar"
    else:
        status_msg = setup_state
    description = f"Régimen: {regime_display} · {status_msg}"

    pair_status_data.append([p, price_display, setup_state, f"{score_val}%", signal_val, description, live_html])

st.markdown("""
<table style="width:100%; border-collapse: collapse; background: var(--glass); border-radius: 12px; overflow: hidden;">
  <thead>
    <tr style="border-bottom: 1px solid var(--border);">
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Pair</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Last Price</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Setup State</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Score</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Signal</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">Description</th>
      <th style="padding: 12px; font-family: 'Orbitron'; font-size: 11px; letter-spacing: 0.1em; color: var(--neon-cyan);">P&L Live</th>
    </tr>
  </thead>
  <tbody>
""", unsafe_allow_html=True)
for row in pair_status_data:
    color_setup = color_map.get(row[2], '#888')
    st.markdown(f"""
    <tr style="border-bottom: 1px solid var(--border2);">
      <td style="padding: 10px; font-family: 'Share Tech Mono'; font-size: 12px;">{row[0]}</td>
      <td style="padding: 10px; font-family: 'Share Tech Mono'; font-size: 12px;">{row[1]}</td>
      <td style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: {color_setup};">{row[2]}</td>
      <td style="padding: 10px; font-family: 'Orbitron'; font-size: 11px;">{row[3]}</td>
      <td style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; font-weight: bold;">{row[4]}</td>
      <td style="padding: 10px; font-family: 'Share Tech Mono'; font-size: 11px; color: var(--text-dim);">{row[5]}</td>
      <td style="padding: 10px; font-family: 'Share Tech Mono'; font-size: 11px;">{row[6]}</td>
    </tr>
    """, unsafe_allow_html=True)
st.markdown("</tbody></table>", unsafe_allow_html=True)

# ---------- LÓGICA DE ACTUALIZACIÓN ----------
now = time.time()
FIXED_RISK_PCT = 0.01
risk_manager.base_risk_pct = FIXED_RISK_PCT

@st.cache_resource(ttl=3600)
def get_engine(symbol):
    return AdaptiveAgent(symbol=symbol, capital=100.0, risk_pct=FIXED_RISK_PCT)

def process_signal_for_pair(res, symbol, token, chat_id):
    if res['signal'] in ('LONG', 'SHORT') and res.get('trade') and not trader.is_paused():
        # P0 — Mejora 2: cooldown por par
        if trader.is_in_cooldown(symbol, COOLDOWN_MINUTES):
            remaining = trader.cooldown_remaining(symbol, COOLDOWN_MINUTES)
            logger.info(f"Señal {res['signal']} {symbol} ignorada por cooldown ({remaining:.1f} min restantes)")
            return

        trade = res['trade']
        trade_id = f"{symbol}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

        signal = {
            'symbol': symbol,
            'side': res['signal'],
            'price': trade['entry'],
            'stop_loss': trade['sl'],
            'take_profit': trade['tp1'],
            'tp2': trade.get('tp2'),
            'risk_usd': trade.get('risk_usd', 0),
            'contracts': trade.get('contracts', 0),
            'contrarian': trade.get('contrarian', False),
            'trade_id': trade_id
        }

        if enable_live_trading:
            try:
                real_side = "BUY" if res['signal'] == "LONG" else "SELL"
                order = exec_mgr.execute_signal({
                    "symbol": symbol,
                    "side": real_side,
                    "quantity": trade['contracts']
                })
                with open("exchange_log.json", "a") as log_ex:
                    log_ex.write(json.dumps({
                        "timestamp": datetime.datetime.now().isoformat(),
                        "symbol": symbol,
                        "side": real_side,
                        "response": order
                    }) + "\n")
                if order.get("error"):
                    st.warning(f"⚠️ Orden real rechazada: {order['error']} — no se abre en paper")
                    return
                else:
                    st.success(f"✅ Orden real ejecutada: ID {order.get('order_id')}")
            except Exception as e:
                st.error(f"❌ Error al enviar orden real: {e} — no se abre en paper")
                logger.error(f"Error orden real {symbol}: {e}", exc_info=True)
                return

        success = trader.open_trade(signal)
        if success:
            # P0 — Mejora 3: trackear última señal para alerta de silencio
            st.session_state['last_signal_time'] = time.time()

            log_signal_taken(
                symbol=symbol,
                side=res['signal'],
                score=res.get('score', 0),
                trade=trade,
                explanation=res.get('explanation', ''),
                fib_label=extract_fib_label(res.get('explanation', '')),
                trade_id=trade_id
            )
            logger.info(f"Señal tomada: {symbol} {res['signal']} @ {trade['entry']:.4f} | trade_id={trade_id}")

            signal_id = f"{symbol}_{res['signal']}_{trade['entry']:.2f}"
            if st.session_state.get('last_telegram_signal_id') != signal_id:
                send_telegram_alert(
                    token, chat_id,
                    symbol=symbol,
                    signal=res['signal'],
                    trade=trade,
                    score=res.get('score', 0),
                    explanation=res.get('explanation', '')
                )
                st.session_state['last_telegram_signal_id'] = signal_id

def update_pair_state(pair, res, price):
    if trader.get_open_position(pair) is not None:
        if pair in st.session_state['pair_states']:
            st.session_state['pair_states'][pair]['price'] = price
            if st.session_state['pair_states'][pair].get('signal') == 'WAIT' or not st.session_state['pair_states'][pair].get('direction'):
                pos = trader.get_open_position(pair)
                st.session_state['pair_states'][pair].update({
                    'setup_state': 'EXECUTE',
                    'signal': pos['side'],
                    'direction': pos['side'].lower(),
                    'score': 95,
                    'agent_scores': {'scanner': 85, 'risk': 80, 'technical': 90, 'momentum': 85, 'guard': 80},
                    'explanation': 'Posición abierta (datos restaurados)',
                    'trend_h4': 'neutral',
                    'market_phase': 'unknown',
                    'fib_label': 'N/A'
                })
        return
    st.session_state['pair_states'][pair] = {
        'setup_state': res.get('setup_state', 'INVALID'),
        'signal': res.get('signal', 'WAIT'),
        'score': res.get('score', 0),
        'price': price,
        'agent_scores': res.get('agent_scores', {}),
        'direction': res.get('direction', 'neutral'),
        'trade': res.get('trade'),
        'explanation': res.get('explanation', 'No analysis yet'),
        'trend_h4': res.get('trend_h4', 'neutral'),
        'market_phase': res.get('market_phase', 'unknown'),
        'fib_label': extract_fib_label(res.get('explanation', ''))
    }

# ----- AUTO-REFRESH MULTI-PAR -----
if auto and now - st.session_state['last_analysis'] > 60:
    with st.spinner(f"Analizando {len(ACTIVE_PAIRS)} pares..."):
        for current_pair in ACTIVE_PAIRS:
            price = get_ticker(current_pair)
            if price:
                st.session_state['backend_price'][current_pair] = price
            engine = get_engine(current_pair)
            if use_real_balance:
                try:
                    engine.capital = exec_mgr.get_balance("USDT")
                except Exception:
                    engine.capital = trader.get_balance()
            else:
                engine.capital = trader.get_balance()
            engine.risk_pct = FIXED_RISK_PCT
            res = engine.run()
            update_pair_state(current_pair, res, price)
            process_signal_for_pair(res, current_pair, telegram_token, telegram_chat_id)
        for sym, price in st.session_state['backend_price'].items():
            trader.update_position(sym, price)
        st.session_state['last_analysis'] = now

        # P0 — Mejora 3: alertas inteligentes
        try:
            _check_and_alert_status(trader, telegram_token, telegram_chat_id)
        except Exception as e:
            logger.error(f"Error en check_and_alert_status: {e}", exc_info=True)

    time.sleep(60)
    st.rerun()

# ----- MODO MANUAL -----
if run_btn:
    with st.spinner(f"Analizando {pair}..."):
        price = get_ticker(pair)
        if price:
            st.session_state['backend_price'][pair] = price
        engine = get_engine(pair)
        if use_real_balance:
            try:
                engine.capital = exec_mgr.get_balance("USDT")
            except Exception:
                engine.capital = trader.get_balance()
        else:
            engine.capital = trader.get_balance()
        engine.risk_pct = FIXED_RISK_PCT
        res = engine.run()
        st.session_state['last_signal'] = res
        update_pair_state(pair, res, price)
        process_signal_for_pair(res, pair, telegram_token, telegram_chat_id)
        trader.update_position(pair, price)

        # En modo manual también corremos los checks de alerta
        try:
            _check_and_alert_status(trader, telegram_token, telegram_chat_id)
        except Exception as e:
            logger.error(f"Error en check_and_alert_status (manual): {e}", exc_info=True)

# ---------- DATOS PARA LA UI ----------
if st.session_state.get('last_signal'):
    res = st.session_state['last_signal']
else:
    res = st.session_state['pair_states'].get(pair, {})

agent_scores = st.session_state['pair_states'].get(pair, {}).get('agent_scores', {})
setup_state = res.get('setup_state', 'INVALID')
signal = res.get('signal', 'WAIT')
direction = res.get('direction', 'neutral')
trade = res.get('trade', None)
dynamic_score = res.get('score', 0)
explanation = res.get('explanation', 'No analysis yet')
pos = trader.get_open_position(pair)

# ---------- TARJETAS DE AGENTES (5 agentes — sin ML AGENT) ----------
st.markdown('<div class="sec-title">Neural Agents — Nodes 01 → 05</div>', unsafe_allow_html=True)
ag_cols = st.columns(5)
agent_labels = {
    'scanner':    ('MARKET SCANNER',    'Setup detection · multi‑TF',  '#00ff88', 'rgba(0,255,136,0.08)'),
    'risk':       ('RISK MANAGER',      'Capital control · 20x exp',   '#4d7cff', 'rgba(77,124,255,0.08)'),
    'technical':  ('TECHNICAL ANALYST', 'Price action · estructura',   '#bf5fff', 'rgba(191,95,255,0.08)'),
    'momentum':   ('MOMENTUM TRACKER',  'Volumen · fuerza direccional','#ffb800', 'rgba(255,184,0,0.08)'),
    'guard':      ('EXECUTION GUARD',   'Validación de entrada',       '#ff2d6b', 'rgba(255,45,107,0.08)'),
}
for i, (key, (name, role, color, glow)) in enumerate(agent_labels.items()):
    score_val = agent_scores.get(key, 0)
    with ag_cols[i]:
        st.markdown(f"""
        <div class="agent-card" style="--accent-color:{color};--accent-glow:{glow};">
          <div class="agent-accent" style="background:{color};box-shadow:0 0 12px {color};color:{color};"></div>
          <div class="agent-header">
            <div class="agent-icon" style="background:{glow.replace('0.08','0.1')};border-color:rgba(255,255,255,0.05);box-shadow:0 0 20px {glow};">
              <span style="color:{color};">⬡</span>
            </div>
            <div>
              <div class="agent-name">{name}</div>
              <div class="agent-role">{role}</div>
            </div>
            <span class="status-pill" style="background:{glow.replace('0.08','0.1')};color:{color};border:1px solid {color}40;">ACTIVE</span>
          </div>
          <div class="agent-body">
            <div class="data-row"><span class="data-label">Confidence</span><span class="data-value" style="color:{color};">{score_val}%</span></div>
            <div class="info-box" style="--accent-color:{color};">
              <span style="font-size:11px;">{'High quality' if score_val >= 70 else 'Moderate'}</span>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

# ---------- COMMANDER AGENT ----------
st.subheader(f"Commander Agent · {pair}")
state_color = {'FORMING':'#ffb800','READY':'#4d7cff','EXECUTE':'#00ff88','INVALID':'#ff2d6b'}
state_text = {'FORMING':'⚙️ FORMING – Setup under construction','READY':'🔵 READY – Waiting for confirmation','EXECUTE':'🟢 EXECUTE – Entry confirmed','INVALID':'🔴 INVALID – No valid setup'}
st.markdown(f"**Setup State:** <span style='color:{state_color.get(setup_state,'gray')}; font-family:Orbitron; font-size:18px;'>{state_text.get(setup_state, setup_state)}</span>", unsafe_allow_html=True)

col_score, col_exp = st.columns([1,3])
with col_score:
    st.metric("Dynamic Score", f"{dynamic_score} / 100", delta=None)
with col_exp:
    st.info(f"**Explanation:** {explanation}")

if signal != 'WAIT':
    st.success(f"**Signal:** {signal} {direction.upper()}")
    if trade is not None:
        st.write(f"**Entry:** ${trade['entry']:,.2f}  |  **Stop Loss:** ${trade['sl']:,.2f}  |  **TP1:** ${trade['tp1']:,.2f}")
        if trade.get('tp2'):
            st.write(f"**TP2:** ${trade['tp2']:,.2f}")
        if trade.get('contrarian'):
            st.warning("⚠️ Contrarian (risk reduced 50%)")
else:
    st.warning("No trade ready for this pair")

if pos:
    st.subheader(f"Open Position · {pair}")
    st.write(f"Side: {pos['side']} | Entry: ${pos['entry_price']:,.2f} | Current: ${pos['current_price']:,.2f}")
    st.write(f"SL: ${pos['stop_loss']:,.2f} | TP1: ${pos.get('take_profit', 0):,.2f} | PnL: ${pos['pnl']:.2f}")
    if st.button(f"Close position for {pair}"):
        trader.force_close(symbol=pair)
        st.rerun()

with st.expander("⚙️ Risk Management Details"):
    dd_pct = trader.get_drawdown_pct() * 100
    st.write(f"**Drawdown actual:** {dd_pct:.2f}%")
    st.write(f"**Racha de pérdidas:** {trader.consecutive_losses}/{trader.max_consecutive_losses}")
    st.write(f"**P&L del día:** ${trader.daily_pnl:.2f}")
    st.write(f"**Balance máximo histórico:** ${trader.get_peak_balance():,.2f}")
    st.write(f"**Riesgo por operación:** 1% fijo (no ajustado por drawdown)")
    if trader.manually_paused:
        st.error(f"🚫 Trading en pausa MANUAL: {trader.pause_reason}")
    elif trader.is_paused():
        st.error("🚫 Trading en pausa por protección de capital.")
    else:
        st.success("✅ Trading activo")

st.subheader("Trade History")
if closed_trades:
    df = pd.DataFrame(closed_trades)
    cols = ['symbol', 'side', 'entry_price', 'exit_price', 'pnl', 'reason', 'exit_time']
    st.dataframe(df[[c for c in cols if c in df.columns]])

# ══════════════════════════════════════════════════════════════════
# PERFORMANCE OVERVIEW
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sec-title">Performance Overview</div>', unsafe_allow_html=True)

if use_real_balance:
    try:
        balance_ps = exec_mgr.get_balance("USDT")
    except Exception:
        balance_ps = trader.get_balance()
else:
    balance_ps = trader.get_balance()

paper_state = {}
if os.path.exists("paper_state.json"):
    with open("paper_state.json", "r") as f:
        try:
            paper_state = json.load(f)
        except Exception:
            paper_state = {}

if use_real_balance:
    peak_balance = max(balance_ps, paper_state.get('peak_balance', balance_ps))
    total_pnl = sum(t.get('pnl', 0) for t in paper_state.get('closed_trades', []))
else:
    peak_balance = paper_state.get('peak_balance', balance_ps) if paper_state else balance_ps
    total_pnl = sum(t.get('pnl', 0) for t in paper_state.get('closed_trades', [])) if paper_state else 0.0

if peak_balance > 0:
    drawdown_percent = (peak_balance - balance_ps) / peak_balance * 100
else:
    drawdown_percent = 0.0

st.markdown(f"""
<div class="perf-row">
  <div class="perf-cell"><div class="perf-label">Balance</div><div class="perf-value" style="color: var(--neon-cyan)">${balance_ps:,.2f}</div></div>
  <div class="perf-cell"><div class="perf-label">PnL Neto</div><div class="perf-value" style="color: {'var(--neon-green)' if total_pnl >= 0 else 'var(--neon-red)'}">${total_pnl:+.2f}</div></div>
  <div class="perf-cell"><div class="perf-label">Drawdown</div><div class="perf-value" style="color: var(--neon-red)">{drawdown_percent:.1f}%</div></div>
</div>
""", unsafe_allow_html=True)

DRAWDOWN_THRESHOLD = 10.0
dd_ratio = min(drawdown_percent / DRAWDOWN_THRESHOLD, 1.0)
bar_color = "#00ff88" if drawdown_percent < DRAWDOWN_THRESHOLD else "#ff2d6b"
st.markdown(f"""
<div style="margin: 0 0 1.2rem 0;">
  <div style="display:flex; justify-content:space-between; font-family: 'Share Tech Mono', monospace; font-size:10px; color: #5a7a99; letter-spacing:0.1em;">
    <span>DRAWDOWN</span>
    <span>{drawdown_percent:.1f}% / {DRAWDOWN_THRESHOLD:.0f}%</span>
  </div>
  <div style="height:4px; background: #2a3d52; border-radius:4px; overflow:hidden; margin-top:4px;">
    <div style="width:{dd_ratio*100:.1f}%; height:100%; background:{bar_color}; border-radius:4px; transition:width 0.5s;"></div>
  </div>
  <div style="margin-top:4px; font-family:'Share Tech Mono', monospace; font-size:9px; color:{bar_color};">
    {"⚠️ DRAWDOWN SUPERIOR AL UMBRAL" if drawdown_percent >= DRAWDOWN_THRESHOLD else "✅ DRAWDOWN BAJO CONTROL"}
  </div>
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════
# EQUITY CURVE & RÉGIMEN ANALYTICS
# ══════════════════════════════════════════════════════════════════
st.markdown("---")
st.markdown('<div class="sec-title">Equity Curve & Régimen Analytics</div>', unsafe_allow_html=True)

# ---------- 1. EQUITY CURVE ----------
closed_trades_for_chart = paper_state.get('closed_trades', [])

if closed_trades_for_chart:
    try:
        df_eq = pd.DataFrame(closed_trades_for_chart)
        if 'pnl' in df_eq.columns:
            df_eq['pnl'] = pd.to_numeric(df_eq['pnl'], errors='coerce').fillna(0)
            if 'exit_time' in df_eq.columns:
                df_eq['exit_time'] = pd.to_datetime(df_eq['exit_time'], errors='coerce')
                df_eq = df_eq.sort_values('exit_time').reset_index(drop=True)
            else:
                df_eq = df_eq.reset_index(drop=True)

            current_balance_chart = paper_state.get('balance', 100.0)
            initial_balance_chart = current_balance_chart - df_eq['pnl'].sum()

            df_eq['cumulative_pnl'] = df_eq['pnl'].cumsum()
            df_eq['equity'] = initial_balance_chart + df_eq['cumulative_pnl']

            x_axis = df_eq['exit_time'] if 'exit_time' in df_eq.columns and df_eq['exit_time'].notna().any() else df_eq.index
            x_labels = [t.strftime('%m-%d %H:%M') if pd.notna(t) else f"#{i}" for i, t in enumerate(x_axis)]

            marker_colors = ['#00ff88' if p >= 0 else '#ff2d6b' for p in df_eq['pnl']]

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=x_labels,
                y=df_eq['equity'].values,
                mode='lines+markers',
                name='Equity',
                line=dict(color='#00d4ff', width=2, shape='hv'),
                marker=dict(size=7, color=marker_colors, line=dict(width=0)),
                hovertemplate='<b>%{x}</b><br>Equity: $%{y:.2f}<extra></extra>'
            ))
            fig.add_hline(y=initial_balance_chart, line_dash='dash', line_color='#5a7a99',
                          line_width=1, annotation_text=f"Inicial ${initial_balance_chart:.2f}",
                          annotation_position='bottom right', annotation_font_size=9,
                          annotation_font_color='#5a7a99')

            fig.update_layout(
                template='plotly_dark',
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(2,4,8,0.6)',
                font=dict(family='Share Tech Mono', size=10, color='#e2f0ff'),
                margin=dict(l=50, r=20, t=10, b=30),
                height=320,
                xaxis=dict(gridcolor='rgba(0,212,255,0.06)', showgrid=True, tickangle=-45, nticks=12),
                yaxis=dict(gridcolor='rgba(0,212,255,0.06)', showgrid=True, title='Balance ($)', tickprefix='$'),
                showlegend=False,
                hovermode='x unified'
            )
            st.plotly_chart(fig, use_container_width=True)

            eq_cols = st.columns(4)
            eq_cols[0].metric("Ops totales", f"{len(df_eq)}")
            eq_cols[1].metric("Win Rate", f"{(len(df_eq[df_eq['pnl'] > 0]) / len(df_eq) * 100):.1f}%" if len(df_eq) > 0 else "—")
            eq_cols[2].metric("Mejor op", f"${df_eq['pnl'].max():+.2f}")
            eq_cols[3].metric("Peor op", f"${df_eq['pnl'].min():+.2f}")
        else:
            st.info("No hay columna 'pnl' en los trades cerrados para construir la equity curve.")
    except Exception as e:
        st.info(f"Error al construir equity curve: {e}")
        logger.error(f"Error equity curve: {e}", exc_info=True)
else:
    st.info("Sin operaciones cerradas todavía. La equity curve aparecerá aquí cuando haya trades cerrados.")

# ---------- 2. MÉTRICAS POR RÉGIMEN ----------
st.markdown('<div class="sec-title" style="font-size:12px; margin-top:1.8rem;">Métricas por Régimen</div>', unsafe_allow_html=True)

audit_file_regime = "audit_log.csv"
if os.path.exists(audit_file_regime):
    try:
        audit_df_reg = pd.read_csv(audit_file_regime)
        if not audit_df_reg.empty and 'pnl_final' in audit_df_reg.columns and 'explanation_raw' in audit_df_reg.columns:
            audit_df_reg['pnl_final_num'] = pd.to_numeric(audit_df_reg['pnl_final'], errors='coerce')
            closed_audit = audit_df_reg[audit_df_reg['pnl_final_num'].notna()].copy()

            if not closed_audit.empty:
                closed_audit['regime'] = closed_audit['explanation_raw'].apply(_extract_regime_from_explanation)
                closed_audit_with_regime = closed_audit[closed_audit['regime'].notna()].copy()

                if not closed_audit_with_regime.empty:
                    regime_metrics = []
                    for regime, group in closed_audit_with_regime.groupby('regime'):
                        wins = group[group['pnl_final_num'] > 0]
                        losses = group[group['pnl_final_num'] <= 0]
                        n_ops = len(group)
                        n_wins = len(wins)
                        wr = (n_wins / n_ops * 100) if n_ops > 0 else 0
                        gross_profit = wins['pnl_final_num'].sum()
                        gross_loss = abs(losses['pnl_final_num'].sum())
                        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
                        pnl_total = group['pnl_final_num'].sum()
                        regime_metrics.append({
                            'Régimen': regime,
                            'Operaciones': n_ops,
                            'Win Rate': wr,
                            'Profit Factor': pf,
                            'PnL Total': pnl_total
                        })

                    regime_order = ['RANGO', 'TENDENCIA_ALCISTA', 'TENDENCIA_BAJISTA', 'RUPTURA']
                    regime_metrics.sort(
                        key=lambda x: regime_order.index(x['Régimen']) if x['Régimen'] in regime_order else len(regime_order)
                    )

                    st.markdown("""
                    <table style="width:100%; border-collapse: collapse; background: var(--glass); border-radius: 10px; overflow: hidden; margin-top: 0.4rem;">
                      <thead>
                        <tr style="border-bottom: 1px solid var(--border);">
                          <th style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: var(--neon-cyan); text-align: left; letter-spacing: 0.1em;">RÉGIMEN</th>
                          <th style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: var(--neon-cyan); text-align: center; letter-spacing: 0.1em;">OPERACIONES</th>
                          <th style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: var(--neon-cyan); text-align: center; letter-spacing: 0.1em;">WIN RATE</th>
                          <th style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: var(--neon-cyan); text-align: center; letter-spacing: 0.1em;">PROFIT FACTOR</th>
                          <th style="padding: 10px; font-family: 'Orbitron'; font-size: 11px; color: var(--neon-cyan); text-align: center; letter-spacing: 0.1em;">PNL TOTAL</th>
                        </tr>
                      </thead>
                      <tbody>
                    """, unsafe_allow_html=True)

                    for m in regime_metrics:
                        wr_color = '#00ff88' if m['Win Rate'] >= 50 else '#ffb800' if m['Win Rate'] >= 40 else '#ff2d6b'
                        pf_display = f"{m['Profit Factor']:.2f}" if m['Profit Factor'] != float('inf') else "∞"
                        pf_color = '#00ff88' if m['Profit Factor'] >= 1.0 else '#ff2d6b'
                        pnl_color = '#00ff88' if m['PnL Total'] >= 0 else '#ff2d6b'
                        pnl_display = f"${m['PnL Total']:+.2f}"
                        st.markdown(f"""
                        <tr style="border-bottom: 1px solid var(--border2);">
                          <td style="padding: 9px 10px; font-family: 'Share Tech Mono'; font-size: 11px; color: var(--text);">{m['Régimen']}</td>
                          <td style="padding: 9px 10px; font-family: 'Share Tech Mono'; font-size: 11px; text-align: center; color: var(--text);">{m['Operaciones']}</td>
                          <td style="padding: 9px 10px; font-family: 'Share Tech Mono'; font-size: 11px; text-align: center; color: {wr_color};">{m['Win Rate']:.1f}%</td>
                          <td style="padding: 9px 10px; font-family: 'Share Tech Mono'; font-size: 11px; text-align: center; color: {pf_color};">{pf_display}</td>
                          <td style="padding: 9px 10px; font-family: 'Share Tech Mono'; font-size: 11px; text-align: center; color: {pnl_color};">{pnl_display}</td>
                        </tr>
                        """, unsafe_allow_html=True)

                    st.markdown("</tbody></table>", unsafe_allow_html=True)

                    st.caption(f"Métricas calculadas sobre {len(closed_audit_with_regime)} operaciones cerradas con régimen identificado. "
                               f"Profit Factor = ganancias brutas / pérdidas brutas (∞ = sin pérdidas).")
                else:
                    st.info("No se pudieron extraer regímenes del audit log. Verificá que las señales se registren con el formato 'Régimen: ...' o 'Régimen 1h: ...' en explanation_raw.")
            else:
                st.info("Sin operaciones cerradas en el audit log. Las métricas por régimen aparecerán cuando el stop_monitor cierre posiciones.")
        else:
            st.info("El audit log no tiene las columnas necesarias (pnl_final, explanation_raw).")
    except Exception as e:
        st.info(f"Error al computar métricas por régimen: {e}")
        logger.error(f"Error métricas régimen: {e}", exc_info=True)
else:
    st.info("Archivo de auditoría no encontrado. Las métricas por régimen aparecerán cuando se registren señales.")

# ============================================================
# AUDIT LOG
# ============================================================
st.markdown('<div class="sec-title">Audit Log (last 10)</div>', unsafe_allow_html=True)
audit_file = "audit_log.csv"
if os.path.exists(audit_file):
    try:
        audit_df = pd.read_csv(audit_file)
        if not audit_df.empty:
            st.dataframe(audit_df.tail(10)[['trade_id','timestamp_entry','symbol','side','entry','exit_price','pnl_final','exit_reason']], use_container_width=True)
        else:
            st.info("Sin registros de auditoría aún.")
    except Exception as e:
        st.info(f"Error al leer el archivo de auditoría: {e}")
        logger.error(f"Error leyendo audit log: {e}", exc_info=True)
else:
    st.info("Archivo de auditoría no encontrado.")

# ============================================================
# FOOTER
# ============================================================
st.caption(f"WebSocket live · Analysis every 60s · Risk fixed 1% · Cooldown {COOLDOWN_MINUTES}min · {datetime.datetime.now().strftime('%H:%M:%S')}")
