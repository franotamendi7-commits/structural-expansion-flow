"""
Risk Manager — gestión de riesgo pasiva y activa.
Métricas en tiempo real para el dashboard + lógica de sizing.
"""
import json
import logging
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

RISK_STATE_PATH = Path(__file__).parent / "risk_state.json"


class RiskManager:
    def __init__(self, base_risk=0.01, max_risk=0.015, min_risk=0.005):
        self.base_risk = base_risk
        self.max_risk = max_risk
        self.min_risk = min_risk
        self.max_dd_pct = -0.05
        self.max_daily_loss_pct = -0.03
        self.max_consecutive_losses = 5
        self.daily_loss_pct = 0.0
        self.consecutive_losses = 0
        self.peak_equity = 0.0
        self.current_dd_pct = 0.0
        self.is_cooling_down = False
        self.cooldown_until = None
        self.trade_results = []
        self._load_state()

    def _load_state(self):
        if RISK_STATE_PATH.exists():
            try:
                with open(RISK_STATE_PATH) as f:
                    d = json.load(f)
                self.daily_loss_pct = d.get("daily_loss_pct", 0.0)
                self.consecutive_losses = d.get("consecutive_losses", 0)
                self.peak_equity = d.get("peak_equity", 0.0)
                self.is_cooling_down = d.get("is_cooling_down", False)
                cd = d.get("cooldown_until")
                if cd:
                    self.cooldown_until = datetime.fromisoformat(cd)
            except Exception as e:
                logger.warning(f"Risk state load error: {e}")

    def _save_state(self):
        try:
            with open(RISK_STATE_PATH, "w") as f:
                json.dump({
                    "daily_loss_pct": self.daily_loss_pct,
                    "consecutive_losses": self.consecutive_losses,
                    "peak_equity": self.peak_equity,
                    "is_cooling_down": self.is_cooling_down,
                    "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Risk state save error: {e}")

    def get_risk_pct(self, score, atr, price):
        score_factor = (score - 50) / 50.0
        score_risk = self.min_risk + (self.max_risk - self.min_risk) * max(0, score_factor)
        vol_pct = atr / price if price > 0 else 0.02
        avg_vol = 0.02
        vol_factor = avg_vol / vol_pct if vol_pct > 0 else 1.0
        vol_factor = max(0.5, min(vol_factor, 1.5))
        final_risk = score_risk * vol_factor
        return max(self.min_risk, min(self.max_risk, final_risk))

    def get_dynamic_risk(self, score: float, atr: float, price: float,
                         equity: float) -> Dict[str, Any]:
        base_pct = self.get_risk_pct(score, atr, price)

        if self.is_cooling_down:
            if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
                return {"risk_pct": 0, "mode": "COOLDOWN", "reason": "risk cooldown active"}
            else:
                self.is_cooling_down = False
                self.cooldown_until = None
                self._save_state()

        if self.consecutive_losses >= self.max_consecutive_losses:
            return {"risk_pct": 0, "mode": "PAUSE",
                    "reason": f"{self.consecutive_losses} consecutive losses"}

        if self.current_dd_pct <= self.max_dd_pct:
            return {"risk_pct": 0, "mode": "DD_PROTECT",
                    "reason": f"drawdown {self.current_dd_pct:.2f}% exceeds limit"}

        if self.daily_loss_pct <= self.max_daily_loss_pct:
            return {"risk_pct": 0, "mode": "DAILY_LOSS",
                    "reason": f"daily loss {self.daily_loss_pct:.2f}% > limit"}

        dd_factor = max(0, 1 + self.current_dd_pct / self.max_dd_pct) if self.current_dd_pct < 0 else 1.0
        streak_factor = max(0, 1 - self.consecutive_losses * 0.15)
        adjusted = base_pct * dd_factor * streak_factor
        adjusted = max(self.min_risk, min(self.max_risk, adjusted))

        return {
            "risk_pct": adjusted,
            "mode": "NORMAL" if adjusted == base_pct else "REDUCED",
            "base_risk": base_pct,
            "dd_factor": dd_factor,
            "streak_factor": streak_factor,
        }

    def record_result(self, net_pnl: float, equity_before: float, equity_after: float):
        self.trade_results.append(net_pnl)
        if len(self.trade_results) > 100:
            self.trade_results.pop(0)

        if net_pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        self.daily_loss_pct += net_pnl / equity_before if equity_before else 0

        if equity_after > self.peak_equity:
            self.peak_equity = equity_after

        self.current_dd_pct = (equity_after - self.peak_equity) / self.peak_equity * 100 if self.peak_equity else 0

        reset_hour = 0
        if datetime.now(timezone.utc).hour == reset_hour:
            self.daily_loss_pct = 0.0

        if self.consecutive_losses >= self.max_consecutive_losses:
            self.is_cooling_down = True
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)

        self._save_state()

    def get_passive_metrics(self) -> Dict[str, Any]:
        return {
            "current_dd_pct": self.current_dd_pct,
            "peak_equity": self.peak_equity,
            "consecutive_losses": self.consecutive_losses,
            "daily_loss_pct": self.daily_loss_pct,
            "is_cooling_down": self.is_cooling_down,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "n_trades_recorded": len(self.trade_results),
            "avg_trade": np.mean(self.trade_results) if self.trade_results else 0,
            "win_rate_recent": (
                sum(1 for r in self.trade_results if r > 0) / len(self.trade_results) * 100
            ) if len(self.trade_results) >= 5 else 0,
        }

    def get_risk_status_html(self) -> str:
        m = self.get_passive_metrics()
        dd = m["current_dd_pct"]
        losses = m["consecutive_losses"]
        daily = m["daily_loss_pct"]

        dd_class = "dd-safe" if dd > -3 else ("dd-warning" if dd > -7 else "dd-danger")
        dd_width = min(100, abs(dd) * 10)

        mode = "NORMAL"
        mode_color = "var(--green-profit)"
        if m["is_cooling_down"]:
            mode = "COOLDOWN"
            mode_color = "var(--orange-warning)"
        elif losses >= self.max_consecutive_losses:
            mode = "PAUSED"
            mode_color = "var(--red-loss)"
        elif dd <= self.max_dd_pct:
            mode = "DD PROTECT"
            mode_color = "var(--red-loss)"

        return f"""
        <div class="glass-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div class="metric-label">Risk Mode</div>
                <span style="color: {mode_color}; font-weight: 700; font-size: 0.85rem;">{mode}</span>
            </div>
            <div style="margin-top: 12px;">
                <div class="metric-label">Current Drawdown</div>
                <div class="dd-bar" style="margin-top: 4px;">
                    <div class="dd-bar-fill {dd_class}" style="width: {dd_width}%;"></div>
                </div>
                <div style="display: flex; justify-content: space-between; font-size: 0.75rem; margin-top: 4px;">
                    <span style="color: var(--text-muted);">{dd:.2f}%</span>
                    <span style="color: var(--text-muted);">{m['peak_equity']:.2f} peak</span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px;">
                <div>
                    <div style="color: var(--text-muted); font-size: 0.65rem;">Racha Perd.</div>
                    <div style="color: var(--{'red-loss' if losses > 0 else 'text-primary'}); font-weight: 600;">{losses}</div>
                </div>
                <div>
                    <div style="color: var(--text-muted); font-size: 0.65rem;">Daily Loss</div>
                    <div style="color: var(--{'red-loss' if daily < 0 else 'text-primary'}); font-weight: 600;">{daily:.2f}%</div>
                </div>
                <div>
                    <div style="color: var(--text-muted); font-size: 0.65rem;">WR Rec.</div>
                    <div style="font-weight: 600;">{m['win_rate_recent']:.1f}%</div>
                </div>
                <div>
                    <div style="color: var(--text-muted); font-size: 0.65rem;">Avg Trade</div>
                    <div style="font-weight: 600;">${m['avg_trade']:+.4f}</div>
                </div>
            </div>
        </div>
        """