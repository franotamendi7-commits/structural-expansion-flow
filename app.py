import streamlit as st
import time, datetime, pandas as pd, json, requests, re
from engine.scalping_engine import ScalpingEngine
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from system_monitor import SystemMonitor

# ======================================================
# CONFIGURACIÓN DE PARES ACTIVOS
# ======================================================
ACTIVE_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

# ─────────────────────────────────────────────────────────────────
# GESTIÓN DINÁMICA DE RIESGO (desactivada: riesgo FIJO 1%)
# ─────────────────────────────────────────────────────────────────
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


# ══════════════════════════════════════════════════════════════════
# FUNCIÓN PARA EXTRAER NIVEL DE FIBONACCI DE LA EXPLICACIÓN
# ══════════════════════════════════════════════════════════════════
def extract_fib_label(explanation):
    match = re.search(r'Fib=(\S+)', explanation)
    return match.group(1) if match else "N/A"


# ══════════════════════════════════════════════════════════════════
# FUNCIÓN PARA ENVIAR ALERTA POR TELEGRAM
# ══════════════════════════════════════════════════════════════════
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
    lines.append(f"TP2: ${trade.get('tp2', 0):.2f}")
    lines.append(f"Score: {score}/100")
    lines.append(f"Explanation: {explanation}")
    message = "\n".join(lines)
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        params = {"chat_id": chat_id, "text": message}
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        print(f"Telegram error: {e}")

def send_test_telegram(token, chat_id):
    """Envía un mensaje de prueba para verificar la configuración."""
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


# ══════════════════════════════════════════════════════════════════
# INICIALIZAR COMPONENTES
# ══════════════════════════════════════════════════════════════════
if 'trader' not in st.session_state:
    st.session_state['trader'] = PaperTrader(initial_balance=100.0, state_file="paper_state.json")
    st.session_state['trader'].save_state() 
trader = st.session_state['trader']

if 'risk_manager' not in st.session_state:
    st.session_state['risk_manager'] = DynamicRiskManager(base_risk_pct=0.01)
risk_manager = st.session_state['risk_manager']

if 'backend_price' not in st.session_state:
    st.session_state['backend_price'] = {}
if 'last_analysis' not in st.session_state:
    st.session_state['last_analysis'] = 0
if 'last_signal' not in st.session_state:
    st.session_state['last_signal'] = None
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

# SystemMonitor singleton
if 'monitor' not in st.session_state:
    st.session_state['monitor'] = SystemMonitor()
    st.session_state['monitor'].start()
monitor = st.session_state['monitor']

# ---------- PAGE CONFIG ----------
st.set_page_config(page_title="QNTFRY · Command Terminal", page_icon="⬡", layout="wide")

# ══════════════════════════════════════════════════════════════════
# CSS — FULL NEON / 3D / GLASSMORPHISM (COMPLETO)
# ══════════════════════════════════════════════════════════════════
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;900&family=Share+Tech+Mono&family=Rajdhani:wght@300;400;500;600;700&display=swap');

/* ── ROOT ───────────────────────────────────────────────────── */
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

/* ── GLOBAL ─────────────────────────────────────────────────── */
html, body, [data-testid="stApp"] {
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: var(--font-body) !important;
}

/* Animated grid background */
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

/* Ambient glow orbs */
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

/* ── SCROLLBAR ──────────────────────────────────────────────── */
::-webkit-scrollbar { width: 3px; height: 3px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--neon-cyan); border-radius: 3px; opacity: 0.3; }

/* ── SIDEBAR ────────────────────────────────────────────────── */
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

/* ── MAIN BUTTONS ───────────────────────────────────────────── */
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

/* ── EXPANDER ───────────────────────────────────────────────── */
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

