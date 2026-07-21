import streamlit as st
from streamlit_autorefresh import st_autorefresh
st_autorefresh(interval=10000, key="investor_refresh")

from portfolio_manager import PortfolioManager
from datetime import date, timedelta

RUTA = "/Users/franciscootamendi/ai-agents-v3/investors.json"

st.set_page_config(page_title="Investor Panel", page_icon="🌿", layout="wide")

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
  max-width: 800px !important;
  margin: 0 auto !important;
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

/* ── METRIC CARDS ROW ── */
.metric-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 1.8rem;
}
.metric-card {
  background: var(--surface);
  border-radius: var(--radius-sm);
  padding: 1.1rem 1.2rem;
  box-shadow: var(--shadow-sm);
  border: 1px solid var(--border);
}
.metric-card .mc-label  { font-size: 0.72rem; font-weight: 500; color: var(--text-dim); letter-spacing: 0.04em; text-transform: uppercase; }
.metric-card .mc-value  { font-size: 1.55rem; font-weight: 700; color: var(--text); margin-top: 4px; }
.mc-delta { font-size: 0.72rem; font-weight: 600; margin-top: 4px; }
.mc-delta.pos { color: var(--green-mid); }
.mc-delta.neg { color: var(--red); }

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

/* Alertas */
.stAlert {
  border-radius: 10px !important;
  border-left-width: 3px !important;
  font-family: 'Outfit', sans-serif !important;
  font-size: 0.85rem !important;
}

/* Labels */
label, .stSelectbox label, .stTextInput label, .stNumberInput label {
  font-size: 0.78rem !important;
  font-weight: 600 !important;
  color: var(--text-mid) !important;
  letter-spacing: 0.03em !important;
}

/* Slider */
.stSlider>div>div>div>div {
  background: var(--green-mid) !important;
}
</style>
""", unsafe_allow_html=True)

if 'pm' not in st.session_state:
    st.session_state.pm = PortfolioManager(investors_file=RUTA)
pm = st.session_state.pm

if 'user' not in st.session_state: st.session_state.user = None
if 'confirm_retiro' not in st.session_state: st.session_state.confirm_retiro = False
if 'datos_retiro' not in st.session_state: st.session_state.datos_retiro = None

# ── LOGIN ──
if st.session_state.user is None:
    st.markdown('<div class="page-header">'
                '<div class="page-header-left">'
                '<div class="logo-mark">🌿</div>'
                '<div><div class="page-title">Investor Panel</div>'
                '<div class="page-sub">Private Access</div></div>'
                '</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="s-card"><h3><span class="icon-badge">🔐</span> Iniciá sesión</h3>', unsafe_allow_html=True)
    usuario = st.text_input("Usuario", key="login_user")
    password = st.text_input("Contraseña", type="password", key="login_pass")
    if st.button("Ingresar →"):
        inv = pm.login(usuario, password)
        if inv:
            st.session_state.user = inv
            st.rerun()
        else:
            st.error("Usuario o contraseña incorrectos.")
    st.markdown('</div>', unsafe_allow_html=True)
    st.stop()

# ── SESIÓN ACTIVA ──
inv = st.session_state.user
st.markdown(f"""
<div class="page-header">
  <div class="page-header-left">
    <div class="logo-mark">🌿</div>
    <div>
      <div class="page-title">{inv['name']}</div>
      <div class="page-sub">Cuenta de inversión · {inv['id']}</div>
    </div>
  </div>
</div>
<div class="metric-row">
  <div class="metric-card">
    <div class="mc-label">Capital actual</div>
    <div class="mc-value">${inv['capital_actual']:,.2f}</div>
    <div class="mc-delta pos">USDT</div>
  </div>
  <div class="metric-card">
    <div class="mc-label">Ganancia neta</div>
    <div class="mc-value">${inv['capital_actual']-inv['capital_invertido']:,.2f}</div>
    <div class="mc-delta {'pos' if (inv['capital_actual']-inv['capital_invertido'])>=0 else 'neg'}">{'▲' if (inv['capital_actual']-inv['capital_invertido'])>=0 else '▼'} PnL</div>
  </div>
  <div class="metric-card">
    <div class="mc-label">High-Water Mark</div>
    <div class="mc-value">${inv['high_water_mark']:,.2f}</div>
    <div class="mc-delta pos">Referencia</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── RENTABILIDAD POR PERÍODOS ──
