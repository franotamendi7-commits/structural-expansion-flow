"""
Position Manager - Gestion de posiciones abiertas en tiempo real.

Responsabilidades:
  - Trackear posiciones abiertas (entry, SL, TP1, TP2, size, unrealized PnL)
  - Ejecutar trailing stop basado en ATR
  - Manejar breakeven automatico
  - Manejar salidas parciales (50% en TP1)
  - Time-based exits (evitar trades estancados)
  - Loggear cada movimiento en position_log.json
  - Persistir estado en position_state.json

Este modulo es usado por institutional_engine_v2.py en backtest Y en live.
"""

import json
import math
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict, field

import numpy as np

logger = logging.getLogger(__name__)

POSITION_LOG_PATH = Path(__file__).parent.parent / "position_log.json"
POSITION_STATE_PATH = Path(__file__).parent.parent / "position_state.json"

# Costos de transaccion (por lado)
COMMISSION = 0.0005      # 0.05% del notional
SPREAD = 0.0002          # 0.02% impacto de precio
SLIP_ATR_MULT = 0.1      # 0.1 x ATR(5m) slippage por lado


@dataclass
class Position:
    """Representa una posicion abierta."""
    position_id: str
    symbol: str
    direction: int           # 1=long, -1=short
    entry_price: float
    entry_time: datetime
    size: float              # cantidad de contratos
    size_remaining: float    # cantidad restante (post partial exit)

    # Niveles de gestion
    stop_loss: float
    take_profit_1: float
    take_profit_2: float

    # Estado de gestion
    tp1_hit: bool = False
    breakeven_active: bool = False
    trailing_stop: Optional[float] = None
    trail_high: float = 0.0   # maximo desde entrada (long)
    trail_low: float = 0.0    # minimo desde entrada (short)

    # Parametros
    atr_at_entry: float = 0.0
    atr_trail_mult: float = 2.0
    breakeven_activate_mult: float = 1.0
    partial_exit_pct: float = 0.50
    time_exit_bars: int = 24
    bars_held: int = 0

    # Score y metadata
    score: float = 0.0
    signal_type: str = ""

    # PnL tracking
    realized_pnl: float = 0.0
    partial_exits: List[Dict] = field(default_factory=list)


