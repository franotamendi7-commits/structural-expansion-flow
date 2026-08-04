"""
CYBER COMMAND — Mission Control Dashboard
==========================================
Cybersecurity-themed Bento Grid: pink/purple/magenta glassmorphism.
Single viewport, zero scroll, zero tabs.

Run: streamlit run app.py
"""
import os
import sys
import time
import json
import hmac
import hashlib
import urllib.parse
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests

# ============================================================
# CONFIGURATION
# ============================================================
PAGE_TITLE = "CYBER COMMAND"
PAGE_ICON = "\U0001f52e"
REFRESH_INTERVAL_SEC = 30
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
PAIR_LABELS = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL",
    "XRPUSDT": "XRP", "BNBUSDT": "BNB"
}
APP_DIR = Path(__file__).resolve().parent


# ============================================================
# BINANCE HELPERS
# ============================================================
def _load_secrets():
    # First check environment variables (for Railway deployment)
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_SECRET_KEY", "")
    if api_key and api_secret:
        return api_key, api_secret
    
    # Fallback to secrets.toml file
    secrets_path = APP_DIR / ".streamlit" / "secrets.toml"
    try:
        import toml
        secrets = toml.load(secrets_path)
        return secrets.get("BINANCE_API_KEY", ""), secrets.get("BINANCE_SECRET_KEY", "")
    except Exception:
        return "", ""


def _sign_params(params: dict, secret: str) -> dict:
    query = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params


def _testnet_request(method: str, endpoint: str, params: dict = None) -> Optional[dict]:
    api_key, api_secret = _load_secrets()
    if not api_key or not api_secret:
        return None
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    signed = _sign_params(params, api_secret)
    headers = {"X-MBX-APIKEY": api_key}
    base = "https://testnet.binancefuture.com"
    try:
        if method == "GET":
            r = requests.get(f"{base}{endpoint}", params=signed, headers=headers, timeout=10)
        else:
            r = requests.post(f"{base}{endpoint}", params=signed, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def get_testnet_balance() -> float:
    data = _testnet_request("GET", "/fapi/v3/balance")
    if data:
        for b in data:
            if b.get("asset") == "USDT":
                return float(b.get("balance", 0))
    return 0.0


def get_testnet_positions() -> List[Dict]:
    data = _testnet_request("GET", "/fapi/v3/positionRisk")
    if data:
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]
    return []


@st.cache_data(ttl=300)
def get_fear_greed() -> Dict:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [{}])[0]
            return {
                "value": int(data.get("value", 50)),
                "label": data.get("value_classification", "Neutral"),
            }
    except Exception:
        pass
    return {"value": 50, "label": "Neutral"}


@st.cache_data(ttl=300)
def get_24h_tickers() -> Dict:
    result = {}
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        if r.status_code == 200:
            for t in r.json():
                sym = t.get("symbol", "")
                if sym in PAIRS:
                    result[sym] = {
                        "price": float(t.get("lastPrice", 0)),
                        "change_pct": float(t.get("priceChangePercent", 0)),
                    }
    except Exception:
        pass
    return result


def fear_greed_color(value: int) -> str:
    if value <= 25: return "#ff4757"
    elif value <= 40: return "#ff6b35"
    elif value <= 60: return "#8b949e"
    elif value <= 75: return "#00cc6a"
    else: return "#00ff88"


def fear_greed_label(value: int) -> str:
    if value <= 25: return "EXTREME FEAR"
    elif value <= 40: return "FEAR"
    elif value <= 60: return "NEUTRAL"
    elif value <= 75: return "GREED"
    else: return "EXTREME GREED"


# ============================================================
# STATE FILE READERS
# ============================================================
def load_json(path: Path, default=None):
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def get_drawdown_state() -> Dict:
    return load_json(APP_DIR / "drawdown_state.json", {
        "peak_equity": 0, "current_equity": 0,
        "current_dd_pct": 0, "is_stopped": False,
    })


def get_parameter_state() -> Dict:
    return load_json(APP_DIR / "parameter_state.json", {
        "params": {}, "is_paused": False, "total_trades_recorded": 0,
    })


