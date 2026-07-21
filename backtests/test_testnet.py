#!/usr/bin/env python3
"""
Test de ejecución real en Binance Futures Testnet usando ExecutionManager.
Envía una orden de compra pequeña con SL/TP y muestra el resultado.
"""
from execution_manager import ExecutionManager

# Credenciales de Testnet (hardcodeadas para esta prueba)
API_KEY = "TEyU8MQ4xWGsTq0bujMJxLs4qd0d4i1JCWtwwiy9W74taSIbi1Mor0m83DsCUu6u"
API_SECRET = "DnIPgWcon8sQ51z2mjz1O67ElZcHr0RXCBEV9FpsGH3BUeVyl5AuLzEIMsyhIaTo"

def main():
    # 1. Crear instancia para Testnet
    exec_mgr = ExecutionManager(api_key=API_KEY, api_secret=API_SECRET, testnet=True)

    # 2. Señal de prueba (compra de 0.001 BTC con SL y TP ficticios)
    signal = {
        "side": "BUY",
        "symbol": "BTCUSDT",
        "quantity": 0.001,
        "stop_loss": 88000.0,
        "take_profit": 92000.0
    }

    print("Enviando orden de prueba...")
    result = exec_mgr.execute_signal(signal)

    # 3. Mostrar resultado detallado
    if result.get("error"):
        print(f"❌ Error: {result['error']}")
        if "sl_error" in result and result["sl_error"]:
            print(f"   -> Error al colocar SL: {result['sl_error']}")
        if "tp_error" in result and result["tp_error"]:
            print(f"   -> Error al colocar TP: {result['tp_error']}")
    else:
        print("✅ Orden ejecutada exitosamente")
        print(f"   Order ID:     {result.get('order_id')}")
        print(f"   Precio ejec.: {result.get('executed_price')}")
        print(f"   Estado:       {result.get('status')}")
        sl_err = result.get('sl_error')
        tp_err = result.get('tp_error')
        if sl_err:
            print(f"   ⚠️ Error al colocar SL: {sl_err}")
        if tp_err:
            print(f"   ⚠️ Error al colocar TP: {tp_err}")
    print("Prueba finalizada.")

if __name__ == "__main__":
    main()
