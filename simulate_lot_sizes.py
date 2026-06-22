import sys
sys.path.insert(0, '.')
from engine.scalping_engine import TradeSetupBuilder

CAPITAL = 10.0
RISK_PCT = 0.01
TEST_PAIRS = {
    'BTCUSDT': 60000.0,
    'ETHUSDT': 1700.0,
    'SOLUSDT': 70.0,
    'XRPUSDT': 1.1,
    'BNBUSDT': 580.0
}
MIN_QTY = {
    'BTCUSDT': 0.0001,
    'ETHUSDT': 0.001,
    'SOLUSDT': 0.01,
    'XRPUSDT': 0.1,
    'BNBUSDT': 0.01
}

print("Simulación de contratos con capital de $10 y riesgo 1%:\n")
for symbol, price in TEST_PAIRS.items():
    # Simular un stop loss típico del 2% del precio
    sl = price * 0.98  # LONG
    builder = TradeSetupBuilder(
        entry_price=price,
        stop_loss=sl,
        capital=CAPITAL,
        risk_pct=RISK_PCT,
        signal_direction='bullish',
        tp_ratio=1.5,
        score=80
    )
    trade = builder.build()
    if trade is None:
        print(f"{symbol}: No se pudo construir el trade (riesgo demasiado bajo)")
        continue
    contracts = trade['contracts']
    min_q = MIN_QTY[symbol]
    ok = contracts >= min_q
    print(f"{symbol}: {contracts:.6f} contratos | Mínimo: {min_q} | {'✅ OK' if ok else '❌ DEMASIADO PEQUEÑO'}")
