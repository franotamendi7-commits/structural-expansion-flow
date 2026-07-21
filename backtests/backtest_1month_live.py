import sys, os, time
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone
from engine.scalping_engine import ScalpingEngine, DataFetcher
import engine.scalping_engine as eng_module

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
START_DATE = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 17, 23, 59, tzinfo=timezone.utc)
BASE_URL   = "https://api.binance.com/api/v3/klines"
MAX_KLINES = 1000

def fetch_klines_range(symbol, interval, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    all_klines = []
    while start_ms < end_ms:
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': MAX_KLINES,
            'startTime': start_ms,
            'endTime': end_ms
        }
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except Exception as e:
            print(f"Error conexión: {e}")
            time.sleep(1)
            continue
        if resp.status_code != 200:
            print(f"Error {resp.status_code}: {resp.text}")
            time.sleep(1)
            continue
        data = resp.json()
        if not data:
            break
        batch = [{
            'timestamp': int(k[0]),
            'open': float(k[1]),
            'high': float(k[2]),
            'low': float(k[3]),
            'close': float(k[4]),
            'volume': float(k[5])
        } for k in data]
        all_klines.extend(batch)
        start_ms = batch[-1]['timestamp'] + 1
        time.sleep(0.3)
    all_klines = [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]
    return all_klines

def fetch_all_data(symbol):
    print(f"Descargando {symbol}...")
    return {
        '5m':  fetch_klines_range(symbol, '5m', START_DATE, END_DATE),
        '15m': fetch_klines_range(symbol, '15m', START_DATE, END_DATE),
        '1h':  fetch_klines_range(symbol, '1h', START_DATE, END_DATE),
        '4h':  fetch_klines_range(symbol, '4h', START_DATE, END_DATE),
        '1d':  fetch_klines_range(symbol, '1d', START_DATE, END_DATE),
    }

original_is_session = eng_module.SessionFilter.is_trading_session
eng_module.SessionFilter.is_trading_session = staticmethod(lambda: True)

signals_by_day = {}
total_signals = 0

for sym in SYMBOLS:
    data = fetch_all_data(sym)
    candles_1h = data['1h']
    if len(candles_1h) < 10:
        print(f"{sym}: datos insuficientes")
        continue

    engine = ScalpingEngine(symbol=sym, capital=1000, risk_pct=0.01, debug_filters=False)
    original_get_ticker = eng_module.get_ticker

    for i in range(10, len(candles_1h)):
        ts = candles_1h[i]['timestamp']
        truncated = {
            '5m':  [k for k in data['5m']  if k['timestamp'] <= ts],
            '15m': [k for k in data['15m'] if k['timestamp'] <= ts],
            '1h':  candles_1h[:i+1],
            '4h':  [k for k in data['4h']  if k['timestamp'] <= ts],
            '1d':  [k for k in data['1d']  if k['timestamp'] <= ts],
        }
        original_fetch = DataFetcher.fetch
        DataFetcher.fetch = lambda self, t=truncated: t
        current_price = candles_1h[i]['close']
        eng_module.get_ticker = lambda symbol=None, price=current_price: price

        try:
            res = engine.run()
        except Exception as e:
            res = {'signal': 'WAIT', 'setup_state': 'INVALID'}

        DataFetcher.fetch = original_fetch
        eng_module.get_ticker = original_get_ticker

        if res['setup_state'] == 'EXECUTE' and res.get('signal') in ('LONG', 'SHORT'):
            signal_time = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
            day_str = signal_time.strftime('%Y-%m-%d')
            score = res.get('score', 'N/A')
            explanation = res.get('explanation', '')
            signal_info = {
                'time': signal_time.strftime('%Y-%m-%d %H:%M UTC'),
                'pair': sym,
                'signal': res['signal'],
                'score': score,
                'explanation': explanation
            }
            signals_by_day.setdefault(day_str, []).append(signal_info)
            total_signals += 1

eng_module.SessionFilter.is_trading_session = original_is_session

if total_signals == 0:
    print("\n0 señales en todo el mes")
else:
    print(f"\nTotal señales: {total_signals}")
    for day in sorted(signals_by_day.keys()):
        day_signals = signals_by_day[day]
        print(f"{day}: {len(day_signals)} señal(es)")
        for s in day_signals:
            print(f"  {s['time']} | {s['pair']} | {s['signal']} | Score: {s['score']} | {s['explanation']}")
