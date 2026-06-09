import json, os, time
from datetime import datetime, timezone

class PaperTrader:
    def __init__(self, initial_balance=100.0, state_file="paper_state.json"):
        self.state_file = state_file
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
        else:
            state = {
                "balance": initial_balance,
                "peak_balance": initial_balance,
                "open_positions": {},
                "closed_trades": [],
                "last_update": None
            }
        self.balance = state["balance"]
        self.peak_balance = state["peak_balance"]
        self.open_positions = state.get("open_positions", {})
        self.closed_trades = state.get("closed_trades", [])
        self.last_update = state.get("last_update")
        self.initial_balance = initial_balance

    def save_state(self):
        with open(self.state_file, 'w') as f:
            json.dump({
                "balance": self.balance,
                "peak_balance": self.peak_balance,
                "open_positions": self.open_positions,
                "closed_trades": self.closed_trades,
                "last_update": datetime.now(timezone.utc).isoformat()
            }, f, indent=2)

    def open_trade(self, trade_signal):
        symbol = trade_signal.get("symbol")
        side = trade_signal.get("side")
        entry_price = trade_signal["entry_price"] if "entry_price" in trade_signal else trade_signal.get("price")
        stop_loss = trade_signal["stop_loss"]
        take_profit1 = trade_signal.get("take_profit1", trade_signal.get("take_profit"))
        take_profit2 = trade_signal.get("take_profit2")
        contracts = trade_signal.get("contracts", 0)
        if not symbol or not side or not entry_price or not stop_loss or contracts <= 0:
            return False
        pos = {
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit1": take_profit1,
            "take_profit2": take_profit2,
            "contracts": contracts,
            "partial_closed": False,
            "breakeven_moved": False,
            "trailing_activated": False,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        self.open_positions[symbol] = pos
        self.save_state()
        return True

    def close_position(self, symbol, exit_price, reason, fraction=1.0):
        pos = self.open_positions.pop(symbol, None)
        if not pos:
            return None
        contracts_closed = pos["contracts"] * fraction
        if fraction < 1.0 and not pos.get("partial_closed"):
            # Si es cierre parcial, actualizar la posición restante
            pos["contracts"] -= contracts_closed
            pos["partial_closed"] = True
            self.open_positions[symbol] = pos
        side = pos["side"]
        if side == "LONG":
            pnl = (exit_price - pos["entry_price"]) * contracts_closed
        else:
            pnl = (pos["entry_price"] - exit_price) * contracts_closed
        self.balance += pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        self.closed_trades.append({
            "symbol": symbol,
            "side": side,
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "contracts": contracts_closed,
            "pnl": pnl,
            "reason": reason,
            "exit_time": datetime.now(timezone.utc).isoformat()
        })
        self.save_state()
        return pnl

    def evaluate_exit_conditions(self, symbol, current_price):
        pos = self.open_positions.get(symbol)
        if not pos:
            return None
        side = pos["side"]
        sl = pos["stop_loss"]
        tp1 = pos.get("take_profit1")
        tp2 = pos.get("take_profit2")
        breakeven = pos.get("breakeven_moved")

        # ── 1. STOP LOSS (prioridad máxima) ──
        if side == "LONG":
            if current_price <= sl:
                pnl = (sl - pos["entry_price"]) * pos["contracts"]
                return {"action": "sl", "pnl": pnl}
        else:  # SHORT
            if current_price >= sl:
                pnl = (pos["entry_price"] - sl) * pos["contracts"]
                return {"action": "sl", "pnl": pnl}

        # ── 2. TAKE PROFIT 1 ──
        if tp1 is not None and not pos.get("partial_closed"):
            if side == "LONG":
                if current_price >= tp1:
                    pnl = (tp1 - pos["entry_price"]) * pos["contracts"] * 0.6
                    return {"action": "tp1", "pnl": pnl}
            else:
                if current_price <= tp1:
                    pnl = (pos["entry_price"] - tp1) * pos["contracts"] * 0.6
                    return {"action": "tp1", "pnl": pnl}

        # ── 3. TAKE PROFIT 2 ──
        if tp2 is not None and pos.get("partial_closed"):
            if side == "LONG":
                if current_price >= tp2:
                    pnl = (tp2 - pos["entry_price"]) * pos["contracts"] * 0.4
                    return {"action": "tp2", "pnl": pnl}
            else:
                if current_price <= tp2:
                    pnl = (pos["entry_price"] - tp2) * pos["contracts"] * 0.4
                    return {"action": "tp2", "pnl": pnl}

        # ── 4. BREAKEVEN (si ya se movió el SL al entry) ──
        if breakeven:
            entry = pos["entry_price"]
            if side == "LONG" and current_price <= entry:
                return {"action": "breakeven", "pnl": 0}
            if side == "SHORT" and current_price >= entry:
                return {"action": "breakeven", "pnl": 0}

        return None

    def move_stop_loss(self, symbol, new_sl):
        pos = self.open_positions.get(symbol)
        if pos:
            pos["stop_loss"] = new_sl
            pos["breakeven_moved"] = True
            self.save_state()

    def check_exits(self, symbol, current_price):
        # Método heredado (compatibilidad), redirige a la nueva versión
        return self.evaluate_exit_conditions(symbol, current_price)

    def get_open_position(self, symbol=None):
        if symbol:
            return self.open_positions.get(symbol)
        return self.open_positions

    def get_balance(self):
        return self.balance

    def get_peak_balance(self):
        return self.peak_balance

    def get_closed_trades(self):
        return self.closed_trades
