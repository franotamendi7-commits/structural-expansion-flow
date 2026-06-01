# test_score.py – Muestra el puntaje dinámico real que produce el motor
from engine.scalping_engine import ScalpingEngine

# Probamos con BTCUSDT
engine = ScalpingEngine(symbol='BTCUSDT', capital=100.0, risk_pct=0.01, debug_filters=False)
res = engine.run()

print("----- RESULTADO DEL MOTOR -----")
print(f"Señal: {res['signal']}")
print(f"Setup: {res['setup_state']}")
print(f"Score dinámico calculado: {res['score']}")
print(f"Explicación: {res.get('explanation','')}")

if res.get('trade'):
    t = res['trade']
    print(f"Trade generado: ENTRY={t['entry']:.2f}, SL={t['sl']:.2f}, TP={t['tp1']:.2f}")
else:
    print("No se generó trade (posiblemente score muy bajo)")
    