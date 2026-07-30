"""
Drawdown Manager — Gestión de drawdown con 5 niveles de reducción de riesgo.

Niveles:
  DD < 3%   → Normal (1.0× risk)
  DD 3-5%   → Reducido (0.75× risk, solo trades score > 65)
  DD 5-7%   → Mínimo (0.5× risk, solo trades score > 75)
  DD 7-9%   → Supervivencia (0.25× risk, solo trades score > 85)
  DD > 9%   → STOP TOTAL (no operar hasta recovery a < 5%)

Persistencia: drawdown_state.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

DD_STATE_PATH = Path(__file__).parent.parent / "drawdown_state.json"

# Niveles de drawdown (valores negativos en DD)
DD_LEVELS = [
    {"max_dd": -3.0,  "risk_mult": 1.00, "min_score": 0,   "label": "NORMAL"},
    {"max_dd": -5.0,  "risk_mult": 0.75, "min_score": 65,  "label": "REDUCIDO"},
    {"max_dd": -7.0,  "risk_mult": 0.50, "min_score": 75,  "label": "MÍNIMO"},
    {"max_dd": -9.0,  "risk_mult": 0.25, "min_score": 85,  "label": "SUPERVIVENCIA"},
    {"max_dd": -100.0,"risk_mult": 0.00, "min_score": 999, "label": "STOP TOTAL"},
]

# Recovery threshold — volver a normal cuando DD baja de este valor
RECOVERY_THRESHOLD = -5.0


class DrawdownManager:
    """
    Calcula y gestiona drawdown en tiempo real.

    Uso:
        dd_mgr = DrawdownManager(initial_equity=1000)
        dd_mgr.update(equity_current)  # después de cada trade
        risk_mult, min_score, label = dd_mgr.get_risk_params()
        dd_mgr.save()
    """

    def __init__(self, initial_equity: float = 1000.0):
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.current_equity = initial_equity
        self.current_dd_pct = 0.0
        self.is_stopped = False
        self.stop_time: Optional[datetime] = None
        self.recovery_dd: Optional[float] = None  # DD when stopped
        self._load_state()

    def _load_state(self):
        """Cargar estado persistido."""
        if DD_STATE_PATH.exists():
            try:
                with open(DD_STATE_PATH) as f:
                    d = json.load(f)
                self.peak_equity = d.get("peak_equity", self.initial_equity)
                self.current_equity = d.get("current_equity", self.initial_equity)
                self.current_dd_pct = d.get("current_dd_pct", 0.0)
                self.is_stopped = d.get("is_stopped", False)
                stop_ts = d.get("stop_time")
                if stop_ts:
                    self.stop_time = datetime.fromisoformat(stop_ts)
                self.recovery_dd = d.get("recovery_dd")
                logger.info(f"DD state loaded: peak=${self.peak_equity:.2f}, "
                           f"current=${self.current_equity:.2f}, DD={self.current_dd_pct:.2f}%")
            except Exception as e:
                logger.warning(f"DD state load error: {e}")

    def load_state(self, state: Dict[str, Any]):
        """Cargar estado desde un dict (para compatibilidad con multi_bot_v2)."""
        try:
            self.peak_equity = state.get("peak_equity", self.initial_equity)
            self.current_equity = state.get("current_equity", self.initial_equity)
            self.current_dd_pct = state.get("current_dd_pct", 0.0)
            self.is_stopped = state.get("is_stopped", False)
            stop_ts = state.get("stop_time")
            if stop_ts:
                self.stop_time = datetime.fromisoformat(stop_ts)
            self.recovery_dd = state.get("recovery_dd")
            logger.info(f"DD state loaded: peak=${self.peak_equity:.2f}, "
                       f"current=${self.current_equity:.2f}, DD={self.current_dd_pct:.2f}%")
        except Exception as e:
            logger.warning(f"DD state load error: {e}")

    def export_state(self) -> Dict[str, Any]:
        """Exportar estado actual como dict."""
        return {
            "peak_equity": self.peak_equity,
            "current_equity": self.current_equity,
            "current_dd_pct": self.current_dd_pct,
            "is_stopped": self.is_stopped,
            "stop_time": self.stop_time.isoformat() if self.stop_time else None,
            "recovery_dd": self.recovery_dd,
        }

    def save(self):
        """Persistir estado a disco."""
        try:
            state = {
                "peak_equity": self.peak_equity,
                "current_equity": self.current_equity,
                "current_dd_pct": self.current_dd_pct,
                "is_stopped": self.is_stopped,
                "stop_time": self.stop_time.isoformat() if self.stop_time else None,
                "recovery_dd": self.recovery_dd,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            with open(DD_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"DD state save error: {e}")

    def update(self, new_equity: float) -> Dict[str, Any]:
        """
        Actualizar con nuevo equity después de un trade.

        Returns: dict con dd_pct, risk_mult, min_score, label, is_stopped
        """
        self.current_equity = new_equity

        # Actualizar peak
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
            # Si teníamos stop, verificar recovery
            if self.is_stopped:
                self.is_stopped = False
                self.stop_time = None
                self.recovery_dd = None
                logger.info(f"DD RECOVERY: equity ${new_equity:.2f} > peak, trading RESUMED")

        # Calcular DD actual
        if self.peak_equity > 0:
            self.current_dd_pct = (new_equity - self.peak_equity) / self.peak_equity * 100
        else:
            self.current_dd_pct = 0.0

        # Determinar nivel
        risk_mult, min_score, label = self._get_level()

        # Si STOP TOTAL y no hay recovery
        if risk_mult == 0.0 and not self.is_stopped:
            self.is_stopped = True
            self.stop_time = datetime.now(timezone.utc)
            self.recovery_dd = self.current_dd_pct
            logger.warning(f"DD STOP TOTAL: DD={self.current_dd_pct:.2f}%, "
                          f"equity=${new_equity:.2f}, peak=${self.peak_equity:.2f}")

        self.save()
        return {
            "dd_pct": self.current_dd_pct,
            "risk_mult": risk_mult,
            "min_score": min_score,
            "label": label,
            "is_stopped": self.is_stopped,
            "peak_equity": self.peak_equity,
        }

    def _get_level(self) -> Tuple[float, int, str]:
        """Determinar nivel de riesgo actual."""
        for level in DD_LEVELS:
            if self.current_dd_pct > level["max_dd"]:
                return level["risk_mult"], level["min_score"], level["label"]
        # Si estamos en STOP, verificar recovery
        if self.is_stopped and self.current_dd_pct > RECOVERY_THRESHOLD:
            # Recovery check: DD mejoró a < 5%
            return 0.75, 65, "REDUCIDO (recovery)"
        return 0.0, 999, "STOP TOTAL"

    def get_risk_params(self) -> Tuple[float, int, str]:
        """
        Obtener parámetros de riesgo actuales.

        Returns: (risk_multiplier, min_score_required, level_label)
        """
        risk_mult, min_score, label = self._get_level()
        return risk_mult, min_score, label

    def can_trade(self, trade_score: float) -> bool:
        """
        Verificar si se puede tomar un trade dado su score.

        Args:
            trade_score: Score del trade (0-100)

        Returns: True si se permite el trade
        """
        _, min_score, label = self.get_risk_params()

        if label == "STOP TOTAL":
            return False

        if trade_score < min_score:
            return False

        return True

    def get_status(self) -> Dict[str, Any]:
        """Status completo para logging/dashboard."""
        risk_mult, min_score, label = self.get_risk_params()
        return {
            "current_dd_pct": round(self.current_dd_pct, 2),
            "peak_equity": round(self.peak_equity, 2),
            "current_equity": round(self.current_equity, 2),
            "risk_multiplier": risk_mult,
            "min_score_required": min_score,
            "level_label": label,
            "is_stopped": self.is_stopped,
            "stop_time": self.stop_time.isoformat() if self.stop_time else None,
            "recovery_dd": self.recovery_dd,
        }

    def reset(self, new_equity: Optional[float] = None):
        """Reset total del manager (para testing)."""
        if new_equity is not None:
            self.initial_equity = new_equity
        self.peak_equity = self.initial_equity
        self.current_equity = self.initial_equity
        self.current_dd_pct = 0.0
        self.is_stopped = False
        self.stop_time = None
        self.recovery_dd = None
        self.save()
