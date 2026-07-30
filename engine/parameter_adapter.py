"""
Parameter Adapter — Adaptación autónoma de parámetros basada en performance rolling.

Cada 2 semanas (o cada 50 trades), analiza:
  - Win Rate rolling (últimos 50 trades)
  - Profit Factor rolling
  - Si WR < 35% → aumentar filtros (CI threshold +5, score mínimo +10)
  - Si WR > 55% → relajar filtros ligeramente (CI -3, score -5)
  - Si PF < 1.0 en 20 trades → pausar configuración actual
  - Strategy weight: 3+ losses seguidas → risk × 0.5

Persistencia: parameter_history.json, parameter_state.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

PARAM_STATE_PATH = Path(__file__).parent.parent / "parameter_state.json"
PARAM_HISTORY_PATH = Path(__file__).parent.parent / "parameter_history.json"

# Defaults del motor V1 (baseline)
DEFAULT_PARAMS = {
    "ci_neutral_threshold": 74.0,     # Choppiness Index neutral threshold
    "ci_trend_threshold": 38.2,       # CI trending threshold
    "min_score": 50,                  # Score minimo para entrar (V1 no tiene filtro de score)
    "supertrend_multiplier": 2.8,     # Supertrend multiplier
    "tp_ratio": 1.5,                  # TP ratio (SL distance multiplier)
    "fibonacci_days": 30,             # Días para Fibonacci adaptativo
    "atr_trail_mult": 2.0,            # Trailing stop ATR multiplier
    "breakeven_activate_mult": 1.0,   # Multiplicador para activar breakeven
    "partial_exit_pct": 0.50,         # % de posición a cerrar en TP1
    "time_exit_bars": 24,             # Velas máximas antes de time-based exit
}

# Límites para evitar overfitting
PARAM_BOUNDS = {
    "ci_neutral_threshold": (60.0, 85.0),
    "ci_trend_threshold": (30.0, 45.0),
    "min_score": (60, 95),
    "supertrend_multiplier": (2.0, 4.0),
    "tp_ratio": (1.0, 3.0),
    "fibonacci_days": (15, 90),
    "atr_trail_mult": (1.5, 4.0),
    "breakeven_activate_mult": (0.5, 2.0),
    "partial_exit_pct": (0.30, 0.70),
    "time_exit_bars": (12, 48),
}

# Pasos de ajuste por ciclo (cambios graduales)
ADJUSTMENT_STEPS = {
    "ci_neutral_threshold": 3.0,
    "ci_trend_threshold": 2.0,
    "min_score": 5,
    "supertrend_multiplier": 0.2,
    "tp_ratio": 0.1,
    "fibonacci_days": 5,
    "atr_trail_mult": 0.2,
    "breakeven_activate_mult": 0.1,
    "partial_exit_pct": 0.05,
    "time_exit_bars": 4,
}


class ParameterAdapter:
    """
    Analiza performance rolling y ajusta parámetros gradualmente.

    Uso:
        adapter = ParameterAdapter()
        adapter.record_trade(trade_result)
        new_params = adapter.get_params()
        # Aplicar new_params al motor
    """

    def __init__(self, lookback_trades: int = 50, review_interval_trades: int = 50):
        self.lookback_trades = lookback_trades
        self.review_interval_trades = review_interval_trades
        self.params = dict(DEFAULT_PARAMS)
        self.trade_history: List[Dict] = []
        self.consecutive_losses = 0
        self.total_trades_recorded = 0
        self.last_review_trade = 0
        self.is_paused = False
        self.pause_reason = ""
        self._load_state()

    def _load_state(self):
        """Cargar estado persistido."""
        if PARAM_STATE_PATH.exists():
            try:
                with open(PARAM_STATE_PATH) as f:
                    d = json.load(f)
                self.params.update(d.get("params", {}))
                self.consecutive_losses = d.get("consecutive_losses", 0)
                self.total_trades_recorded = d.get("total_trades_recorded", 0)
                self.last_review_trade = d.get("last_review_trade", 0)
                self.is_paused = d.get("is_paused", False)
                self.pause_reason = d.get("pause_reason", "")
                logger.info(f"Param adapter state loaded: {self.total_trades_recorded} trades, "
                           f"paused={self.is_paused}")
            except Exception as e:
                logger.warning(f"Param state load error: {e}")

    def load_state(self, state: Dict[str, Any]):
        """Cargar estado desde un dict (para compatibilidad con multi_bot_v2)."""
        try:
            self.params.update(state.get("params", {}))
            self.consecutive_losses = state.get("consecutive_losses", 0)
            self.total_trades_recorded = state.get("total_trades_recorded", 0)
            self.last_review_trade = state.get("last_review_trade", 0)
            self.is_paused = state.get("is_paused", False)
            self.pause_reason = state.get("pause_reason", "")
            logger.info(f"Param adapter state loaded: {self.total_trades_recorded} trades, "
                       f"paused={self.is_paused}")
        except Exception as e:
            logger.warning(f"Param state load error: {e}")

    def export_state(self) -> Dict[str, Any]:
        """Exportar estado actual como dict."""
        return {
            "params": self.params,
            "consecutive_losses": self.consecutive_losses,
            "total_trades_recorded": self.total_trades_recorded,
            "last_review_trade": self.last_review_trade,
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
        }

    def save(self):
        """Persistir estado."""
        try:
            state = {
                "params": self.params,
                "consecutive_losses": self.consecutive_losses,
                "total_trades_recorded": self.total_trades_recorded,
                "last_review_trade": self.last_review_trade,
                "is_paused": self.is_paused,
                "pause_reason": self.pause_reason,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            with open(PARAM_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"Param state save error: {e}")

    def _save_history(self, event: Dict):
        """Agregar evento al historial de cambios."""
        try:
            history = []
            if PARAM_HISTORY_PATH.exists():
                with open(PARAM_HISTORY_PATH) as f:
                    history = json.load(f)
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
            history.append(event)
            # Mantener últimos 200 eventos
            if len(history) > 200:
                history = history[-200:]
            with open(PARAM_HISTORY_PATH, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.warning(f"Param history save error: {e}")

    def record_trade(self, trade_result: Dict):
        """
        Registrar un trade y posiblemente revisar parámetros.

        Args:
            trade_result: dict con keys: net_pnl, score, direction, entry, exit
        """
        self.total_trades_recorded += 1
        self.trade_history.append(trade_result)

        # Mantener solo los últimos N trades
        if len(self.trade_history) > self.lookback_trades * 2:
            self.trade_history = self.trade_history[-self.lookback_trades:]

        # Trackear streaks
        if trade_result.get("net_pnl", 0) <= 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Strategy weight: 3+ losses seguidas → reducir risk
        if self.consecutive_losses >= 3:
            logger.warning(f"Consecutive losses: {self.consecutive_losses}, "
                          f"reducing risk multiplier")

        # Revisar si toca review
        if self.total_trades_recorded - self.last_review_trade >= self.review_interval_trades:
            self._review_parameters()

        self.save()

    def _review_parameters(self):
        """Revisar y ajustar parámetros basado en performance rolling."""
        recent = self.trade_history[-self.lookback_trades:]
        if len(recent) < 20:
            return

        self.last_review_trade = self.total_trades_recorded

        # Calcular métricas rolling
        wins = sum(1 for t in recent if t.get("net_pnl", 0) > 0)
        total = len(recent)
        wr = wins / total * 100

        gross_win = sum(t["net_pnl"] for t in recent if t.get("net_pnl", 0) > 0)
        gross_loss = -sum(t["net_pnl"] for t in recent if t.get("net_pnl", 0) <= 0)
        pf = gross_win / gross_loss if gross_loss > 0 else 99.0

        avg_score = sum(t.get("score", 80) for t in recent) / total

        logger.info(f"Parameter review: WR={wr:.1f}%, PF={pf:.2f}, "
                   f"AvgScore={avg_score:.1f}, Trades={total}")

        changes = []

        # WR < 35% → aumentar filtros (ser más selectivo)
        if wr < 35.0:
            old_ci = self.params["ci_neutral_threshold"]
            self.params["ci_neutral_threshold"] = min(
                self.params["ci_neutral_threshold"] + ADJUSTMENT_STEPS["ci_neutral_threshold"],
                PARAM_BOUNDS["ci_neutral_threshold"][1]
            )
            if self.params["ci_neutral_threshold"] != old_ci:
                changes.append(f"CI threshold: {old_ci:.1f} → {self.params['ci_neutral_threshold']:.1f}")

            old_score = self.params["min_score"]
            self.params["min_score"] = min(
                self.params["min_score"] + ADJUSTMENT_STEPS["min_score"],
                PARAM_BOUNDS["min_score"][1]
            )
            if self.params["min_score"] != old_score:
                changes.append(f"Min score: {old_score} → {self.params['min_score']}")

        # WR > 55% → relajar ligeramente
        elif wr > 55.0:
            old_ci = self.params["ci_neutral_threshold"]
            self.params["ci_neutral_threshold"] = max(
                self.params["ci_neutral_threshold"] - ADJUSTMENT_STEPS["ci_neutral_threshold"],
                PARAM_BOUNDS["ci_neutral_threshold"][0]
            )
            if self.params["ci_neutral_threshold"] != old_ci:
                changes.append(f"CI threshold: {old_ci:.1f} → {self.params['ci_neutral_threshold']:.1f}")

            old_score = self.params["min_score"]
            self.params["min_score"] = max(
                self.params["min_score"] - ADJUSTMENT_STEPS["min_score"],
                PARAM_BOUNDS["min_score"][0]
            )
            if self.params["min_score"] != old_score:
                changes.append(f"Min score: {old_score} → {self.params['min_score']}")

        # PF < 1.0 en últimos 20 trades → pausar configuración
        recent_20 = self.trade_history[-20:]
        if len(recent_20) >= 20:
            gw_20 = sum(t.get("net_pnl", 0) for t in recent_20 if t.get("net_pnl", 0) > 0)
            gl_20 = -sum(t.get("net_pnl", 0) for t in recent_20 if t.get("net_pnl", 0) <= 0)
            pf_20 = gw_20 / gl_20 if gl_20 > 0 else 99.0
            if pf_20 < 1.0:
                self.is_paused = True
                self.pause_reason = f"PF={pf_20:.2f} in last 20 trades (WR={wr:.1f}%)"
                logger.warning(f"PARAM PAUSE: {self.pause_reason}")

        # Strategy weight: 3+ losses → risk × 0.5
        # Esto se maneja externamente al consultar get_risk_multiplier()

        if changes:
            self._save_history({
                "type": "param_adjustment",
                "wr": round(wr, 1),
                "pf": round(pf, 2),
                "changes": changes,
                "params": dict(self.params),
            })
            logger.info(f"Parameters adjusted: {'; '.join(changes)}")

    def get_params(self) -> Dict[str, Any]:
        """Obtener parámetros actuales."""
        return dict(self.params)

    def get_risk_multiplier(self) -> float:
        """
        Obtener multiplicador de riesgo basado en streaks.

        Returns: 1.0 (normal) o 0.5 (si 3+ losses seguidas)
        """
        if self.consecutive_losses >= 3:
            return 0.5
        return 1.0

    def is_trading_allowed(self) -> Tuple[bool, str]:
        """Verificar si el trading está permitido."""
        if self.is_paused:
            return False, f"Pausado: {self.pause_reason}"
        return True, "OK"

    def get_status(self) -> Dict[str, Any]:
        """Status completo."""
        recent = self.trade_history[-self.lookback_trades:]
        wr = 0.0
        pf = 0.0
        if recent:
            wins = sum(1 for t in recent if t.get("net_pnl", 0) > 0)
            wr = wins / len(recent) * 100
            gw = sum(t["net_pnl"] for t in recent if t.get("net_pnl", 0) > 0)
            gl = -sum(t["net_pnl"] for t in recent if t.get("net_pnl", 0) <= 0)
            pf = gw / gl if gl > 0 else 99.0

        return {
            "params": dict(self.params),
            "total_trades": self.total_trades_recorded,
            "lookback_trades": len(recent),
            "rolling_wr": round(wr, 1),
            "rolling_pf": round(pf, 2),
            "consecutive_losses": self.consecutive_losses,
            "risk_multiplier": self.get_risk_multiplier(),
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "last_review_trade": self.last_review_trade,
        }

    def reset(self):
        """Reset total (para testing)."""
        self.params = dict(DEFAULT_PARAMS)
        self.trade_history = []
        self.consecutive_losses = 0
        self.total_trades_recorded = 0
        self.last_review_trade = 0
        self.is_paused = False
        self.pause_reason = ""
        self.save()


# Type alias for return
from typing import Tuple