class PositionManager:
    """
    Gestiona posiciones abiertas con trailing stop, breakeven, salidas parciales,
    y time-based exits.

    Uso en backtest:
        pm = PositionManager()
        pos = pm.open_position(symbol, direction, entry, sl, tp1, tp2, size, ...)
        # ... cada bar ...
        action = pm.check_exit(pos, bar_high, bar_low, bar_close, atr_current, bar_index)
        if action:
            pm.close_position(pos, action)

    Uso en live:
        pm = PositionManager()
        pos = pm.open_position(...)
        pm.save_state()
        # En cada tick:
        action = pm.check_exit(pos, ...)
    """

    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.closed_positions: List[Dict] = []
        self._position_counter = 0
        self._load_state()

    def _load_state(self):
        """Cargar posiciones abiertas desde disco."""
        if POSITION_STATE_PATH.exists():
            try:
                with open(POSITION_STATE_PATH) as f:
                    d = json.load(f)
                self._position_counter = d.get("counter", 0)
                for pos_data in d.get("open_positions", []):
                    pos_data["entry_time"] = datetime.fromisoformat(pos_data["entry_time"])
                    pos = Position(**pos_data)
                    self.positions[pos.position_id] = pos
                logger.info(f"Position state loaded: {len(self.positions)} open positions")
            except Exception as e:
                logger.warning(f"Position state load error: {e}")

    def load_state(self, state: Dict[str, Any]):
        """Cargar estado desde un dict (para compatibilidad con multi_bot_v2)."""
        try:
            self._position_counter = state.get("counter", 0)
            for pos_data in state.get("open_positions", []):
                pos_data["entry_time"] = datetime.fromisoformat(pos_data["entry_time"])
                pos = Position(**pos_data)
                self.positions[pos.position_id] = pos
            logger.info(f"Position state loaded: {len(self.positions)} open positions")
        except Exception as e:
            logger.warning(f"Position state load error: {e}")

    def export_state(self) -> Dict[str, Any]:
        """Exportar estado actual como dict."""
        open_positions = []
        for pos in self.positions.values():
            d = asdict(pos)
            d["entry_time"] = pos.entry_time.isoformat()
            open_positions.append(d)
        return {
            "counter": self._position_counter,
            "open_positions": open_positions,
        }

    def save_state(self):
        """Persistir estado a disco."""
        try:
            open_positions = []
            for pos in self.positions.values():
                d = asdict(pos)
                d["entry_time"] = pos.entry_time.isoformat()
                open_positions.append(d)
            state = {
                "counter": self._position_counter,
                "open_positions": open_positions,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }
            with open(POSITION_STATE_PATH, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Position state save error: {e}")

    def _log_position(self, event: Dict):
        """Agregar evento al log de posiciones."""
        try:
            log = []
            if POSITION_LOG_PATH.exists():
                with open(POSITION_LOG_PATH) as f:
                    log = json.load(f)
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
            log.append(event)
            if len(log) > 5000:
                log = log[-2500:]
            with open(POSITION_LOG_PATH, "w") as f:
                json.dump(log, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Position log error: {e}")

    def open_position(
        self,
        symbol: str,
        direction: int,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        size: float,
        atr_at_entry: float = 0.0,
        atr_trail_mult: float = 2.0,
        breakeven_activate_mult: float = 1.0,
        partial_exit_pct: float = 0.50,
        time_exit_bars: int = 24,
        score: float = 0.0,
        signal_type: str = "",
    ) -> Position:
        """Abrir una nueva posicion."""
        self._position_counter += 1
        pos_id = f"POS-{self._position_counter:06d}"

        pos = Position(
            position_id=pos_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            entry_time=datetime.now(timezone.utc),
            size=size,
            size_remaining=size,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            atr_at_entry=atr_at_entry,
            atr_trail_mult=atr_trail_mult,
            breakeven_activate_mult=breakeven_activate_mult,
            partial_exit_pct=partial_exit_pct,
            time_exit_bars=time_exit_bars,
            trail_high=entry_price,
            trail_low=entry_price,
            score=score,
            signal_type=signal_type,
        )

        self.positions[pos_id] = pos
        self._log_position({
            "event": "open",
            "position_id": pos_id,
            "symbol": symbol,
            "direction": "LONG" if direction == 1 else "SHORT",
            "entry": entry_price,
            "sl": stop_loss,
            "tp1": take_profit_1,
            "tp2": take_profit_2,
            "size": size,
            "score": score,
        })
        self.save_state()
        logger.info(f"Position opened: {pos_id} {'LONG' if direction==1 else 'SHORT'} "
                   f"@ {entry_price:.2f}, size={size:.6f}")
        return pos

    def check_exit(
        self,
        pos: Position,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        atr_current: float,
        bar_index: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Verificar si la posicion debe cerrarse en esta bar.

        Returns: dict con reason, exit_price, partial_ratio (None si no hay salida)
        """
        pos.bars_held += 1
        d = pos.direction

        # Actualizar trail high/low
        if d == 1:
            pos.trail_high = max(pos.trail_high, bar_high)
        else:
            pos.trail_low = min(pos.trail_low, bar_low)

        # 1. CHECK STOP LOSS (siempre primero)
        sl_hit = (d == 1 and bar_low <= pos.stop_loss) or \
                 (d == -1 and bar_high >= pos.stop_loss)
        if sl_hit:
            return {
                "reason": "stop_loss",
                "exit_price": pos.stop_loss,
                "partial_ratio": 1.0,
            }

        # 2. CHECK TRAILING STOP (si esta activo)
        if pos.trailing_stop is not None:
            trail_hit = (d == 1 and bar_low <= pos.trailing_stop) or \
                        (d == -1 and bar_high >= pos.trailing_stop)
            if trail_hit:
                return {
                    "reason": "trailing_stop",
                    "exit_price": pos.trailing_stop,
                    "partial_ratio": 1.0,
                }

        # 3. CHECK TP1 (salida parcial)
        if not pos.tp1_hit:
            tp1_hit = (d == 1 and bar_high >= pos.take_profit_1) or \
                      (d == -1 and bar_low <= pos.take_profit_1)
            if tp1_hit:
                pos.tp1_hit = True
                # Mover SL a breakeven + spread
                spread_adjustment = pos.entry_price * SPREAD
                if d == 1:
                    pos.stop_loss = pos.entry_price + spread_adjustment
                else:
                    pos.stop_loss = pos.entry_price - spread_adjustment
                pos.breakeven_active = True

                # Activar trailing stop inmediatamente
                trail_dist = pos.atr_trail_mult * atr_current
                if d == 1:
                    pos.trailing_stop = pos.trail_high - trail_dist
                else:
                    pos.trailing_stop = pos.trail_low + trail_dist

                return {
                    "reason": "tp1_partial",
                    "exit_price": pos.take_profit_1,
                    "partial_ratio": pos.partial_exit_pct,
                }

        # 4. UPDATE TRAILING STOP (si TP1 ya fue hit)
        if pos.tp1_hit and pos.trailing_stop is not None:
            trail_dist = pos.atr_trail_mult * atr_current
            if d == 1:
                new_trail = pos.trail_high - trail_dist
                if new_trail > pos.trailing_stop:
                    pos.trailing_stop = new_trail
            else:
                new_trail = pos.trail_low + trail_dist
                if new_trail < pos.trailing_stop:
                    pos.trailing_stop = new_trail

        # 5. CHECK TP2 (full exit)
        tp2_hit = (d == 1 and bar_high >= pos.take_profit_2) or \
                  (d == -1 and bar_low <= pos.take_profit_2)
        if tp2_hit:
            return {
                "reason": "take_profit_2",
                "exit_price": pos.take_profit_2,
                "partial_ratio": 1.0,
            }

        # 6. TIME-BASED EXIT
        if pos.bars_held >= pos.time_exit_bars:
            return {
                "reason": "time_exit",
                "exit_price": bar_close,
                "partial_ratio": 1.0,
            }

        return None

    def close_position(
        self,
        pos: Position,
        action: Dict[str, Any],
        atr_current: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Ejecutar cierre de posicion (total o parcial).

        Returns: dict con PnL, equity change, etc.
        """
        exit_price = action["exit_price"]
        partial_ratio = action["partial_ratio"]
        reason = action["reason"]

        # Calcular size a cerrar
        close_size = pos.size_remaining * partial_ratio
        if close_size <= 0:
            return {"pnl": 0, "closed": False}

        # Aplicar slippage a la salida
        slip = SLIP_ATR_MULT * atr_current if atr_current > 0 else 0.0
        if pos.direction == 1:
            exit_fill = exit_price * (1 - SPREAD) - slip
        else:
            exit_fill = exit_price * (1 + SPREAD) + slip

        # Calcular PnL
        if pos.direction == 1:
            gross = close_size * (exit_fill - pos.entry_price)
        else:
            gross = close_size * (pos.entry_price - exit_fill)

        # Comision (entry + exit)
        comm = close_size * (pos.entry_price + exit_fill) * COMMISSION
        net = gross - comm

        pos.size_remaining -= close_size
        pos.realized_pnl += net

        # Registrar salida parcial
        if partial_ratio < 1.0:
            pos.partial_exits.append({
                "reason": reason,
                "price": exit_fill,
                "size": close_size,
                "pnl": net,
                "bars_held": pos.bars_held,
            })

        is_full_close = pos.size_remaining <= 1e-10

        self._log_position({
            "event": "close",
            "position_id": pos.position_id,
            "symbol": pos.symbol,
            "direction": "LONG" if pos.direction == 1 else "SHORT",
            "entry": pos.entry_price,
            "exit": exit_fill,
            "size_closed": close_size,
            "size_remaining": pos.size_remaining,
            "reason": reason,
            "pnl": net,
            "partial": not is_full_close,
            "bars_held": pos.bars_held,
        })

        result = {
            "position_id": pos.position_id,
            "pnl": net,
            "exit_price": exit_fill,
            "reason": reason,
            "partial": not is_full_close,
            "size_closed": close_size,
            "size_remaining": pos.size_remaining,
            "bars_held": pos.bars_held,
            "total_realized": pos.realized_pnl,
            "closed": is_full_close,
        }

        if is_full_close:
            self.closed_positions.append(asdict(pos))
            del self.positions[pos.position_id]
            logger.info(
                f"Position CLOSED: {pos.position_id} {reason} "
                f"PnL={net:+.4f} total_realized={pos.realized_pnl:+.4f}"
            )

        self.save_state()
        return result

    def get_unrealized_pnl(self, pos: Position, current_price: float) -> float:
        """Calcular PnL no realizado de una posicion."""
        if pos.direction == 1:
            return pos.size_remaining * (current_price - pos.entry_price)
        else:
            return pos.size_remaining * (pos.entry_price - current_price)

    def get_all_positions(self) -> List[Dict]:
        """Obtener todas las posiciones abiertas como dicts."""
        return [asdict(pos) for pos in self.positions.values()]

    def get_status(self) -> Dict[str, Any]:
        """Status completo para logging/dashboard."""
        return {
            "open_positions": len(self.positions),
            "total_realized_pnl": sum(p.realized_pnl for p in self.positions.values()),
            "positions": {
                pid: {
                    "direction": "LONG" if p.direction == 1 else "SHORT",
                    "entry": p.entry_price,
                    "size_remaining": p.size_remaining,
                    "tp1_hit": p.tp1_hit,
                    "trailing_stop": p.trailing_stop,
                    "bars_held": p.bars_held,
                    "realized_pnl": p.realized_pnl,
                }
                for pid, p in self.positions.items()
            },
        }
    
    @staticmethod
    def calculate_vol_adjusted_size(base_size: float, current_atr: float, baseline_atr: float) -> float:
        """
        Ajustar tamaño de posición inversamente proporcional a la volatilidad.
        
        Fórmula: position_scale = baseline_vol / current_vol
        
        Ejemplos:
            - Si vol actual = baseline → scale = 1.0 (normal)
            - Si vol sube 2x → scale = 0.5 (reducir a la mitad)
            - Si vol baja a 0.5x → scale = 2.0 (aumentar al doble)
        
        Args:
            base_size: Tamaño base calculado por risk sizing
            current_atr: ATR actual (1h o 15m)
            baseline_atr: ATR baseline (promedio 30 días)
        
        Returns: Tamaño ajustado por volatilidad
        """
        if baseline_atr <= 0 or current_atr <= 0:
            return base_size
        
        vol_ratio = baseline_atr / current_atr
        # Limitar entre 0.25x y 2.0x para evitar tamaños extremos
        vol_ratio = max(0.25, min(vol_ratio, 2.0))
        
        adjusted_size = base_size * vol_ratio
        return adjusted_size
    
    @staticmethod
    def calculate_baseline_atr(klines_1d: List[Dict], days: int = 30) -> Optional[float]:
        """
        Calcular ATR baseline (promedio de últimos N días).
        
        Args:
            klines_1d: Velas diarias
            days: Número de días para el promedio (default 30)
        
        Returns: ATR promedio o None si no hay datos suficientes
        """
        if not klines_1d or len(klines_1d) < days:
            return None
        
        # Tomar las últimas N velas
        recent = klines_1d[-days:]
        
        highs = np.array([float(k["high"]) for k in recent])
        lows = np.array([float(k["low"]) for k in recent])
        closes = np.array([float(k["close"]) for k in recent])
        
        # Calcular TR para cada vela
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1])
        )
        
        # ATR = promedio de TR
        atr_value = float(np.mean(tr))
        return atr_value
