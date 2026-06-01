import json
import os
import datetime

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
        self.open_positions = {}          # dict {symbol: position_dict}
        self.closed_trades = []           # list of trade dicts
        self.load_state()

    # ------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------
    def save_state(self):
        state = {
            "balance": self.balance,
            "peak_balance": self.peak_balance,
            "consecutive_losses": self.consecutive_losses,
            "daily_pnl": self.daily_pnl,
            "last_day": self.last_day.isoformat() if self.last_day else None,
            "open_positions": self.open_positions.copy(),
            "closed_trades": self.closed_trades.copy()
        }
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"Error saving PaperTrader state: {e}")

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
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
                # Convertir números flotantes por si vienen como string
                for pos in self.open_positions.values():
                    for key in ["entry_price", "current_price", "stop_loss", "take_profit", "contracts", "risk_usd", "pnl"]:
                        if key in pos:
                            pos[key] = float(pos[key])
                for trade in self.closed_trades:
                    for key in ["entry_price", "exit_price", "pnl"]:
                        if key in trade:
                            trade[key] = float(trade[key])
                # Reiniciar daily_pnl si cambió el día
                today = datetime.date.today()
                if self.last_day != today:
                    self.daily_pnl = 0.0
                    self.last_day = today
            except Exception as e:
                print(f"Error loading state: {e}, using defaults")
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

    # ------------------------------------------------------------
    # Métodos principales
    # ------------------------------------------------------------
    def open_trade(self, signal):
        """
        signal: dict con keys: symbol, side, price, stop_loss, take_profit,
                contracts, risk_usd, contrarian (opcional)
        Retorna True si se abrió la posición, False si ya existía una para ese símbolo.
        """
        symbol = signal.get("symbol")
        if not symbol:
            return False
        if symbol in self.open_positions:
            return False
        # Actualizar tracking diario
        today = datetime.date.today()
        if self.last_day != today:
            self.daily_pnl = 0.0
            self.last_day = today

        position = {
            "symbol": symbol,
            "side": signal["side"],
            "entry_price": signal["price"],
            "current_price": signal["price"],
            "stop_loss": signal["stop_loss"],
            "take_profit": signal["take_profit"],
            "take_profit2": signal.get("tp2"),
            "contracts": signal["contracts"],
            "risk_usd": signal["risk_usd"],
            "contrarian": signal.get("contrarian", False),
            "pnl": 0.0,
            "entry_time": datetime.datetime.now().isoformat()
        }
        self.open_positions[symbol] = position
        self.save_state()
        return True

    def close_position(self, symbol, exit_price=None):
        if symbol not in self.open_positions:
            return False
        pos = self.open_positions.pop(symbol)
        if exit_price is None:
            exit_price = pos["current_price"]
        # Calcular PnL
        if pos["side"] == "LONG":
            pnl = (exit_price - pos["entry_price"]) * pos["contracts"]
        else:  # SHORT
            pnl = (pos["entry_price"] - exit_price) * pos["contracts"]

        # Registrar trade cerrado
        closed_trade = {
            "symbol": symbol,
            "side": pos["side"],
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "pnl": pnl,
            "contracts": pos["contracts"],
            "reason": "manual",
            "exit_time": datetime.datetime.now().isoformat()
        }
        self.closed_trades.append(closed_trade)

        # Actualizar balance y racha
        self.balance += pnl
        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.daily_pnl += pnl

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        self.save_state()
        return True

    def update_position(self, symbol, current_price):
        if symbol not in self.open_positions:
            return
        pos = self.open_positions[symbol]
        pos["current_price"] = current_price
        if pos["side"] == "LONG":
            pnl = (current_price - pos["entry_price"]) * pos["contracts"]
        else:
            pnl = (pos["entry_price"] - current_price) * pos["contracts"]
        pos["pnl"] = pnl
        self.save_state()

    def get_open_position(self, symbol=None):
        if symbol:
            return self.open_positions.get(symbol)
        # Si no se especifica, devuelve la primera (compatibilidad con código viejo)
        if self.open_positions:
            return next(iter(self.open_positions.values()))
        return None

    def get_all_open_positions(self):
        return self.open_positions

    def force_close(self, symbol=None):
        if symbol:
            return self.close_position(symbol)
        else:
            # Cerrar todas las posiciones
            for sym in list(self.open_positions.keys()):
                self.close_position(sym)
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
        if self.consecutive_losses >= self.max_consecutive_losses:
            return True
        if abs(self.daily_pnl) >= self.balance * self.max_daily_loss_pct:
            return True
        return False

    def get_closed_trades(self):
        return self.closed_trades