"""
============================================================
 STRUCTURAL EXPANSION FLOW — Trading Dashboard
============================================================
 Main orchestrator app. Integrates:
   - multi_bot.py         (5 bots: BTC/ETH/SOL/XRP/BNB)
   - ws_binance_feed.py   (WebSocket price feed — optional)
   - stop_monitor.py      (unified stop monitor — optional)
   - paper_trader.py      (paper trading state — optional)
   - execution_manager.py (Binance live execution — optional)
   - risk_manager.py      (risk metrics — optional)
   - Telegram alerts

 Run with:
   streamlit run app.py

 If optional modules are missing, the dashboard still runs
 using only multi_bot.py + REST fallbacks.
============================================================
"""
import os
import sys
import time
import math
import json
import logging
import threading
import traceback
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

# Persistent equity history file
_EQUITY_HISTORY_PATH = Path(__file__).parent / "equity_history.json"

def _load_equity_history():
    if _EQUITY_HISTORY_PATH.exists():
        try:
            with open(_EQUITY_HISTORY_PATH) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _save_equity_history(history: list):
    try:
        with open(_EQUITY_HISTORY_PATH, "w") as f:
            json.dump(history[-2000:], f, indent=2)
    except Exception:
        pass

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import requests

# Auto-refresh (ajuste #1)
try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except Exception:
    AUTOREFRESH_AVAILABLE = False

# Make local modules importable from multiple candidate locations.
# This is critical: Streamlit may run app.py from a different working directory,
# and __file__ may resolve differently depending on how the script is launched.
# We try: (1) script's directory, (2) script's directory / "download",
# (3) script's parent directory, (4) current working directory, (5) cwd / "download".
_APP_DIR = Path(__file__).resolve().parent
_CANDIDATE_PATHS = [
    _APP_DIR,                          # app.py is in the same dir as multi_bot.py
    _APP_DIR / "download",             # app.py is one level up, modules in download/
    _APP_DIR.parent,                   # app.py is in download/, modules one level up
    Path.cwd(),                        # user ran streamlit from the modules' dir
    Path.cwd() / "download",           # user ran from parent dir
]
for _p in _CANDIDATE_PATHS:
    _p_str = str(_p)
    if _p_str not in sys.path and _p.exists():
        sys.path.insert(0, _p_str)

# ============================================================
# LOGGING SETUP (ajuste #10)
# ============================================================
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_logger = logging.getLogger("multi_bot_app")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    fh = RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=10*1024*1024,
                             backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _logger.addHandler(fh)
    # Also log to console
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _logger.addHandler(sh)
logger = _logger

# ============================================================
# OPTIONAL MODULE IMPORTS (graceful fallback)
# ============================================================
MODULE_ERRORS: Dict[str, str] = {}

try:
    from multi_bot import (
        MultiBotSystem, BaseBot, BtcBot, EthBot, SolBot, XrpBot, BnbBot,
        get_recent_data, fetch_klines, detect_regime, rolling_vwap, atr,
        apply_entry_slippage, apply_exit_slippage, compute_size,
        COMMISSION, SPREAD, SLIP_ATR_MULT, ATR5M_N, ATR15M_N,
        LOSS_STREAK_TRIGGER, PAUSE_MINUTES, INITIAL_EQUITY,
    )
    MULTI_BOT_AVAILABLE = True
except Exception as e:
    logger.error(f"multi_bot.py not available: {e}")
    MODULE_ERRORS["multi_bot"] = str(e)
    MULTI_BOT_AVAILABLE = False

try:
    import ws_binance_feed
    WS_FEED_AVAILABLE = True
    logger.info("ws_binance_feed loaded")
except Exception as e:
    MODULE_ERRORS["ws_binance_feed"] = str(e)
    WS_FEED_AVAILABLE = False
    logger.warning(f"ws_binance_feed not available: {e}")

try:
    import stop_monitor
    STOP_MONITOR_AVAILABLE = True
    logger.info("stop_monitor loaded")
except Exception as e:
    MODULE_ERRORS["stop_monitor"] = str(e)
    STOP_MONITOR_AVAILABLE = False
    logger.warning(f"stop_monitor not available: {e}")

try:
    import paper_trader
    PAPER_TRADER_AVAILABLE = True
    logger.info("paper_trader loaded")
except Exception as e:
    MODULE_ERRORS["paper_trader"] = str(e)
    PAPER_TRADER_AVAILABLE = False
    logger.warning(f"paper_trader not available: {e}")

try:
    import execution_manager
    EXECUTION_MANAGER_AVAILABLE = True
    logger.info("execution_manager loaded")
except Exception as e:
    MODULE_ERRORS["execution_manager"] = str(e)
    EXECUTION_MANAGER_AVAILABLE = False
    logger.warning(f"execution_manager not available: {e}")

try:
    import risk_manager
    RISK_MANAGER_AVAILABLE = True
    _risk_manager_instance = risk_manager.RiskManager()
    logger.info("risk_manager loaded")
except Exception as e:
    MODULE_ERRORS["risk_manager"] = str(e)
    RISK_MANAGER_AVAILABLE = False
    _risk_manager_instance = None
    logger.warning(f"risk_manager not available: {e}")

try:
    import db
    DB_AVAILABLE = True
    logger.info("db (SQLite) loaded")
except Exception as e:
    MODULE_ERRORS["db"] = str(e)
    DB_AVAILABLE = False
    logger.warning(f"db not available: {e}")

try:
    import agent_dashboard
    AGENT_DASHBOARD_AVAILABLE = True
    logger.info("agent_dashboard loaded")
except Exception as e:
    MODULE_ERRORS["agent_dashboard"] = str(e)
    AGENT_DASHBOARD_AVAILABLE = False
    logger.warning(f"agent_dashboard not available: {e}")

try:
    import webhook_tradingview
    WEBHOOK_AVAILABLE = True
    logger.info("webhook_tradingview loaded")
except Exception as e:
    MODULE_ERRORS["webhook_tradingview"] = str(e)
    WEBHOOK_AVAILABLE = False
    logger.warning(f"webhook_tradingview not available: {e}")

try:
    from cred_manager import (
        get_credentials_or_prompt as _get_creds,
        clear_credentials as _clear_creds,
        DEFAULT_ENCRYPTED_PATH as _CREDS_PATH,
    )
    CRED_MANAGER_AVAILABLE = True
    logger.info("cred_manager loaded")
except Exception as e:
    MODULE_ERRORS["cred_manager"] = str(e)
    CRED_MANAGER_AVAILABLE = False
    logger.warning(f"cred_manager not available: {e}")


# ============================================================
# CONFIGURATION
# ============================================================
PAGE_TITLE = "STRUCTURAL EXPANSION FLOW"
PAGE_ICON = "⚡"
REFRESH_INTERVAL_SEC = 60  # auto-refresh every 60s
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
PAIR_LABELS = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL",
               "XRPUSDT": "XRP", "BNBUSDT": "BNB"}

# Telegram defaults (override via sidebar)
DEFAULT_TG_TOKEN = "8813532919:AAF4FcqNCMA5jfeiHDp71M-lbqLbBh3RuzY"
DEFAULT_TG_CHAT_ID = "8026382563"


