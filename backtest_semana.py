import sys
sys.path.insert(0, '.')
from datetime import datetime, timedelta
from data.binance_feed import get_klines
from engine.scalping_engine import ScalpingEngine

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TIMEFRAMES = {'5m':5, '15m':15, '1h':60, '4h':240, '1d':1440}
INICIO = datetime(2026,6,10,0,0,0)
FIN = datetime(2026,6,17,23,59,59)
LIMITE = 500

def a_listas(k):
    if not k: return []
    if isinstance(k[0], list): return k
    return [[v.get('timestamp', v.get('openTime', 0)),
             float(v.get('open',0)), float(v.get('high',0)),
             float(v.get('low',0)), float(v.get('close',0)),
             float(v.get('volume',0)), float(v.get('quoteAssetVolume',0))] for v in k]

def obtener(simbolo):
    datos = {}
    for tf, m in TIMEFRAMES.items():
        horas = (FIN - INICIO).total_seconds()/3600
        lim = min(int(horas*60/m) + 200, LIMITE)
        raw = get_klines(simbolo, interval=tf, limit=lim)
        if raw is None:
            datos[tf] = []
            continue
        k = a_listas(raw)
        ms_ini = int(INICIO.timestamp()*1000)
        ms_fin = int(FIN.timestamp()*1000)
        datos[tf] = [v for v in k if ms_ini <= v[0] <= ms_fin]
    return datos

def main():
    total = []
    for sym in PARES:
        velas = obtener(sym)
        if not velas.get('1h'):
            continue
        v1h = velas['1h']
        for i in range(50, len(v1h)):
            ks = {}
            for tf in TIMEFRAMES:
                if not velas.get(tf):
                    ks[tf] = None
                    continue
                ts = v1h[i][0]
                idx = 0
                for j, v in enumerate(velas[tf]):
                    if v[0] <= ts:
                        idx = j
                    else:
                        break
                ks[tf] = velas[tf][:idx+1]
                if len(ks[tf]) < 30:
                    ks[tf] = None
            if any(ks[tf] is None for tf in ['5m','15m','1h']):
                continue
            motor = ScalpingEngine(sym, capital=100.0, risk_pct=0.01, debug_filters=False)
            def override(self):
                return ks
            import types
            motor._fetch_klines_dict = types.MethodType(override, motor)
            try:
                res = motor.run()
            except:
                continue
            if res.get('signal') in ('LONG', 'SHORT'):
                ts = v1h[i][0] / 1000
                total.append(f"{datetime.fromtimestamp(ts)} | {sym} | {res['signal']} | Score:{res.get('score',0)} | {res.get('explanation','')}")
    if not total:
        print("0 señales en la semana")
    else:
        for linea in total:
            print(linea)

if __name__ == "__main__":
    main()
