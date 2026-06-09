#!/usr/bin/env python3
import time
import json
import os
from datetime import datetime
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager
from portfolio_manager import PortfolioManager

PAPER_STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"
WAIT_SECONDS = 0.1

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def main():
    # Credenciales
    API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
    API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)
    pm = PortfolioManager()

    log("Stop Monitor iniciado. Ciclo cada 100ms.")

    while True:
        try:
            # Leer el estado actual del archivo
            if not os.path.exists(PAPER_STATE_FILE):
                time.sleep(WAIT_SECONDS)
                continue

            with open(PAPER_STATE_FILE, 'r') as f:
                state = json.load(f)

            open_positions = state.get('open_positions', {})

            for symbol, pos in open_positions.items():
                current_price = get_ticker(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # Crear un trader temporal para esta iteración (cargando el estado)
                trader = PaperTrader(state_file=PAPER_STATE_FILE)
                # Actualizar precio en la posición
                trader.update_position(symbol, current_price)

                # Evaluar condiciones
                exit_signal = trader.evaluate_exit_conditions(symbol, current_price)
                if exit_signal is None:
                    continue

                action = exit_signal['action']
                exit_price = exit_signal['exit_price']

                if action == 'tp1':
                    log(f"TP1 en {symbol} a {exit_price:.2f}")
                    pnl = trader.close_position(symbol, exit_price=exit_price, reason="take_profit")
                    if pnl != 0:
                        pm.actualizar_balances(pnl)
                        log(f"Balance actualizado: +{pnl:.2f}")

                elif action == 'tp2':
                    log(f"TP2 en {symbol} a {exit_price:.2f}")
                    pnl = trader.close_position(symbol, exit_price=exit_price, reason="take_profit_tp2")
                    if pnl != 0:
                        pm.actualizar_balances(pnl)
                        log(f"Balance actualizado: +{pnl:.2f}")

                elif action == 'sl':
                    log(f"SL en {symbol} a {exit_price:.2f}")
                    pnl = trader.close_position(symbol, exit_price=exit_price, reason="stop_loss")
                    if pnl != 0:
                        pm.actualizar_balances(pnl)
                        log(f"Balance actualizado: {pnl:.2f}")

            time.sleep(WAIT_SECONDS)

        except KeyboardInterrupt:
            log("Monitor detenido.")
            break
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(WAIT_SECONDS)

if __name__ == "__main__":
    main()