# ============================================================
# CSS EMBED
# ============================================================
CUSTOM_CSS = """
<style>
/* =====================================================================
   DASHBOARD DE TRADING PROFESIONAL — CSS GLOBAL
   ===================================================================== */

* { box-sizing: border-box; margin: 0; padding: 0; }

:root {
    --bg-primary: #17191E;
    --bg-secondary: #1A1D24;
    --text-primary: #FFFFFF;
    --text-secondary: #E2E8F0;
    --text-muted: #94A3B8;
    --gold-primary: #D4AF37;
    --gold-light: #F2C94C;
    --green-profit: #4ADE80;
    --red-loss: #F87171;
    --orange-warning: #FB923C;
    --glass-bg: rgba(255, 255, 255, 0.03);
    --glass-border: rgba(212, 175, 55, 0.08);
    --blur-amount: 12px;
    --transition-base: 0.3s ease;
    --radius-card: 20px;
    --radius-sm: 10px;
    --shadow-soft: 0 4px 24px rgba(0, 0, 0, 0.2);
    --shadow-elevated: 0 12px 40px rgba(0, 0, 0, 0.4);
}

/* Streamlit dark theme override */
.stApp {
    background: linear-gradient(135deg, #17191E 0%, #1A1D24 100%) !important;
    color: var(--text-secondary) !important;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

#MainMenu, footer, header { visibility: hidden; }

.block-container {
    padding: 1rem 1.5rem !important;
    max-width: 100% !important;
}

/* Glassmorphism cards */
.glass-card {
    background: var(--glass-bg);
    backdrop-filter: blur(var(--blur-amount));
    -webkit-backdrop-filter: blur(var(--blur-amount));
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-card);
    padding: 22px 24px;
    box-shadow: var(--shadow-soft), inset 0 1px 0 rgba(255, 255, 255, 0.04);
    transition: all var(--transition-base);
    position: relative;
    overflow: hidden;
}
.glass-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: linear-gradient(90deg, transparent, var(--gold-primary), transparent);
    opacity: 0;
    transition: opacity var(--transition-base);
}
.glass-card:hover {
    transform: translateY(-4px);
    border-color: rgba(212, 175, 55, 0.25);
    box-shadow: var(--shadow-elevated), 0 0 30px rgba(212, 175, 55, 0.08);
}
.glass-card:hover::before { opacity: 1; }

.metric-label {
    color: var(--text-muted);
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 10px;
}
.metric-value {
    color: var(--text-primary);
    font-size: 1.8rem;
    font-weight: 700;
    font-family: 'JetBrains Mono', monospace;
    letter-spacing: -0.02em;
    line-height: 1.2;
}
.metric-change {
    margin-top: 8px;
    font-size: 0.85rem;
    font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
}
.metric-change.positive { color: var(--green-profit); }
.metric-change.negative { color: var(--red-loss); }

/* Header */
.header-title {
    font-size: 1.8rem;
    font-weight: 700;
    background: linear-gradient(135deg, #FFFFFF 0%, var(--gold-light) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.02em;
}
.header-subtitle {
    color: var(--text-muted);
    font-size: 0.9rem;
    margin-top: 4px;
}
.header-clock {
    font-family: 'JetBrains Mono', monospace;
    color: var(--text-muted);
    font-size: 0.95rem;
    font-weight: 500;
    letter-spacing: 0.05em;
    padding: 8px 14px;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: 10px;
}

/* LIVE indicator */
.live-indicator {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px;
    background: rgba(74, 222, 128, 0.08);
    border: 1px solid rgba(74, 222, 128, 0.25);
    border-radius: 50px;
}
.live-text {
    color: var(--green-profit);
    font-weight: 600;
    font-size: 0.8rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}
.live-dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--green-profit);
    box-shadow: 0 0 12px var(--green-profit);
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.7; transform: scale(1.1); }
}

/* Pairs table */
.pairs-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: var(--glass-bg);
    backdrop-filter: blur(var(--blur-amount));
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-card);
    overflow: hidden;
    font-size: 0.9rem;
    box-shadow: var(--shadow-soft);
}
.pairs-table th {
    background: rgba(212, 175, 55, 0.06);
    color: var(--gold-light);
    padding: 14px 18px;
    text-align: left;
    font-weight: 600;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    border-bottom: 1px solid rgba(212, 175, 55, 0.15);
}
.pairs-table td {
    padding: 14px 18px;
    color: var(--text-secondary);
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
}
.pairs-table tbody tr:nth-child(even) td { background: rgba(255, 255, 255, 0.015); }
.pairs-table tbody tr:hover td { background: rgba(212, 175, 55, 0.05); }
.pairs-table tbody tr:last-child td { border-bottom: none; }

/* Status badges */
.status-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 50px;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-family: 'Inter', sans-serif;
    white-space: nowrap;
}
.status-execute { background: rgba(74, 222, 128, 0.15); color: var(--green-profit); border: 1px solid rgba(74, 222, 128, 0.3); }
.status-forming { background: rgba(212, 175, 55, 0.15); color: var(--gold-light); border: 1px solid rgba(212, 175, 55, 0.3); }
.status-invalid { background: rgba(248, 113, 113, 0.15); color: var(--red-loss); border: 1px solid rgba(248, 113, 113, 0.3); }
.status-cooldown { background: rgba(251, 146, 60, 0.15); color: var(--orange-warning); border: 1px solid rgba(251, 146, 60, 0.3); }

/* Signal badges */
.signal-badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-family: 'Inter', sans-serif;
    white-space: nowrap;
}
.signal-long { background: linear-gradient(135deg, rgba(74, 222, 128, 0.2), rgba(74, 222, 128, 0.05)); color: var(--green-profit); border: 1px solid rgba(74, 222, 128, 0.4); }
.signal-short { background: linear-gradient(135deg, rgba(248, 113, 113, 0.2), rgba(248, 113, 113, 0.05)); color: var(--red-loss); border: 1px solid rgba(248, 113, 113, 0.4); }
.signal-wait { background: rgba(148, 163, 184, 0.12); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.25); }

/* P&L bar */
.pnl-bar {
    width: 100%;
    height: 6px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 50px;
    overflow: hidden;
}
.pnl-bar-fill {
    height: 100%;
    border-radius: 50px;
    transition: width 0.6s ease;
}
.pnl-bar-fill.positive { background: linear-gradient(90deg, rgba(74, 222, 128, 0.4), var(--green-profit)); box-shadow: 0 0 8px rgba(74, 222, 128, 0.5); }
.pnl-bar-fill.negative { background: linear-gradient(90deg, rgba(248, 113, 113, 0.4), var(--red-loss)); box-shadow: 0 0 8px rgba(248, 113, 113, 0.5); }

/* Activity log */
.activity-log {
    background: rgba(0, 0, 0, 0.4);
    backdrop-filter: blur(var(--blur-amount));
    border: 1px solid rgba(255, 255, 255, 0.05);
    border-radius: var(--radius-card);
    padding: 20px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.82rem;
    max-height: 400px;
    overflow-y: auto;
    line-height: 1.7;
}
.log-entry {
    color: var(--text-secondary);
    padding: 6px 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.02);
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
}
.log-entry .timestamp { color: var(--text-muted); opacity: 0.7; flex-shrink: 0; }
.log-entry .pair { color: var(--gold-light); font-weight: 500; min-width: 70px; }
.log-entry .win { color: var(--green-profit); font-weight: 600; }
.log-entry .loss { color: var(--red-loss); font-weight: 600; }

/* Cooldown timer */
.cooldown-timer {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: rgba(0, 0, 0, 0.3);
    border-radius: var(--radius-sm);
    border: 1px solid rgba(255, 255, 255, 0.05);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    margin-bottom: 8px;
}
.cooldown-timer .pair-name { color: var(--gold-light); font-weight: 500; }
.cooldown-timer .time-remaining { color: var(--text-secondary); margin-left: auto; }

/* DD bar */
.dd-bar {
    width: 100%;
    height: 10px;
    background: rgba(255, 255, 255, 0.05);
    border-radius: 50px;
    overflow: hidden;
}
.dd-bar-fill {
    height: 100%;
    border-radius: 50px;
    transition: width 0.6s ease;
}
.dd-safe { background: linear-gradient(90deg, rgba(74, 222, 128, 0.4), var(--green-profit)); }
.dd-warning { background: linear-gradient(90deg, rgba(251, 146, 60, 0.4), var(--orange-warning)); }
.dd-danger { background: linear-gradient(90deg, rgba(248, 113, 113, 0.4), var(--red-loss)); animation: pulse-danger 1s infinite; }
@keyframes pulse-danger {
    0%, 100% { box-shadow: 0 0 12px rgba(248, 113, 113, 0.5); }
    50% { box-shadow: 0 0 20px rgba(248, 113, 113, 0.8); }
}

/* Control panel */
.control-panel {
    background: var(--glass-bg);
    backdrop-filter: blur(var(--blur-amount));
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-card);
    padding: 20px;
    box-shadow: var(--shadow-soft);
}

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, var(--gold-primary), var(--gold-light)) !important;
    color: var(--bg-primary) !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 700 !important;
    font-size: 0.85rem !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
    box-shadow: 0 4px 16px rgba(212, 175, 55, 0.3) !important;
    transition: all 0.3s ease !important;
    width: 100% !important;
}
.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 24px rgba(212, 175, 55, 0.5) !important;
    filter: brightness(1.1) !important;
}

/* Toggle styling */
.stToggle > div > label { color: var(--text-secondary) !important; }

/* Inputs */
.stTextInput > div > div > input,
.stNumberInput > div > div > input {
    background: rgba(0, 0, 0, 0.3) !important;
    border: 1px solid rgba(212, 175, 55, 0.15) !important;
    border-radius: 10px !important;
    color: var(--text-primary) !important;
    font-family: 'JetBrains Mono', monospace !important;
}
.stSelectbox > div > div > div {
    background: rgba(0, 0, 0, 0.3) !important;
    border: 1px solid rgba(212, 175, 55, 0.15) !important;
    border-radius: 10px !important;
}

/* Section titles */
.section-title {
    font-size: 1.05rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 18px;
    padding-bottom: 12px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    display: flex;
    align-items: center;
    gap: 10px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.section-title::before {
    content: '';
    width: 4px;
    height: 18px;
    background: linear-gradient(180deg, var(--gold-light), var(--gold-primary));
    border-radius: 2px;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: var(--bg-secondary) !important;
    border-right: 1px solid var(--glass-border) !important;
}
section[data-testid="stSidebar"] .stMarkdown, 
section[data-testid="stSidebar"] label {
    color: var(--text-secondary) !important;
}

/* Regime table */
.regime-table {
    width: 100%;
    border-collapse: separate;
    border-spacing: 0;
    background: var(--glass-bg);
    border: 1px solid var(--glass-border);
    border-radius: var(--radius-sm);
    overflow: hidden;
    font-size: 0.85rem;
}
.regime-table th {
    background: rgba(212, 175, 55, 0.06);
    color: var(--gold-light);
    padding: 10px 14px;
    text-align: left;
    font-weight: 600;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
}
.regime-table td {
    padding: 10px 14px;
    color: var(--text-secondary);
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    font-family: 'JetBrains Mono', monospace;
}

/* Scrollbar */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: linear-gradient(180deg, var(--gold-primary), rgba(212, 175, 55, 0.5));
    border-radius: 50px;
}
::-webkit-scrollbar-thumb:hover { background: var(--gold-light); }

/* Animations */
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
@keyframes slideUp { from { opacity: 0; transform: translateY(20px); } to { opacity: 1; transform: translateY(0); } }

.fade-in { animation: fadeIn 0.5s ease; }

/* Footer */
.footer {
    color: var(--text-muted);
    font-size: 0.78rem;
    text-align: center;
    padding: 20px;
    border-top: 1px solid var(--glass-border);
    margin-top: 30px;
    font-family: 'JetBrains Mono', monospace;
}
</style>
"""


