import json
import os
import datetime
import time
import threading
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Optional

# ============================================================
# CONFIGURACIÓN DE LOGGING ESTRUCTURADO (P0 — Mejora 1)
# ============================================================
os.makedirs("logs", exist_ok=True)
_root_logger = logging.getLogger()
if not _root_logger.handlers:
    _formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    _file_handler = RotatingFileHandler(
        "logs/bot.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    _file_handler.setFormatter(_formatter)
    _file_handler.setLevel(logging.DEBUG)
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(_formatter)
    _console_handler.setLevel(logging.INFO)
    _root_logger.addHandler(_file_handler)
    _root_logger.addHandler(_console_handler)
    _root_logger.setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)


class PaperTrader:
    def __init__(self, initial_balance=100.0, state_file="paper_state.json"):
        self.state_file = state_file
        self.max_consecutive_losses = 5
        self.max_daily_loss_pct = 0.03
        self.balance = initial_balance
        self.peak_balance = initial_balance
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.last_day = None
        self.open_positions = {}
        self.closed_trades = []
        self.price_monitor_running = False
        self.monitor_thread = None
        self.feed = None
        self.SLIPPAGE_PCT = 0.001
        # Cooldown por par (P0 — Mejora 2)
        self.cooldowns = {}
        # Pausa manual (P0 — Mejora 3, para alertas de Telegram)
        self.manually_paused = False
        self.pause_reason = None
        self._lock = threading.Lock()
        self.load_state()

    def save_state(self):
        state = {
            "balance": self.balance,
            "peak_balance": self.peak_balance,
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl": self.daily_pnl,
            "last_day": self.last_day.isoformat() if self.last_day else None,
            "open_positions": self.open_positions,
            "closed_trades": self.closed_trades,
            "cooldowns": {sym: ts.isoformat() for sym, ts in self.cooldowns.items()},
            "manually_paused": self.manually_paused,
            "pause_reason": self.pause_reason,
        }
        try:
            with self._lock:
                with open(self.state_file, "w") as f:
                    json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with self._lock:
                    with open(self.state_file, "r") as f:
                        state = json.load(f)
                self.balance = state.get("balance", 100.0)
                self.peak_balance = state.get("peak_balance", self.balance)
                self.consecutive_losses = state.get("consecutive_losses", 0)
                self.daily_pnl = state.get("daily_pnl", 0.0)
                last_day_str = state.get("last_day")
                self.last_day = datetime.date.fromisoformat(last_day_str) if last_day_str else None
                self.open_positions = state.get("open_positions", {})
                self.closed_trades = state.get("closed_trades", [])

                # Cooldowns: convertir ISO strings a datetime
                self.cooldowns = {}
                for sym, ts_str in state.get("cooldowns", {}).items():
                    try:
                        self.cooldowns[sym] = datetime.datetime.fromisoformat(ts_str)
                    except (TypeError, ValueError):
                        pass

                # Pausa manual
                self.manually_paused = state.get("manually_paused", False)
                self.pause_reason = state.get("pause_reason")

                # Conversión de tipos: solo para campos numéricos reales.
                # take_profit y take_profit2 pueden ser None legítimamente.
                NUMERIC_FIELDS = ["entry_price", "current_price", "stop_loss", "contracts", "risk_usd", "pnl"]
                for pos in self.open_positions.values():
                    for key in NUMERIC_FIELDS:
                        if key in pos and pos[key] is not None:
                            try:
                                pos[key] = float(pos[key])
                            except (TypeError, ValueError):
                                pass
                    for key in ("take_profit", "take_profit2"):
                        if key in pos and pos[key] is not None:
                            try:
                                pos[key] = float(pos[key])
                            except (TypeError, ValueError):
                                pos[key] = None
                    pos.setdefault("breakeven_activated", False)
                    pos.setdefault("trade_id", None)

                for trade in self.closed_trades:
                    for key in ["entry_price", "exit_price", "pnl"]:
                        if key in trade and trade[key] is not None:
                            try:
                                trade[key] = float(trade[key])
                            except (TypeError, ValueError):
                                pass

                today = datetime.date.today()
                if self.last_day != today:
                    self.daily_pnl = 0.0
                    self.last_day = today
                logger.info(f"Estado cargado: balance=${self.balance:.2f}, ops abiertas={len(self.open_positions)}, cooldowns={len(self.cooldowns)}")
            except Exception as e:
                logger.error(f"Error loading state: {e}, using defaults")
                self._reset_defaults()
        else:
            self._reset_defaults()

    def _reset_defaults(self):
        self.balance = 100.0
        self.peak_balance = 100.0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.last_day = datetime.date.today()
        self.open_positions = {}
        self.closed_trades = []
        self.cooldowns = {}
        self.manually_paused = False
        self.pause_reason = None

    def open_trade(self, signal):
        symbol = signal.get("symbol")
        if not symbol or symbol in self.open_positions:
            return False
        today = datetime.date.today()
        if self.last_day != today:
            self.daily_pnl = 0.0
            self.last_day = today

        position = {
            "trade_id": signal.get("trade_id"),
            "symbol": symbol,
            "side": signal["side"],
            "entry_price": float(signal["price"]),
            "current_price": float(signal["price"]),
            "stop_loss": float(signal["stop_loss"]),
            "take_profit": float(signal["take_profit"]) if signal.get("take_profit") is not None else None,
            "take_profit2": float(signal["tp2"]) if signal.get("tp2") is not None else None,
            "contracts": float(signal["contracts"]),
            "risk_usd": float(signal["risk_usd"]),
            "contrarian": signal.get("contrarian", False),
            "pnl": 0.0,
            "entry_time": datetime.datetime.now().isoformat(),
            "breakeven_activated": False
        }
        self.open_positions[symbol] = position
        logger.info(f"Posición abierta: {symbol} {signal['side']} @ {position['entry_price']:.4f} | contracts={position['contracts']:.6f} | trade_id={position['trade_id']}")
        self.save_state()
        return True

    def close_position(self, symbol, exit_price=None, reason="manual", close_percent=1.0):
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions[symbol]
        if exit_price is None:
            exit_price = pos["current_price"]
        exit_price = float(exit_price)
        if pos["side"] == "LONG":
            pnl = (exit_price - pos["entry_price"]) * pos["contracts"] * close_percent
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["contracts"] * close_percent

        closed_trade = {
            "symbol": symbol,
            "side": pos["side"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "pnl": pnl,
            "contracts": pos["contracts"] * close_percent,
            "reason": reason,
            "exit_time": datetime.datetime.now().isoformat()
        }
        self.closed_trades.append(closed_trade)

        self.balance += pnl
        self.daily_pnl += pnl

        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        if close_percent < 1.0:
            remaining = 1.0 - close_percent
            pos["contracts"] *= remaining
            pos["risk_usd"] *= remaining
            logger.info(f"Posición parcial {symbol}: cerrado {close_percent*100:.0f}% por {reason} @ {exit_price:.4f} | PnL=${pnl:.4f}")
        else:
            # Cierre total → setear cooldown (P0 — Mejora 2)
            self.cooldowns[symbol] = datetime.datetime.now()
            del self.open_positions[symbol]
            logger.info(f"Posición cerrada {symbol}: {reason} @ {exit_price:.4f} | PnL=${pnl:.4f} | cooldown seteado")

        self.save_state()
        return pnl

    def update_position(self, symbol, current_price):
        """Solo actualiza precio y P&L flotante. No dispara ninguna lógica de salida."""
        if symbol not in self.open_positions:
            return
        pos = self.open_positions[symbol]
        current_price = float(current_price)
        pos["current_price"] = current_price
        if pos["side"] == "LONG":
            pnl = (current_price - pos["entry_price"]) * pos["contracts"]
        else:
            pnl = (pos["entry_price"] - current_price) * pos["contracts"]
        pos["pnl"] = pnl
        self.save_state()

    def move_stop_loss(self, symbol, new_sl):
        """Mantenido por compatibilidad. Ya no se invoca internamente."""
        if symbol in self.open_positions:
            self.open_positions[symbol]['stop_loss'] = float(new_sl)
            self.save_state()

    def set_breakeven_activated(self, symbol, value):
        """Mantenido por compatibilidad. Ya no se invoca internamente."""
        if symbol in self.open_positions:
            self.open_positions[symbol]['breakeven_activated'] = bool(value)
            self.save_state()

    def is_in_cooldown(self, symbol, minutes=15):
        """Devuelve True si el símbolo está en cooldown (P0 — Mejora 2).
        El cooldown se setea al cerrar una posición completa.
        """
        ts = self.cooldowns.get(symbol)
        if ts is None:
            return False
        elapsed = (datetime.datetime.now() - ts).total_seconds() / 60
        if elapsed >= minutes:
            # Limpiar cooldown expirado
            del self.cooldowns[symbol]
            self.save_state()
            return False
        return True

    def cooldown_remaining(self, symbol, minutes=15):
        """Devuelve minutos restantes de cooldown para un símbolo (0 si no está)."""
        ts = self.cooldowns.get(symbol)
        if ts is None:
            return 0
        elapsed = (datetime.datetime.now() - ts).total_seconds() / 60
        remaining = minutes - elapsed
        return max(0, remaining)

    def evaluate_exit_conditions(self, symbol, current_price):
        """ÚNICA lógica de salida: SL o TP1, ambos al 100%. Sin breakeven, sin TP2."""
        if symbol not in self.open_positions:
            return None
        pos = self.open_positions[symbol]
        entry = pos["entry_price"]
        sl = pos["stop_loss"]
        tp1 = pos["take_profit"]
        tp2 = pos.get("take_profit2")
        side = pos["side"]
        contracts = pos["contracts"]
        current_price = float(current_price)

        def calc_pnl(price):
            if side == "LONG":
                return (price - entry) * contracts
            else:
                return (entry - price) * contracts

        if (side == "LONG" and current_price <= sl) or (side == "SHORT" and current_price >= sl):
            return {"action": "sl", "reason": "stop_loss",
                    "pnl": calc_pnl(sl), "exit_price": sl, "entry": entry}

        if tp1 is not None:
            if (side == "LONG" and current_price >= tp1) or (side == "SHORT" and current_price <= tp1):
                return {"action": "tp1", "reason": "take_profit",
                        "pnl": calc_pnl(tp1), "exit_price": tp1, "entry": entry}

        if tp2 is not None:
            if (side == "LONG" and current_price >= tp2) or (side == "SHORT" and current_price <= tp2):
                return {"action": "tp2", "reason": "take_profit_tp2",
                        "pnl": calc_pnl(tp2), "exit_price": tp2, "entry": entry}

        return None

    def pause(self, reason="manual"):
        """Pausa manual del trading (P0 — Mejora 3)."""
        self.manually_paused = True
        self.pause_reason = reason
        self.save_state()
        logger.warning(f"Trading pausado manualmente: {reason}")

    def resume(self):
        """Reanuda el trading después de una pausa manual."""
        self.manually_paused = False
        self.pause_reason = None
        self.save_state()
        logger.info("Trading reanudado desde pausa manual")

    def start_price_monitor(self, symbols: List[str], interval: int = 5, feed=None):
        if self.price_monitor_running:
            return
        if feed is None:
            logger.error("feed must be provided to start_price_monitor")
            return
        self.feed = feed
        self.price_monitor_running = True

        def monitor_loop():
            logger.info(f"Price monitor started for {symbols}, interval={interval}s")
            while self.price_monitor_running:
                try:
                    for sym in symbols:
                        price = self.feed.get_current_price(sym)
                        if price is not None and price > 0:
                            self.update_position(sym, price)
                    time.sleep(interval)
                except Exception as e:
                    logger.error(f"Monitor error: {e}")
                    time.sleep(interval)
            logger.info("Price monitor stopped.")

        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

    def stop_price_monitor(self):
        self.price_monitor_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

    def get_open_position(self, symbol=None):
        if symbol:
            return self.open_positions.get(symbol)
        if self.open_positions:
            return next(iter(self.open_positions.values()))
        return None

    def get_all_open_positions(self):
        return self.open_positions

    def force_close(self, symbol=None):
        if symbol:
            return self.close_position(symbol, reason="force_close", close_percent=1.0)
        else:
            for sym in list(self.open_positions.keys()):
                self.close_position(sym, reason="force_close", close_percent=1.0)
            return True

    def get_balance(self):
        return self.balance

    def get_peak_balance(self):
        return self.peak_balance

    def get_drawdown_pct(self):
        if self.peak_balance == 0:
            return 0.0
        return (self.peak_balance - self.balance) / self.peak_balance

    def is_paused(self):
        # Pausa manual (P0 — Mejora 3)
        if self.manually_paused:
            return True
        # Protecciones automáticas
        if self.consecutive_losses >= self.max_consecutive_losses:
            return True
        if abs(self.daily_pnl) >= self.balance * self.max_daily_loss_pct:
            return True
        return False

    def get_closed_trades(self):
        return self.closed_trades
