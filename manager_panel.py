import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import json
from datetime import datetime, timedelta
from portfolio_manager import PortfolioManager

matplotlib.rcParams.update({
    'figure.facecolor': 'white',
    'axes.facecolor': 'white',
    'axes.edgecolor': '#E8E4DF',
    'axes.labelcolor': '#3A4A3E',
    'xtick.color': '#8A9E8F',
    'ytick.color': '#8A9E8F',
    'text.color': '#3A4A3E',
    'grid.color': '#F0EDE8',
    'grid.linestyle': '--',
    'grid.alpha': 0.7,
})

RUTA = "/Users/franciscootamendi/ai-agents-v3/investors.json"
MASTER_PASSWORD = "Fran2026"
PAPER_STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"

st.set_page_config(page_title="Manager Panel", page_icon="🌿", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=DM+Mono:wght@300;400;500&display=swap');

:root {
  --bg:          #EDEAE4;
  --surface:     #FFFFFF;
  --surface-2:   #F5F2ED;
  --sidebar:     #2C3B30;
  --green-dark:  #3D5A45;
  --green-mid:   #547A5E;
  --green-light: #7BAF87;
  --green-pale:  #EAF2EC;
  --text:        #1C2B20;
  --text-mid:    #4A6050;
  --text-dim:    #8FA896;
  --border:      rgba(0,0,0,0.07);
  --red:         #D94F4F;
  --red-pale:    #FDEEEE;
  --shadow-sm:   0 2px 8px rgba(44,59,48,0.06);
  --shadow-md:   0 6px 24px rgba(44,59,48,0.10);
  --radius:      16px;
  --radius-sm:   10px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; }

html, body, [data-testid="stApp"] {
  background: var(--bg) !important;
  color: var(--text) !important;
  font-family: 'Outfit', sans-serif !important;
}

[data-testid="stHeader"], [data-testid="stToolbar"],
[data-testid="stDecoration"], footer { display: none !important; }

[data-testid="stMainBlockContainer"] {
  padding: 2rem 2.5rem !important;
  max-width: 1280px !important;
}

/* ── TOP HEADER ── */
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 2rem;
}
.page-header-left { display: flex; align-items: center; gap: 14px; }
.logo-mark {
  width: 46px; height: 46px; border-radius: 12px;
  background: var(--green-dark);
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
}
.page-title { font-size: 1.6rem; font-weight: 700; color: var(--text); line-height: 1.1; }
.page-sub   { font-size: 0.78rem; color: var(--text-dim); font-weight: 400; margin-top: 2px; }