def get_signal_log() -> List[Dict]:
    data = load_json(APP_DIR / "signal_log.json", [])
    if isinstance(data, list):
        return data[-30:]
    return []


def get_trade_history() -> List[Dict]:
    data = load_json(APP_DIR / "trade_history.json", [])
    if isinstance(data, list):
        return data[-50:]
    return []


def get_equity_history() -> List[Dict]:
    data = load_json(APP_DIR / "equity_history.json", [])
    if isinstance(data, list):
        return data
    return []


def get_bot_status() -> str:
    import os
    # Method 1: Check if supervisord is managing the bot
    try:
        result = subprocess.run(["pgrep", "-f", "supervisord"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            # Check if bot process is alive
            result2 = subprocess.run(["pgrep", "-f", "run_bots_background"],
                                     capture_output=True, text=True, timeout=5)
            if result2.returncode == 0:
                pid = result2.stdout.strip().split("\n")[0]
                return f"RUNNING (PID {pid})"
            # Also check for multi_bot_v2
            result3 = subprocess.run(["pgrep", "-f", "multi_bot_v2"],
                                     capture_output=True, text=True, timeout=5)
            if result3.returncode == 0:
                pid = result3.stdout.strip().split("\n")[0]
                return f"RUNNING (PID {pid})"
    except Exception:
        pass
    # Method 2: Check if any python bot process exists
    try:
        result4 = subprocess.run(["pgrep", "-f", "run_bots_background|multi_bot_v2"],
                                 capture_output=True, text=True, timeout=5)
        if result4.returncode == 0:
            pid = result4.stdout.strip().split("\n")[0]
            return f"RUNNING (PID {pid})"
    except Exception:
        pass
    # Method 3: Check signal_log.json modification time (fallback)
    try:
        log_path = APP_DIR / "signal_log.json"
        if log_path.exists():
            mtime = os.path.getmtime(log_path)
            age_seconds = time.time() - mtime
            if age_seconds < 300:  # Updated in last 5 minutes
                return f"RUNNING (log active {int(age_seconds)}s ago)"
    except Exception:
        pass
    return "STOPPED"


def get_last_log_lines(n: int = 4) -> List[str]:
    log_path = APP_DIR / "logs" / "paper_trading.log"
    try:
        if log_path.exists():
            with open(log_path) as f:
                lines = f.readlines()
                return [l.rstrip() for l in lines[-n:]]
    except Exception:
        pass
    return []


def calculate_performance_metrics(trades: List[Dict]) -> Dict:
    if not trades:
        return {
            "sharpe": 0, "profit_factor": 0, "win_rate": 0,
            "total_trades": 0, "expectancy": 0, "best_trade": 0,
            "worst_trade": 0, "total_pnl": 0,
        }

    pnls = [t.get("net", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_trades = len(trades)
    win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(pnls)

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')

    best_trade = max(pnls) if pnls else 0
    worst_trade = min(pnls) if pnls else 0

    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    if len(pnls) > 1:
        returns = np.array(pnls)
        sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252) if np.std(returns) > 0 else 0
    else:
        sharpe = 0

    return {
        "sharpe": round(sharpe, 2),
        "profit_factor": round(profit_factor, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": total_trades,
        "expectancy": round(expectancy, 4),
        "best_trade": round(best_trade, 4),
        "worst_trade": round(worst_trade, 4),
        "total_pnl": round(total_pnl, 4),
    }


# ============================================================
# PLOTLY THEME
# ============================================================
PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#e6edf3", family="JetBrains Mono", size=9),
    margin=dict(l=4, r=4, t=16, b=4),
    xaxis=dict(showgrid=False, showline=False, zeroline=False, tickfont=dict(size=8, color="#8b949e")),
    yaxis=dict(showgrid=True, gridcolor="rgba(48,54,61,0.4)", showline=False, zeroline=False,
               tickfont=dict(size=8, color="#8b949e")),
    hoverlabel=dict(bgcolor="#0f0f19", bordercolor="rgba(147,51,234,0.3)",
                     font=dict(color="#e6edf3", family="JetBrains Mono", size=9)),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)", font=dict(size=8, color="#8b949e")),
)


# ============================================================
# MAIN — Pure HTML/CSS Grid Dashboard
# ============================================================
def main():
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown("<style>#MainMenu, footer, header, [data-testid='stDecoration'] { visibility: hidden !important; height: 0 !important; min-height: 0 !important; } .block-container { padding-top: 0 !important; padding-bottom: 0 !important; padding-left: 0 !important; padding-right: 0 !important; } [data-testid='stToolbar'] { display: none !important; } iframe { width: 100% !important; }</style>", unsafe_allow_html=True)

    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=REFRESH_INTERVAL_SEC * 1000, key="refresh")
    except Exception:
        pass

    # ============================================================
    # DATA
    # ============================================================
    now = datetime.now(timezone.utc)
    bot_status = get_bot_status()
    is_running = "RUNNING" in bot_status
    balance = get_testnet_balance()
    dd_state = get_drawdown_state()
    positions = get_testnet_positions()
    unrealized_pnl = sum(float(p.get("unRealizedProfit", 0)) for p in positions)
    n_positions = len(positions)
    trade_history = get_trade_history()
    equity_hist = get_equity_history()
    signals = get_signal_log()
    log_lines = get_last_log_lines(6)
    fng = get_fear_greed()
    tickers = get_24h_tickers()
    dd_pct = dd_state.get("current_dd_pct", 0)
    peak_equity = dd_state.get("peak_equity", 0)
    param_state = get_parameter_state()
    total_trades = param_state.get("total_trades_recorded", 0)
    perf = calculate_performance_metrics(trade_history)
    fng_val = fng["value"]
    fng_color = fear_greed_color(fng_val)
    fng_label = fear_greed_label(fng_val)

    btc_price = tickers.get('BTCUSDT', {}).get('price', 0)
    btc_change = tickers.get('BTCUSDT', {}).get('change_pct', 0)
    btc_color = "#00ff88" if btc_change >= 0 else "#ff4757"

    status_cls = "pulse-green" if is_running else "pulse-red"
    status_text = "LIVE" if is_running else "STOPPED"
    status_color = "#00ff88" if is_running else "#ff4757"

    # ============================================================
    # BUILD EQUITY CHART HTML
    # ============================================================
    equity_chart_html = ""
    if len(equity_hist) > 1:
        df = pd.DataFrame(equity_hist)
        df["peak"] = df["equity"].cummax()
        df["dd"] = (df["equity"] - df["peak"]) / df["peak"] * 100
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            vertical_spacing=0.04, row_heights=[0.72, 0.28],
        )
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["equity"], mode="lines",
            line=dict(color="#c026d3", width=2), fill="tozeroy",
            fillcolor="rgba(192,38,211,0.06)", name="Equity",
            hovertemplate="Equity: $%{y:,.2f}<extra></extra>"
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["dd"], mode="lines",
            line=dict(color="#ff2d78", width=1.5), fill="tozeroy",
            fillcolor="rgba(255,45,120,0.08)", name="DD",
            hovertemplate="DD: %{y:.2f}%<extra></extra>"
        ), row=2, col=1)
        fig.add_hline(y=0, row=2, col=1, line_color="rgba(255,255,255,0.08)", line_width=1)
        fig.update_layout(**PLOTLY_LAYOUT, showlegend=False, height=500)
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(gridcolor="rgba(48,54,61,0.4)")
        fig.update_yaxes(gridcolor="rgba(48,54,61,0.4)", row=2, col=1)
        equity_chart_html = fig.to_html(full_html=False, include_plotlyjs='cdn', config={'displayModeBar': False, 'responsive': True})
    else:
        equity_chart_html = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#484f58;font-size:11px;font-family:Inter,sans-serif;">Awaiting equity data stream...</div>'

    # ============================================================
    # BUILD CARD CONTENT HTML
    # ============================================================
    
    # -- HEATMAP --
    heatmap_cells = ""
    for sym in PAIRS:
        t = tickers.get(sym, {})
        change = t.get("change_pct", 0)
        label = PAIR_LABELS.get(sym, sym[:3])
        if change > 3: bg, txt = "rgba(0,255,136,0.12)", "#00ff88"
        elif change > 1: bg, txt = "rgba(0,255,136,0.08)", "#00ff88"
        elif change > 0: bg, txt = "rgba(0,204,106,0.05)", "#00cc6a"
        elif change > -1: bg, txt = "rgba(255,71,87,0.05)", "#ff6b6b"
        elif change > -3: bg, txt = "rgba(255,71,87,0.08)", "#ff4757"
        else: bg, txt = "rgba(255,71,87,0.12)", "#ff4757"
        heatmap_cells += f'<div class="heatmap-cell" style="background:{bg};"><span class="heatmap-label">{label}</span><span style="color:{txt};font-size:8px;">{change:+.2f}%</span></div>'

    # -- PERFORMANCE --
    perf_html = f"""<div class="perf-grid">
        <div class="perf-item"><div class="perf-label">WIN RATE</div><div class="perf-value" style="color:{'#00ff88' if perf['win_rate']>50 else '#ff2d78' if perf['win_rate']>35 else '#ff4757'};">{perf['win_rate']:.1f}%</div></div>
        <div class="perf-item"><div class="perf-label">PROFIT FACTOR</div><div class="perf-value" style="color:{'#00ff88' if perf['profit_factor']>1.3 else '#ff4757' if perf['profit_factor']<1 else '#e6edf3'};">{perf['profit_factor']:.2f}</div></div>
        <div class="perf-item"><div class="perf-label">SHARPE</div><div class="perf-value" style="color:{'#00ff88' if perf['sharpe']>1 else '#e6edf3'};">{perf['sharpe']:.2f}</div></div>
        <div class="perf-item"><div class="perf-label">TRADES</div><div class="perf-value" style="color:#c026d3;">{perf['total_trades']}</div></div>
        <div class="perf-item"><div class="perf-label">TOTAL PNL</div><div class="perf-value" style="color:{'#00ff88' if perf['total_pnl']>=0 else '#ff4757'};">{'+'if perf['total_pnl']>=0 else ''}${perf['total_pnl']:.2f}</div></div>
        <div class="perf-item"><div class="perf-label">EXPECTANCY</div><div class="perf-value" style="color:{'#00ff88' if perf['expectancy']>=0 else '#ff4757'};">${perf['expectancy']:.2f}</div></div>
        <div class="perf-item"><div class="perf-label">BEST TRADE</div><div class="perf-value" style="color:#00ff88;">+${perf['best_trade']:.2f}</div></div>
        <div class="perf-item"><div class="perf-label">WORST TRADE</div><div class="perf-value" style="color:#ff4757;">${perf['worst_trade']:.2f}</div></div>
    </div>"""

    # -- FEAR & GREED --
    fng_html = f"""<div class="fng-gauge">
        <div class="fng-ring"><div class="fng-number" style="color:{fng_color};">{fng_val}</div></div>
        <div class="fng-label" style="color:{fng_color};">{fng_label}</div>
        <div class="fng-bar"><div class="fng-fill" style="width:{fng_val}%;background:linear-gradient(90deg,{fng_color},#9333ea);"></div></div>
    </div>"""

    # -- POSITIONS --
    pos_rows = ""
    if positions:
        for p in positions:
            symbol = p.get("symbol", "?")
            amt = float(p.get("positionAmt", 0))
            entry = float(p.get("entryPrice", 0))
            pnl = float(p.get("unRealizedProfit", 0))
            side = "LONG" if amt > 0 else "SHORT"
            badge_cls = "badge-long" if amt > 0 else "badge-short"
            pnl_cls = "positive" if pnl >= 0 else "negative"
            pos_rows += f'<div class="pos-row"><span style="color:#ff2d78;font-weight:700;">{PAIR_LABELS.get(symbol, symbol)}</span><span class="pos-badge {badge_cls}">{side}</span><span style="color:#8b949e;">${entry:,.2f}</span><span class="{pnl_cls}">{"+" if pnl>=0 else ""}${pnl:,.2f}</span></div>'
    else:
        pos_rows = '<div style="color:#484f58;font-size:9px;text-align:center;padding:12px;font-family:Inter,sans-serif;">No open positions</div>'

    # -- SIGNALS --
    sig_rows = ""
    if signals:
        for s in reversed(signals[-6:]):
            ts = s.get("ts", "?")
            if isinstance(ts, str) and "T" in ts:
                try:
                    dt = datetime.fromisoformat(ts.replace(" ", "T") + "+00:00" if "+" not in ts else ts)
                    ts_short = dt.strftime("%H:%M")
                except Exception:
                    ts_short = ts[-5:]
            else:
                ts_short = str(ts)[-5:]
            sym = s.get("symbol", "?")
            direction = s.get("dir", 0)
            signal_type = s.get("type", "")
            if signal_type == "exit": dot_cls = "sig-exit"
            elif direction == 1: dot_cls = "sig-long"
            elif direction == -1: dot_cls = "sig-short"
            else: dot_cls = ""
            sig_rows += f'<div class="sig-row"><div style="display:flex;align-items:center;"><span class="sig-dot {dot_cls}"></span><span style="color:#ff2d78;font-weight:600;">{PAIR_LABELS.get(sym, sym)}</span></div><span style="color:#8b949e;font-size:9px;">{ts_short}</span></div>'
    else:
        sig_rows = '<div style="color:#484f58;font-size:9px;text-align:center;padding:12px;font-family:Inter,sans-serif;">No signals yet</div>'

    # -- BOT STATUS --
    is_paused = param_state.get("is_paused", False)
    consec_losses = param_state.get("consecutive_losses", 0)
    bot_status_html = f"""<div class="bot-status-grid">
        <div class="bot-item"><div class="bot-item-label">STATUS</div><div class="bot-item-value" style="color:{status_color};font-size:12px;">{status_text}</div></div>
        <div class="bot-item"><div class="bot-item-label">PAUSED</div><div class="bot-item-value" style="color:{'#ff9f43' if is_paused else '#00ff88'};font-size:12px;">{'YES' if is_paused else 'NO'}</div></div>
        <div class="bot-item"><div class="bot-item-label">CONSEC LOSSES</div><div class="bot-item-value" style="color:{'#ff4757' if consec_losses>=3 else '#e6edf3'};font-size:14px;">{consec_losses}</div></div>
        <div class="bot-item"><div class="bot-item-label">PEAK EQUITY</div><div class="bot-item-value" style="color:#9333ea;font-size:12px;">${peak_equity:,.0f}</div></div>
    </div>"""

    # -- BOT LOG --
    log_inner = ""
    if log_lines:
        for line in reversed(log_lines):
            css = "log-warning" if "WARNING" in line else ("log-error" if "ERROR" in line else ("log-success" if "SUCCESS" in line or "PROFIT" in line else "log-info"))
            short_line = line[:80] + "..." if len(line) > 80 else line
            log_inner += f'<div class="log-line {css}">{short_line}</div>'
    else:
        log_inner = '<div style="color:#484f58;font-size:9px;text-align:center;padding:12px;font-family:Inter,sans-serif;">No logs</div>'

    # ============================================================
    # FULL HTML DASHBOARD
    # ============================================================
    dd_color = "#00ff88" if dd_pct >= 0 else "#ff9f43" if dd_pct > -5 else "#ff4757"
    pnl_color = "#00ff88" if unrealized_pnl >= 0 else "#ff4757"
    pnl_sign = "+" if unrealized_pnl >= 0 else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
