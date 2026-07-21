#!/usr/bin/env python3
"""
Backtest exacto de los últimos 4 días para los 4 pares.
Usa los 5 timeframes reales (5m, 15m, 1h, 4h, 1d) y ejecuta el motor en cada vela de 1h.
Sin simplificaciones.
"""
import sys
import os
sys.path.insert(0, '.')

from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from data.binance_feed import get_klines
from engine.scalping_engine import ScalpingEngine

PARES = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
CAPITAL = 100.0
RISK_PCT = 0.01

TIMEFRAMES = {
    '5m':  (5, 100),
    '15m': (15, 100),
    '1h':  (60, 100),
    '4h':  (240, 100),
    '1d':  (1440, 200)
}

def convertir_a_lista(klines):
    """Convierte una lista de dicts a lista de listas (formato motor)."""
    if not klines:
        return []
    # Si ya es lista de listas, devolver igual
    if isinstance(klines[0], list):
        return klines
    # Si es dict, convertir a lista ordenada
    # Orden: [timestamp, open, high, low, close, volume, turnover]
    lista = []
    for k in klines:
        # Si tiene claves de dict, mapear
        if isinstance(k, dict):
            ts = k.get('timestamp', k.get('openTime', 0))
            open_price = float(k.get('open', 0))
            high = float(k.get('high', 0))
            low = float(k.get('low', 0))
            close = float(k.get('close', 0))
            volume = float(k.get('volume', 0))
            # Binance también puede tener turnover como 7mo elemento
            turnover = float(k.get('quoteAssetVolume', 0)) if 'quoteAssetVolume' in k else 0.0
            lista.append([ts, open_price, high, low, close, volume, turnover])
        else:
            # Ya es lista, añadir tal cual
            lista.append(k)
    return lista

def descargar_velas(symbol, horas_atras=4*24, buffer_horas=48):
    """Descarga velas de todos los timeframes y las convierte a listas."""
    now = datetime.now()
    start = now - timedelta(hours=horas_atras + buffer_horas)
    start_ms = int(start.timestamp() * 1000)
    datos = {}
    for tf, (minutos, limit) in TIMEFRAMES.items():
        limit_estimado = int(horas_atras * 60 / minutos) + buffer_horas * 60 // minutos + 50
        limit_estimado = min(limit_estimado, 500)
        klines_raw = get_klines(symbol, interval=tf, limit=limit_estimado)
        if klines_raw is None:
            print(f"⚠️ No se pudieron descargar velas {tf} para {symbol}")
            datos[tf] = []
            continue
        # Convertir a listas
        klines = convertir_a_lista(klines_raw)
        # Filtrar por timestamp
        start_ts = start_ms / 1000
        datos[tf] = [k for k in klines if k[0] / 1000 >= start_ts]
    return datos

def construir_datos_hasta(velas, indice, tf):
    if tf not in velas or not velas[tf]:
        return None
    return velas[tf][:indice+1]

def main():
    print("🔍 Backtest exacto de los últimos 4 días (sin simplificaciones)")
    print(f"📊 Pares: {', '.join(PARES)}")
    print("⏳ Descargando datos históricos...\n")

    todas_senales = []
    total_por_par = {}

    for par in PARES:
        print(f"📥 Procesando {par}...")
        velas = descargar_velas(par)
        if not velas or not velas.get('1h'):
            print(f"  ❌ No se obtuvieron datos para {par}")
            continue

        velas_1h = velas['1h']
        if not velas_1h:
            print(f"  ❌ No hay velas 1h para {par}")
            continue

        print(f"  ✅ {len(velas_1h)} velas 1h disponibles")

        senales_par = []
        for i in range(50, len(velas_1h)):
            klines_actual = {}
            for tf in TIMEFRAMES:
                if tf not in velas or not velas[tf]:
                    klines_actual[tf] = None
                    continue
                ts_actual = velas_1h[i][0]
                tf_velas = velas[tf]
                idx = 0
                for j, v in enumerate(tf_velas):
                    if v[0] <= ts_actual:
                        idx = j
                    else:
                        break
                klines_actual[tf] = tf_velas[:idx+1]
                if len(klines_actual[tf]) < 30:
                    klines_actual[tf] = None

            if any(klines_actual[tf] is None for tf in ['5m', '15m', '1h']):
                continue

            engine = ScalpingEngine(par, capital=CAPITAL, risk_pct=RISK_PCT, debug_filters=False)

            def fetch_override(self):
                return klines_actual

            import types
            engine._fetch_klines_dict = types.MethodType(fetch_override, engine)

            try:
                resultado = engine.run()
            except Exception as e:
                print(f"  ⚠️ Error en {par} vela {i}: {e}")
                continue

            if resultado.get('signal') in ('LONG', 'SHORT'):
                ts = velas_1h[i][0] / 1000
                fecha = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
                senales_par.append({
                    'fecha': fecha,
                    'symbol': par,
                    'signal': resultado['signal'],
                    'score': resultado.get('score', 0),
                    'setup_state': resultado.get('setup_state', ''),
                    'explanation': resultado.get('explanation', '')
                })

        print(f"  🔔 Señales encontradas en {par}: {len(senales_par)}")
        todas_senales.extend(senales_par)
        total_por_par[par] = len(senales_par)

    if not todas_senales:
        print("\n❌ No se encontraron señales en los últimos 4 días.")
        return

    df = pd.DataFrame(todas_senales)
    df['fecha_dia'] = pd.to_datetime(df['fecha']).dt.date

    print("\n📋 Resumen por día:")
    resumen = df.groupby('fecha_dia').size().reset_index(name='cantidad')
    print(resumen.to_string(index=False))

    print("\n📋 Detalle de señales:")
    for _, row in df.iterrows():
        print(f"{row['fecha']} | {row['symbol']} | {row['signal']} | Score: {row['score']} | {row['explanation']}")

    hoy = datetime.now().date()
    lunes = hoy - timedelta(days=(hoy.weekday() + 1) % 7)
    if lunes < hoy - timedelta(days=7):
        lunes = hoy - timedelta(days=(hoy.weekday() + 1) % 7)

    senales_lunes = df[pd.to_datetime(df['fecha']).dt.date == lunes]
    print(f"\n🔎 Señales del lunes {lunes}:")
    if not senales_lunes.empty:
        print(f"✅ {len(senales_lunes)} señales:")
        for _, row in senales_lunes.iterrows():
            print(f"   {row['fecha']} | {row['symbol']} | {row['signal']} | {row['explanation']}")
    else:
        print("❌ No hay señales ese día.")

    print(f"\n📊 Total por par:")
    for par, cnt in total_por_par.items():
        print(f"   {par}: {cnt} señales")

if __name__ == "__main__":
    main()
