#!/usr/bin/env python3
"""
Micro-servicio de vigilancia de Stop Loss / Take Profit.
Lee paper_state.json cada 100 ms, verifica SL/TP, cierra posiciones en PaperTrader
y envía órdenes de mercado reales a Binance Futures Testnet.
"""
import time
import json
import os
from datetime import datetime
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

# ─── CONFIGURACIÓN ─────────────────────────────────────────────
STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"
CHECK_INTERVAL = 0.1  # segundos (100 ms)

# Credenciales de Binance Futures Testnet (hardcodeadas)
API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"

def log(msg):
    """Imprime un mensaje con timestamp."""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def run():
    log("Stop Monitor iniciado")
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)

    while True:
        # Leer el archivo de estado
        if not os.path.exists(STATE_FILE):
            log("paper_state.json no encontrado. Esperando...")
            time.sleep(CHECK_INTERVAL)
            continue

        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            log(f"Error al leer paper_state.json: {e}")
            time.sleep(CHECK_INTERVAL)
            continue

        open_positions = state.get("open_positions", {})
        if not open_positions:
            time.sleep(CHECK_INTERVAL)
            continue

        try:
            trader = PaperTrader(state_file=STATE_FILE)
        except Exception as e:
            log(f"Error al instanciar PaperTrader: {e}")
            time.sleep(CHECK_INTERVAL)
            continue

        for symbol, pos in list(open_positions.items()):
            try:
                current_price = get_ticker(symbol)
                if current_price is None or current_price == 0:
                    log(f"No se pudo obtener precio para {symbol}")
                    continue

                exit_signal = trader.evaluate_exit_conditions(symbol, current_price)
                if exit_signal is not None:
                    action = exit_signal.get("action", "manual")
                    pnl = exit_signal.get("pnl", 0.0)
                    log(f"Activado {action} en {symbol} a ${current_price:.2f} (PnL estimado: ${pnl:.2f}).")

                    # Determinar fracción de cierre
                    if action == "tp1":
                        close_percent = 0.6
                        reason = "take_profit"
                    elif action == "tp2":
                        close_percent = 1.0
                        reason = "take_profit"
                    elif action == "sl":
                        close_percent = 1.0
                        reason = "stop_loss"
                    elif action == "breakeven":
                        close_percent = 1.0
                        reason = "breakeven"
                    else:
                        close_percent = 1.0
                        reason = action

                    # Cantidad a cerrar
                    contracts_total = pos.get("contracts", 0)
                    contracts_to_close = contracts_total * close_percent

                    # Lado opuesto para la orden de mercado real
                    pos_side = pos.get("side", "LONG").upper()
                    close_side = "SELL" if pos_side == "LONG" else "BUY" if pos_side == "SHORT" else None

                    if close_side and contracts_to_close > 0:
                        log(f"Enviando orden real {close_side} {contracts_to_close} contratos de {symbol}")
                        real_order = exec_mgr.execute_signal({
                            "symbol": symbol,
                            "side": close_side,
                            "quantity": contracts_to_close
                        })
                        if real_order.get("error"):
                            log(f"Error al enviar orden real: {real_order['error']}")
                        else:
                            log(f"Orden real ejecutada: ID={real_order.get('order_id')}, precio={real_order.get('executed_price')}")

                    # Cerrar en PaperTrader
                    trader.close_position(
                        symbol,
                        exit_price=current_price,
                        reason=reason,
                        fraction=close_percent
                    )
                    log(f"Posición cerrada en PaperTrader: {symbol} ({reason}, {close_percent*100:.0f}%)")

                    # Si fue tp1, mover el SL a breakeven
                    if action == "tp1":
                        entry_price = pos.get("entry_price")
                        if entry_price:
                            trader.move_stop_loss(symbol, entry_price)
                            log(f"SL movido a breakeven: {symbol} → ${entry_price:.2f}")
            except Exception as e:
                log(f"Error procesando {symbol}: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    run()