# ============================================================
# TELEGRAM ALERTS
# ============================================================
def send_telegram(token: str, chat_id: str, text: str) -> bool:
    """Send a message via Telegram bot."""
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


def format_signal_alert(bot_name: str, symbol: str, side: str, price: float,
                        stop: float, tp: float, equity: float) -> str:
    return (
        f"🚨 *SIGNAL ALERT*\n"
        f"`{bot_name}` `{symbol}`\n"
        f"Side: *{side}*\n"
        f"Entry: `${price:.2f}`\n"
        f"Stop: `${stop:.2f}`\n"
        f"TP: `${tp:.2f}`\n"
        f"Equity: `${equity:.2f}`\n"
        f"Time: `{datetime.now(timezone.utc).isoformat()}`"
    )


def format_dd_alert(dd_pct: float, equity: float) -> str:
    return (
        f"⚠️ *DRAWDOWN ALERT*\n"
        f"Current DD: *{dd_pct:.2f}%*\n"
        f"Equity: `${equity:.2f}`\n"
        f"Time: `{datetime.now(timezone.utc).isoformat()}`"
    )


def format_trade_close_alert(trade: dict, bot_name: str) -> str:
    dir_str = "LONG" if trade.get("dir") == 1 else "SHORT"
    return (
        f"📊 *TRADE CLOSED*\n"
        f"`{bot_name}` {dir_str}\n"
        f"Entry: `${trade.get('entry', 0):.4f}` → Exit: `${trade.get('exit', 0):.4f}`\n"
        f"PnL: `{'🟢' if trade.get('net', 0) > 0 else '🔴'}${trade.get('net', 0):+.4f}`\n"
        f"Reason: `{trade.get('exit_reason', '?')}` · Hold: `{trade.get('hold_minutes', 0):.1f}min`\n"
        f"Equity: `${trade.get('equity_after', 0):.2f}`\n"
        f"Time: `{datetime.now(timezone.utc).isoformat()}`"
    )


def format_daily_summary(system_status: Dict) -> str:
    ps = system_status.get("portfolio_summary", {})
    return (
        f"📊 *DAILY SUMMARY*\n"
        f"Total equity: `${ps.get('total_equity', 0):.2f}`\n"
        f"Net PnL: `${ps.get('net_pnl', 0):+.2f}` ({ps.get('return_pct', 0):+.2f}%)\n"
        f"Total trades: {ps.get('total_trades', 0)}\n"
        f"Win rate: {ps.get('win_rate', 0):.1f}%\n"
        f"Profit factor: {ps.get('profit_factor', 0):.2f}\n"
        f"Time: `{datetime.now(timezone.utc).isoformat()}`"
    )


# ============================================================
# SESSION STATE INIT
# ============================================================
def init_session_state():
    """Initialize Streamlit session state with defaults.

    Ajustes:
      - #2: Start WS feed once and reuse
      - #5: Robust bot init (one failing bot doesn't break others)
      - #8: Start stop_monitor thread if available
    """
    # ---- Robust MultiBotSystem init (ajuste #5) ----
    # Rebuild the system when Live Trading is toggled OR when credentials are
    # newly unlocked, so the exchange client (ExecutionManager) attaches.
    live = st.session_state.get("live_trading", False)
    has_creds = bool(st.session_state.get("binance_api_key")
                     and st.session_state.get("binance_api_secret"))
    prev_live = st.session_state.get("_prev_live_trading", None)
    prev_creds = st.session_state.get("_prev_has_creds", None)
    rebuild = ("system" not in st.session_state) or (
        prev_live is not None and prev_live != live) or (
        prev_creds is not None and prev_creds != has_creds)
    st.session_state._prev_live_trading = live
    st.session_state._prev_has_creds = has_creds

    if rebuild:
        # Build exchange client only if live trading is ON and creds are available.
        # Creds come from (1) decrypted credentials.enc, or (2) env vars
        # (testnet) so keys never need to be pasted in chat.
        exchange_client = None
        if live:
            ak = st.session_state.get("binance_api_key")
            sk = st.session_state.get("binance_api_secret")
            use_testnet = bool(st.session_state.get("use_testnet", True))
            if not (ak and sk):
                ak = os.environ.get("BINANCE_TESTNET_API_KEY") or os.environ.get("BINANCE_API_KEY")
                sk = os.environ.get("BINANCE_TESTNET_API_SECRET") or os.environ.get("BINANCE_API_SECRET")
                if os.environ.get("USE_TESTNET") is not None:
                    use_testnet = os.environ.get("USE_TESTNET", "true").lower() != "false"
            if ak and sk:
                try:
                    from execution_manager import ExecutionManager
                    exchange_client = ExecutionManager(ak, sk, testnet=use_testnet)
                    logger.info(f"ExecutionManager built (testnet={use_testnet})")
                except Exception as e:
                    logger.error(f"ExecutionManager build failed: {e}")
        if MULTI_BOT_AVAILABLE:
            try:
                system = MultiBotSystem(exchange_client=exchange_client)
                # Verify each bot individually
                bot_errors = {}
                for name, bot in list(system.bots.items()):
                    try:
                        # Quick sanity check
                        _ = bot.status()
                        logger.info(f"Bot {name} ({bot.symbol}) initialized OK")
                    except Exception as e:
                        bot_errors[name] = str(e)
                        logger.error(f"Bot {name} failed init: {e}")
                        # Remove broken bot from the system so others can run
                        del system.bots[name]
                st.session_state.system = system
                st.session_state.bot_errors = bot_errors
                st.session_state.system_error = None
                logger.info(f"MultiBotSystem initialized with {len(system.bots)} bots "
                            f"(errors: {bot_errors})")
            except Exception as e:
                st.session_state.system = None
                st.session_state.system_error = str(e)
                st.session_state.bot_errors = {}
                logger.error(f"MultiBotSystem init failed: {e}")
        else:
            st.session_state.system = None
            st.session_state.system_error = "multi_bot.py not available"
            st.session_state.bot_errors = {}

    # ---- Toggles & UI state ----
    # If testnet env creds or credentials.enc are present, default
    # Live Trading + Auto-refresh ON so the bot runs immediately
    # (testnet only, fake money).
    _env_creds = bool(os.environ.get("BINANCE_TESTNET_API_KEY")
                     or os.environ.get("BINANCE_API_KEY"))
    _enc_exists = Path("credentials.enc").exists()
    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = _env_creds or _enc_exists
    if "live_trading" not in st.session_state:
        st.session_state.live_trading = _env_creds or _enc_exists
    if "use_real_balance" not in st.session_state:
        st.session_state.use_real_balance = False
    if "paused" not in st.session_state:
        st.session_state.paused = False
    if "tg_token" not in st.session_state:
        st.session_state.tg_token = DEFAULT_TG_TOKEN
    if "tg_chat_id" not in st.session_state:
        st.session_state.tg_chat_id = DEFAULT_TG_CHAT_ID
    if "last_refresh" not in st.session_state:
        st.session_state.last_refresh = None
    if "last_daily_summary" not in st.session_state:
        st.session_state.last_daily_summary = None
    if "equity_history" not in st.session_state:
        st.session_state.equity_history = _load_equity_history()
    if "price_cache" not in st.session_state:
        st.session_state.price_cache = {}
    if "last_alert_check" not in st.session_state:
        st.session_state.last_alert_check = None
    if "alerts_dd_sent" not in st.session_state:
        st.session_state.alerts_dd_sent = False
    if "last_tick_summary" not in st.session_state:
        st.session_state.last_tick_summary = None

    # ---- Start WebSocket feed once (ajuste #2) ----
    if "ws_feed_started" not in st.session_state:
        st.session_state.ws_feed_started = False
    if WS_FEED_AVAILABLE and not st.session_state.ws_feed_started:
        try:
            # ws_binance_feed exposes start() that launches a background thread
            if hasattr(ws_binance_feed, "start"):
                ws_binance_feed.start(symbols=PAIRS)
            elif hasattr(ws_binance_feed, "run"):
                # Alternative API
                t = threading.Thread(target=ws_binance_feed.run,
                                     args=(PAIRS,), daemon=True,
                                     name="ws-feed")
                t.start()
            st.session_state.ws_feed_started = True
            logger.info(f"WebSocket feed started for {PAIRS}")
        except Exception as e:
            logger.warning(f"WS feed start failed (will use REST): {e}")
            st.session_state.ws_feed_started = False

    # ---- Migrate históricos a SQLite ----
    if DB_AVAILABLE:
        try:
            db.migrate_trade_history()
        except Exception as e:
            logger.warning(f"Trade history migration failed: {e}")

    # ---- Start webhook thread ----
    if "webhook_started" not in st.session_state:
        st.session_state.webhook_started = False
    if WEBHOOK_AVAILABLE and not st.session_state.webhook_started:
        try:
            webhook_tradingview.start_webhook_thread()
            st.session_state.webhook_started = True
            logger.info("TradingView webhook thread started")
        except Exception as e:
            logger.warning(f"Webhook start failed: {e}")

    # ---- Start stop_monitor thread (ajuste #8) ----
    if "stop_monitor_started" not in st.session_state:
        st.session_state.stop_monitor_started = False
    if (STOP_MONITOR_AVAILABLE and not st.session_state.stop_monitor_started
            and st.session_state.system is not None):
        try:
            # stop_monitor exposes start(bots_dict) that launches a monitor thread
            if hasattr(stop_monitor, "start"):
                stop_monitor.start(st.session_state.system.bots)
            elif hasattr(stop_monitor, "run"):
                t = threading.Thread(target=stop_monitor.run,
                                     args=(st.session_state.system.bots,),
                                     daemon=True, name="stop-monitor")
                t.start()
            st.session_state.stop_monitor_started = True
            logger.info("Stop monitor thread started")
        except Exception as e:
            logger.warning(f"Stop monitor start failed: {e}")
            st.session_state.stop_monitor_started = False


