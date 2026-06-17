#!/usr/bin/env python3
import time
import json
import os
import csv
from datetime import datetime
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

PAPER_STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"
AUDIT_FILE = "audit_log.csv"
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

# ── AUDITOR DE SALIDA ──
AUDIT_FIELDS = ["trade_id","timestamp_entry","symbol","side","score",
                "entry","sl","tp1","tp2","fib_label","phase",
                "ci","wr","st","veto","explanation_raw","exit_reason",
                "exit_price","pnl_final","duration_min"]

def log_signal_closed(trade_id, exit_reason, exit_price, pnl, duration_min):
    """Actualiza la fila del trade_id con los datos de salida."""
    if not os.path.exists(AUDIT_FILE):
        return
    rows = []
    with open(AUDIT_FILE, "r") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for row in rows:
        if row['trade_id'] == trade_id:
            row['exit_reason'] = exit_reason
            row['exit_price'] = str(exit_price)
            row['pnl_final'] = str(pnl)
            row['duration_min'] = str(round(duration_min, 2))
            break
    with open(AUDIT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

def main():
    trader = PaperTrader(state_file=PAPER_STATE_FILE)
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)

    log("Stop Monitor iniciado. Ciclo cada 100ms. Auditor de salida activo.")
    
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

                # ── AUDITOR DE SALIDA (ANTES DE CERRAR POSICIÓN) ──
                # Guardar trade_id y entry_time antes de que la posición se elimine
                trade_id = pos.get('trade_id')
                entry_time_str = pos.get('entry_time')
                if trade_id and entry_time_str:
                    try:
                        entry_time = datetime.fromisoformat(entry_time_str)
                        duration_min = (datetime.now() - entry_time).total_seconds() / 60.0
                    except:
                        duration_min = 0.0
                else:
                    duration_min = 0.0

                # 2. Cerrar en PaperTrader (CORREGIDO: close_percent, no fraction)
                pnl_closed = trader.close_position(symbol, exit_price=exit_price, reason=reason, close_percent=close_percent)
                log(f"Posición cerrada en PaperTrader: {symbol} ({reason}, {close_percent*100:.0f}%) PnL: ${pnl_closed:.2f}")

                # 3. Registrar en auditor de salida
                if trade_id:
                    log_signal_closed(trade_id, action, exit_price, pnl_closed, duration_min)
                    log(f"Auditoría registrada: {trade_id} cerrado por {action} con PnL ${pnl_closed:.2f}")

                # Si fue TP1, mover el SL a breakeven (el PaperTrader ya lo hace si se usa check_exits,
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