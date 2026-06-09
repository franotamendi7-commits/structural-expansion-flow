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
WAIT_SECONDS = 0.1  # 100 ms

# Credenciales de Binance Futures Testnet (hardcodeadas)
API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"

log_file = None
logs_dir = "/Users/franciscootamendi/ai-agents-v3/logs"
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir, exist_ok=True)
log_file = open(os.path.join(logs_dir, "stop_monitor.log"), "a")

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if log_file:
        log_file.write(f"[{timestamp}] {msg}\n")
        log_file.flush()
    print(f"[{timestamp}] {msg}")

def main():
    trader = PaperTrader(state_file=PAPER_STATE_FILE)
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)
    pm = PortfolioManager()

    log("Stop Monitor iniciado. Ciclo cada 100ms.")
    
    while True:
        try:
            # Crear una nueva instancia para leer el estado más reciente
            trader = PaperTrader(state_file=PAPER_STATE_FILE)
            open_positions = trader.open_positions

            for symbol, pos in open_positions.items():
                current_price = get_ticker(symbol)
                if current_price is None or current_price <= 0:
                    continue

                # Actualizar precio en el trader (para PnL flotante)
                trader.update_position(symbol, current_price)

                # Evaluar condiciones de salida (sin modificar estado)
                exit_signal = trader.evaluate_exit_conditions(symbol, current_price)
                if exit_signal is None:
                    continue

                action = exit_signal['action']
                exit_price = exit_signal['exit_price']

                # Determinar fracción y razón según la acción
                if action == 'tp1':
                    close_percent = 0.6
                    reason = "take_profit"
                elif action == 'tp2':
                    close_percent = 1.0   # el 40% restante se cierra en una sola operación
                    reason = "take_profit_tp2"
                elif action in ('sl', 'breakeven'):
                    close_percent = 1.0
                    reason = "stop_loss"
                else:
                    close_percent = 1.0
                    reason = action

                # Cantidad a cerrar
                contracts_total = pos.get("contracts", 0)
                contracts_to_close = contracts_total * close_percent

                # Lado de la orden de cierre (inverso a la posición)
                pos_side = pos.get("side", "LONG").upper()
                close_side = "SELL" if pos_side == "LONG" else "BUY"

                # 1. Enviar orden real a Binance Testnet
                if close_side and contracts_to_close > 0:
                    log(f"Enviando orden real {close_side} {contracts_to_close} contratos de {symbol} a precio de mercado")
                    real_order = exec_mgr.execute_signal({
                        "symbol": symbol,
                        "side": close_side,
                        "quantity": contracts_to_close
                    })
                    if real_order.get("error"):
                        log(f"Error en orden real: {real_order['error']}")
                    else:
                        log(f"Orden real ejecutada: ID={real_order.get('order_id')}, precio={real_order.get('executed_price')}")

                # 2. Cerrar en PaperTrader (actualiza balance simulado y estado)
                pnl_closed = trader.close_position(symbol, exit_price=exit_price, reason=reason, fraction=close_percent)
                log(f"Posición cerrada en PaperTrader: {symbol} ({reason}, {close_percent*100:.0f}%) PnL: ${pnl_closed:.2f}")

                # 3. Distribuir el PnL entre los inversores
                if pnl_closed != 0:
                    try:
                        pm.actualizar_balances(pnl_closed)
                        log(f"PortfolioManager actualizado: PnL global ${pnl_closed:.2f}")
                    except Exception as e:
                        log(f"Error al actualizar PortfolioManager: {e}")

                # Si fue TP1, mover el SL a breakeven (opcional, el PaperTrader ya lo hace si se usa check_exits,
                # pero aquí lo hacemos explícitamente para mantener consistencia)
                if action == 'tp1':
                    entry_price = pos.get("entry_price")
                    if entry_price:
                        trader.move_stop_loss(symbol, entry_price)
                        log(f"SL movido a breakeven para {symbol} → ${entry_price:.2f}")

            time.sleep(WAIT_SECONDS)

        except KeyboardInterrupt:
            log("Monitor detenido por el usuario.")
            break
        except Exception as e:
            log(f"Error en bucle principal: {e}")
            time.sleep(WAIT_SECONDS)

if __name__ == "__main__":
    main()