# ============================================================
# DATA FETCHING — WebSocket-first with REST fallback (ajuste #2)
# ============================================================
def get_latest_price(symbol: str) -> Optional[float]:
    """
    Get latest price for a symbol.
    Priority:
      1. WebSocket feed (if ws_binance_feed available and running)
      2. REST cache (30s)
      3. REST fetch (with cache update)
    """
    # 1. Try WebSocket feed first
    if WS_FEED_AVAILABLE:
        try:
            ws_price = ws_binance_feed.get_price(symbol)
            if ws_price is not None and ws_price > 0:
                # Update cache too
                st.session_state.price_cache[symbol] = (time.time(), float(ws_price))
                return float(ws_price)
        except Exception as e:
            logger.debug(f"WS price fetch failed for {symbol}: {e}")

    # 2. REST cache (30s)
    if symbol in st.session_state.price_cache:
        cached_time, cached_price = st.session_state.price_cache[symbol]
        if time.time() - cached_time < 30:
            return cached_price

    # 3. REST fetch
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/ticker/price",
                         params={"symbol": symbol}, timeout=5)
        if r.status_code == 200:
            price = float(r.json()["price"])
            st.session_state.price_cache[symbol] = (time.time(), price)
            return price
    except Exception as e:
        logger.warning(f"REST price fetch failed for {symbol}: {e}")
    return None


def get_24h_ticker(symbol: str) -> Dict:
    """Get 24h ticker stats for a symbol."""
    try:
        r = requests.get(f"https://fapi.binance.com/fapi/v1/ticker/24hr",
                         params={"symbol": symbol}, timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {
                "price": float(d["lastPrice"]),
                "price_change_pct": float(d["priceChangePercent"]),
                "high": float(d["highPrice"]),
                "low": float(d["lowPrice"]),
                "volume": float(d["quoteVolume"]),
            }
    except Exception:
        pass
    return {}


# ============================================================
# TICK LOGIC
# ============================================================
def run_system_tick() -> Dict[str, str]:
    """
    Run a single tick of all available bots.
    Returns dict of status strings, and also writes a summary to
    st.session_state.last_tick_summary (ajuste #9).
    """
    system = st.session_state.system
    if system is None:
        return {p: "system unavailable" for p in PAIRS}

    if st.session_state.paused:
        return {p: "PAUSED" for p in PAIRS}

    # Snapshot trade counts before the tick so we can compute "closed this tick"
    trades_before = {name: len(bot.trade_log) for name, bot in system.bots.items()}

    try:
        results = system.run_once()
        st.session_state.last_refresh = datetime.now(timezone.utc)

        # ---- Build tick summary (ajuste #9) ----
        new_signals = 0
        trades_closed = 0
        cooldowns = 0
        errors = 0
        for name, status_str in results.items():
            if "entered" in status_str.lower():
                new_signals += 1
            if "exited" in status_str.lower():
                trades_closed += 1
            if "cooldown" in status_str.lower():
                cooldowns += 1
            if "error" in status_str.lower():
                errors += 1
        # Also count trades closed via trade_log diff (more reliable)
        trades_closed_via_log = 0
        for name, bot in system.bots.items():
            trades_closed_via_log += max(0, len(bot.trade_log) - trades_before.get(name, 0))
        trades_closed = max(trades_closed, trades_closed_via_log)

        summary = {
            "timestamp": datetime.now(timezone.utc),
            "new_signals": new_signals,
            "trades_closed": trades_closed,
            "in_cooldown": cooldowns,
            "errors": errors,
            "results": results,
        }
        st.session_state.last_tick_summary = summary
        logger.info(f"Tick done: {new_signals} new signals, "
                    f"{trades_closed} trades closed, {cooldowns} in cooldown, "
                    f"{errors} errors")

        # Append to equity history
        try:
            ps = system.portfolio_summary()
            st.session_state.equity_history.append({
                "time": datetime.now(timezone.utc),
                "equity": ps["total_equity"],
                "net_pnl": ps["net_pnl"],
            })
            # Keep last 2000 points + persist
            if len(st.session_state.equity_history) > 2000:
                st.session_state.equity_history = st.session_state.equity_history[-2000:]
            _save_equity_history(st.session_state.equity_history)
        except Exception as e:
            logger.warning(f"Equity history update failed: {e}")

        # Record into DB + Risk Manager
        if DB_AVAILABLE:
            try:
                for name, bot in system.bots.items():
                    db.save_equity_snapshot(name, bot.equity)
                    for t in bot.trade_log[-1:]:
                        db.save_trade(t, name)
            except Exception as e:
                logger.warning(f"DB record failed: {e}")

        if RISK_MANAGER_AVAILABLE and _risk_manager_instance:
            try:
                ps = system.portfolio_summary()
                for name, bot in system.bots.items():
                    if bot.trade_log:
                        t = bot.trade_log[-1]
                        _risk_manager_instance.record_result(
                            t["net"], t.get("equity_before", ps["total_equity"]), t.get("equity_after", ps["total_equity"])
                        )
            except Exception as e:
                logger.warning(f"Risk manager record failed: {e}")

        return results
    except Exception as e:
        logger.error(f"Tick failed: {e}", exc_info=True)
        return {p: f"ERROR: {e}" for p in PAIRS}


def check_alerts():
    """Check alert conditions and send Telegram messages."""
    if not st.session_state.tg_token or not st.session_state.tg_chat_id:
        return
    system = st.session_state.system
    if system is None:
        return
    try:
        # Trade closed alerts (recently closed trades not yet notified)
        if "notified_trade_ids" not in st.session_state:
            st.session_state.notified_trade_ids = set()
        for name, bot in system.bots.items():
            for t in bot.trade_log:
                trade_id = (t.get("exit_time", ""), t.get("entry", 0), t.get("exit", 0))
                if trade_id not in st.session_state.notified_trade_ids:
                    send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id,
                                  format_trade_close_alert(t, name))
                    st.session_state.notified_trade_ids.add(trade_id)

        ps = system.portfolio_summary()
        # Drawdown alert (if < 5%)
        # We don't have real-time DD, use equity vs max equity in history
        if len(st.session_state.equity_history) > 5:
            equities = [h["equity"] for h in st.session_state.equity_history]
            peak = max(equities)
            current = equities[-1]
            dd_pct = (current - peak) / peak * 100 if peak > 0 else 0
            if dd_pct < -5 and not st.session_state.alerts_dd_sent:
                send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id,
                              format_dd_alert(dd_pct, current))
                st.session_state.alerts_dd_sent = True
            elif dd_pct > -2:
                st.session_state.alerts_dd_sent = False
        # Daily summary at 00:00 UTC
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute < 5:
            last = st.session_state.last_daily_summary
            if last is None or last.date() < now.date():
                send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id,
                              format_daily_summary({"portfolio_summary": ps}))
                st.session_state.last_daily_summary = now
    except Exception as e:
        logger.error(f"Alert check failed: {e}", exc_info=True)


# ============================================================
# UI COMPONENTS
# ============================================================
def render_header():
    """Render the dashboard header."""
    now = datetime.now(timezone.utc)
    clock_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    live_class = "" if st.session_state.live_trading else "gold"
    live_text = "LIVE" if st.session_state.live_trading else "PAPER"
    live_color = "var(--green-profit)" if st.session_state.live_trading else "var(--gold-light)"

    header_html = f"""
    <div style="display: flex; justify-content: space-between; align-items: center;
                padding: 18px 28px; background: var(--glass-bg);
                backdrop-filter: blur(12px); border: 1px solid var(--glass-border);
                border-radius: 20px; box-shadow: 0 4px 24px rgba(0,0,0,0.2); margin-bottom: 20px;">
        <div>
            <div class="header-title">{PAGE_TITLE}</div>
            <div class="header-subtitle">BTC · ETH · SOL · XRP · BNB — 5-bot portfolio</div>
        </div>
        <div style="display: flex; align-items: center; gap: 16px;">
            <div class="live-indicator" style="background: rgba({'74,222,128' if st.session_state.live_trading else '212,175,55'},0.08);
                 border-color: rgba({'74,222,128' if st.session_state.live_trading else '212,175,55'},0.25);">
                <div class="live-dot {'gold' if not st.session_state.live_trading else ''}"
                     style="background: {live_color}; box-shadow: 0 0 12px {live_color};"></div>
                <span class="live-text" style="color: {live_color};">{live_text}</span>
            </div>
            <div class="header-clock">{clock_str}</div>
        </div>
    </div>
    """
    st.html(header_html)


def render_metric_card(label: str, value: str, change: str = "",
                       change_class: str = ""):
    """Render a single metric card."""
    change_html = ""
    if change:
        change_html = f'<div class="metric-change {change_class}">{change}</div>'
    return f"""
    <div class="glass-card">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value}</div>
        {change_html}
    </div>
    """