/* ── METRIC CARDS ROW ── */
.metric-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 1.8rem;
}
.metric-row.bot-row {
  grid-template-columns: repeat(5, 1fr);
}
.metric-card {
  background: var(--surface);
  border-radius: var(--radius-sm);
  padding: 1.1rem 1.2rem;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
}
.metric-card.accent {
  background: var(--green-dark);
  border-color: transparent;
}
.metric-card .mc-label  { font-size: 0.72rem; font-weight: 500; color: var(--text-dim); letter-spacing: 0.04em; text-transform: uppercase; }
.metric-card .mc-value  { font-size: 1.55rem; font-weight: 700; color: var(--text); margin-top: 4px; }
.metric-card.accent .mc-label { color: rgba(255,255,255,0.6); }
.metric-card.accent .mc-value { color: #FFFFFF; }
.mc-delta { font-size: 0.72rem; font-weight: 600; margin-top: 4px; }
.mc-delta.pos { color: var(--green-mid); }
.mc-delta.neg { color: var(--red); }

/* ── SECTION CARD ── */
.s-card {
  background: var(--surface);
  border-radius: var(--radius);
  padding: 1.5rem 1.6rem;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
  margin-bottom: 1.4rem;
  transition: box-shadow 0.2s;
}
.s-card:hover { box-shadow: var(--shadow-md); }
.s-card h3 {
  font-size: 0.95rem; font-weight: 600;
  color: var(--text); margin-bottom: 1.1rem;
  display: flex; align-items: center; gap: 7px;
}
.s-card h3 .icon-badge {
  width: 28px; height: 28px; border-radius: 8px;
  background: var(--green-pale);
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 14px;
}

/* ── INVESTOR MINI-CARDS ── */
.inv-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 12px; }
.inv-card {
  background: var(--surface-2);
  border-radius: var(--radius-sm);
  padding: 1rem 1.1rem;
  border: 1px solid var(--border);
  transition: all 0.2s;
}
.inv-card:hover { border-color: var(--green-light); box-shadow: var(--shadow-sm); transform: translateY(-2px); }
.inv-name { font-size: 0.9rem; font-weight: 600; color: var(--text); }
.inv-capital { font-size: 1.15rem; font-weight: 700; color: var(--text); margin-top: 4px; }
.inv-pnl { font-size: 0.78rem; font-weight: 600; margin-top: 3px; }
.inv-pnl.pos { color: var(--green-mid); }
.inv-pnl.neg { color: var(--red); }
.inv-badge {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 0.65rem; font-weight: 600; margin-top: 6px; letter-spacing: 0.03em;
}
.inv-badge.pos { background: var(--green-pale); color: var(--green-dark); }
.inv-badge.neg { background: var(--red-pale); color: var(--red); }

/* ── SECTION TITLE (outside card) ── */
.section-label {
  font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--text-dim);
  margin-bottom: 0.8rem; margin-top: 0.5rem;
}

/* ── INPUTS ── */
.stTextInput>div>div>input,
.stNumberInput>div>div>input,
.stSelectbox>div>div,
.stTextInput>div>div>input:focus {
  background: var(--surface-2) !important;
  border: 1px solid var(--border) !important;
  border-radius: 8px !important;
  color: var(--text) !important;
  font-family: 'Outfit', sans-serif !important;
  font-size: 0.88rem !important;
  box-shadow: none !important;
}
.stTextInput>div>div>input:focus,
.stNumberInput>div>div>input:focus {
  border-color: var(--green-mid) !important;
  box-shadow: 0 0 0 3px rgba(84,122,94,0.12) !important;
}

/* ── BUTTONS ── */
.stButton > button {
  background: var(--green-dark) !important;
  border: none !important;
  color: #FFFFFF !important;
  border-radius: 8px !important;
  font-family: 'Outfit', sans-serif !important;
  font-size: 0.82rem !important;
  font-weight: 600 !important;
  padding: 0.45rem 1.1rem !important;
  letter-spacing: 0.02em !important;
  transition: all 0.2s !important;
}
.stButton > button:hover {
  background: var(--green-mid) !important;
  box-shadow: 0 4px 12px rgba(61,90,69,0.28) !important;
  transform: translateY(-1px) !important;
}

/* Warning / success / error pills */
.stAlert {
  border-radius: 10px !important;
  border-left-width: 3px !important;
  font-family: 'Outfit', sans-serif !important;
  font-size: 0.85rem !important;
}

/* ── DATAFRAME ── */
.stDataFrame {
  border-radius: 10px !important;
  overflow: hidden !important;
  border: 1px solid var(--border) !important;
  font-family: 'DM Mono', monospace !important;
  font-size: 0.8rem !important;
}
.stDataFrame thead th {
  background: var(--green-pale) !important;
  color: var(--green-dark) !important;
  font-size: 0.72rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.05em !important;
  text-transform: uppercase !important;
}
.stDataFrame tbody td { color: var(--text) !important; }
.stDataFrame tbody tr:hover { background: var(--surface-2) !important; }

/* Expander */
.streamlit-expanderHeader {
  background: var(--surface-2) !important;
  border-radius: 8px !important;
  font-family: 'Outfit', sans-serif !important;
  font-size: 0.85rem !important;
  color: var(--text-mid) !important;
}