rend = pm.obtener_rentabilidad_por_periodos(inv["id"])
if rend:
    st.markdown('<div class="s-card">', unsafe_allow_html=True)
    st.markdown("### 📈 Rentabilidad por períodos")
    periodos = [
        ("Esta semana", "semana"),
        ("Este mes", "mes"),
        ("Este trimestre", "trimestre"),
        ("Este cuatrimestre", "cuatrimestre"),
        ("Este año", "año"),
        ("Total acumulado", "total")
    ]
    if all(rend[p[1]]["pnl"] == 0 for p in periodos):
        st.markdown("*Aún no hay rentabilidad registrada.*")
    else:
        # Tabla simple HTML
        html = '<table style="width:100%;border-collapse:collapse;font-size:0.9rem;">'
        html += '<tr style="border-bottom:1px solid var(--border);"><th style="text-align:left;padding:8px 0;">Período</th><th style="text-align:right;padding:8px 0;">PnL</th><th style="text-align:right;padding:8px 0;">Rentabilidad %</th></tr>'
        for label, key in periodos:
            pnl = rend[key]["pnl"]
            pct = rend[key]["porcentaje"]
            color_pnl = '#3D5A45' if pnl >= 0 else '#D94F4F'
            color_pct = '#3D5A45' if pct >= 0 else '#D94F4F'
            html += f'<tr><td style="padding:6px 0;">{label}</td><td style="text-align:right;padding:6px 0;color:{color_pnl};">${pnl:+,.2f}</td><td style="text-align:right;padding:6px 0;color:{color_pct};">{pct:+.1f}%</td></tr>'
        html += '</table>'
        st.markdown(html, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

# ── RETIRO PENDIENTE ──
if inv.get("retiro_pendiente"):
    st.warning(f"Tenés un retiro pendiente por **${inv.get('monto_retiro_solicitado', 0):,.2f}**.")
else:
    ventana_abierta = pm.ventana_retiro_abierta()
    st.markdown('<div class="s-card"><h3><span class="icon-badge">💰</span> Solicitar retiro</h3>', unsafe_allow_html=True)
    if not ventana_abierta:
        hoy = date.today()
        if hoy.month == 12:
            prox_ventana = date(hoy.year, 12, 29)
        else:
            ultimo_prox = date(hoy.year, hoy.month+1, 1) - timedelta(days=1)
            prox_ventana = ultimo_prox - timedelta(days=2)
        st.info(f"La ventana de retiro está cerrada. Solo se puede solicitar retiro los últimos 3 días del mes. Próxima ventana: {prox_ventana.strftime('%d/%m/%Y')}")
        st.button("📤 Solicitar retiro", disabled=True)
    else:
        porcentaje = st.select_slider("Porcentaje a retirar", options=[10,20,30,40,50,60,70,80,90,100], value=100)
        monto_bruto = inv["capital_actual"] * (porcentaje/100)
        ganancia_total = max(0.0, inv["capital_actual"] - inv["high_water_mark"])
        ganancia_prop = ganancia_total * (porcentaje/100)
        com_g = ganancia_prop * 0.50
        com_s = monto_bruto * 0.005
        total = monto_bruto - com_g - com_s
        st.markdown(f"""
        <div style="background:var(--surface-2);border-radius:10px;padding:1rem 1.2rem;
             border:1px solid var(--border);font-size:0.88rem;line-height:2;">
          Monto bruto: <b>${monto_bruto:,.2f}</b><br>
          Comisión gestión (50% s/ganancia): <b>${com_g:,.2f}</b><br>
          Comisión salida (0.5%): <b>${com_s:,.2f}</b><br>
          <span style="color:var(--green-dark);font-weight:700;">Total a recibir: ${total:,.2f}</span>
        </div>
        """, unsafe_allow_html=True)
        if st.button("📤 Solicitar retiro"):
            st.session_state.datos_retiro = {"porcentaje": porcentaje, "total": total}
            st.session_state.confirm_retiro = True
    st.markdown('</div>', unsafe_allow_html=True)

if st.session_state.confirm_retiro and st.session_state.datos_retiro:
    d = st.session_state.datos_retiro
    st.warning(f"¿Confirmás el retiro del {d['porcentaje']}% (recibirás ${d['total']:,.2f})?")
    c1,c2 = st.columns(2)
    if c1.button("✅ Confirmar", key="cnf_retiro"):
        res = pm.procesar_retiro(inv["id"], porcentaje=d["porcentaje"])
        if res:
            if 'error' in res:
                st.error(res['error'])
            else:
                st.success(f"Retiro solicitado. Total a recibir: ${res['total_recibir']:,.2f}")
                st.balloons()
            st.session_state.confirm_retiro = False
            st.session_state.datos_retiro = None
            st.session_state.user = pm.login(inv["usuario"], inv["password"])
            st.rerun()
    if c2.button("Cancelar", key="cnl_retiro"):
        st.session_state.confirm_retiro = False
        st.session_state.datos_retiro = None
        st.rerun()

# ── CAMBIAR CONTRASEÑA ──
st.markdown('<div class="s-card"><h3><span class="icon-badge">🔒</span> Cambiar contraseña</h3>', unsafe_allow_html=True)
if st.button("Quiero cambiar mi contraseña"):
    st.session_state.mostrar_cambio = True
if st.session_state.get("mostrar_cambio"):
    nueva = st.text_input("Nueva contraseña", type="password", key="nueva_pass")
    if st.button("Guardar nueva contraseña"):
        pm.cambiar_password(inv["id"], nueva)
        st.success("Contraseña actualizada.")
        st.session_state.mostrar_cambio = False
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

# ── CERRAR SESIÓN ──
if st.button("🚪 Cerrar sesión"):
    st.session_state.user = None
    st.rerun()
