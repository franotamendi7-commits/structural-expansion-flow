import sys
sys.path.insert(0, '.')
from data.binance_feed import get_klines
from engine.scalping_engine import ScalpingEngine

print("1. Probando API...")
k = get_klines('BTCUSDT', '1h', limit=1)
if k:
    print("   API responde OK")
else:
    print("   ERROR: API no responde")
    sys.exit(1)

print("2. Probando motor con datos falsos...")
velas = [[1700000000000 + i*3600000, 50000, 50100, 49900, 50000, 10, 0] for i in range(100)]
klines = {tf: velas for tf in ['5m','15m','1h','4h','1d']}
engine = ScalpingEngine('BTCUSDT', capital=100.0, risk_pct=0.01, debug_filters=False)
def fetch(self):
    return klines
import types
engine._fetch_klines_dict = types.MethodType(fetch, engine)
try:
    res = engine.run()
    print("   Motor responde OK")
except Exception as e:
    print(f"   ERROR: {e}")
