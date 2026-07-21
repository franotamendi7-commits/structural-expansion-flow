#!/usr/bin/env python3
"""
Prueba de latencia del servicio stop_monitor.py.
Crea una posición SHORT con SL activable inmediatamente y TP1 muy lejos.
Ejecutar con: python3 test_stop_monitor.py
"""
import json, os, time
from data.binance_feed import get_ticker

STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"
TEST_SYMBOL = "SOLUSDT"
TIMEOUT = 5.0

def main():
    print("🔍 Preparando prueba del monitor...")
    if not os.path.exists(STATE_FILE):
        print("❌ paper_state.json no encontrado. ¿Está corriendo el stop_monitor?")
        return

    price = get_ticker(TEST_SYMBOL)
    if price is None or price == 0:
        print("❌ No se pudo obtener el precio actual de", TEST_SYMBOL)
        return
    print(f"💰 Precio actual de {TEST_SYMBOL}: ${price:.2f}")

    # Posición SHORT con SL inmediatamente alcanzable (SL por debajo del precio actual)
    entry = price
    stop_loss = price - 2.0          # el precio actual (mayor) supera el SL → activación instantánea
    take_profit = entry - 1000.0     # tan bajo que nunca se alcanzará

    position = {
        "side": "SHORT",
        "entry_price": entry,
        "stop_loss": stop_loss,
        "take_profit1": take_profit,
        "take_profit2": None,
        "contracts": 1,
        "breakeven_moved": False,
        "trailing_activated": False
    }

    with open(STATE_FILE, 'r') as f:
        state = json.load(f)
    state.setdefault("open_positions", {})[TEST_SYMBOL] = position
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)
    print(f"📝 Posición SHORT creada: entry={entry}, SL={stop_loss} (activado), TP1={take_profit} (lejos)")

    start_time = time.time()
    detected = False
    while time.time() - start_time < TIMEOUT:
        with open(STATE_FILE, 'r') as f:
            current_state = json.load(f)
        if TEST_SYMBOL not in current_state.get("open_positions", {}):
            detected = True
            break
        time.sleep(0.01)

    latency = (time.time() - start_time) * 1000

    if detected:
        with open(STATE_FILE, 'r') as f:
            final_state = json.load(f)
        closed = final_state.get("closed_trades", [])
        found_sl = any(t.get("symbol") == TEST_SYMBOL and t.get("reason") == "stop_loss" for t in closed)
        if found_sl and latency < 200:
            print(f"✅ Prueba exitosa - Latencia: {latency:.1f} ms (stop_loss detectado)")
        elif found_sl:
            print(f"⚠️ Cierre detectado pero latencia alta: {latency:.1f} ms")
        else:
            print("⚠️ Posición cerrada pero no se registró como stop_loss en closed_trades")
    else:
        print(f"❌ Fallo - La posición no se cerró en {TIMEOUT} segundos")
        # Limpiar posición residual
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        state.get("open_positions", {}).pop(TEST_SYMBOL, None)
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
        print("🧹 Posición de prueba eliminada manualmente.")

if __name__ == "__main__":
    main()