/* ── MASTER HEADER ──────────────────────────────────────────── */
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
  letter-spacing: 0.15em;
  text-transform: uppercase;
  background: linear-gradient(135deg, #ffffff 0%, var(--neon-cyan) 45%, var(--neon-green) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  line-height: 1;
  text-shadow: none;
}
.hdr-sub {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: var(--text-dim);
  letter-spacing: 0.22em;
  text-transform: uppercase;
  margin-top: 6px;
}
.hdr-live {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.live-dot-wrap {
  display: flex;
  align-items: center;
  gap: 7px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--neon-green);
  letter-spacing: 0.1em;
}
.live-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--neon-green);
  box-shadow: 0 0 8px var(--neon-green), 0 0 20px rgba(0,255,136,0.4);
  animation: livePulse 1.2s ease-in-out infinite;
}
@keyframes livePulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 8px var(--neon-green), 0 0 20px rgba(0,255,136,0.4); }
  50%       { opacity: 0.5; box-shadow: 0 0 4px var(--neon-green); }
}
.clock {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* ── METRIC BAR ─────────────────────────────────────────────── */
.metric-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 8px;
  margin-bottom: 1.6rem;
}
.metric-cell {
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  backdrop-filter: blur(16px);
  position: relative;
  overflow: hidden;
  transition: all 0.3s ease;
  box-shadow: 0 4px 16px rgba(0,0,0,0.4),
              inset 0 1px 0 rgba(255,255,255,0.04);
  transform: perspective(600px) rotateX(2deg);
}
.metric-cell::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(0,212,255,0.4), transparent);
}
.metric-cell:hover {
  border-color: rgba(0,212,255,0.35);
  box-shadow: 0 0 25px rgba(0,212,255,0.12),
              0 8px 24px rgba(0,0,0,0.5),
              inset 0 1px 0 rgba(0,212,255,0.1);
  transform: perspective(600px) rotateX(0deg) translateY(-2px);
}
.mc-label {
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--text-ghost);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  margin-bottom: 5px;
}
.mc-value {
  font-family: var(--font-hud);
  font-size: 16px;
  font-weight: 700;
  color: var(--text);
  line-height: 1;
}
.mc-sub {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 4px;
  font-weight: 400;
}

/* ── SECTION TITLE ──────────────────────────────────────────── */
.sec-title {
  font-family: var(--font-hud);
  font-size: 0.6rem;
  letter-spacing: 0.28em;
  text-transform: uppercase;
  color: var(--neon-cyan);
  margin: 0 0 12px;
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

/* ── AGENT CARD ─────────────────────────────────────────────── */
.agent-card {
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.15rem;
  position: relative;
  overflow: hidden;
  backdrop-filter: blur(20px);
  transition: all 0.35s cubic-bezier(.4,0,.2,1);
  box-shadow:
    0 4px 24px rgba(0,0,0,0.5),
    inset 0 1px 0 rgba(255,255,255,0.03);
  transform: perspective(800px) rotateX(1deg) rotateY(0deg);
}
.agent-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-color, var(--neon-cyan)), transparent);
  opacity: 0.6;
}
.agent-card::after {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(ellipse at 50% 0%, var(--accent-glow, rgba(0,212,255,0.04)) 0%, transparent 65%);
  pointer-events: none;
}
.agent-card:hover {
  border-color: var(--accent-color, rgba(0,212,255,0.4));
  box-shadow:
    0 0 30px var(--accent-glow, rgba(0,212,255,0.12)),
    0 12px 40px rgba(0,0,0,0.6),
    inset 0 1px 0 rgba(255,255,255,0.06);
  transform: perspective(800px) rotateX(0deg) translateY(-3px);
}
.agent-accent {
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  border-radius: 2px 0 0 2px;
  box-shadow: 0 0 12px currentColor;
}
.agent-header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 10px;
  position: relative; z-index: 1;
}
.agent-icon {
  width: 36px; height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  border: 1px solid rgba(255,255,255,0.06);
  box-shadow: 0 0 16px var(--accent-glow, rgba(0,212,255,0.15));
}
.agent-name {
  font-family: var(--font-hud);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text);
  line-height: 1.2;
}
.agent-role {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--text-dim);
  margin-top: 2px;
  font-weight: 400;
}
.status-pill {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 500;
  padding: 3px 8px;
  border-radius: 20px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  flex-shrink: 0;
}
.agent-body {
  border-top: 1px solid var(--border2);
  padding-top: 9px;
  position: relative; z-index: 1;
}
.data-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 5px;
}
.data-label {
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--text-dim);
  font-weight: 400;
}
.data-value {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  color: var(--text);
}
.info-box {
  background: rgba(0,5,15,0.7);
  border: 1px solid var(--border2);
  border-radius: 7px;
  padding: 7px 10px;
  margin-top: 8px;
  font-family: var(--font-body);
  font-size: 11px;
  color: var(--text-dim);
  line-height: 1.55;
  position: relative;
  overflow: hidden;
}
.info-box::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0; width: 2px;
  background: var(--accent-color, var(--neon-cyan));
  opacity: 0.4;
}
.info-hi { color: var(--text); font-weight: 600; }

