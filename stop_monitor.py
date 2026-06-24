#!/usr/bin/env python3
import time, json, os, csv, hashlib, hmac, urllib.parse, requests
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

PAPER_STATE_FILE = "paper_state.json"
AUDIT_FILE = "audit_log.csv"
WAIT_SECONDS = 1.0
STOPMON_STATUS_FILE = "stop_monitor_status.json"

API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"

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

# ─── Set de órdenes en vuelo para evitar doble cierre en Binance ───
in_flight_orders = set()

# ─── Contador de errores consecutivos (P0 — Mejora 3) ───
errors_consecutive = 0

def update_stopmon_status(errors_consecutive_val, last_error=None, last_success_time=None):
    """Escribe el estado del stop_monitor a un JSON para que app.py pueda leerlo
    y disparar alertas de Telegram si hay 3+ errores consecutivos.
    """
    status = {
        "errors_consecutive": errors_consecutive_val,
        "last_error": last_error,
        "last_error_time": datetime.now().isoformat() if last_error else None,
        "last_success_time": last_success_time or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    try:
        with open(STOPMON_STATUS_FILE, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        logger.error(f"Error writing stopmon status: {e}")

AUDIT_FIELDS = ["trade_id","timestamp_entry","symbol","side","score","entry","sl","tp1","tp2","fib_label","phase","ci","wr","st","veto","explanation_raw","exit_reason","exit_price","pnl_final","duration_min"]

def log_signal_closed(trade_id, exit_reason, exit_price, pnl, duration_min):
    """Actualiza la fila del trade_id en audit_log.csv con los datos de salida."""
    if not trade_id or not os.path.exists(AUDIT_FILE):
        return
    try:
        with open(AUDIT_FILE, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames
        for row in rows:
            if row.get('trade_id') == trade_id:
                row['exit_reason'] = exit_reason
                row['exit_price'] = str(exit_price)
                row['pnl_final'] = str(round(pnl, 4))
                row['duration_min'] = str(round(duration_min, 2))
                break
        with open(AUDIT_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        logger.debug(f"Audit actualizado: trade_id={trade_id} reason={exit_reason} pnl={pnl:.4f}")
    except Exception as e:
        logger.error(f"log_signal_closed error: {e}")

# ─── Obtener posiciones abiertas reales desde Binance Testnet ───
def fetch_binance_positions(exec_mgr):
    base = 'https://testnet.binancefuture.com' if exec_mgr.testnet else 'https://fapi.binance.com'
    ts = int(time.time() * 1000)
    params = {'timestamp': ts}
    query = urllib.parse.urlencode(params)
    signature = hmac.new(exec_mgr.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params['signature'] = signature
    headers = {'X-MBX-APIKEY': exec_mgr.api_key}
    try:
        r = requests.get(f'{base}/fapi/v2/positionRisk', headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            logger.warning(f"positionRisk HTTP {r.status_code}: {r.text[:200]}")
            return []
        positions = []
        for p in r.json():
            amt = float(p['positionAmt'])
            if amt != 0:
                positions.append({
                    'symbol': p['symbol'],
                    'side': 'LONG' if amt > 0 else 'SHORT',
                    'quantity': abs(amt),
                    'entry_price': float(p['entryPrice']),
                    'stop_loss': None,
                    'take_profit': None,
                    'pnl': float(p['unRealizedProfit'])
                })
        return positions
    except Exception as e:
        logger.error(f"fetch_binance_positions error: {e}")
        return []

def main():
    global errors_consecutive
    trader = PaperTrader(state_file=PAPER_STATE_FILE)
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)
    logger.info("Stop Monitor REAL iniciado (paper + binance)")
    update_stopmon_status(0, last_success_time=datetime.now().isoformat())

    while True:
        try:
            # ── 1. PAPER TRADER ──
            for sym, pos in list(trader.open_positions.items()):
                px = get_ticker(sym)
                if px is None or px <= 0:
                    continue

                trader.update_position(sym, px)

                exit_signal = trader.evaluate_exit_conditions(sym, px)
                if exit_signal is None:
                    continue

                action = exit_signal['action']
                reason = exit_signal.get('reason', action)
                exit_px = exit_signal['exit_price']

                close_pct = 1.0
                contracts = pos.get("contracts", 0) * close_pct
                side = pos.get("side", "LONG").upper()
                close_side = "SELL" if side == "LONG" else "BUY"

                if sym in in_flight_orders:
                    logger.warning(f"Skip {sym}: orden en vuelo (doble cierre evitado)")
                    continue

                if close_side and contracts > 0:
                    in_flight_orders.add(sym)
                    try:
                        real_order = exec_mgr.execute_signal(
                            {"symbol": sym, "side": close_side, "quantity": contracts},
                            reduce_only=True
                        )
                        logger.info(f"Paper→Real {sym}: {close_side} {contracts} | order_id={real_order.get('order_id', 'error')}")
                    except Exception as e:
                        logger.error(f"Error orden real {sym}: {e}")
                    finally:
                        in_flight_orders.discard(sym)

                pnl = trader.close_position(sym, exit_price=exit_px, reason=reason, close_percent=close_pct)
                logger.info(f"Paper cerrado {sym} ({reason} {close_pct*100:.0f}%) PnL=${pnl:.2f}")

                tid = pos.get('trade_id')
                if tid:
                    try:
                        dur = (datetime.now() - datetime.fromisoformat(pos.get('entry_time', ''))).total_seconds() / 60
                    except Exception:
                        dur = 0
                    log_signal_closed(tid, reason, exit_px, pnl, dur)

            # ── 2. POSICIONES REALES DE BINANCE ──
            real_positions = fetch_binance_positions(exec_mgr)
            for rp in real_positions:
                sym = rp['symbol']

                if sym in in_flight_orders:
                    continue

                px = get_ticker(sym)
                if px is None or px <= 0:
                    continue

                paper_pos = trader.open_positions.get(sym)
                if paper_pos:
                    sl = paper_pos.get('stop_loss')
                    tp = paper_pos.get('take_profit')

                    if sl and ((rp['side'] == 'LONG' and px <= sl) or (rp['side'] == 'SHORT' and px >= sl)):
                        close_side = "SELL" if rp['side'] == 'LONG' else "BUY"
                        qty = rp['quantity']
                        in_flight_orders.add(sym)
                        try:
                            real_order = exec_mgr.execute_signal(
                                {"symbol": sym, "side": close_side, "quantity": qty},
                                reduce_only=True
                            )
                            logger.info(f"Real SL {sym}: cerrado a {px} | order_id={real_order.get('order_id', 'error')}")
                        except Exception as e:
                            logger.error(f"Error real SL {sym}: {e}")
                        finally:
                            in_flight_orders.discard(sym)

                    if tp and ((rp['side'] == 'LONG' and px >= tp) or (rp['side'] == 'SHORT' and px <= tp)):
                        close_side = "SELL" if rp['side'] == 'LONG' else "BUY"
                        qty = rp['quantity']
                        in_flight_orders.add(sym)
                        try:
                            real_order = exec_mgr.execute_signal(
                                {"symbol": sym, "side": close_side, "quantity": qty},
                                reduce_only=True
                            )
                            logger.info(f"Real TP {sym}: cerrado a {px} | order_id={real_order.get('order_id', 'error')}")
                        except Exception as e:
                            logger.error(f"Error real TP {sym}: {e}")
                        finally:
                            in_flight_orders.discard(sym)

            # Ciclo completado sin excepción → resetear contador de errores
            if errors_consecutive > 0:
                logger.info(f"Ciclo OK tras {errors_consecutive} errores consecutivos, reseteando contador")
            errors_consecutive = 0
            update_stopmon_status(errors_consecutive, last_success_time=datetime.now().isoformat())

            time.sleep(WAIT_SECONDS)
        except KeyboardInterrupt:
            logger.info("Monitor detenido por KeyboardInterrupt")
            break
        except Exception as e:
            errors_consecutive += 1
            logger.error(f"Error en ciclo #{errors_consecutive}: {e}", exc_info=True)
            update_stopmon_status(errors_consecutive, last_error=str(e))
            time.sleep(WAIT_SECONDS)

if __name__ == "__main__":
    main()