html, body {{ height:100vh; overflow:hidden; background:#050508; font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; color:#e6edf3; }}

.dashboard {{
    display: grid;
    grid-template-columns: 1.1fr 0.8fr 0.6fr 0.8fr;
    grid-template-rows: 48px 1fr 0.8fr;
    gap: 6px;
    height: 100vh;
    padding: 6px 8px;
    background:
        radial-gradient(ellipse 80% 50% at 50% -20%, rgba(147,51,234,0.08), transparent),
        radial-gradient(ellipse 60% 40% at 80% 100%, rgba(255,45,120,0.05), transparent),
        radial-gradient(ellipse 40% 30% at 10% 60%, rgba(192,38,211,0.04), transparent),
        #050508;
}}

.card {{
    background: rgba(15,15,25,0.70);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255,45,120,0.15);
    border-radius: 12px;
    padding: 12px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    transition: border-color 0.3s ease, box-shadow 0.3s ease;
}}
.card:hover {{
    border-color: rgba(147,51,234,0.30);
    box-shadow: 0 0 24px rgba(147,51,234,0.10), inset 0 0 24px rgba(147,51,234,0.04);
}}

/* HEADER */
.header {{
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 16px;
}}
.header-left {{ display:flex; align-items:center; gap:14px; }}
.header-logo {{
    font-size: 14px; font-weight: 900; letter-spacing: -0.03em;
    background: linear-gradient(135deg, #ff2d78, #9333ea, #c026d3);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    background-clip: text;
}}
.header-divider {{ width:1px; height:20px; background:rgba(255,255,255,0.08); }}
.header-status {{ display:flex; align-items:center; gap:6px; }}
.pulse-dot {{ width:8px; height:8px; border-radius:50%; position:relative; }}
.pulse-dot::after {{ content:''; position:absolute; inset:-3px; border-radius:50%; animation:ping 2s cubic-bezier(0,0,0.2,1) infinite; }}
.pulse-green {{ background:#00ff88; box-shadow:0 0 8px #00ff88; }}
.pulse-green::after {{ background:#00ff88; opacity:0.4; }}
.pulse-red {{ background:#ff4757; box-shadow:0 0 8px #ff4757; }}
.pulse-red::after {{ background:#ff4757; opacity:0.3; animation:none; }}
@keyframes ping {{ 75%,100% {{ transform:scale(2); opacity:0; }} }}
.header-right {{ display:flex; align-items:center; gap:20px; }}
.header-stat {{ text-align:right; line-height:1.2; }}
.header-stat-label {{ font-size:8px; color:#484f58; text-transform:uppercase; letter-spacing:0.1em; font-weight:600; }}
.header-stat-value {{ font-size:13px; font-weight:700; font-family:'JetBrains Mono',monospace; color:#e6edf3; }}

/* EQUITY ROW 2-3 */
.equity {{ grid-row: span 2; }}

/* CARD TITLE */
.card-title {{ font-size:9px; text-transform:uppercase; letter-spacing:0.14em; font-weight:700; margin-bottom:6px; white-space:nowrap; display:flex; align-items:center; gap:6px; }}
.card-title .dot {{ width:5px; height:5px; border-radius:50%; display:inline-block; }}

/* HEATMAP */
.heatmap-bar {{ display:grid; grid-template-columns:repeat(5,1fr); gap:3px; height:100%; align-items:center; }}
.heatmap-cell {{ display:flex; align-items:center; justify-content:center; gap:8px; border-radius:8px; padding:4px 4px; font-family:'JetBrains Mono',monospace; font-size:9px; font-weight:600; border:1px solid rgba(255,255,255,0.04); transition:all 0.3s; }}
.heatmap-cell:hover {{ border-color:rgba(255,255,255,0.1); transform:translateY(-1px); }}
.heatmap-label {{ color:#8b949e; font-weight:700; font-size:8px; }}

/* PERFORMANCE */
.perf-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px; }}
.perf-item {{ background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:6px 8px; display:flex; flex-direction:column; justify-content:center; transition:border-color 0.3s; }}
.perf-item:hover {{ border-color:rgba(147,51,234,0.2); }}
.perf-label {{ font-size:8px; text-transform:uppercase; letter-spacing:0.1em; color:#484f58; font-weight:600; white-space:nowrap; }}
.perf-value {{ font-size:15px; font-weight:700; font-family:'JetBrains Mono',monospace; color:#fff; line-height:1.3; }}

/* POSITIONS */
.pos-row {{ display:grid; grid-template-columns:50px 50px 1fr 70px; align-items:center; gap:6px; padding:4px 6px; border-bottom:1px solid rgba(255,255,255,0.03); font-family:'JetBrains Mono',monospace; font-size:10px; transition:background 0.2s; }}
.pos-row:hover {{ background:rgba(255,255,255,0.02); }}
.pos-row:last-child {{ border-bottom:none; }}
.pos-badge {{ font-size:8px; font-weight:700; padding:2px 6px; border-radius:4px; text-align:center; letter-spacing:0.05em; }}
.badge-long {{ background:rgba(0,255,136,0.1); color:#00ff88; border:1px solid rgba(0,255,136,0.15); }}
.badge-short {{ background:rgba(255,71,87,0.1); color:#ff4757; border:1px solid rgba(255,71,87,0.15); }}

/* SIGNALS */
.sig-row {{ display:flex; align-items:center; justify-content:space-between; padding:3px 4px; border-bottom:1px solid rgba(255,255,255,0.03); font-family:'JetBrains Mono',monospace; font-size:10px; transition:background 0.2s; }}
.sig-row:hover {{ background:rgba(255,255,255,0.02); }}
.sig-row:last-child {{ border-bottom:none; }}
.sig-dot {{ width:6px; height:6px; border-radius:50%; margin-right:6px; display:inline-block; box-shadow:0 0 6px currentColor; }}
.sig-long {{ background:#00ff88; color:#00ff88; }}
.sig-short {{ background:#ff4757; color:#ff4757; }}
.sig-exit {{ background:#ff9f43; color:#ff9f43; }}

/* BOT LOG */
.log-line {{ font-family:'JetBrains Mono',monospace; font-size:9px; line-height:1.6; padding:1px 4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; border-left:2px solid transparent; padding-left:6px; margin-bottom:1px; }}
.log-info {{ color:#8b949e; border-left-color:rgba(255,255,255,0.05); }}
.log-warning {{ color:#ff9f43; border-left-color:#ff9f43; }}
.log-error {{ color:#ff4757; border-left-color:#ff4757; }}
.log-success {{ color:#00ff88; border-left-color:#00ff88; }}

/* FEAR & GREED */
.fng-gauge {{ display:flex; flex-direction:column; align-items:center; justify-content:center; gap:4px; }}
.fng-ring {{ width:80px; height:80px; border-radius:50%; display:flex; align-items:center; justify-content:center; position:relative; }}
.fng-ring::before {{ content:''; position:absolute; inset:0; border-radius:50%; border:3px solid rgba(255,255,255,0.05); }}
.fng-number {{ font-size:26px; font-weight:900; font-family:'JetBrains Mono',monospace; line-height:1; }}
.fng-label {{ font-size:9px; text-transform:uppercase; letter-spacing:0.12em; font-weight:700; }}
.fng-bar {{ width:100%; height:4px; background:rgba(255,255,255,0.04); border-radius:2px; overflow:hidden; margin-top:4px; }}
.fng-fill {{ height:100%; border-radius:2px; transition:width 0.5s ease; box-shadow:0 0 8px currentColor; }}

/* BOT STATUS */
.bot-status-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:4px; }}
.bot-item {{ background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; padding:6px 8px; display:flex; flex-direction:column; justify-content:center; text-align:center; }}
.bot-item-label {{ font-size:8px; text-transform:uppercase; letter-spacing:0.1em; color:#484f58; font-weight:600; }}
.bot-item-value {{ font-size:14px; font-weight:700; font-family:'JetBrains Mono',monospace; line-height:1.3; }}

/* PLOTLY FIX */
.js-plotly-plot, .plotly {{ height:100% !important; width:100% !important; }}
</style>
</head>
<body>
<div class="dashboard">
    <!-- HEADER -->
    <div class="card header">
        <div class="header-left">
            <div class="header-logo">CYBER COMMAND</div>
            <div class="header-divider"></div>
            <div class="header-status">
                <div class="pulse-dot {status_cls}"></div>
                <span style="font-size:10px;color:{status_color};font-weight:700;letter-spacing:0.08em;">{status_text}</span>
            </div>
            <div class="header-divider"></div>
            <div style="font-size:11px;color:#484f58;font-family:'JetBrains Mono',monospace;">{now.strftime('%H:%M:%S')} UTC</div>
        </div>
        <div class="header-right">
            <div class="header-stat"><div class="header-stat-label">BTC PRICE</div><div class="header-stat-value" style="color:#ff2d78;">${btc_price:,.0f}</div></div>
            <div class="header-stat"><div class="header-stat-label">24H</div><div class="header-stat-value" style="color:{btc_color};">{btc_change:+.2f}%</div></div>
            <div class="header-stat"><div class="header-stat-label">EQUITY</div><div class="header-stat-value">${balance:,.2f}</div></div>
            <div class="header-stat"><div class="header-stat-label">UNREAL PNL</div><div class="header-stat-value" style="color:{pnl_color};">{pnl_sign}${unrealized_pnl:,.2f}</div></div>
            <div class="header-stat"><div class="header-stat-label">DRAWDOWN</div><div class="header-stat-value" style="color:{dd_color};">{dd_pct:+.1f}%</div></div>
            <div class="header-stat"><div class="header-stat-label">POSITIONS</div><div class="header-stat-value" style="color:#c026d3;">{n_positions}</div></div>
        </div>
    </div>

    <!-- EQUITY CURVE (spans 2 rows, col 1) -->
    <div class="card equity">
        <div class="card-title"><span class="dot" style="background:#9333ea;box-shadow:0 0 6px #9333ea;"></span> EQUITY CURVE</div>
        <div style="flex:1;min-height:0;overflow:hidden;">{equity_chart_html}</div>
    </div>

    <!-- PERFORMANCE (row 2, col 2) -->
    <div class="card">
        <div class="card-title"><span class="dot" style="background:#ff2d78;box-shadow:0 0 6px #ff2d78;"></span> PERFORMANCE</div>
        <div style="flex:1;min-height:0;overflow:hidden;">{perf_html}</div>
    </div>

    <!-- FEAR / GREED (row 2, col 3) -->
    <div class="card">
        <div class="card-title"><span class="dot" style="background:#c026d3;box-shadow:0 0 6px #c026d3;"></span> FEAR / GREED</div>
        <div style="flex:1;min-height:0;overflow:hidden;">{fng_html}</div>
    </div>

    <!-- HEATMAP (row 2, col 4) -->
    <div class="card">
        <div class="card-title"><span class="dot" style="background:#00d4ff;box-shadow:0 0 6px #00d4ff;"></span> MARKET HEATMAP</div>
        <div class="heatmap-bar">{heatmap_cells}</div>
    </div>

    <!-- POSITIONS (row 3, col 2) -->
    <div class="card">
        <div class="card-title"><span class="dot" style="background:#ff2d78;box-shadow:0 0 6px #ff2d78;"></span> POSITIONS ({n_positions})</div>
        <div style="flex:1;min-height:0;overflow-y:auto;">{pos_rows}</div>
    </div>

    <!-- SIGNALS (row 3, col 3) -->
    <div class="card">
        <div class="card-title"><span class="dot" style="background:#9333ea;box-shadow:0 0 6px #9333ea;"></span> SIGNALS</div>
        <div style="flex:1;min-height:0;overflow-y:auto;">{sig_rows}</div>
    </div>

    <!-- BOT STATUS + BOT LOG (row 3, col 4, combined) -->
    <div class="card" style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div>
            <div class="card-title"><span class="dot" style="background:#c026d3;box-shadow:0 0 6px #c026d3;"></span> BOT STATUS</div>
            <div style="flex:1;min-height:0;">{bot_status_html}</div>
        </div>
        <div>
            <div class="card-title"><span class="dot" style="background:#00ff88;box-shadow:0 0 6px #00ff88;"></span> BOT LOG</div>
            <div style="flex:1;min-height:0;overflow-y:auto;">{log_inner}</div>
        </div>
    </div>
</div>
</body>
</html>"""

    components.html(html, height=720, scrolling=False)


if __name__ == "__main__":
    main()
