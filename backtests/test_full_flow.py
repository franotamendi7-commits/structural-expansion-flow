#!/usr/bin/env python3
import time
import json
import os
from paper_trader import PaperTrader
from portfolio_manager import PortfolioManager
from data.binance_feed import get_ticker
from execution_manager import ExecutionManager

STATE_FILE = "/Users/franciscootamendi/ai-agents-v3/paper_state.json"
TEST_SYMBOL = "SOLUSDT"

def main():
    print("🔧 Iniciando prueba completa del flujo...")
    
    # 1. Obtener precio actual
    price = get_ticker(TEST_SYMBOL)
    if not price:
        print("❌ No se pudo obtener precio actual.")
        return
    print(f"💰 Precio actual {TEST_SYMBOL}: ${price:.2f}")
    
    # 2. Limpiar estado anterior
    os.system(f'echo "{{}}" > {STATE_FILE}')
    
    # 3. Crear posición LONG con SL por debajo (para activación inmediata al forzar precio)
    entry = price
    stop_loss = price - 0.50   # SL 0.50 USD por debajo
    take_profit = price + 100  # muy lejos
    
    signal = {
        "symbol": TEST_SYMBOL,
        "side": "LONG",
        "price": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "contracts": 1.0,
        "risk_usd": 1.0,
        "notional": entry,
        "contrarian": False
    }
    
    # 4. Abrir posición en PaperTrader
    trader = PaperTrader(state_file=STATE_FILE)
    success = trader.open_trade(signal)
    if not success:
        print("❌ No se pudo abrir la posición.")
        return
    print("✅ Posición abierta en PaperTrader")
    
    # 5. AUM antes
    pm = PortfolioManager()
    total_aum = sum(inv["capital_actual"] for inv in pm.inversores)
    print(f"💰 AUM total antes del trade: ${total_aum:.2f}")
    
    # 6. SIMULAR precio por debajo del SL (activación inmediata)
    precio_simulado = stop_loss - 0.01
    print(f"🔻 Simulando precio ${precio_simulado:.2f} (SL en ${stop_loss:.2f})")
    trader.update_position(TEST_SYMBOL, precio_simulado)
    
    # 7. Verificar que la posición se cerró
    time.sleep(0.5)  # dar tiempo a que se escriba el estado
    if TEST_SYMBOL not in trader.open_positions:
        print("✅ La posición fue cerrada por el stop loss.")
    else:
        print("❌ La posición sigue abierta.")
        trader.force_close(TEST_SYMBOL)
        return
    
    # 8. AUM después
    pm = PortfolioManager()
    new_total = sum(inv["capital_actual"] for inv in pm.inversores)
    print(f"💰 AUM total después del trade: ${new_total:.2f}")
    if new_total != total_aum:
        print("✅ PortfolioManager actualizó los balances.")
    else:
        print("⚠️ El AUM no cambió (el PnL fue cero o no se actualizó).")
    
    print("🏁 Prueba completa finalizada.")

if __name__ == "__main__":
    main()