/* Divider */
hr { border: none; border-top: 1px solid var(--border); margin: 1rem 0; }

/* Labels */
label, .stSelectbox label, .stTextInput label, .stNumberInput label {
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  color: var(--text-mid) !important;
  letter-spacing: 0.03em !important;
}
</style>
""", unsafe_allow_html=True)


# ── AUTH ──
if "autenticado" not in st.session_state:
    st.session_state.autenticado = False
if not st.session_state.autenticado:
    st.markdown("""
    <div style="max-width:360px;margin:80px auto;">
      <div style="text-align:center;margin-bottom:2rem;">
        <div style="width:56px;height:56px;border-radius:14px;background:#2C3B30;
             display:inline-flex;align-items:center;justify-content:center;font-size:26px;margin-bottom:12px;">🌿</div>
        <div style="font-size:1.4rem;font-weight:700;color:#1C2B20;">Manager Panel</div>
        <div style="font-size:0.8rem;color:#8FA896;margin-top:4px;">Capital Allocation · Private Access</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
    pwd = st.text_input("Contraseña maestra", type="password", placeholder="••••••••••")
    if st.button("Ingresar →"):
        if pwd == MASTER_PASSWORD:
            st.session_state.autenticado = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta")
    st.stop()


# ── PM INIT ──
if 'pm' not in st.session_state:
    st.session_state.pm = PortfolioManager(investors_file=RUTA)
pm = st.session_state.pm

if 'confirm_nuevo' not in st.session_state:    st.session_state.confirm_nuevo = False
if 'confirm_agregar' not in st.session_state:  st.session_state.confirm_agregar = False
if 'confirm_corregir' not in st.session_state: st.session_state.confirm_corregir = False
if 'eliminar_id' not in st.session_state:      st.session_state.eliminar_id = None
if 'pending_action' not in st.session_state:   st.session_state.pending_action = {}

def reset_all():
    st.session_state.confirm_nuevo = False
    st.session_state.confirm_agregar = False
    st.session_state.confirm_corregir = False
    st.session_state.eliminar_id = None
    st.session_state.pending_action = {}


# ── HEADER ──
res = pm.generar_resumen()
total_aum = sum(r['capital_actual'] for r in res) if res else 0
total_pnl  = sum(r['ganancia_neta'] for r in res) if res else 0
n_inv = len(res) if res else 0
pendientes = pm.listar_retiros_pendientes()
n_retiros  = len(pendientes) if pendientes else 0

