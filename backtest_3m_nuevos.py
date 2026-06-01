"""
Backtest 3 meses (27‑Feb‑2026 → 27‑May‑2026) para XRPUSDT, LTCUSDT, DOGEUSDT.
Riesgo fijo 1% por operación, sin gestión dinámica.
"""
import sys, os, time
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone
from engine.scalping_engine import ScalpingEngine, DataFetcher
import engine.scalping_engine as eng_module

# ─── CONFIGURACIÓN ─────────────────────────────────────────────
SYMBOLS = ["XRPUSDT", "LTCUSDT", "DOGEUSDT"]
START_DATE = datetime(2026, 2, 27, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 5, 27, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT   = 0.01
OUTPUT_DIR = "backtest_results"

# Crear carpeta de resultados si no existe
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── DESCARGA DE VELAS ─────────────────────────────────────────
def fetch_klines_range(symbol, interval, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    all_klines = []
    page = 0
    max_pages = 50
    while start_ms < end_ms and page < max_pages:
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': MAX_KLINES,
            'startTime': start_ms,
            'endTime': end_ms
        }
        resp = requests.get(BASE_URL, params=params, timeout=30)
        if resp.status_code != 200:
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
        page += 1
        time.sleep(0.3)
    # Filtrar exactamente dentro del rango
    all_klines = [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]
    return all_klines

def fetch_historical(symbol, start, end):
    print(f"  {symbol}: descargando...")
    k1d  = fetch_klines_range(symbol, '1d', start, end)
    k4h  = fetch_klines_range(symbol, '4h', start, end)
    k1h  = fetch_klines_range(symbol, '1h', start, end)
    k15m = fetch_klines_range(symbol, '15m', start, end)
    k5m  = fetch_klines_range(symbol, '5m', start, end)
    print(f"     -> {len(k1d)}d {len(k4h)}4h {len(k1h)}1h {len(k15m)}15m {len(k5m)}5m")
    return {'1d': k1d, '4h': k4h, '1h': k1h, '15m': k15m, '5m': k5m}

# ─── BACKTEST DE UN PAR ────────────────────────────────────────
def backtest_pair(symbol, capital):
    print(f"\n🔍 Backtesting {symbol}...")
    data = fetch_historical(symbol, START_DATE, END_DATE)
    candles = data['5m']
    if not candles or len(candles) < 100:
        print(f"  {symbol}: datos insuficientes")
        return [], capital

    engine = ScalpingEngine(symbol=symbol, capital=capital, risk_pct=FIXED_RISK_PCT, debug_filters=False)
    trades = []

    # Parche para ignorar restricción horaria en backtest
    original_is_session = eng_module.SessionFilter.is_trading_session
    eng_module.SessionFilter.is_trading_session = staticmethod(lambda: True)
    original_get_ticker = eng_module.get_ticker

    for i in range(100, len(candles)):
        current_ts = candles[i]['timestamp']
        truncated = {
            '1d':  [k for k in data['1d']  if k['timestamp'] <= current_ts],
            '4h':  [k for k in data['4h']  if k['timestamp'] <= current_ts],
            '1h':  [k for k in data['1h']  if k['timestamp'] <= current_ts],
            '15m': [k for k in data['15m'] if k['timestamp'] <= current_ts],
            '5m':  candles[:i+1]
        }
        if len(truncated['1d']) < 5 or len(truncated['4h']) < 5:
            continue

        original_fetch = DataFetcher.fetch
        DataFetcher.fetch = lambda self, t=truncated: t
        current_price = candles[i]['close']
        eng_module.get_ticker = lambda symbol=None, price=current_price: price

        engine.capital = capital
        engine.risk_pct = FIXED_RISK_PCT

        try:
            res = engine.run()
        except Exception:
            res = {'signal': 'WAIT', 'setup_state': 'INVALID'}

        DataFetcher.fetch = original_fetch
        eng_module.get_ticker = original_get_ticker

        if res['setup_state'] == 'EXECUTE' and res.get('trade'):
            trade = res['trade']
            if trade is None:
                continue
            entry = trade['entry']
            sl = trade['sl']
            tp1 = trade['tp1']
            contracts = trade.get('contracts', 0)
            direction = res.get('direction', 'neutral')
            if entry == 0 or contracts == 0:
                continue

            # Simular salida
            exit_price = None
            exit_reason = None
            for j in range(i+1, len(candles)):
                high = candles[j]['high']
                low = candles[j]['low']
                if direction == 'bullish':
                    if low <= sl:
                        exit_price = sl; exit_reason = 'stop_loss'; break
                    if high >= tp1:
                        exit_price = tp1; exit_reason = 'take_profit'; break
                else:
                    if high >= sl:
                        exit_price = sl; exit_reason = 'stop_loss'; break
                    if low <= tp1:
                        exit_price = tp1; exit_reason = 'take_profit'; break

            if exit_price is not None:
                pnl = (exit_price - entry) * contracts if direction == 'bullish' else (entry - exit_price) * contracts
                capital += pnl
                exit_dt = datetime.fromtimestamp(candles[j]['timestamp']/1000, tz=timezone.utc)
                trades.append({
                    'symbol': symbol,
                    'entry_time': datetime.fromtimestamp(candles[i]['timestamp']/1000, tz=timezone.utc).isoformat(),
                    'exit_time': exit_dt.isoformat(),
                    'signal': direction,
                    'entry': entry,
                    'exit': exit_price,
                    'pnl': pnl,
                    'reason': exit_reason,
                    'contracts': contracts
                })

    eng_module.SessionFilter.is_trading_session = original_is_session
    print(f"  {symbol}: TRADES={len(trades)} | Capital final=${capital:,.2f}")
    return trades, capital

# ─── MAIN ──────────────────────────────────────────────────────
def main():
    all_trades = []
    capital = INITIAL_CAPITAL
    for sym in SYMBOLS:
        trades, capital = backtest_pair(sym, capital)
        all_trades.extend(trades)

    if all_trades:
        df = pd.DataFrame(all_trades)
        csv_path = os.path.join(OUTPUT_DIR, 'backtest_3m_nuevos.csv')
        df.to_csv(csv_path, index=False)
        print(f"\n📁 {len(df)} trades guardados en {csv_path}")

        total_pnl = df['pnl'].sum()
        win_rate = len(df[df['pnl'] > 0]) / len(df) * 100
        df['capital_curve'] = INITIAL_CAPITAL + df['pnl'].cumsum()
        max_dd = (df['capital_curve'].cummax() - df['capital_curve']).max()

        print(f"Win rate general: {win_rate:.1f}%")
        print(f"PnL total: ${total_pnl:,.2f}")
        print(f"Máximo drawdown: ${max_dd:,.2f}")

        # Desglose por par
        for sym in SYMBOLS:
            sym_trades = df[df['symbol'] == sym]
            if len(sym_trades) > 0:
                wr = len(sym_trades[sym_trades['pnl'] > 0]) / len(sym_trades) * 100
                pnl = sym_trades['pnl'].sum()
                print(f"{sym}: trades={len(sym_trades)}, WR={wr:.1f}%, PnL=${pnl:,.2f}")
            else:
                print(f"{sym}: 0 trades")
    else:
        print("\nNo se generaron trades en ningún par.")

if __name__ == "__main__":
    main()
