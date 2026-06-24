import json
import os
import datetime
import time
import threading
from typing import List, Optional

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
        self.load_state()

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
            print(f"[PaperTrader] Error saving state: {e}")

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
                for pos in self.open_positions.values():
                    for key in ["entry_price", "current_price", "stop_loss", "take_profit", "take_profit2", "contracts", "risk_usd", "pnl"]:
                        if key in pos and pos[key] is not None:
                            pos[key] = float(pos[key])
                        elif key in pos and pos[key] is None:
                            pos[key] = 0.0
                for trade in self.closed_trades:
                    for key in ["entry_price", "exit_price", "pnl"]:
                        if key in trade and trade[key] is not None:
                            trade[key] = float(trade[key])
                        elif key in trade and trade[key] is None:
                            trade[key] = 0.0
                today = datetime.date.today()
                if self.last_day != today:
                    self.daily_pnl = 0.0
                    self.last_day = today
            except Exception as e:
                print(f"[PaperTrader] Error loading state: {e}, using defaults")
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

    def open_trade(self, signal):
        symbol = signal.get("symbol")
        if not symbol or symbol in self.open_positions:
            return False
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
            "entry_time": datetime.datetime.now().isoformat(),
            "breakeven_activated": False,
            "tp1_hit": False
        }
        self.open_positions[symbol] = position
        self.save_state()
        return True

    def close_position(self, symbol, exit_price=None, reason="manual", close_percent=1.0):
        if symbol not in self.open_positions:
            return 0.0
        pos = self.open_positions[symbol]
        if exit_price is None:
            exit_price = pos["current_price"]
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
        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.daily_pnl += pnl

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance

        if close_percent < 1.0:
            remaining = 1.0 - close_percent
            pos["contracts"] *= remaining
            pos["risk_usd"] *= remaining
        else:
            del self.open_positions[symbol]

        self.save_state()
        return pnl

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
        # Verificar condiciones de salida inmediatamente
        self.check_exits(symbol, current_price)
        self.save_state()

    def move_stop_loss(self, symbol, new_sl):
        if symbol in self.open_positions:
            self.open_positions[symbol]['stop_loss'] = new_sl
            self.save_state()

    def set_breakeven_activated(self, symbol, value):
        if symbol in self.open_positions:
            self.open_positions[symbol]['breakeven_activated'] = value
            self.save_state()

    def check_exits(self, symbol, current_price, telegram_callback=None, telegram_token=None, telegram_chat_id=None):
        if symbol not in self.open_positions:
            return None
        pos = self.open_positions[symbol]
        entry = pos["entry_price"]
        sl = pos["stop_loss"]
        tp1 = pos["take_profit"]
        tp2 = pos.get("take_profit2")
        side = pos["side"]
        breakeven_done = pos.get("breakeven_activated", False)
        partial_done = pos.get("partial_tp1_done", False)

        # Breakeven (50% hacia TP1)
        if not breakeven_done and not partial_done and tp1 and tp1 != entry:
            if side == "LONG":
                distance = tp1 - entry
                half = entry + distance * 0.5
                if current_price >= half:
                    self.move_stop_loss(symbol, entry)
                    self.set_breakeven_activated(symbol, True)
                    if telegram_callback:
                        telegram_callback(
                            telegram_token, telegram_chat_id,
                            symbol=symbol,
                            signal="BREAKEVEN",
                            trade={"entry": entry, "sl": entry},
                            score=0,
                            explanation="🔒 SL movido a breakeven"
                        )
            else:
                distance = entry - tp1
                half = entry - distance * 0.5
                if current_price <= half:
                    self.move_stop_loss(symbol, entry)
                    self.set_breakeven_activated(symbol, True)
                    if telegram_callback:
                        telegram_callback(
                            telegram_token, telegram_chat_id,
                            symbol=symbol,
                            signal="BREAKEVEN",
                            trade={"entry": entry, "sl": entry},
                            score=0,
                            explanation="🔒 SL movido a breakeven"
                        )

        # TP1 parcial (60%)
        if not partial_done and tp1:
            if (side == "LONG" and current_price >= tp1) or (side == "SHORT" and current_price <= tp1):
                pnl_partial = self.close_position(symbol, exit_price=tp1, reason="take_profit", close_percent=1.0)
                if symbol in self.open_positions:
                    pos = self.open_positions[symbol]
                    pos["partial_tp1_done"] = True
                    if not pos.get("breakeven_activated", False):
                        self.move_stop_loss(symbol, entry)
                        self.set_breakeven_activated(symbol, True)
                    self.save_state()
                return {"action": "tp1_partial", "pnl": pnl_partial, "symbol": symbol, "exit_price": tp1, "entry": entry}

        # TP2 final (40%)
        if partial_done and tp2:
            if (side == "LONG" and current_price >= tp2) or (side == "SHORT" and current_price <= tp2):
                partial_pnl = sum(t["pnl"] for t in self.closed_trades
                                  if t["symbol"] == symbol and t["reason"] == "take_profit_partial")
                pnl_final = self.close_position(symbol, exit_price=tp2, reason="take_profit_final", close_percent=1.0)
                total_pnl = partial_pnl + pnl_final
                return {"action": "tp2_final", "pnl": pnl_final, "total_pnl": total_pnl,
                        "symbol": symbol, "exit_price": tp2, "entry": entry}

        # SL
        if (side == "LONG" and current_price <= sl) or (side == "SHORT" and current_price >= sl):
            reason = "stop_loss_after_partial" if partial_done else "stop_loss"
            pnl = self.close_position(symbol, exit_price=sl, reason=reason, close_percent=1.0)
            return {"action": "sl", "pnl": pnl, "symbol": symbol, "exit_price": sl, "entry": entry}

        return None

    def evaluate_exit_conditions(self, symbol, current_price):
        if symbol not in self.open_positions:
            return None
        pos = self.open_positions[symbol]
        entry = pos["entry_price"]
        sl = pos["stop_loss"]
        tp1 = pos["take_profit"]
        tp2 = pos.get("take_profit2")
        side = pos["side"]
        contracts = pos["contracts"]

        def calc_pnl(price):
            if side == "LONG":
                return (price - entry) * contracts
            else:
                return (entry - price) * contracts

        if (side == "LONG" and current_price <= sl) or (side == "SHORT" and current_price >= sl):
            return {"action": "sl", "pnl": calc_pnl(sl), "exit_price": sl, "entry": entry}

        if tp1 is not None:
            if (side == "LONG" and current_price >= tp1) or (side == "SHORT" and current_price <= tp1):
                return {"action": "tp1", "pnl": calc_pnl(tp1), "exit_price": tp1, "entry": entry}

        if tp2 is not None:
            if (side == "LONG" and current_price >= tp2) or (side == "SHORT" and current_price <= tp2):
                return {"action": "tp2", "pnl": calc_pnl(tp2), "exit_price": tp2, "entry": entry}

        return None

    def start_price_monitor(self, symbols: List[str], interval: int = 5, feed=None):
        if self.price_monitor_running:
            return
        if feed is None:
            print("[PaperTrader] Error: feed must be provided.")
            return
        self.feed = feed
        self.price_monitor_running = True

        def monitor_loop():
            print(f"[PaperTrader] Price monitor started for symbols {symbols}, interval={interval}s")
            while self.price_monitor_running:
                try:
                    for sym in symbols:
                        price = self.feed.get_current_price(sym)
                        if price is not None and price > 0:
                            self.update_position(sym, price)
                    time.sleep(interval)
                except Exception as e:
                    print(f"[PaperTrader] Monitor error: {e}")
                    time.sleep(interval)
            print("[PaperTrader] Price monitor stopped.")

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
        if self.consecutive_losses >= self.max_consecutive_losses:
            return True
        if abs(self.daily_pnl) >= self.balance * self.max_daily_loss_pct:
            return True
        return False

    def get_closed_trades(self):
        return self.closed_trades
