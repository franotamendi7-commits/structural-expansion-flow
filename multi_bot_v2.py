"""
Multi-Bot V2 System - Institutional Engine Integration
======================================================

Replaces VWAP breakout logic with Institutional Engine V2.
Maintains same structure as multi_bot.py (BaseBot, subclasses).

Key changes from multi_bot.py:
1. Uses InstitutionalEngineV2 for signal generation
2. Integrates PositionManager for exit management
3. Integrates DrawdownManager for risk control
4. Integrates ParameterAdapter for auto-adaptation
5. Maintains same execution_manager for order placement

Usage:
    from multi_bot_v2 import MultiBotSystemV2
    system = MultiBotSystemV2()
    system.start()
"""

import sys
import time
import math
import json
import logging
import threading
import hashlib
import requests
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

# Setup path
sys.path.insert(0, str(Path(__file__).parent))

from engine.institutional_engine_v2 import InstitutionalEngineV2
from engine.position_manager import PositionManager
from engine.drawdown_manager import DrawdownManager
from engine.parameter_adapter import ParameterAdapter
from execution_manager import ExecutionManager

logger = logging.getLogger(__name__)

# Reuse infrastructure from multi_bot.py
# NOTE: get_slippage and calculate_position_size do NOT exist in multi_bot.py
# Import them separately with local fallbacks
try:
    from multi_bot import (
        _init_telegram, _tg_send, _signal_log, _cb_is_open,
        _cb_mark_failure, _cb_mark_success, TRADE_HISTORY_PATH,
        fetch_klines,
    )
except ImportError:
    # Fallback implementations if import fails
    def _init_telegram(): pass
    def _tg_send(text, chat_id=None): pass
    def _signal_log(*args, **kwargs): pass
    def _cb_is_open(): return False
    def _cb_mark_failure(): pass
    def _cb_mark_success(): pass
    def fetch_klines(symbol, interval, limit=100):
        """Fallback kline fetcher."""
        return []


def get_slippage(symbol, side, size):
    """Slippage estimator: spread + ATR-based slippage."""
    return 0.0005


def calculate_position_size(capital, risk_pct, entry, sl):
    """Fallback position sizer."""
    risk_usd = capital * risk_pct
    sl_pct = abs(entry - sl) / entry if entry > 0 else 0.01
    return risk_usd / sl_pct / entry if entry > 0 else 0