st.markdown(f"""
<div class="page-header">
  <div class="page-header-left">
    <div class="logo-mark">🌿</div>
    <div>
      <div class="page-title">Manager Panel</div>
      <div class="page-sub">Capital Allocation · Investor Management</div>
    </div>
  </div>
</div>

<div class="metric-row">
  <div class="metric-card">
    <div class="mc-label">Total AUM</div>
    <div class="mc-value">${total_aum:,.0f}</div>
    <div class="mc-delta pos">USDT</div>
  </div>
  <div class="metric-card">
    <div class="mc-label">PnL Total</div>
    <div class="mc-value">${total_pnl:,.2f}</div>
    <div class="mc-delta {'pos' if total_pnl >= 0 else 'neg'}">{'▲' if total_pnl >= 0 else '▼'} Ganancia neta</div>
  </div>
  <div class="metric-card">
    <div class="mc-label">Inversores</div>
    <div class="mc-value">{n_inv}</div>
    <div class="mc-delta pos">activos</div>
  </div>
  <div class="metric-card accent">
    <div class="mc-label">Retiros</div>
    <div class="mc-value">{n_retiros}</div>
    <div class="mc-delta pos" style="color:rgba(255,255,255,0.7)">pendientes</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── MÉTRICAS DEL BOT EN VIVO (CORREGIDO) ──
try:
    with open(PAPER_STATE_FILE, 'r') as f:
        paper_state = json.load(f)

    # Aseguramos que paper_state sea un diccionario
    if not isinstance(paper_state, dict):
        raise ValueError("Formato inesperado en paper_state.json (no es un objeto)")

    bot_balance = paper_state.get('balance', 0)
    peak_balance = paper_state.get('peak_balance', bot_balance)

    # Posiciones abiertas (puede ser lista o dict)
    open_positions_raw = paper_state.get('open_positions', [])
    if isinstance(open_positions_raw, dict):
        open_positions = list(open_positions_raw.values())
    elif isinstance(open_positions_raw, list):
        open_positions = open_positions_raw
    else:
        open_positions = []

    open_pnl = 0.0
    for pos in open_positions:
        if isinstance(pos, dict):
            open_pnl += pos.get('pnl', 0)

    # Trades cerrados (puede ser lista de dicts)
    closed_trades_raw = paper_state.get('closed_trades', [])
    if isinstance(closed_trades_raw, list):
        closed_trades = [t for t in closed_trades_raw if isinstance(t, dict)]
    else:
        closed_trades = []

    # Hoy
    today_str = datetime.now().strftime('%Y-%m-%d')
    today_trades = [t for t in closed_trades if t.get('exit_time', '').startswith(today_str)]
    closed_pnl_today = sum(t.get('pnl', 0) for t in today_trades)
    win_rate_today = (len([t for t in today_trades if t.get('pnl', 0) > 0]) / len(today_trades) * 100) if today_trades else 0.0

    # Drawdown
    if peak_balance > 0:
        dd_pct = (peak_balance - bot_balance) / peak_balance * 100
    else:
        dd_pct = 0.0

    # Equity curve (últimos 30 días)
    initial_balance = paper_state.get('initial_balance', 100.0)
    sorted_closed = sorted(closed_trades, key=lambda x: x.get('exit_time', ''))
    curve_dates = [datetime.now() - timedelta(days=i) for i in range(30, -1, -1)]
    curve_values = []
    balance = initial_balance
    trade_idx = 0
    for d in curve_dates:
        while trade_idx < len(sorted_closed) and sorted_closed[trade_idx]['exit_time'][:10] <= d.strftime('%Y-%m-%d'):
            balance += sorted_closed[trade_idx]['pnl']
            trade_idx += 1
        curve_values.append(balance)
    # El último punto lo igualamos al balance actual para reflejar PnL flotante
    curve_values[-1] = bot_balance

    st.markdown('<div class="section-label" style="margin-top:1.6rem;">🤖 Métricas del Bot en Vivo</div>', unsafe_allow_html=True)
    cols_bot = st.columns(5)
    cols_bot[0].markdown(f"""
    <div class="metric-card">
      <div class="mc-label">Balance</div>
      <div class="mc-value">${bot_balance:,.2f}</div>
      <div class="mc-delta pos">USDT</div>
    </div>
    """, unsafe_allow_html=True)
    cols_bot[1].markdown(f"""
    <div class="metric-card">
      <div class="mc-label">P&L Abierto</div>
      <div class="mc-value" style="color:{'#3D5A45' if open_pnl>=0 else '#D94F4F'}">${open_pnl:,.2f}</div>
      <div class="mc-delta {'pos' if open_pnl>=0 else 'neg'}">{'▲' if open_pnl>=0 else '▼'} Flotante</div>
    </div>
    """, unsafe_allow_html=True)
    cols_bot[2].markdown(f"""
    <div class="metric-card">
      <div class="mc-label">P&L Cerrado Hoy</div>
      <div class="mc-value" style="color:{'#3D5A45' if closed_pnl_today>=0 else '#D94F4F'}">${closed_pnl_today:,.2f}</div>
      <div class="mc-delta {'pos' if closed_pnl_today>=0 else 'neg'}">{'▲' if closed_pnl_today>=0 else '▼'} Hoy</div>
    </div>
    """, unsafe_allow_html=True)
    cols_bot[3].markdown(f"""
    <div class="metric-card">
      <div class="mc-label">Drawdown</div>
      <div class="mc-value" style="color:#D94F4F">{dd_pct:.1f}%</div>
      <div class="mc-delta neg">desde pico</div>
    </div>
    """, unsafe_allow_html=True)
    cols_bot[4].markdown(f"""
    <div class="metric-card">
      <div class="mc-label">Win Rate Hoy</div>
      <div class="mc-value">{win_rate_today:.0f}%</div>
      <div class="mc-delta {'pos' if win_rate_today>=50 else 'neg'}">{len(today_trades)} trades</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="s-card">', unsafe_allow_html=True)
    st.markdown("**Equity Curve (últimos 30 días)**")
    fig_eq, ax_eq = plt.subplots(figsize=(6, 3))
    fig_eq.patch.set_facecolor('white')
    ax_eq.set_facecolor('white')
    ax_eq.plot(curve_dates, curve_values, color='#3D5A45', linewidth=2)
    ax_eq.fill_between(curve_dates, curve_values, min(curve_values)-10, color='#EAF2EC', alpha=0.5)
    ax_eq.axhline(y=initial_balance, color='#8FA896', linestyle='--', linewidth=0.8, label='Inicio')
    ax_eq.set_ylabel('Balance (USDT)', fontsize=8, color='#4A6050')
    ax_eq.grid(axis='y', linestyle='--', alpha=0.4, color='#E8E4DF')
    ax_eq.spines[['top','right']].set_visible(False)
    ax_eq.legend(fontsize=8)
    st.pyplot(fig_eq, use_container_width=True)
    st.markdown('</div>', unsafe_allow_html=True)

except FileNotFoundError:
    st.info("Métricas del bot no disponibles (paper_state.json no encontrado).")
except Exception as e:
    st.warning(f"No se pudieron cargar las métricas del bot: {e}")


# ── ACCIONES (2 columnas) ──
col_left, col_right = st.columns(2, gap="large")

with col_left:
    # Nuevo inversor
    st.markdown('<div class="s-card"><h3><span class="icon-badge">💵</span> Nuevo inversor</h3>', unsafe_allow_html=True)
    nombre   = st.text_input("Nombre completo", key="nuevo_nombre", placeholder="Ej: Juan García")
    usuario  = st.text_input("Usuario (único)", key="nuevo_usuario", placeholder="juangarcia")
    contra   = st.text_input("Contraseña inicial", type="password", key="nuevo_password")
    monto    = st.number_input("Depósito inicial (USDT)", min_value=0.0, step=10.0, value=1000.0, key="nuevo_monto")
    if st.button("Crear inversor", key="btn_crear"):
        if nombre.strip() and monto > 0 and usuario.strip():
            st.session_state.pending_action = {
                "tipo":"nuevo","nombre":nombre.strip(),"usuario":usuario.strip(),
                "password":contra or "changeme","monto":monto
            }
            st.session_state.confirm_nuevo = True
        else:
            st.error("Completá nombre, usuario y monto.")
    if st.session_state.confirm_nuevo:
        a = st.session_state.pending_action
        st.warning(f"¿Crear a **{a['nombre']}** con ${a['monto']:,.2f}?")
        c1, c2 = st.columns(2)
        if c1.button("✅ Confirmar", key="cnf_nuevo"):
            pm.registrar_deposito(a['nombre'], a['monto'], usuario=a['usuario'], password=a['password'])
            st.success(f"Inversor {a['nombre']} creado.")
            reset_all(); st.rerun()
        if c2.button("Cancelar", key="cnl_nuevo"): reset_all(); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    # Corregir capital
    if pm.inversores:
        st.markdown('<div class="s-card"><h3><span class="icon-badge">🔧</span> Corregir capital</h3>', unsafe_allow_html=True)
        ops_corr  = [f"{i['name']} ({i['id']})" for i in pm.inversores]
        ids_corr  = {op: inv["id"] for op, inv in zip(ops_corr, pm.inversores)}
        sel_corr  = st.selectbox("Inversor", ops_corr, key="sel_corregir")
        nuevo_val = st.number_input("Nuevo capital exacto", min_value=0.0, step=10.0, value=1000.0, key="corregir_valor")
        if st.button("Corregir capital", key="btn_corregir"):
            st.session_state.pending_action = {
                "tipo":"corregir","id":ids_corr[sel_corr],
                "valor":nuevo_val,"nombre":sel_corr.split(" (")[0]
            }
            st.session_state.confirm_corregir = True
        if st.session_state.confirm_corregir:
            a = st.session_state.pending_action
            st.warning(f"¿Ajustar capital de **{a['nombre']}** a ${a['valor']:,.2f}?")
            c1, c2 = st.columns(2)
            if c1.button("✅ Confirmar ajuste", key="cnf_corregir"):
                pm.ajustar_capital(a["id"], a["valor"])
                st.success(f"Capital ajustado."); reset_all(); st.rerun()
            if c2.button("Cancelar", key="cnl_corregir"): reset_all(); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

with col_right:
    if pm.inversores:
        # Agregar capital
        st.markdown('<div class="s-card"><h3><span class="icon-badge">💰</span> Agregar capital</h3>', unsafe_allow_html=True)
        ops     = [f"{i['name']} ({i['id']})" for i in pm.inversores]
        ids     = {op: inv["id"] for op, inv in zip(ops, pm.inversores)}
        sel     = st.selectbox("Inversor", ops, key="sel_agregar")
        cant    = st.number_input("Monto a agregar (USDT)", min_value=0.0, step=10.0, value=1000.0, key="monto_agregar")
        if st.button("Agregar capital", key="btn_agregar"):
            if cant > 0:
                st.session_state.pending_action = {
                    "tipo":"agregar","id":ids[sel],"monto":cant,"nombre":sel.split(" (")[0]
                }
                st.session_state.confirm_agregar = True
            else: st.warning("Monto > 0")
        if st.session_state.confirm_agregar:
            a = st.session_state.pending_action
            st.warning(f"¿Agregar ${a['monto']:,.2f} a **{a['nombre']}**?")
            c1, c2 = st.columns(2)
            if c1.button("✅ Confirmar", key="cnf_agregar"):
                pm.registrar_deposito("", a["monto"], inversor_id=a["id"])
                st.success(f"Capital agregado."); reset_all(); st.rerun()
            if c2.button("Cancelar", key="cnl_agregar"): reset_all(); st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

        # Eliminar inversor
        st.markdown('<div class="s-card"><h3><span class="icon-badge">🗑️</span> Eliminar inversor</h3>', unsafe_allow_html=True)
        ops_elim  = [f"{i['name']} ({i['id']})" for i in pm.inversores]
        ids_elim  = {op: inv["id"] for op, inv in zip(ops_elim, pm.inversores)}
        sel_elim  = st.selectbox("Inversor a eliminar", ops_elim, key="sel_eliminar")
        if st.button("⚠️ Eliminar inversor", key="btn_eliminar"):
            st.session_state.eliminar_id = ids_elim[sel_elim]; st.rerun()
        if st.session_state.eliminar_id:
            inv_elim = next((i for i in pm.inversores if i["id"] == st.session_state.eliminar_id), None)
            if inv_elim:
                st.error(f"⚠️ Vas a eliminar a **{inv_elim['name']}**. Irreversible.")
                c1, c2 = st.columns(2)
                if c1.button("✅ Confirmar eliminación", key="cnf_eliminar"):
                    pm.eliminar_inversor(st.session_state.eliminar_id)
                    st.success("Inversor eliminado."); st.session_state.eliminar_id = None; st.rerun()
                if c2.button("❌ Cancelar", key="cnl_eliminar"):
                    st.session_state.eliminar_id = None; st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)