def render_metrics_row():
    """Render row 1: 6 metric cards + sub-table of open P&L per pair (ajuste #4)."""
    system = st.session_state.system
    if system is None:
        st.error("Multi-bot system not available")
        return

    try:
        ps = system.portfolio_summary()
    except Exception:
        ps = {"total_equity": 500, "net_pnl": 0, "return_pct": 0,
              "total_trades": 0, "win_rate": 0, "profit_factor": 0}

    # Compute open P&L per pair (ajuste #4) using WS-or-REST price
    open_pnl_per_pair: Dict[str, Dict[str, float]] = {}
    open_pnl = 0.0
    active_pairs = 0
    margin_used = 0.0
    for name, bot in system.bots.items():
        if bot.position is not None:
            active_pairs += 1
            # Try WS price first, fallback to REST cache, fallback to entry
            price = get_latest_price(bot.symbol)
            if price is None:
                price = bot.position["entry_fill"]
            if bot.position["dir"] == 1:
                unreal = bot.position["size"] * (price - bot.position["entry_fill"])
            else:
                unreal = bot.position["size"] * (bot.position["entry_fill"] - price)
            open_pnl += unreal
            margin_used += bot.position["size"] * bot.position["entry_fill"]
            open_pnl_per_pair[name] = {
                "symbol": bot.symbol,
                "dir": "LONG" if bot.position["dir"] == 1 else "SHORT",
                "size": bot.position["size"],
                "entry": bot.position["entry_fill"],
                "current_price": price,
                "unrealized_pnl": unreal,
                "pnl_pct": (unreal / bot.initial_equity * 100) if bot.initial_equity else 0,
            }

    closed_pnl = ps["net_pnl"] - open_pnl

    # Drawdown from equity history
    dd_pct = 0.0
    if len(st.session_state.equity_history) > 5:
        equities = [h["equity"] for h in st.session_state.equity_history]
        peak = max(equities)
        current = equities[-1]
        dd_pct = (current - peak) / peak * 100 if peak > 0 else 0

    # Render 6 cards in a grid
    cards_html = '<div style="display: grid; grid-template-columns: repeat(6, 1fr); gap: 16px; margin-bottom: 20px;">'
    cards_html += render_metric_card("Balance Total", f"${ps['total_equity']:.2f}",
                                      f"{ps['return_pct']:+.2f}%",
                                      "positive" if ps['return_pct'] >= 0 else "negative")
    cards_html += render_metric_card("P&L Abierto",
                                      f"${open_pnl:+.2f}",
                                      f"{(open_pnl/ps['total_equity']*100) if ps['total_equity'] else 0:+.2f}%",
                                      "positive" if open_pnl >= 0 else "negative")
    cards_html += render_metric_card("P&L Cerrado",
                                      f"${closed_pnl:+.2f}",
                                      f"{(closed_pnl/500*100) if 500 else 0:+.2f}%",
                                      "positive" if closed_pnl >= 0 else "negative")
    dd_class = "negative" if dd_pct < 0 else "positive"
    cards_html += render_metric_card("Drawdown Actual",
                                      f"{dd_pct:.2f}%",
                                      "vs peak equity",
                                      dd_class)
    cards_html += render_metric_card("Margen Usado",
                                      f"${margin_used:.2f}",
                                      f"{(margin_used/ps['total_equity']*100) if ps['total_equity'] else 0:.1f}%",
                                      "")
    cards_html += render_metric_card("Pares Activos",
                                      f"{active_pairs}/5",
                                      f"{ps['total_trades']} trades total",
                                      "")
    cards_html += '</div>'
    st.html(cards_html)

    # ---- Sub-table: Open P&L per pair (ajuste #4) ----
    if open_pnl_per_pair:
        rows = ""
        for name, info in open_pnl_per_pair.items():
            pnl_class = "var(--green-profit)" if info["unrealized_pnl"] >= 0 else "var(--red-loss)"
            rows += f"""
            <tr>
                <td style="color: var(--gold-light); font-weight: 600;">{name}</td>
                <td>{info['symbol']}</td>
                <td><span class="signal-badge {'signal-long' if info['dir']=='LONG' else 'signal-short'}">{info['dir']}</span></td>
                <td>{info['size']:.6f}</td>
                <td>${info['entry']:.4f}</td>
                <td>${info['current_price']:.4f}</td>
                <td style="color: {pnl_class}; font-weight: 600;">${info['unrealized_pnl']:+.4f}</td>
                <td style="color: {pnl_class};">{info['pnl_pct']:+.2f}%</td>
            </tr>
            """
        sub_table_html = f"""
        <div style="margin-top: 12px; margin-bottom: 20px;">
            <div class="section-title" style="font-size: 0.9rem;">P&L Abierto por Par</div>
            <table class="pairs-table">
                <thead>
                    <tr>
                        <th>Bot</th>
                        <th>Symbol</th>
                        <th>Dir</th>
                        <th>Size</th>
                        <th>Entry</th>
                        <th>Precio Actual</th>
                        <th>P&L No Realizado</th>
                        <th>% Equity</th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
        """
        st.html(sub_table_html)


def render_pairs_table():
    """Render row 2: pairs table. Ajuste #5: warning icon for failed bots."""
    system = st.session_state.system
    if system is None:
        return

    bot_errors = st.session_state.get("bot_errors", {})

    rows_html = ""
    # Iterate over expected bots so we can show error rows for failed ones
    expected_bots = ["BTC", "ETH", "SOL", "XRP", "BNB"]
    for name in expected_bots:
        # Case A: bot failed to init
        if name in bot_errors:
            err_msg = bot_errors[name][:80]
            rows_html += f"""
            <tr>
                <td style="color: var(--red-loss); font-weight: 600;">⚠️ {name}</td>
                <td colspan="7" style="color: var(--red-loss); font-style: italic;">
                    INIT FAILED: {err_msg}
                </td>
            </tr>
            """
            continue

        # Case B: bot not present (was removed)
        if name not in system.bots:
            rows_html += f"""
            <tr>
                <td style="color: var(--text-muted); font-weight: 600;">{name}</td>
                <td colspan="7" style="color: var(--text-muted); font-style: italic;">
                    Not initialized
                </td>
            </tr>
            """
            continue

        # Case C: bot is healthy
        bot = system.bots[name]
        symbol = bot.symbol
        price = get_latest_price(symbol) or 0
        ticker = get_24h_ticker(symbol)
        change_24h = ticker.get("price_change_pct", 0)

        # Status
        if bot.cooldown_until is not None:
            now = datetime.now(timezone.utc)
            if now < bot.cooldown_until:
                remaining = (bot.cooldown_until - now).total_seconds() / 60
                status_badge = f'<span class="status-badge status-cooldown">COOLDOWN {remaining:.0f}m</span>'
            else:
                status_badge = '<span class="status-badge status-execute">READY</span>'
        elif bot.position is not None:
            status_badge = '<span class="status-badge status-forming">IN POSITION</span>'
        else:
            status_badge = '<span class="status-badge status-execute">READY</span>'

        # Signal
        sig = bot.last_signal
        if sig is not None:
            side = "LONG" if sig["dir"] == 1 else "SHORT"
            sig_class = "signal-long" if sig["dir"] == 1 else "signal-short"
            signal_badge = f'<span class="signal-badge {sig_class}">{side}</span>'
        else:
            signal_badge = '<span class="signal-badge signal-wait">WAIT</span>'

        # Score (mock: based on WR if available, else 50)
        score = 50 + (change_24h * 0.5)  # placeholder
        score = max(0, min(100, score))

        # Regime (BtcBot has regime; others show "single")
        regime = getattr(bot, "last_regime", None) or "—"

        # P&L bar (bot equity vs initial)
        bot_pnl = bot.equity - bot.initial_equity
        bot_pnl_pct = (bot_pnl / bot.initial_equity) * 100 if bot.initial_equity else 0
        bar_class = "positive" if bot_pnl >= 0 else "negative"
        bar_width = min(100, abs(bot_pnl_pct) * 10)
        pnl_bar = f"""
        <div style="display: flex; align-items: center; gap: 8px;">
            <div class="pnl-bar" style="flex: 1;">
                <div class="pnl-bar-fill {bar_class}" style="width: {bar_width}%;"></div>
            </div>
            <span style="font-family: 'JetBrains Mono', monospace; font-size: 0.8rem;
                         color: var(--{'green-profit' if bot_pnl >= 0 else 'red-loss'});">
                {bot_pnl_pct:+.2f}%
            </span>
        </div>
        """

        # SL distance
        if bot.position is not None:
            sl_dist = abs(price - bot.position["stop_raw"]) / price * 100
            sl_str = f"{sl_dist:.2f}%"
        elif sig is not None:
            sl_dist = abs(sig["entry"] - sig["stop"]) / sig["entry"] * 100 if sig["entry"] else 0
            sl_str = f"{sl_dist:.2f}%"
        else:
            sl_str = "—"

        rows_html += f"""
        <tr>
            <td style="color: var(--gold-light); font-weight: 600;">{name}</td>
            <td>${price:.4f}</td>
            <td>{status_badge}</td>
            <td>{signal_badge}</td>
            <td>{score:.0f}</td>
            <td>{regime}</td>
            <td style="min-width: 140px;">{pnl_bar}</td>
            <td>{sl_str}</td>
        </tr>
        """

    table_html = f"""
    <div class="section-title">Pairs Overview</div>
    <table class="pairs-table">
        <thead>
            <tr>
                <th>Par</th>
                <th>Último Precio</th>
                <th>Estado</th>
                <th>Señal</th>
                <th>Score</th>
                <th>Régimen</th>
                <th>P&L Live</th>
                <th>SL Dist.</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    """
    st.html(table_html)