.pair-chips { display: flex; gap: 4px; flex-wrap: wrap; margin-top: 8px; }
.pair-chip {
  font-family: var(--font-mono);
  font-size: 9px;
  padding: 2px 7px;
  border-radius: 20px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

/* ── COMMANDER CARD ─────────────────────────────────────────── */
.commander-wrap {
  background: var(--glass);
  border: 1px solid rgba(77,124,255,0.35);
  border-radius: 16px;
  padding: 1.3rem 1.5rem;
  margin-bottom: 1.5rem;
  position: relative;
  overflow: hidden;
  backdrop-filter: blur(28px);
  box-shadow:
    0 0 60px rgba(77,124,255,0.1),
    0 20px 60px rgba(0,0,0,0.6),
    inset 0 1px 0 rgba(77,124,255,0.2);
  animation: cmdGlow 4s ease-in-out infinite alternate;
}
@keyframes cmdGlow {
  from { box-shadow: 0 0 40px rgba(77,124,255,0.08), 0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(77,124,255,0.15); }
  to   { box-shadow: 0 0 70px rgba(77,124,255,0.18), 0 20px 60px rgba(0,0,0,0.6), inset 0 1px 0 rgba(77,124,255,0.25); }
}
.commander-wrap::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0; height: 2px;
  background: linear-gradient(90deg,
    transparent 0%, rgba(77,124,255,0.8) 30%,
    var(--neon-cyan) 50%, rgba(77,124,255,0.8) 70%, transparent 100%);
  animation: cmdScan 5s ease-in-out infinite;
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

/* ── SIGNAL DECISION AREA ───────────────────────────────────── */
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

/* ── AGENT CONSENSUS ROW ────────────────────────────────────── */
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

/* ── CONFIDENCE BAR ─────────────────────────────────────────── */
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

/* ── RAW JSON ───────────────────────────────────────────────── */
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

/* ── SIDEBAR WIDGETS ────────────────────────────────────────── */
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

/* ── NEON COLORS ────────────────────────────────────────────── */
.n-cyan   { color: var(--neon-cyan); }
.n-green  { color: var(--neon-green); }
.n-red    { color: var(--neon-red); }
.n-gold   { color: var(--neon-gold); }
.n-purple { color: var(--neon-purple); }
.n-blue   { color: var(--neon-blue); }
.n-dim    { color: var(--text-dim); }

/* ── GLOW TEXT UTIL ─────────────────────────────────────────── */
.glow-green { text-shadow: 0 0 10px rgba(0,255,136,0.6), 0 0 30px rgba(0,255,136,0.3); }
.glow-red   { text-shadow: 0 0 10px rgba(255,45,107,0.6), 0 0 30px rgba(255,45,107,0.3); }
.glow-cyan  { text-shadow: 0 0 10px rgba(0,212,255,0.6), 0 0 30px rgba(0,212,255,0.3); }
.glow-gold  { text-shadow: 0 0 10px rgba(255,184,0,0.6), 0 0 30px rgba(255,184,0,0.3); }
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

    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    st.markdown('<div style="font-family:\'Share Tech Mono\',monospace;font-size:9px;'
                'letter-spacing:.22em;text-transform:uppercase;color:#2a3d52;'
                'margin-bottom:8px;padding-bottom:6px;'
                'border-bottom:1px solid rgba(0,212,255,0.08);">SYSTEM HEALTH</div>',
                unsafe_allow_html=True)

    metrics = monitor.get_metrics()
    st.markdown(f"WebSocket: {'🟢 CONNECTED' if metrics.get('websocket_status')=='CONNECTED' else '🔴 DISCONNECTED'}")
    edad_tick = metrics.get('last_tick_age_ms')
    st.markdown(f"Edad tick: {edad_tick:.0f}ms" if edad_tick is not None else "Edad tick: —")
    st.markdown(f"CPU: {metrics.get('cpu_percent', 0.0):.1f}% | RAM: {metrics.get('ram_total_percent', 0.0):.1f}%")
    st.markdown(f"Uptime: {metrics.get('uptime_sec', 0):.0f}s")

    # ── RISK STATUS ───────────────────────────────────────────
    st.markdown("---")
    st.markdown("### ⚙️ RISK STATUS")
    dd_pct = trader.get_drawdown_pct() * 100
    st.markdown(f"Drawdown: {dd_pct:.1f}%")
    st.markdown(f"Peak Balance: ${trader.get_peak_balance():,.2f}")
    if trader.is_paused():
        st.error("⚠️ TRADING PAUSADO")
    st.markdown(f"Consecutive Losses: {trader.consecutive_losses}/{trader.max_consecutive_losses}")
    st.markdown(f"Daily PnL: ${risk_manager.daily_pnl:,.2f}")

    # ── TELEGRAM ALERTS con fallback hardcodeado ─────────────────
    st.markdown("---")
    st.markdown("### 📨 TELEGRAM ALERTS")
    # TODO: eliminar hardcode antes de VPS
    DEFAULT_TOKEN = "8813532919:AAF4FcqNCMA5jfeiHDp71M-lbqLbBh3RuzY"
    DEFAULT_CHAT_ID = "8026382563"
    default_token = DEFAULT_TOKEN
    default_chat_id = DEFAULT_CHAT_ID
    telegram_token = st.text_input(
        "Bot Token",
        type="password",
        placeholder="Ej: 123456:ABC-DEF",
        value=default_token
    )
    telegram_chat_id = st.text_input(
        "Chat ID",
        placeholder="Ej: 123456789",
        value=default_chat_id
    )
    if telegram_token and telegram_chat_id:
        st.success("✅ Alertas activas")
        if st.button("📤 Enviar mensaje de prueba", key="test_telegram_btn"):
            send_test_telegram(telegram_token, telegram_chat_id)
    else:
        st.info("Completa ambos campos para recibir alertas")

    # ── LIVE EXECUTION (Binance) ──────────────────────────────
    st.markdown("---")
    st.markdown("### 🟢 LIVE EXECUTION (Binance)")
    binance_api_key = st.text_input(
        "API Key",
        type="password",
        placeholder="Ej: abc123...",
        value=""
    )
    binance_secret_key = st.text_input(
        "Secret Key",
        type="password",
        placeholder="Ej: def456...",
        value=""
    )
    use_testnet = st.checkbox("Usar Testnet", value=True)
    enable_live_trading = st.checkbox("Activar ejecución real (riesgo real)", value=False)
    if enable_live_trading and (not binance_api_key or not binance_secret_key):
        st.warning("⚠️ Las credenciales de API son necesarias para activar ejecución real")
    elif enable_live_trading:
        st.success("✅ Live trading activado. Las órdenes se enviarán a Binance.")
    else:
        st.info("Modo paper trading (ejecución simulada)")

# ══════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════
now_str = datetime.datetime.now().strftime("%Y-%m-%d  |  %H:%M:%S UTC")
st.markdown(f"""
<div class="master-header">
  <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;">
    <div>
      <div class="hdr-title">Quantfury Command Terminal</div>
      <div class="hdr-sub">Institutional Neural Grid · 6 Agent System · 20x Leverage · Fixed 1% Risk</div>
    </div>
    <div class="hdr-live">
      <div class="live-dot-wrap"><div class="live-dot"></div>LIVE FEED</div>
      <div class="clock">{now_str}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ---------- MÉTRICAS DE BALANCE ----------
balance = trader.get_balance()
posiciones_abiertas = len(trader.get_all_open_positions())
pnl_abierto_total = sum(pos.get('pnl', 0) for pos in trader.get_all_open_positions().values())
closed_trades = trader.get_closed_trades()
pnl_cerrado = sum(t['pnl'] for t in closed_trades) if closed_trades else 0.0

col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("BALANCE", f"${balance:,.2f}")
col2.metric("MARGIN 20x", f"${balance * 20:,.0f}")
col3.metric("RISK / OP", f"${balance * 0.01:,.2f} (1.0%)")
col4.metric("P&L ABIERTO", f"${pnl_abierto_total:,.2f}")
col5.metric("OPEN OPS", f"{posiciones_abiertas} / 4")
col6.metric("P&L CERRADO", f"${pnl_cerrado:,.2f}")

# ========== PANEL DE ESTADO MULTI-PAR CON DESCRIPCIÓN ==========
st.markdown('<div class="sec-title">Market Scan · Multi-Pair Status</div>', unsafe_allow_html=True)
pair_status_data = []
color_map = {'FORMING':'#ffb800', 'EXECUTE':'#00ff88', 'READY':'#4d7cff', 'INVALID':'#ff2d6b'}
for p in ACTIVE_PAIRS:
    state = st.session_state['pair_states'].get(p, {})
    setup_state = state.get('setup_state', 'INVALID')
    signal_val = state.get('signal', 'WAIT')
    score_val = state.get('score', 0)
    price_val = state.get('price')
    price_display = f"${price_val:,.2f}" if price_val else "—"
    # Construir mini-descripción
    fib = state.get('fib_label', 'N/A')
    trend = state.get('trend_h4', 'neutral')
    phase = state.get('market_phase', 'unknown')
    setup = setup_state
    if setup_state == 'FORMING':
        extra = "Formando setup"
    elif setup_state == 'INVALID':
        extra = "Setup inválido"
    elif setup_state == 'EXECUTE':
        extra = "Listo para ejecutar"
    else:
        extra = "Esperando"
    description = f"Fib {fib} | H4: {trend} | Phase: {phase} | {extra}"
    pair_status_data.append([p, price_display, setup_state, f"{score_val}%", signal_val, description])

# Tabla con columna adicional
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
      <td style="padding: 10px; font-family: 'Rajdhani'; font-size: 11px; color: var(--text-dim);">{row[5]}</td>
    </tr>
    """, unsafe_allow_html=True)
st.markdown("</tbody></table>", unsafe_allow_html=True)

# ---------- LÓGICA DE ACTUALIZACIÓN ----------
now = time.time()
FIXED_RISK_PCT = 0.01
risk_manager.base_risk_pct = FIXED_RISK_PCT

@st.cache_resource(ttl=3600)
def get_engine(symbol):
    return ScalpingEngine(symbol=symbol, capital=100.0, risk_pct=FIXED_RISK_PCT, debug_filters=False)

def process_signal_for_pair(res, symbol, token, chat_id):
    if res['signal'] in ('LONG', 'SHORT') and res.get('trade') and not trader.is_paused():
        trade = res['trade']
        signal = {
            'symbol': symbol,
            'side': res['signal'],
            'price': trade['entry'],
            'stop_loss': trade['sl'],
            'take_profit': trade['tp1'],
            'tp2': trade.get('tp2'),
            'notional': trade.get('notional', 0),
            'risk_usd': trade.get('risk_usd', 0),
            'contracts': trade.get('contracts', 0),
            'contrarian': trade.get('contrarian', False)
        }
        success = trader.open_trade(signal)
        if success:
            risk_manager.update_daily_pnl(0)
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
    """Actualiza el estado completo de un par en session_state."""
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

# ----- AUTO-REFRESH MULTI-PAR (con sleep y rerun) -----
if auto and now - st.session_state['last_analysis'] > 60:
    with st.spinner(f"Analizando {len(ACTIVE_PAIRS)} pares..."):
        for current_pair in ACTIVE_PAIRS:
            price = get_ticker(current_pair)
            if price:
                st.session_state['backend_price'][current_pair] = price
            engine = get_engine(current_pair)
            engine.capital = trader.get_balance()
            engine.risk_pct = FIXED_RISK_PCT
            res = engine.run()
            update_pair_state(current_pair, res, price)
            process_signal_for_pair(res, current_pair, telegram_token, telegram_chat_id)
        for sym, price in st.session_state['backend_price'].items():
            trader.update_position(sym, price)
        st.session_state['last_analysis'] = now
    time.sleep(60)
    st.rerun()

# ----- MODO MANUAL (solo el par seleccionado) -----
if run_btn:
    with st.spinner(f"Analizando {pair}..."):
        price = get_ticker(pair)
        if price:
            st.session_state['backend_price'][pair] = price
        engine = get_engine(pair)
        engine.capital = trader.get_balance()
        engine.risk_pct = FIXED_RISK_PCT
        res = engine.run()
        st.session_state['last_signal'] = res
        update_pair_state(pair, res, price)
        process_signal_for_pair(res, pair, telegram_token, telegram_chat_id)
        trader.update_position(pair, price)

# ---------- DATOS PARA LA UI (Commander y Agentes) ----------
if st.session_state.get('last_signal'):
    res = st.session_state['last_signal']
else:
    res = st.session_state['pair_states'].get(pair, {})

# CORRECCIÓN: agent_scores se toma del estado multi-par, no de res
agent_scores = st.session_state['pair_states'].get(pair, {}).get('agent_scores', {})

setup_state = res.get('setup_state', 'INVALID')
signal = res.get('signal', 'WAIT')
direction = res.get('direction', 'neutral')
trade = res.get('trade', None)
dynamic_score = res.get('score', 0)
explanation = res.get('explanation', 'No analysis yet')
pos = trader.get_open_position(pair)

# ---------- TARJETAS DE AGENTES (con los scores reales) ----------
st.markdown('<div class="sec-title">Neural Agents — Nodes 01 → 05</div>', unsafe_allow_html=True)
ag_cols = st.columns(5)
agent_labels = {
    'scanner': ('MARKET SCANNER', 'Setup detection · multi‑TF', '#00ff88', 'rgba(0,255,136,0.08)'),
    'risk': ('RISK MANAGER', 'Capital control · 20x exp', '#4d7cff', 'rgba(77,124,255,0.08)'),
    'technical': ('TECHNICAL ANALYST', 'Price action · estructura', '#bf5fff', 'rgba(191,95,255,0.08)'),
    'momentum': ('MOMENTUM TRACKER', 'Volumen · fuerza direccional', '#ffb800', 'rgba(255,184,0,0.08)'),
    'guard': ('EXECUTION GUARD', 'Validación de entrada', '#ff2d6b', 'rgba(255,45,107,0.08)')
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
        st.write(f"**Entry:** ${trade['entry']:,.2f}  |  **Stop Loss:** ${trade['sl']:,.2f}  |  **TP1:** ${trade['tp1']:,.2f}  |  **TP2:** ${trade.get('tp2',0):,.2f}")
        if trade.get('contrarian'):
            st.warning("⚠️ Contrarian (risk reduced 50%)")
else:
    st.warning("No trade ready for this pair")

# Mostrar posición abierta para este par (si existe)
if pos:
    st.subheader(f"Open Position · {pair}")
    st.write(f"Side: {pos['side']} | Entry: ${pos['entry_price']:,.2f} | Current: ${pos['current_price']:,.2f}")
    st.write(f"SL: ${pos['stop_loss']:,.2f} | TP1: ${pos.get('take_profit', 0):,.2f} | PnL: ${pos['pnl']:.2f}")
    if st.button(f"Close position for {pair}"):
        trader.force_close(symbol=pair)
        st.rerun()

# ---------- ESTADO DE RIESGO ----------
with st.expander("⚙️ Risk Management Details"):
    dd_pct = trader.get_drawdown_pct() * 100
    st.write(f"**Drawdown actual:** {dd_pct:.2f}%")
    st.write(f"**Racha de pérdidas:** {trader.consecutive_losses}/{trader.max_consecutive_losses}")
    st.write(f"**Balance máximo histórico:** ${trader.get_peak_balance():,.2f}")
    st.write(f"**Riesgo por operación:** 1% fijo (no ajustado por drawdown)")
    if trader.is_paused():
        st.error("🚫 Trading en pausa por protección de capital.")
    else:
        st.success("✅ Trading activo")

# ---------- HISTORIAL ----------
st.subheader("Trade History")
if closed_trades:
    df = pd.DataFrame(closed_trades)
    cols = ['symbol', 'side', 'entry_price', 'exit_price', 'pnl', 'reason', 'exit_time']
    st.dataframe(df[[c for c in cols if c in df.columns]])

st.caption(f"WebSocket live · Analysis every 60s · Risk fixed 1% · {datetime.datetime.now().strftime('%H:%M:%S')}")
