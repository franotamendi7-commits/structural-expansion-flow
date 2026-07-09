"""
Agent Dashboard — panel de monitoreo de agentes individuales.
Cada agente (scout, momentum, range, structure, execution) expone su
estado y este dashboard los visualiza en tiempo real.
"""
import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

AGENT_STATE_DIR = Path(__file__).parent / "agent_states"
AGENT_STATE_DIR.mkdir(exist_ok=True)

AGENT_NAMES = ["scout", "momentum", "range", "structure", "execution"]


def get_agent_state(name: str) -> Dict[str, Any]:
    path = AGENT_STATE_DIR / f"{name}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Agent state load error {name}: {e}")
    return {"name": name, "status": "unknown", "last_seen": None}


def save_agent_state(name: str, state: dict):
    state["name"] = name
    state["last_seen"] = datetime.now(timezone.utc).isoformat()
    path = AGENT_STATE_DIR / f"{name}.json"
    try:
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Agent state save error {name}: {e}")


def get_all_agent_states() -> Dict[str, Dict]:
    return {name: get_agent_state(name) for name in AGENT_NAMES}


def is_agent_alive(state: Dict, max_seconds: int = 300) -> bool:
    last = state.get("last_seen")
    if not last:
        return False
    try:
        dt = datetime.fromisoformat(last)
        return (datetime.now(timezone.utc) - dt).total_seconds() < max_seconds
    except Exception:
        return False


def summarize_agents():
    states = get_all_agent_states()
    summary = {}
    for name, s in states.items():
        alive = is_agent_alive(s)
        status = "alive" if alive else "dead"
        last_action = s.get("last_action", "none")
        score = s.get("last_score", 0)
        summary[name] = {"status": status, "last_action": last_action, "score": score}
    return summary


def render_agent_html(system_stats: Dict = None):
    states = get_all_agent_states()
    cards = ""
    for name, s in states.items():
        alive = is_agent_alive(s)
        status_color = "var(--green-profit)" if alive else "var(--red-loss)"
        status_text = "ALIVE" if alive else "OFFLINE"
        last_seen = s.get("last_seen", "N/A")
        if last_seen != "N/A":
            try:
                dt = datetime.fromisoformat(last_seen)
                last_seen = dt.strftime("%H:%M:%S UTC")
            except Exception:
                pass
        last_action = s.get("last_action", "—")
        score = s.get("last_score", 0)
        n_signals = s.get("signals_today", 0)
        n_trades = s.get("trades_today", 0)

        cards += f"""
        <div class="glass-card" style="min-width: 180px;">
            <div style="display: flex; justify-content: space-between; align-items: start;">
                <div class="metric-label" style="font-size: 0.8rem;">{name.upper()}</div>
                <span style="color: {status_text}; font-size: 0.7rem; font-weight: 600;">{status_text}</span>
            </div>
            <div style="margin-top: 8px;">
                <div style="display: flex; justify-content: space-between; gap: 12px;">
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">Score</div>
                        <div style="color: var(--text-primary); font-size: 1.1rem; font-weight: 700;">{score:.0f}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">Señales</div>
                        <div style="color: var(--text-primary); font-size: 1.1rem; font-weight: 700;">{n_signals}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">Trades</div>
                        <div style="color: var(--text-primary); font-size: 1.1rem; font-weight: 700;">{n_trades}</div>
                    </div>
                </div>
                <div style="margin-top: 8px; font-size: 0.7rem; color: var(--text-muted);">
                    Última acción: {last_action}
                </div>
                <div style="font-size: 0.65rem; color: var(--text-muted);">
                    {last_seen}
                </div>
            </div>
        </div>
        """

    if system_stats:
        ps = system_stats
        system_cards = f"""
        <div class="glass-card" style="min-width: 180px;">
            <div class="metric-label" style="font-size: 0.8rem;">SISTEMA</div>
            <div style="margin-top: 8px;">
                <div style="display: flex; justify-content: space-between; gap: 12px;">
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">Equity</div>
                        <div style="color: var(--text-primary); font-size: 1.1rem; font-weight: 700;">${ps.get('total_equity', 0):.2f}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">P&L</div>
                        <div style="color: var(--{'green-profit' if ps.get('net_pnl', 0) >= 0 else 'red-loss'}); font-size: 1.1rem; font-weight: 700;">${ps.get('net_pnl', 0):+.2f}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.65rem;">Trades</div>
                        <div style="color: var(--text-primary); font-size: 1.1rem; font-weight: 700;">{ps.get('total_trades', 0)}</div>
                    </div>
                </div>
                <div style="margin-top: 8px; font-size: 0.7rem; color: var(--text-muted);">
                    WR: {ps.get('win_rate', 0):.1f}% · PF: {ps.get('profit_factor', 0):.2f}
                </div>
            </div>
        </div>
        """
        cards = system_cards + cards

    html = f"""
    <div class="section-title">Agentes</div>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; margin-bottom: 20px;">
        {cards}
    </div>
    """
    return html


def render_agent_detail() -> str:
    states = get_all_agent_states()
    rows = ""
    for name, s in states.items():
        alive = is_agent_alive(s)
        status_color = "var(--green-profit)" if alive else "var(--red-loss)"
        status_text = "ALIVE" if alive else "OFFLINE"
        last_action = s.get("last_action", "—")
        score = s.get("last_score", 0)
        signals = s.get("signals_today", 0)
        trades = s.get("trades_today", 0)
        last = s.get("last_seen") or "N/A"
        confidence = s.get("last_confidence") or 0

        rows += f"""
        <tr>
            <td style="color: var(--gold-light); font-weight: 600;">{name.upper()}</td>
            <td><span style="color: {status_color};">● {status_text}</span></td>
            <td>{last_action}</td>
            <td>{score:.0f}</td>
            <td>{confidence:.0f}%</td>
            <td>{signals}</td>
            <td>{trades}</td>
            <td style="color: var(--text-muted); font-size: 0.75rem;">{last[:19] if isinstance(last, str) and last != "N/A" else "—"}</td>
        </tr>
        """

    html = f"""
    <div class="section-title">Estado Detallado de Agentes</div>
    <table class="pairs-table">
        <thead>
            <tr>
                <th>Agente</th>
                <th>Estado</th>
                <th>Última Acción</th>
                <th>Score</th>
                <th>Confianza</th>
                <th>Señales Hoy</th>
                <th>Trades Hoy</th>
                <th>Último Latido</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>
    """
    return html