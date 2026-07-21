#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
from datetime import datetime, timedelta
import pandas as pd
from data.binance_feed import get_klines
from engine.scalping_engine import ScalpingEngine

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
CAPITAL = 100.0
RISK_PCT = 0.01

TIMEFRAMES = {
    '5m': (5, 100),
    '15m': (15, 100),
    '1h': (60, 100),
    '4h': (240, 100),
    '1d': (1440, 200)
}

FECHA_INICIO = datetime(2026, 6, 10, 0, 0, 0)
FECHA_FIN = datetime(2026, 6, 17, 23, 59, 59)

def convertir_a_lista(klines):
    if not klines:
        return []
    if isinstance(klines[0], list):
        return klines
    lista = []
    for k in klines:
        if isinstance(k, dict):
            lista.append([
                k.get('timestamp', k.get('openTime', 0)),
                float(k.get('open', 0)),
                float(k.get('high', 0)),
                float(k.get('low', 0)),
                float(k.get('close', 0)),
                float(k.get('volume', 0)),
                float(k.get('quoteAssetVolume', 0)) if 'quoteAssetVolume' in k else 0.0
            ])
        else:
            lista.append(k)
    return lista

def descargar_velas(symbol):
    start_ms = int(FECHA_INICIO.timestamp() * 1000)
    end_ms = int(FECHA_FIN.timestamp() * 1000)
    datos = {}
    for tf, (minutos, limit) in TIMEFRAMES.items():
        horas = (FECHA_FIN - FECHA_INICIO).total_seconds() / 3600
        limit_est = min(int(horas * 60 / minutos) + 200, 500)
        klines_raw = get_klines(symbol, interval=tf, limit=limit_est)
        if klines_raw is None:
            datos[tf] = []
            continue
        klines = convertir_a_lista(klines_raw)
        datos[tf] = [k for k in klines if start_ms <= k[0] <= end_ms]
    return datos

def main():
    todas = []
    for par in PARES:
        velas = descargar_velas(par)
        if not velas.get('1h'):
            continue
        v1h = velas['1h']
        for i in range(50, len(v1h)):
            k_actual = {}
            for tf in TIMEFRAMES:
                if not velas.get(tf):
                    k_actual[tf] = None
                    continue
                ts_actual = v1h[i][0]
                idx = 0
                for j, v in enumerate(velas[tf]):
                    if v[0] <= ts_actual:
                        idx = j
                    else:
                        break
                k_actual[tf] = velas[tf][:idx+1]
                if len(k_actual[tf]) < 30:
                    k_actual[tf] = None
            if any(k_actual[tf] is None for tf in ['5m', '15m', '1h']):
                continue
            engine = ScalpingEngine(par, capital=CAPITAL, risk_pct=RISK_PCT, debug_filters=False)
            def fetch(self):
                return k_actual
            import types
            engine._fetch_klines_dict = types.MethodType(fetch, engine)
            try:
                res = engine.run()
            except:
                continue
            if res.get('signal') in ('LONG', 'SHORT'):
                ts = v1h[i][0] / 1000
                todas.append({
                    'fecha': datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'),
                    'symbol': par,
                    'signal': res['signal'],
                    'score': res.get('score', 0),
                    'explanation': res.get('explanation', '')
                })
    if not todas:
        print("0 señales en la semana")
        return
    df = pd.DataFrame(todas)
    df['dia'] = pd.to_datetime(df['fecha']).dt.date
    print(df.to_string(index=False))

if __name__ == "__main__":
    main()
