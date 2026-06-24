#!/usr/bin/env python3
import time, json, os, csv, hashlib, hmac, urllib.parse, requests
from datetime import datetime
from paper_trader import PaperTrader
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

PAPER_STATE_FILE = "paper_state.json"
AUDIT_FILE = "audit_log.csv"
WAIT_SECONDS = 0.1

API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"

logs_dir = "logs"
os.makedirs(logs_dir, exist_ok=True)
log_file = open(os.path.join(logs_dir, "stop_monitor.log"), "a")

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_file.write(f"[{timestamp}] {msg}\n")
    log_file.flush()
    print(f"[{timestamp}] {msg}")

AUDIT_FIELDS = ["trade_id","timestamp_entry","symbol","side","score","entry","sl","tp1","tp2","fib_label","phase","ci","wr","st","veto","explanation_raw","exit_reason","exit_price","pnl_final","duration_min"]

def log_signal_closed(trade_id, exit_reason, exit_price, pnl, duration_min):
    if not os.path.exists(AUDIT_FILE): return
    with open(AUDIT_FILE, "r") as f:
        reader = csv.DictReader(f); rows = list(reader); fieldnames = reader.fieldnames
    for row in rows:
        if row['trade_id'] == trade_id:
            row['exit_reason'] = exit_reason; row['exit_price'] = str(exit_price); row['pnl_final'] = str(pnl); row['duration_min'] = str(round(duration_min,2))
            break
    with open(AUDIT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames); writer.writeheader(); writer.writerows(rows)

# ─── Obtener posiciones abiertas reales desde Binance Testnet ───
def fetch_binance_positions(exec_mgr):
    base = 'https://testnet.binancefuture.com' if exec_mgr.testnet else 'https://fapi.binance.com'
    ts = int(time.time()*1000)
    params = {'timestamp': ts}
    query = urllib.parse.urlencode(params)
    signature = hmac.new(exec_mgr.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params['signature'] = signature
    headers = {'X-MBX-APIKEY': exec_mgr.api_key}
    r = requests.get(f'{base}/fapi/v2/positionRisk', headers=headers, params=params)
    positions = []
    for p in r.json():
        amt = float(p['positionAmt'])
        if amt != 0:
            positions.append({
                'symbol': p['symbol'],
                'side': 'LONG' if amt > 0 else 'SHORT',
                'quantity': abs(amt),
                'entry_price': float(p['entryPrice']),
                'stop_loss': None,   # no hay SL en la respuesta; lo llevamos nosotros
                'take_profit': None,
                'pnl': float(p['unRealizedProfit'])
            })
    return positions

def main():
    trader = PaperTrader(state_file=PAPER_STATE_FILE)
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)
    log("Stop Monitor REAL iniciado (paper + binance).")

    while True:
        try:
            # ── 1. PAPER TRADER ──
            trader = PaperTrader(state_file=PAPER_STATE_FILE)
            for sym, pos in list(trader.open_positions.items()):
                px = get_ticker(sym)
                if px is None or px <= 0: continue
                trader.update_position(sym, px)
                exit_signal = trader.evaluate_exit_conditions(sym, px)
                if exit_signal is None: continue
                action = exit_signal['action']; exit_px = exit_signal['exit_price']
                if action == 'tp1': close_pct, reason = 1.0, "take_profit"
                elif action == 'tp2': close_pct, reason = 1.0, "take_profit_tp2"
                else: close_pct, reason = 1.0, action
                contracts = pos.get("contracts",0) * close_pct
                side = pos.get("side","LONG").upper()
                close_side = "SELL" if side == "LONG" else "BUY"
                if close_side and contracts > 0:
                    real_order = exec_mgr.execute_signal({"symbol":sym,"side":close_side,"quantity":contracts}, reduce_only=True)
                    log(f"Paper→Real {sym}: {close_side} {contracts} | {real_order.get('order_id','error')}")
                pnl = trader.close_position(sym, exit_price=exit_px, reason=reason, close_percent=close_pct)
                log(f"Paper cerrado {sym} ({reason} {close_pct*100:.0f}%) PnL ${pnl:.2f}")
                tid = pos.get('trade_id')
                if tid:
                    try: dur = (datetime.now() - datetime.fromisoformat(pos.get('entry_time',''))).total_seconds()/60
                    except: dur = 0
                    log_signal_closed(tid, action, exit_px, pnl, dur)
                if action == 'tp1' and pos.get("entry_price"):
                    trader.move_stop_loss(sym, pos["entry_price"])

            # ── 2. POSICIONES REALES DE BINANCE ──
            real_positions = fetch_binance_positions(exec_mgr)
            for rp in real_positions:
                sym = rp['symbol']
                px = get_ticker(sym)
                if px is None or px <= 0: continue
                # Verificar si hay un SL definido en el paper para este símbolo
                paper_pos = trader.open_positions.get(sym)
                if paper_pos:
                    sl = paper_pos.get('stop_loss')
                    tp = paper_pos.get('take_profit')
                    if sl and ((rp['side']=='LONG' and px <= sl) or (rp['side']=='SHORT' and px >= sl)):
                        close_side = "SELL" if rp['side']=='LONG' else "BUY"
                        qty = rp['quantity']
                        real_order = exec_mgr.execute_signal({"symbol":sym,"side":close_side,"quantity":qty}, reduce_only=True)
                        log(f"Real SL {sym}: cerrado a {px} | {real_order.get('order_id','error')}")
                    if tp and ((rp['side']=='LONG' and px >= tp) or (rp['side']=='SHORT' and px <= tp)):
                        close_side = "SELL" if rp['side']=='LONG' else "BUY"
                        qty = rp['quantity']
                        real_order = exec_mgr.execute_signal({"symbol":sym,"side":close_side,"quantity":qty}, reduce_only=True)
                        log(f"Real TP {sym}: cerrado a {px} | {real_order.get('order_id','error')}")

            time.sleep(WAIT_SECONDS)
        except KeyboardInterrupt:
            log("Monitor detenido.")
            break
        except Exception as e:
            log(f"Error: {e}")
            time.sleep(WAIT_SECONDS)

if __name__ == "__main__":
    main()