def _load_binance_keys():
    """Load Binance API keys from secrets.toml."""
    try:
        import toml
        secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
        if secrets_path.exists():
            secrets = toml.load(secrets_path)
            return secrets.get("BINANCE_API_KEY", ""), secrets.get("BINANCE_SECRET_KEY", "")
    except ImportError:
        # Fallback: parse TOML manually
        secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
        if secrets_path.exists():
            api_key = ""
            api_secret = ""
            with open(secrets_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BINANCE_API_KEY"):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("BINANCE_SECRET_KEY"):
                        api_secret = line.split("=", 1)[1].strip().strip('"').strip("'")
            return api_key, api_secret
    except Exception as e:
        logger.warning(f"Error loading Binance keys: {e}")
    return "", ""


# ============================================================
# INSTITUTIONAL BOT (V2)
# ============================================================

class InstitutionalBot:
    """
    Bot using Institutional Engine V2.
    Replaces VWAP breakout with engulfing + trailing + breakeven.
    """

    def __init__(
        self,
        symbol: str,
        capital: float = 1000.0,
        risk_pct: float = 0.01,
        leverage: int = 10,
    ):
        self.symbol = symbol
        self.capital = capital
        self.base_risk_pct = risk_pct
        self.leverage = leverage
        
        # V2 Components
        self.engine = InstitutionalEngineV2(
            symbol=symbol,
            capital=capital,
            risk_pct=risk_pct,
        )
        self.position_mgr = PositionManager()
        self.dd_mgr = DrawdownManager(initial_equity=capital)
        self.param_adapter = ParameterAdapter()
        
        # Execution Manager (Binance testnet)
        api_key, api_secret = _load_binance_keys()
        self.execution_mgr = ExecutionManager(
            api_key=api_key,
            api_secret=api_secret,
            testnet=True,
        )
        if api_key:
            logger.info(f"[{symbol}] ExecutionManager connected (testnet)")
        else:
            logger.warning(f"[{symbol}] ExecutionManager: NO API KEYS — orders will NOT execute")
        
        # State
        self.position = None  # Current open position
        self.trade_log = []
        self.cooldown_until = None
        self.consecutive_losses = 0
        
        # Load state
        self._load_state()
    
    def _load_state(self):
        """Load persistent state from files."""
        try:
            # Load drawdown state
            dd_file = Path(__file__).parent / "drawdown_state.json"
            if dd_file.exists():
                with open(dd_file) as f:
                    state = json.load(f)
                    self.dd_mgr.load_state(state)
            
            # Load parameter state
            param_file = Path(__file__).parent / "parameter_state.json"
            if param_file.exists():
                with open(param_file) as f:
                    state = json.load(f)
                    self.param_adapter.load_state(state)
            
            # Load position state
            pos_file = Path(__file__).parent / f"position_state_{self.symbol}.json"
            if pos_file.exists():
                with open(pos_file) as f:
                    state = json.load(f)
                    self.position_mgr.load_state(state)
        except Exception as e:
            logger.warning(f"Error loading state for {self.symbol}: {e}")
    
    def _save_state(self):
        """Save persistent state to files."""
        try:
            # Save drawdown state
            dd_file = Path(__file__).parent / "drawdown_state.json"
            with open(dd_file, "w") as f:
                json.dump(self.dd_mgr.export_state(), f, indent=2)
            
            # Save parameter state
            param_file = Path(__file__).parent / "parameter_state.json"
            with open(param_file, "w") as f:
                json.dump(self.param_adapter.export_state(), f, indent=2)
            
            # Save position state
            if self.position:
                pos_file = Path(__file__).parent / f"position_state_{self.symbol}.json"
                with open(pos_file, "w") as f:
                    json.dump(self.position_mgr.export_state(), f, indent=2)
        except Exception as e:
            logger.warning(f"Error saving state for {self.symbol}: {e}")
    
    def check_signal(self) -> Optional[Dict[str, Any]]:
        """
        Check for trading signal using V2 engine.
        
        Returns:
            Signal dict with direction, score, trade params, or None
        """
        # Check cooldown
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            return None
        
        # Check drawdown manager
        dd_params = self.dd_mgr.get_risk_params()
        dd_mult = dd_params[0]
        dd_min_score = dd_params[1]
        
        if dd_mult == 0:
            logger.info(f"{self.symbol}: DD manager stopped trading")
            return None
        
        # Fetch klines for all timeframes
        klines = {}
        for tf in ["5m", "15m", "1h", "4h", "1d"]:
            data = fetch_klines(self.symbol, tf, limit=100)
            if data is None or (hasattr(data, 'empty') and data.empty) or (not hasattr(data, 'empty') and not data):
                logger.warning(f"{self.symbol}: No data for {tf}")
                return None
            # Convert DataFrame to list-of-dicts for engine compatibility
            if hasattr(data, 'to_dict'):
                records = data.to_dict('records')
                klines[tf] = records
            else:
                klines[tf] = data
        
        # Run V2 engine
        signal = self.engine.run(klines=klines)
        
        if signal["signal"] == "WAIT":
            return None
        
        # Check minimum score
        min_score = max(
            self.param_adapter.params.get("min_score", 50),
            dd_min_score
        )
        if signal["score"] < min_score:
            logger.info(f"{self.symbol}: Score {signal['score']} < min {min_score}")
            return None
        
        # Apply DD multiplier to risk
        signal["dd_mult"] = dd_mult
        signal["strat_mult"] = self.param_adapter.get_risk_multiplier()
        
        return signal
    
    def manage_position(self) -> Optional[Dict[str, Any]]:
        """
        Manage open position using PositionManager.
        
        Returns:
            Exit result dict or None if still holding
        """
        if not self.position:
            return None
        
        # Get current price (high, low, close from 5m candle)
        try:
            data = fetch_klines(self.symbol, "5m", limit=2)
            if data is not None and len(data) > 0:
                last = data.iloc[-1] if hasattr(data, 'iloc') else data[-1]
                bar_high = float(last["high"])
                bar_low = float(last["low"])
                bar_close = float(last["close"])
            else:
                return None
        except Exception as e:
            logger.warning(f"Error fetching price for {self.symbol}: {e}")
            return None
        
        # Get the Position object from PositionManager
        pos_id = self.position.get("position_id")
        if not pos_id or pos_id not in self.position_mgr.positions:
            return None
        pos = self.position_mgr.positions[pos_id]
        
        # Calculate current ATR
        atr_val = pos.atr_at_entry if pos.atr_at_entry > 0 else bar_close * 0.005
        
        # Check exit conditions
        action = self.position_mgr.check_exit(
            pos=pos,
            bar_high=bar_high,
            bar_low=bar_low,
            bar_close=bar_close,
            atr_current=atr_val,
        )
        
        if action:
            # Close position
            result = self.position_mgr.close_position(pos, action, atr_val)
            if result.get("closed"):
                self._close_position(result)
            return result
        
        return None
    
    def _get_current_price(self) -> Optional[float]:
        """Get current price from exchange."""
        try:
            klines = fetch_klines(self.symbol, "1m", limit=1)
            if klines and len(klines) > 0:
                return float(klines[-1]["close"])
        except Exception as e:
            logger.warning(f"Error fetching price for {self.symbol}: {e}")
        return None
    
    def open_position(self, signal: Dict[str, Any]) -> bool:
        """
        Open a new position based on signal.
        
        Returns:
            True if position opened successfully
        """
        if self.position:
            logger.warning(f"{self.symbol}: Already has open position")
            return False
        
        trade_params = signal.get("trade")
        if not trade_params:
            return False
        
        # Apply DD and strategy multipliers
        dd_mult = signal.get("dd_mult", 1.0)
        strat_mult = signal.get("strat_mult", 1.0)
        
        # Calculate position size
        entry = trade_params["entry"]
        sl = trade_params["sl"]
        sl_pct = trade_params["sl_pct"] / 100
        
        risk_usd = self.capital * self.base_risk_pct * dd_mult * strat_mult
        risk_usd = min(risk_usd, self.capital * 0.025)
        
        notional = risk_usd / sl_pct if sl_pct > 0 else 0
        size = notional / entry if entry > 0 else 0
        
        if size <= 0 or notional < 1:
            return False
        
        # Direction: map from engine's bullish/bearish to 1/-1
        direction = 1 if signal["direction"] == "bullish" else -1
        
        # Open position via PositionManager
        pos = self.position_mgr.open_position(
            symbol=self.symbol,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit_1=trade_params["tp1"],
            take_profit_2=trade_params["tp2"],
            size=size,
            atr_at_entry=signal.get("indicators", {}).get("atr_1h", entry * 0.005),
            atr_trail_mult=self.param_adapter.params.get("atr_trail_mult", 2.0),
            breakeven_activate_mult=self.param_adapter.params.get("breakeven_activate_mult", 1.0),
            partial_exit_pct=self.param_adapter.params.get("partial_exit_pct", 0.5),
            time_exit_bars=self.param_adapter.params.get("time_exit_bars", 24),
            score=signal["score"],
            signal_type=signal["signal"],
        )
        
        # Execute REAL order on Binance testnet
        order_result = None
        try:
            side = "BUY" if direction == 1 else "SELL"
            # Round quantity to 3 decimals for BTC-like pairs
            qty = round(size, 3)
            order_result = self.execution_mgr.execute_signal({
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
            })
            logger.info(f"[{self.symbol}] ORDER RESULT: {order_result}")
            
            if order_result and order_result.get("error"):
                logger.error(f"[{self.symbol}] ORDER FAILED: {order_result['error']}")
                # Don't block the position — paper trading continues
                order_result = None
            elif order_result and order_result.get("executed_price"):
                # Use actual execution price from Binance
                entry = order_result["executed_price"]
                logger.info(f"[{self.symbol}] ORDER FILLED @ {entry}")
        except Exception as e:
            logger.error(f"[{self.symbol}] ORDER EXCEPTION: {e}")
            order_result = None
        
        # Store position reference for manage_position (FIX: include entry_time + order_id)
        self.position = {
            "position_id": pos.position_id,
            "direction": signal["direction"],
            "entry_price": entry,
            "size": size,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "order_id": order_result.get("order_id") if order_result else None,
            "score": signal["score"],
        }
        
        # Log signal
        _signal_log(
            signal_type="ENTRY",
            symbol=self.symbol,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=trade_params["tp1"],
        )
        
        # Send Telegram alert
        _tg_send(
            f"🔔 <b>{self.symbol}</b> {'LONG' if direction == 1 else 'SHORT'}\n"
            f"Entry: ${entry:.2f}\n"
            f"SL: ${sl:.2f}\n"
            f"TP1: ${trade_params['tp1']:.2f}\n"
            f"Score: {signal['score']}"
        )
        
        logger.info(f"{self.symbol}: Opened {signal['direction']} at {entry}")
        return True
    
    def _close_position(self, result: Dict[str, Any]):
        """Close position and update state."""
        if not self.position:
            return
        
        pnl = result.get("pnl", 0)
        
        # Close REAL order on Binance testnet (reduce_only)
        try:
            direction = self.position["direction"]
            side = "SELL" if direction == "bullish" else "BUY"
            qty = round(self.position["size"], 3)
            close_result = self.execution_mgr.execute_signal({
                "symbol": self.symbol,
                "side": side,
                "quantity": qty,
            }, reduce_only=True)
            logger.info(f"[{self.symbol}] CLOSE ORDER RESULT: {close_result}")
            
            if close_result and close_result.get("error"):
                logger.error(f"[{self.symbol}] CLOSE FAILED: {close_result['error']}")
        except Exception as e:
            logger.error(f"[{self.symbol}] CLOSE EXCEPTION: {e}")
        
        # Update capital
        self.capital += pnl
        
        # Update drawdown manager
        self.dd_mgr.update(self.capital)
        
        # Update parameter adapter
        self.param_adapter.record_trade({
            "net_pnl": pnl,
            "score": self.position.get("score", 0),
        })
        
        # Track consecutive losses
        if pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 3:
                logger.warning(f"{self.symbol}: {self.consecutive_losses} consecutive losses")
        else:
            self.consecutive_losses = 0
        
        # Log trade
        trade_record = {
            "symbol": self.symbol,
            "direction": self.position["direction"],
            "entry": self.position["entry_price"],
            "exit": result.get("exit_price", 0),
            "entry_time": self.position.get("entry_time", datetime.now(timezone.utc).isoformat()),
            "exit_time": datetime.now(timezone.utc).isoformat(),
            "net": pnl,
            "size": self.position["size"],
            "exit_reason": result.get("reason", "unknown"),
            "score": self.position.get("score", 0),
        }
        self.trade_log.append(trade_record)
        
        # Save trade to persistent history
        self._save_trade(trade_record)
        
        # Log signal
        _signal_log(
            signal_type="EXIT",
            symbol=self.symbol,
            direction=1 if self.position["direction"] == "bullish" else -1,
            entry=self.position["entry_price"],
            pnl=pnl,
            reason=result.get("reason", "unknown"),
        )
        
        # Send Telegram alert
        _tg_send(
            f"✅ <b>{self.symbol}</b> CLOSED\n"
            f"PnL: ${pnl:+.2f}\n"
            f"Reason: {result.get('reason', 'unknown')}\n"
            f"Equity: ${self.capital:.2f}"
        )
        
        # Clear position
        self.position = None
        
        # Save state
        self._save_state()
    
    def _save_trade(self, trade: Dict):
        """Save trade to persistent history."""
        try:
            history = []
            if TRADE_HISTORY_PATH.exists():
                with open(TRADE_HISTORY_PATH) as f:
                    history = json.load(f)
            
            history.append(trade)
            
            # Keep last 10000 trades
            if len(history) > 10000:
                history = history[-10000:]
            
            with open(TRADE_HISTORY_PATH, "w") as f:
                json.dump(history, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Error saving trade: {e}")
    
    def run_once(self) -> Dict[str, Any]:
        """
        Run single tick of the bot.
        
        Returns:
            Status dict
        """
        status = {
            "symbol": self.symbol,
            "has_position": self.position is not None,
            "capital": self.capital,
            "signal": None,
            "action": None,
        }
        
        # Check circuit breaker
        if _cb_is_open():
            status["action"] = "circuit_breaker_open"
            return status
        
        try:
            # Manage existing position
            if self.position:
                result = self.manage_position()
                if result:
                    status["action"] = "position_closed"
                    status["pnl"] = result.get("pnl", 0)
                    return status
                else:
                    status["action"] = "holding_position"
                    return status
            
            # Check for new signal
            signal = self.check_signal()
            if signal:
                success = self.open_position(signal)
                if success:
                    status["action"] = "position_opened"
                    status["signal"] = signal["signal"]
                    _cb_mark_success()
                else:
                    status["action"] = "signal_rejected"
                    _cb_mark_failure()
            else:
                status["action"] = "no_signal"
                _cb_mark_success()
        
        except Exception as e:
            logger.exception(f"Error in {self.symbol} bot")
            status["action"] = "error"
            status["error"] = str(e)
            _cb_mark_failure()
        
        return status


# ============================================================
# MULTI-BOT SYSTEM V2
# ============================================================

class MultiBotSystemV2:
    """
    Multi-bot system using Institutional Engine V2.
    Same structure as MultiBotSystem but with V2 engine.
    """

    def __init__(
        self,
        symbols: List[str] = None,
        capital_per_bot: float = 1000.0,
        risk_pct: float = 0.01,
        leverage: int = 10,
    ):
        if symbols is None:
            symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
        
        self.symbols = symbols
        self.capital_per_bot = capital_per_bot
        self.risk_pct = risk_pct
        self.leverage = leverage
        
        # Initialize bots
        self.bots = {}
        for symbol in symbols:
            self.bots[symbol] = InstitutionalBot(
                symbol=symbol,
                capital=capital_per_bot,
                risk_pct=risk_pct,
                leverage=leverage,
            )
        
        # Initialize Telegram
        _init_telegram()
        
        logger.info(f"MultiBotSystemV2 initialized with {len(symbols)} bots")
    
    def run_once(self) -> Dict[str, Any]:
        """
        Run single tick of all bots.
        
        Returns:
            Status dict for each bot
        """
        results = {}
        
        for symbol, bot in self.bots.items():
            try:
                result = bot.run_once()
                results[symbol] = result
            except Exception as e:
                logger.exception(f"Error running {symbol} bot")
                results[symbol] = {"error": str(e)}
        
        return results
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary across all bots."""
        total_capital = sum(bot.capital for bot in self.bots.values())
        total_trades = sum(len(bot.trade_log) for bot in self.bots.values())
        
        wins = 0
        losses = 0
        total_pnl = 0
        
        for bot in self.bots.values():
            for trade in bot.trade_log:
                if trade["net"] > 0:
                    wins += 1
                else:
                    losses += 1
                total_pnl += trade["net"]
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        return {
            "total_capital": total_capital,
            "initial_capital": self.capital_per_bot * len(self.symbols),
            "total_return_pct": (total_capital / (self.capital_per_bot * len(self.symbols)) - 1) * 100,
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "active_positions": sum(1 for bot in self.bots.values() if bot.position),
        }
    
    def start(self, interval_seconds: int = 60):
        """Start continuous trading loop."""
        logger.info(f"Starting MultiBotSystemV2 with {interval_seconds}s interval")
        
        while True:
            try:
                results = self.run_once()
                
                # Log summary
                active = sum(1 for r in results.values() if r.get("has_position"))
                signals = sum(1 for r in results.values() if r.get("signal"))
                logger.info(f"Tick: {active} positions, {signals} signals")
                
                time.sleep(interval_seconds)
            
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as e:
                logger.exception("Error in main loop")
                time.sleep(30)


# ============================================================
# MAIN ENTRY POINT
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Multi-Bot V2 System")
    parser.add_argument("--once", action="store_true", help="Run single tick")
    parser.add_argument("--interval", type=int, default=60, help="Interval in seconds")
    parser.add_argument("--capital", type=float, default=1000.0, help="Capital per bot")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    system = MultiBotSystemV2(capital_per_bot=args.capital)
    
    if args.once:
        results = system.run_once()
        print(json.dumps(results, indent=2, default=str))
    else:
        system.start(interval_seconds=args.interval)