# ── CARTERA ──
st.markdown('<div class="section-label">👥 Inversores activos</div>', unsafe_allow_html=True)
if res:
    cards_html = '<div class="inv-grid">'
    for r in res:
        pnl       = r['ganancia_neta']
        pct_cls   = "pos" if pnl >= 0 else "neg"
        pct_arrow = "▲" if pnl >= 0 else "▼"
        pct_show  = f"{r['porcentaje']:.1f}%"
        cards_html += f"""
        <div class="inv-card">
          <div class="inv-name">{r['name']}</div>
          <div class="inv-capital">${r['capital_actual']:,.2f}</div>
          <div class="inv-pnl {pct_cls}">{pct_arrow} ${pnl:,.2f} PnL</div>
          <span class="inv-badge {pct_cls}">{pct_show} del fondo</span>
        </div>"""
    cards_html += '</div>'
    st.markdown(cards_html, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    with st.expander("📋 Ver tabla completa de cartera"):
        df = pd.DataFrame(res)[['name','usuario','id','capital_actual','porcentaje',
                                  'high_water_mark','ganancia_neta','retiro_pendiente','password']]
        df.columns = ['Nombre','Usuario','ID','Capital actual','%','HWM','PnL neto','Retiro','Pass']
        df['Retiro'] = df['Retiro'].map({True:'Sí', False:'No'})
        show_pass = st.checkbox("Mostrar contraseñas")
        cols_show = df.columns.tolist() if show_pass else [c for c in df.columns if c != 'Pass']
        st.dataframe(
            df[cols_show].style.format({
                'Capital actual':'${:,.2f}','%':'{:.2f}%',
                'HWM':'${:,.2f}','PnL neto':'${:,.2f}'
            }),
            use_container_width=True, height=300
        )


# ── RETIROS ──
st.markdown('<div class="section-label" style="margin-top:1.6rem;">📤 Retiros pendientes</div>', unsafe_allow_html=True)
if pendientes:
    dfp = pd.DataFrame(pendientes)[['name','fecha_solicitud','porcentaje','monto_solicitado','id','capital_actual','high_water_mark']]
    dfp.columns = ['Nombre','Fecha','%','Monto solicitado','ID','Capital actual','HWM']
    st.dataframe(dfp.style.format({'Monto solicitado':'${:,.2f}','Capital actual':'${:,.2f}','HWM':'${:,.2f}'}), use_container_width=True)

    st.markdown('<div class="s-card"><h3><span class="icon-badge">✅</span> Completar retiro</h3>', unsafe_allow_html=True)
    opciones_ret = [f"{p['name']} (${p['monto_solicitado']:,.2f})" for p in pendientes]
    ids_ret      = {op: p for op, p in zip(opciones_ret, pendientes)}
    sel_ret      = st.selectbox("Seleccionar retiro", opciones_ret, key="sel_retiro")
    if st.button("📋 Ver detalle y completar"):
        st.session_state["ver_detalle"] = ids_ret[sel_ret]
    if "ver_detalle" in st.session_state:
        p       = st.session_state["ver_detalle"]
        ganancia = max(0.0, p['capital_actual'] - p['high_water_mark'])
        com_g   = ganancia * 0.50
        com_s   = p['capital_actual'] * 0.005
        st.markdown(f"""
        <div style="background:var(--surface-2);border-radius:10px;padding:1rem 1.2rem;
             border:1px solid var(--border);font-size:0.88rem;line-height:2;">
          <b>{p['name']}</b><br>
          Capital actual: <b>${p['capital_actual']:,.2f}</b><br>
          Ganancia: <b>${ganancia:,.2f}</b><br>
          Comisión gestión (50%): <b>${com_g:,.2f}</b><br>
          Comisión salida (0.5%): <b>${com_s:,.2f}</b><br>
          <span style="color:var(--green-dark);font-weight:700;">
            Total a recibir: ${p['monto_solicitado']:,.2f}
          </span>
        </div>
        """, unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if c1.button("✅ Confirmar retiro"):
            if pm.completar_retiro(p['id']):
                st.success("Retiro completado.")
                del st.session_state["ver_detalle"]; st.rerun()
        if c2.button("Cancelar"):
            del st.session_state["ver_detalle"]; st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
else:
    st.markdown("""
    <div class="s-card" style="text-align:center;padding:2rem;color:var(--text-dim);">
      ✓ No hay retiros pendientes
    </div>
    """, unsafe_allow_html=True)


# ── HISTORIAL ──
st.markdown('<div class="section-label" style="margin-top:1.6rem;">📜 Historial</div>', unsafe_allow_html=True)
with st.expander("Ver todas las transacciones"):
    hist = pm.obtener_historial_global()
    if hist:
        dfh = pd.DataFrame(hist)[['fecha','inversor_nombre','tipo','monto','capital_antes','capital_despues']]
        dfh.columns = ['Fecha','Inversor','Tipo','Monto','Antes','Después']
        st.dataframe(
            dfh.style.format({'Monto':'${:,.2f}','Antes':'${:,.2f}','Después':'${:,.2f}'}),
            use_container_width=True, height=380
        )
    else:
        st.info("Sin transacciones registradas.")


# ── GRÁFICOS ──
if res:
    st.markdown('<div class="section-label" style="margin-top:1.6rem;">📊 Visualización</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2, gap="large")

    GREEN_PALETTE = ['#3D5A45','#547A5E','#7BAF87','#A8CDB0','#C9E3CE','#E4F1E7']

    with col1:
        st.markdown('<div class="s-card">', unsafe_allow_html=True)
        st.markdown("**Distribución de capital**")
        fig1, ax1 = plt.subplots(figsize=(5, 4))
        fig1.patch.set_facecolor('white')
        labels = [r['name'] for r in res]
        sizes  = [r['capital_actual'] for r in res]
        colors = GREEN_PALETTE[:len(sizes)]
        wedges, texts, autotexts = ax1.pie(
            sizes, labels=labels, autopct='%1.1f%%', startangle=140,
            colors=colors, wedgeprops=dict(width=0.6, edgecolor='white', linewidth=2)
        )
        for t in texts:    t.set_fontsize(9);  t.set_color('#3A4A3E')
        for t in autotexts: t.set_fontsize(8); t.set_color('white'); t.set_fontweight('bold')
        ax1.axis('equal')
        st.pyplot(fig1, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="s-card">', unsafe_allow_html=True)
        st.markdown("**PnL neto por inversor**")
        fig2, ax2 = plt.subplots(figsize=(5, 4))
        fig2.patch.set_facecolor('white')
        nombres  = [r['name'] for r in res]
        ganancias = [r['ganancia_neta'] for r in res]
        bar_colors = ['#3D5A45' if g >= 0 else '#D94F4F' for g in ganancias]
        bars = ax2.barh(nombres, ganancias, color=bar_colors, height=0.55,
                        edgecolor='none', zorder=2)
        ax2.axvline(0, color='#E8E4DF', linewidth=1.2, zorder=1)
        ax2.set_xlabel('USD', fontsize=8, color='#8FA896')
        ax2.grid(axis='x', linestyle='--', alpha=0.4, color='#E8E4DF', zorder=0)
        ax2.spines[['top','right','left','bottom']].set_visible(False)
        for bar, val in zip(bars, ganancias):
            xpos = val + (max(abs(v) for v in ganancias) * 0.02)
            ax2.text(xpos, bar.get_y() + bar.get_height()/2,
                     f'${val:,.0f}', va='center', fontsize=7.5,
                     color='#3D5A45' if val >= 0 else '#D94F4F', fontweight='600')
        st.pyplot(fig2, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