def render_charts():
    """Render row 3: Equity Curve + Drawdown Chart."""
    col1, col2 = st.columns(2)

    with col1:
        st.html('<div class="section-title">Equity Curve</div>')
        if len(st.session_state.equity_history) > 1:
            df = pd.DataFrame(st.session_state.equity_history)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["time"], y=df["equity"],
                mode="lines",
                line=dict(color="#D4AF37", width=2),
                fill="tozeroy",
                fillcolor="rgba(212, 175, 55, 0.1)",
                name="Equity",
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#E2E8F0", family="JetBrains Mono"),
                margin=dict(l=10, r=10, t=10, b=10),
                height=300,
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.html("""
            <div class="glass-card" style="text-align: center; padding: 60px; color: var(--text-muted);">
                Esperando datos de equity...<br>
                <small>El gráfico se poblará con cada tick del sistema.</small>
            </div>
            """)

    with col2:
        st.html('<div class="section-title">Drawdown Chart</div>')
        if len(st.session_state.equity_history) > 5:
            df = pd.DataFrame(st.session_state.equity_history)
            df["peak"] = df["equity"].cummax()
            df["dd_pct"] = (df["equity"] - df["peak"]) / df["peak"] * 100
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df["time"], y=df["dd_pct"],
                mode="lines",
                line=dict(color="#F87171", width=2),
                fill="tozeroy",
                fillcolor="rgba(248, 113, 113, 0.15)",
                name="Drawdown %",
            ))
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#E2E8F0", family="JetBrains Mono"),
                margin=dict(l=10, r=10, t=10, b=10),
                height=300,
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.05)"),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.html("""
            <div class="glass-card" style="text-align: center; padding: 60px; color: var(--text-muted);">
                Esperando datos de drawdown...
            </div>
            """)


def render_regime_and_activity():
    """Render row 4: Regime table + Activity log.
    Ajuste #3: read regime from ALL bots (not just BTC).
    Single-strategy bots log regime='single' in their trades.
    """
    col1, col2 = st.columns([1, 1.2])

    with col1:
        st.html('<div class="section-title">Métricas por Régimen</div>')
        system = st.session_state.system
        # Aggregate trades by regime across ALL bots
        # BtcBot: ALCISTA/BAJISTA/LATERAL
        # ETH/SOL/XRP/BNB: "single" (no regime detection)
        regime_stats: Dict[str, List] = {
            "ALCISTA": [0, 0, 0.0],   # [trades, wins, pnl]
            "BAJISTA": [0, 0, 0.0],
            "LATERAL": [0, 0, 0.0],
            "SINGLE":  [0, 0, 0.0],   # for non-multimodal bots
        }
        if system is not None:
            for name, bot in system.bots.items():
                for t in bot.trade_log:
                    # Read regime from trade dict (set by BaseBot in multi_bot.py)
                    regime = t.get("regime", "single")
                    if regime is None:
                        regime = "single"
                    # Normalize to uppercase for matching
                    regime_key = regime.upper() if isinstance(regime, str) else "SINGLE"
                    if regime_key not in regime_stats:
                        regime_stats[regime_key] = [0, 0, 0.0]
                    regime_stats[regime_key][0] += 1
                    if t["net"] > 0:
                        regime_stats[regime_key][1] += 1
                    regime_stats[regime_key][2] += t["net"]
        rows = ""
        # Display in fixed order, with SINGLE last
        display_order = ["ALCISTA", "BAJISTA", "LATERAL", "SINGLE", "RUPTURA"]
        seen = set()
        for r in display_order + [k for k in regime_stats if k not in display_order]:
            if r in seen or r not in regime_stats:
                continue
            seen.add(r)
            n, w, pnl = regime_stats[r]
            wr = (w/n*100) if n > 0 else 0
            pnl_class = "var(--green-profit)" if pnl >= 0 else "var(--red-loss)"
            rows += f"""
            <tr>
                <td style="color: var(--gold-light); font-weight: 600;">{r}</td>
                <td>{n}</td>
                <td>{wr:.1f}%</td>
                <td style="color: {pnl_class};">${pnl:+.2f}</td>
            </tr>
            """
        st.html(f"""
        <table class="regime-table">
            <thead>
                <tr><th>Régimen</th><th>Trades</th><th>WR</th><th>PnL</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """)

    with col2:
        st.html('<div class="section-title">Actividad Reciente</div>')
        system = st.session_state.system
        log_entries = []
        if system is not None:
            for name, bot in system.bots.items():
                for t in bot.trade_log[-10:]:
                    regime = t.get("regime", "single")
                    log_entries.append({
                        "time": t.get("exit_time", datetime.now(timezone.utc)),
                        "pair": name,
                        "action": f"{t.get('exit_reason', '?').upper()} dir={t['dir']} [{regime}]",
                        "net": t["net"],
                    })
            log_entries.sort(key=lambda x: x["time"], reverse=True)
            log_entries = log_entries[:10]

        if not log_entries:
            st.html("""
            <div class="activity-log" style="min-height: 200px;">
                <div style="color: var(--text-muted); text-align: center; padding: 40px;">
                    Sin actividad reciente. Los trades aparecerán aquí cuando se ejecuten.
                </div>
            </div>
            """)
        else:
            entries_html = ""
            for e in log_entries:
                ts = e["time"].strftime("%H:%M:%S") if hasattr(e["time"], "strftime") else str(e["time"])
                net_class = "win" if e["net"] > 0 else "loss"
                sign = "+" if e["net"] > 0 else ""
                entries_html += f"""
                <div class="log-entry">
                    <span class="timestamp">{ts}</span>
                    <span class="pair">{e['pair']}</span>
                    <span class="action">{e['action']}</span>
                    <span class="{net_class}">{sign}${e['net']:.4f}</span>
                </div>
                """
            st.html(f'<div class="activity-log">{entries_html}</div>')


def render_risk_management():
    """Render row 5: Risk Management Details + Trade History."""
    col1, col2 = st.columns([1, 1.5])

    with col1:
        st.html('<div class="section-title">Risk Management</div>')
        system = st.session_state.system
        if system is None:
            return

        # Compute risk metrics
        total_trades = 0
        total_wins = 0
        max_loss_streak = 0
        current_streak = 0
        all_nets = []
        daily_pnl = {}

        for name, bot in system.bots.items():
            for t in bot.trade_log:
                total_trades += 1
                if t["net"] > 0:
                    total_wins += 1
                    current_streak = 0
                else:
                    current_streak += 1
                    max_loss_streak = max(max_loss_streak, current_streak)
                all_nets.append(t["net"])
                day = t.get("exit_time", datetime.now(timezone.utc)).strftime("%Y-%m-%d") \
                    if hasattr(t.get("exit_time"), "strftime") else str(datetime.now().date())
                daily_pnl[day] = daily_pnl.get(day, 0) + t["net"]

        wr = (total_wins / total_trades * 100) if total_trades else 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_pnl = daily_pnl.get(today, 0)
        peak_equity = max([h["equity"] for h in st.session_state.equity_history], default=500)
        current_dd = 0
        if st.session_state.equity_history:
            current_equity = st.session_state.equity_history[-1]["equity"]
            current_dd = (current_equity - peak_equity) / peak_equity * 100 if peak_equity else 0

        # DD bar
        dd_class = "dd-safe" if current_dd > -3 else ("dd-warning" if current_dd > -7 else "dd-danger")
        dd_level_class = "safe" if current_dd > -3 else ("warning" if current_dd > -7 else "danger")
        dd_level_text = "SAFE" if current_dd > -3 else ("WARNING" if current_dd > -7 else "DANGER")
        dd_width = min(100, abs(current_dd) * 10)

        st.html(f"""
        <div class="glass-card">
            <div style="margin-bottom: 16px;">
                <div class="metric-label">Drawdown Actual</div>
                <div class="dd-bar" style="margin-top: 8px;">
                    <div class="dd-bar-fill {dd_class}" style="width: {dd_width}%;"></div>
                </div>
                <div class="dd-label">
                    <span class="level {dd_level_class}">{dd_level_text}</span>
                    <span class="value">{current_dd:.2f}%</span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 16px;">
                <div>
                    <div class="metric-label">Max Racha Pérdidas</div>
                    <div class="metric-value" style="font-size: 1.2rem; color: var(--red-loss);">{max_loss_streak}</div>
                </div>
                <div>
                    <div class="metric-label">P&L Hoy</div>
                    <div class="metric-value" style="font-size: 1.2rem; color: var(--{'green-profit' if today_pnl >= 0 else 'red-loss'});">${today_pnl:+.2f}</div>
                </div>
                <div>
                    <div class="metric-label">Peak Equity</div>
                    <div class="metric-value" style="font-size: 1.2rem;">${peak_equity:.2f}</div>
                </div>
                <div>
                    <div class="metric-label">Win Rate</div>
                    <div class="metric-value" style="font-size: 1.2rem;">{wr:.1f}%</div>
                </div>
            </div>
        </div>
        """)

    with col2:
        st.html('<div class="section-title">Trade History (Closed)</div>')
        system = st.session_state.system
        all_trades = []
        if system is not None:
            for name, bot in system.bots.items():
                for t in bot.trade_log:
                    all_trades.append({
                        "Bot": name,
                        "Dir": "LONG" if t["dir"] == 1 else "SHORT",
                        "Entry": t.get("entry", 0),
                        "Exit": t.get("exit", 0),
                        "Net PnL": t["net"],
                        "Equity After": t.get("equity_after", 0),
                        "Hold (min)": t.get("hold_minutes", 0),
                        "Reason": t.get("exit_reason", ""),
                        "Time": t.get("exit_time", datetime.now(timezone.utc)),
                    })
        if all_trades:
            df = pd.DataFrame(all_trades).sort_values("Time", ascending=False).head(20)
            df["Time"] = pd.to_datetime(df["Time"]).dt.strftime("%m-%d %H:%M")
            df["Net PnL"] = df["Net PnL"].apply(lambda x: f"${x:+.4f}")
            df["Entry"] = df["Entry"].apply(lambda x: f"{x:.4f}")
            df["Exit"] = df["Exit"].apply(lambda x: f"{x:.4f}")
            df["Equity After"] = df["Equity After"].apply(lambda x: f"${x:.2f}")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.html("""
            <div class="glass-card" style="text-align: center; padding: 40px; color: var(--text-muted);">
                Sin trades cerrados todavía.
            </div>
            """)


def render_persistent_history():
    """Show the persistent trade history from trade_history.json
    (survives dashboard restarts)."""
    st.html('<div class="section-title">Trade History Persistido</div>')
    try:
        from multi_bot import _load_trade_history
        trades = _load_trade_history()
    except Exception:
        trades = []
    if trades:
        df = pd.DataFrame(trades)
        if "exit_time" in df.columns:
            df = df.sort_values("exit_time", ascending=False).head(20)
            df["exit_time"] = pd.to_datetime(df["exit_time"]).dt.strftime("%m-%d %H:%M")
        if "dir" in df.columns:
            df["Dir"] = df["dir"].apply(lambda x: "LONG" if x == 1 else "SHORT")
        if "net" in df.columns:
            df["Net PnL"] = df["net"].apply(lambda x: f"${x:+.4f}")
        if "entry" in df.columns:
            df["Entry"] = df["entry"].apply(lambda x: f"{x:.4f}")
        if "exit" in df.columns:
            df["Exit"] = df["exit"].apply(lambda x: f"{x:.4f}")
        if "equity_after" in df.columns:
            df["Equity"] = df["equity_after"].apply(lambda x: f"${x:.2f}")
        cols = ["Time", "bot_name", "Dir", "Entry", "Exit", "Net PnL", "Equity", "exit_reason", "hold_minutes"]
        cols = [c for c in cols if c in df.columns]
        if cols:
            st.dataframe(df[cols], use_container_width=True, hide_index=True)
        total = len(trades)
        wins = sum(1 for t in trades if t.get("net", 0) > 0)
        wr = wins / total * 100 if total else 0
        total_pnl = sum(t.get("net", 0) for t in trades)
        st.caption(f"Total: {total} trades, WR: {wr:.1f}%, PnL: ${total_pnl:+.2f} (persistido en trade_history.json)")
    else:
        st.html("""
        <div class="glass-card" style="text-align: center; padding: 20px; color: var(--text-muted);">
            Historial persistente vacío. Aparecerán trades aquí cuando se cierren.
        </div>
        """)


def _export_trades_csv():
    """Export the last 100 closed trades to CSV (ajuste #7)."""
    system = st.session_state.system
    if system is None:
        st.error("System not available")
        return
    try:
        all_trades = []
        for name, bot in system.bots.items():
            for t in bot.trade_log:
                row = {
                    "bot": name,
                    "symbol": t.get("symbol", bot.symbol),
                    "regime": t.get("regime", "single"),
                    "dir": "LONG" if t["dir"] == 1 else "SHORT",
                    "entry": t.get("entry", 0),
                    "exit": t.get("exit", 0),
                    "net_pnl": t["net"],
                    "equity_after": t.get("equity_after", 0),
                    "hold_minutes": t.get("hold_minutes", 0),
                    "exit_reason": t.get("exit_reason", ""),
                    "exit_time": t.get("exit_time", datetime.now(timezone.utc)).isoformat()
                        if hasattr(t.get("exit_time"), "isoformat") else str(t.get("exit_time")),
                }
                all_trades.append(row)
        if not all_trades:
            st.warning("No trades to export")
            return
        df = pd.DataFrame(all_trades).sort_values("exit_time", ascending=False).head(100)
        csv = df.to_csv(index=False)
        st.download_button(
            label="⬇️ Download trades.csv",
            data=csv,
            file_name=f"trades_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )
        logger.info(f"Exported {len(df)} trades to CSV")
    except Exception as e:
        logger.error(f"CSV export failed: {e}", exc_info=True)
        st.error(f"Export failed: {e}")


def _export_portfolio_json():
    """Export current portfolio state to JSON (ajuste #7)."""
    system = st.session_state.system
    if system is None:
        st.error("System not available")
        return
    try:
        ps = system.portfolio_summary()
        # Add per-bot detail
        bot_states = {}
        for name, bot in system.bots.items():
            bot_states[name] = {
                "symbol": bot.symbol,
                "equity": bot.equity,
                "initial_equity": bot.initial_equity,
                "position": {
                    "dir": bot.position["dir"] if bot.position else None,
                    "size": bot.position["size"] if bot.position else 0,
                    "entry_fill": bot.position["entry_fill"] if bot.position else 0,
                    "stop_raw": bot.position["stop_raw"] if bot.position else 0,
                    "tp_raw": bot.position["tp_raw"] if bot.position else 0,
                } if bot.position else None,
                "loss_streak": bot.loss_streak,
                "cooldown_until": bot.cooldown_until.isoformat() if bot.cooldown_until else None,
                "n_trades": len(bot.trade_log),
                "last_regime": getattr(bot, "last_regime", "single"),
            }
        export_data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "portfolio_summary": ps,
            "bots": bot_states,
            "config": {
                "commission": COMMISSION,
                "spread": SPREAD,
                "slip_atr_mult": SLIP_ATR_MULT,
                "loss_streak_trigger": LOSS_STREAK_TRIGGER,
                "pause_minutes": PAUSE_MINUTES,
            },
        }
        json_str = json.dumps(export_data, indent=2, default=str)
        st.download_button(
            label="⬇️ Download portfolio.json",
            data=json_str,
            file_name=f"portfolio_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json",
            mime="application/json",
        )
        logger.info("Exported portfolio state to JSON")
    except Exception as e:
        logger.error(f"JSON export failed: {e}", exc_info=True)
        st.error(f"Export failed: {e}")


def render_sidebar():
    """Render the left sidebar with all controls.

    Ajustes:
      - #6: Module indicators with tooltips + expanders for errors
      - #7: Export buttons (CSV trades, JSON portfolio state)
      - #9: Visual feedback on Run Tick Now (progress bar + summary)
    """
    with st.sidebar:
        st.markdown("### ⚡ Control Panel")

        # ---- Module availability with tooltips (ajuste #6) ----
        st.markdown("#### Modules")
        mods = [
            ("multi_bot.py", MULTI_BOT_AVAILABLE),
            ("ws_binance_feed", WS_FEED_AVAILABLE),
            ("stop_monitor", STOP_MONITOR_AVAILABLE),
            ("paper_trader", PAPER_TRADER_AVAILABLE),
            ("execution_manager", EXECUTION_MANAGER_AVAILABLE),
            ("risk_manager", RISK_MANAGER_AVAILABLE),
            ("cred_manager", CRED_MANAGER_AVAILABLE),
            ("db (SQLite)", DB_AVAILABLE),
            ("agent_dashboard", AGENT_DASHBOARD_AVAILABLE),
            ("webhook_tradingview", WEBHOOK_AVAILABLE),
        ]
        for name, available in mods:
            icon = "✅" if available else "❌"
            if available:
                st.html(f'<span title="{name}: loaded successfully">{icon} `{name}`</span>')
            else:
                # Expander with the error message
                err = MODULE_ERRORS.get(name, "Module not found")
                with st.expander(f"{icon} `{name}` (failed)", expanded=False):
                    st.error(err)

        st.markdown("---")

        # Pair selector
        selected_pair = st.selectbox(
            "Par Activo (detalle)",
            options=PAIRS,
            format_func=lambda x: f"{PAIR_LABELS[x]} ({x})",
        )
        st.session_state.selected_pair = selected_pair

        # Toggles
        st.markdown("#### Toggles")
        st.session_state.auto_refresh = st.toggle(
            "Auto-refresh (60s)", value=st.session_state.auto_refresh)
        st.session_state.live_trading = st.toggle(
            "🔴 Live Trading", value=st.session_state.live_trading,
            help="When ON, real orders will be sent to Binance")
        st.session_state.use_real_balance = st.toggle(
            "Usar balance real (Testnet)", value=st.session_state.use_real_balance)

        st.markdown("---")

        # Telegram config
        st.markdown("#### Telegram Alerts")
        st.session_state.tg_token = st.text_input(
            "Bot Token", value=st.session_state.tg_token, type="password")
        st.session_state.tg_chat_id = st.text_input(
            "Chat ID", value=st.session_state.tg_chat_id)
        if st.button("📨 Enviar mensaje de prueba"):
            if st.session_state.tg_token and st.session_state.tg_chat_id:
                msg = f"✅ Test from {PAGE_TITLE} at {datetime.now(timezone.utc).isoformat()}"
                ok = send_telegram(st.session_state.tg_token, st.session_state.tg_chat_id, msg)
                if ok:
                    st.success("Mensaje enviado!")
                else:
                    st.error("Error enviando mensaje")
            else:
                st.warning("Token y Chat ID requeridos")

        st.markdown("---")

        # ---- Credentials (Binance Testnet/Mainnet) — patrón de v2 ----
        st.markdown("#### 🔐 Credentials")
        if CRED_MANAGER_AVAILABLE:
            creds = _get_creds()
            if creds:
                st.session_state.binance_api_key = creds.get("BINANCE_API_KEY", "")
                st.session_state.binance_api_secret = creds.get("BINANCE_API_SECRET", "")
                st.session_state.use_testnet = creds.get("USE_TESTNET", True)
                if st.button("🔓 Cerrar sesión (logout)"):
                    _clear_creds()
                    st.rerun()
            else:
                st.caption("Sin credenciales. El bot corre en PAPER mode "
                           "con saldo virtual.")
        else:
            st.caption("cred_manager no disponible (instalá `cryptography`).")

        st.markdown("---")

        # Cooldown indicators per pair
        st.markdown("#### Cooldowns por Par")
        system = st.session_state.system
        if system is not None:
            for name in ["BTC", "ETH", "SOL", "XRP", "BNB"]:
                if name not in system.bots:
                    st.html(f"""
                    <div class="cooldown-timer">
                        <span class="pair-name" style="color: var(--red-loss);">⚠️ {name}</span>
                        <span class="time-remaining" style="color: var(--red-loss);">FAILED</span>
                    </div>
                    """)
                    continue
                bot = system.bots[name]
                if bot.cooldown_until is not None:
                    now = datetime.now(timezone.utc)
                    if now < bot.cooldown_until:
                        remaining = (bot.cooldown_until - now).total_seconds() / 60
                        st.html(f"""
                        <div class="cooldown-timer">
                            <span class="pair-name">{name}</span>
                            <span class="time-remaining">{remaining:.1f}m</span>
                        </div>
                        """)
                    else:
                        st.html(f"""
                        <div class="cooldown-timer">
                            <span class="pair-name">{name}</span>
                            <span class="time-remaining" style="color: var(--green-profit);">READY</span>
                        </div>
                        """)
                else:
                    streak = bot.loss_streak
                    color = "var(--green-profit)" if streak == 0 else (
                            "var(--orange-warning)" if streak < 3 else "var(--red-loss)")
                    st.html(f"""
                    <div class="cooldown-timer">
                        <span class="pair-name">{name}</span>
                        <span class="time-remaining" style="color: {color};">streak: {streak}</span>
                    </div>
                    """)

        st.markdown("---")

        # ---- Manual controls with feedback (ajuste #9) ----
        st.markdown("#### Controles")
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("▶️ Run Tick Now"):
                # Visible progress bar (ajuste #9)
                progress_text = "Running tick across all bots..."
                progress = st.progress(0, text=progress_text)
                for pct in [25, 50, 75, 100]:
                    time.sleep(0.05)
                    progress.progress(pct, text=progress_text)
                with st.spinner("Executing bots..."):
                    results = run_system_tick()
                    st.session_state.last_tick_results = results
                    check_alerts()
                progress.empty()
                # Show summary (ajuste #9)
                summary = st.session_state.get("last_tick_summary")
                if summary:
                    st.success(
                        f"✅ Tick done: "
                        f"{summary['new_signals']} signals, "
                        f"{summary['trades_closed']} closed, "
                        f"{summary['in_cooldown']} in cooldown, "
                        f"{summary['errors']} errors"
                    )
        with col_b:
            if st.session_state.paused:
                if st.button("▶️ Resume"):
                    st.session_state.paused = False
                    st.success("Sistema reanudado")
            else:
                if st.button("⏸️ Pause"):
                    st.session_state.paused = True
                    st.warning("Sistema pausado")

        # Show last tick summary if available
        if st.session_state.get("last_tick_summary"):
            s = st.session_state.last_tick_summary
            ts = s["timestamp"].strftime("%H:%M:%S") if hasattr(s["timestamp"], "strftime") else "?"
            st.info(
                f"Último tick ({ts}): "
                f"🆕 {s['new_signals']} signals | "
                f"✅ {s['trades_closed']} closed | "
                f"⏸️ {s['in_cooldown']} cooldown"
            )

        st.markdown("---")

        # ---- Export buttons (ajuste #7) ----
        st.markdown("#### Exportar Datos")
        col_exp1, col_exp2 = st.columns(2)
        with col_exp1:
            if st.button("📥 Export Trades CSV"):
                _export_trades_csv()
        with col_exp2:
            if st.button("📥 Export Portfolio JSON"):
                _export_portfolio_json()

        st.markdown("---")

        # Last refresh info
        if st.session_state.last_refresh:
            st.markdown(f"**Último refresh:**")
            st.markdown(f"`{st.session_state.last_refresh.strftime('%H:%M:%S UTC')}`")
        else:
            st.markdown("**Último refresh:** nunca")


def render_footer():
    """Render the footer."""
    last = st.session_state.last_refresh
    last_str = last.strftime("%Y-%m-%d %H:%M:%S UTC") if last else "nunca"
    st.html(f"""
    <div class="footer">
        {PAGE_TITLE} · Última actualización: {last_str} ·
        Modo: {"LIVE" if st.session_state.live_trading else "PAPER"} ·
        Estado: {"PAUSED" if st.session_state.paused else "RUNNING"}
    </div>
    """)


# ============================================================
# AUTO-REFRESH LOGIC (ajuste #1: clean st_autorefresh, no manual fallback)
# ============================================================
# The maybe_auto_refresh() function has been removed.
# Auto-refresh is now handled exclusively by st_autorefresh() in main().
# On each refresh, we run a tick (if auto_refresh toggle is ON and not paused).


# ============================================================
# MAIN APP
# ============================================================
def main():
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # ---- Inject Google Fonts + main CSS via st.html() ----
    # CRITICAL: Streamlit 1.39+ sanitizes st.markdown(unsafe_allow_html=True)
    # with DOMPurify, which strips @import and breaks complex CSS.
    # st.html() bypasses the sanitizer entirely and is the correct way
    # to inject global stylesheets in modern Streamlit.
    fonts_html = (
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">'
    )
    # st.html() is available in Streamlit >= 1.39. Fallback to st.markdown for older versions.
    if hasattr(st, "html"):
        st.html(fonts_html)
        st.html(CUSTOM_CSS)
    else:
        st.html(fonts_html)
        st.html(CUSTOM_CSS)

    init_session_state()

    # ---- Auto-refresh (ajuste #1) ----
    # st_autorefresh reruns the whole script every 60s.
    # Inside the rerun, we check the auto_refresh toggle and run a tick if needed.
    if AUTOREFRESH_AVAILABLE:
        st_autorefresh(interval=60000, key="autorefresh")
    # If streamlit_autorefresh isn't installed, the dashboard still works but
    # the user must click "Run Tick Now" manually.

    # Run a tick on each rerun if auto_refresh is enabled and not paused
    if st.session_state.auto_refresh and not st.session_state.paused:
        last = st.session_state.last_refresh
        now = datetime.now(timezone.utc)
        if last is None or (now - last).total_seconds() >= REFRESH_INTERVAL_SEC:
            run_system_tick()
            check_alerts()

    # Render
    with st.sidebar:
        render_sidebar()
    render_header()
    render_metrics_row()
    render_pairs_table()

    # Agent Dashboard (agentes vivos/muertos)
    if AGENT_DASHBOARD_AVAILABLE:
        try:
            system = st.session_state.system
            ps = system.portfolio_summary() if system else None
            st.html(agent_dashboard.render_agent_html(ps))
        except Exception:
            pass

    render_charts()
    render_regime_and_activity()

    # Risk Management (expandido)
    render_risk_management()

    # Risk pasivo card
    if RISK_MANAGER_AVAILABLE and _risk_manager_instance:
        st.html('<div class="section-title">Risk Status (Pasivo)</div>')
        st.html(_risk_manager_instance.get_risk_status_html())

    # Agent detail table
    if AGENT_DASHBOARD_AVAILABLE:
        st.html(agent_dashboard.render_agent_detail())

    # Webhook latest signal
    if WEBHOOK_AVAILABLE:
        sig = webhook_tradingview.get_latest_signal()
        if sig:
            dir_str = "LONG" if sig["dir"] == 1 else "SHORT"
            st.html(f"""
            <div class="section-title">Última Señal Webhook</div>
            <div class="glass-card" style="padding: 14px 20px;">
                <div style="display: flex; gap: 20px; align-items: center; flex-wrap: wrap;">
                    <span style="color: var(--gold-light); font-weight: 600;">{sig['symbol']}</span>
                    <span class="signal-badge {'signal-long' if sig['dir']==1 else 'signal-short'}">{dir_str}</span>
                    <span style="font-family: 'JetBrains Mono', monospace;">${sig['price']:.4f}</span>
                    <span style="color: var(--text-muted); font-size: 0.75rem;">{sig.get('strategy', '?')}</span>
                    <span style="color: var(--text-muted); font-size: 0.75rem;">{sig.get('received_at', '')[:19]}</span>
                </div>
            </div>
            """)
    with st.expander("📁 Historial Persistido (trade_history.json)", expanded=False):
        render_persistent_history()
    render_footer()

    # Show system error if any
    if not MULTI_BOT_AVAILABLE:
        st.error(f"⚠️ multi_bot.py no disponible. Dashboard en modo limitado. "
                 f"Error: {st.session_state.get('system_error', 'unknown')}")
    elif st.session_state.system is None:
        st.error(f"⚠️ No se pudo inicializar MultiBotSystem: "
                 f"{st.session_state.get('system_error', '')}")


if __name__ == "__main__":
    main()
