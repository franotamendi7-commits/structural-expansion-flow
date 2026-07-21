from paper_trader import PaperTrader

trader = PaperTrader(initial_balance=100)
signal = {
    "symbol": "SOLUSDT",
    "side": "LONG",
    "price": 100,
    "stop_loss": 95,
    "take_profit": 110,
    "tp2": 120,
    "contracts": 1,
    "risk_usd": 1,
    "notional": 100
}
trader.open_trade(signal)
print("Posición abierta")

# Simular TP1
trader.update_position("SOLUSDT", 110)
pos = trader.get_open_position("SOLUSDT")
assert pos and abs(pos["contracts"] - 0.4) < 0.01, f"Error: contracts={pos['contracts'] if pos else None}"
# El SL debe estar en breakeven (precio de entrada, o muy cerca)
assert abs(pos["stop_loss"] - 100) < 0.01, f"SL no movió a breakeven: {pos['stop_loss']}"
print("✅ TP1 parcial OK (60% cerrado, SL breakeven)")

# Simular TP2
trader.update_position("SOLUSDT", 120)
assert trader.get_open_position("SOLUSDT") is None, "TP2 no cerró"
print("✅ TP2 final OK (40% restante cerrado)")

print("🎉 Flujo TP completo correcto")
