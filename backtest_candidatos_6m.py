"""
Backtesting de 6 meses (27-Dic-2025 al 27-May-2026) con riesgo fijo 1% para pares candidatos.
Evalúa LINKUSDT, BNBUSDT, AVAXUSDT, ADAUSDT, MATICUSDT.
Criterio automático de aprobación: WR > 60% y drawdown máximo <= 20%.
"""
import sys, os, time
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, date, timezone
from engine.scalping_engine import ScalpingEngine, DataFetcher
import engine.scalping_engine as eng_module

# ========== CONFIGURACIÓN ==========
SYMBOLS = ["LINKUSDT", "BNBUSDT", "AVAXUSDT", "ADAUSDT", "MATICUSDT"]
START_DATE = datetime(2025, 12, 27, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 5, 27, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01

# ========== DESCARGA DE DATOS ==========
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
        page += 1
        time.sleep(0.3)
    # Filtrar exactamente dentro del rango
    all_klines = [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]
    return all_klines

def fetch_historical(symbol, start, end):
    print(f"  {symbol}: descargando datos...")
    k1d  = fetch_klines_range(symbol, '1d', start, end)
    k4h  = fetch_klines_range(symbol, '4h', start, end)
    k1h  = fetch_klines_range(symbol, '1h', start, end)
    k15m = fetch_klines_range(symbol, '15m', start, end)
    k5m  = fetch_klines_range(symbol, '5m', start, end)
    print(f"     -> {len(k1d)}d {len(k4h)}4h {len(k1h)}1h {len(k15m)}15m {len(k5m)}5m")
    return {'1d': k1d, '4h': k4h, '1h': k1h, '15m': k15m, '5m': k5m}

# ========== BACKTEST POR PAR (RIESGO FIJO) ==========
def backtest_pair_fixed(symbol, capital=INITIAL_CAPITAL):
    print(f"\n🔍 Backtesting {symbol}...")
    data = fetch_historical(symbol, START_DATE, END_DATE)
    candles = data['5m']
    if not candles or len(candles) < 100:
        print(f"  {symbol}: datos insuficientes")
        return [], capital, 0.0

    engine = ScalpingEngine(symbol=symbol, capital=capital, risk_pct=FIXED_RISK_PCT, debug_filters=False)
    trades = []

    original_get_ticker = eng_module.get_ticker
    # Parche de sesión siempre activa
    original_is_session = eng_module.SessionFilter.is_trading_session
    eng_module.SessionFilter.is_trading_session = staticmethod(lambda: True)

    # Curva de capital para calcular drawdown
    capital_curve = [capital]
    peak = capital

    for i in range(100, len(candles)):
        current_ts = candles[i]['timestamp']
        truncated = {
            '1d':  [k for k in data['1d']  if k['timestamp'] <= current_ts],
            '4h':  [k for k in data['4h']  if k['timestamp'] <= current_ts],
            '1h':  [k for k in data['1h']  if k['timestamp'] <= current_ts],
            '15m': [k for k in data['15m'] if k['timestamp'] <= current_ts],
            '5m':  candles[:i+1]
        }
        if len(truncated['1d']) < 5 or len(truncated['4h']) < 5 or len(truncated['1h']) < 5:
            continue

        original_fetch = DataFetcher.fetch
        DataFetcher.fetch = lambda self, t=truncated: t

        current_price = candles[i]['close']
        eng_module.get_ticker = lambda symbol=None, price=current_price: price

        engine.capital = capital   # actualizar capital en el motor

        try:
            res = engine.run()
        except Exception as e:
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

            exit_price = None
            exit_reason = None
            for j in range(i+1, len(candles)):
                high = candles[j]['high']
                low = candles[j]['low']
                if direction == 'bullish':
                    if low <= sl:
                        exit_price = sl
                        exit_reason = 'stop_loss'
                        break
                    if high >= tp1:
                        exit_price = tp1
                        exit_reason = 'take_profit'
                        break
                else:
                    if high >= sl:
                        exit_price = sl
                        exit_reason = 'stop_loss'
                        break
                    if low <= tp1:
                        exit_price = tp1
                        exit_reason = 'take_profit'
                        break

            if exit_price is not None:
                if direction == 'bullish':
                    pnl = (exit_price - entry) * contracts
                else:
                    pnl = (entry - exit_price) * contracts

                capital += pnl
                capital_curve.append(capital)
                if capital > peak:
                    peak = capital

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
                    'risk_pct_used': FIXED_RISK_PCT,
                    'contracts': contracts
                })

    # Restaurar filtro de sesión
    eng_module.SessionFilter.is_trading_session = original_is_session

    # Calcular drawdown máximo (en $ y % del pico)
    max_dd = 0.0
    if len(capital_curve) > 1:
        curve_series = pd.Series(capital_curve)
        running_max = curve_series.cummax()
        dd_series = running_max - curve_series
        max_dd = dd_series.max()
    dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0

    print(f"  {symbol}: TRADES={len(trades)} | Capital final=${capital:,.2f} | Max DD=${max_dd:,.2f} ({dd_pct:.1f}%)")
    return trades, capital, dd_pct

# ========== MAIN ==========
def main():
    os.makedirs("backtest_results", exist_ok=True)
    all_trades = []
    resultados = []

    for sym in SYMBOLS:
        trades, capital_final, dd_pct = backtest_pair_fixed(sym)
        if trades:
            df_par = pd.DataFrame(trades)
            win_rate = (df_par['pnl'] > 0).mean() * 100
            total_pnl = df_par['pnl'].sum()
            max_dd_dolar = (pd.Series([INITIAL_CAPITAL] + df_par['pnl'].tolist()).cummax() - 
                            pd.Series([INITIAL_CAPITAL] + df_par['pnl'].tolist()).cumsum()).max()
            veredicto = "APROBADO" if (win_rate > 60 and dd_pct <= 20) else "RECHAZADO"
            resultados.append({
                'Par': sym,
                'Trades': len(trades),
                'Win Rate (%)': f"{win_rate:.1f}%",
                'PnL Total ($)': f"${total_pnl:,.2f}",
                'Max DD ($)': f"${max_dd_dolar:,.2f}",
                'Max DD (%)': f"{dd_pct:.1f}%",
                'Veredicto': veredicto
            })
            all_trades.extend(trades)
        else:
            resultados.append({
                'Par': sym,
                'Trades': 0,
                'Win Rate (%)': "N/A",
                'PnL Total ($)': "$0.00",
                'Max DD ($)': "$0.00",
                'Max DD (%)': "0.0%",
                'Veredicto': "RECHAZADO (sin datos)"
            })

    # Guardar todos los trades en CSV
    if all_trades:
        df_all = pd.DataFrame(all_trades)
        csv_path = "backtest_results/backtest_6m_candidatos.csv"
        df_all.to_csv(csv_path, index=False)
        print(f"\n📁 Trades guardados en {csv_path}")

    # Imprimir resumen final
    print("\n" + "=" * 80)
    print("RESUMEN DE EVALUACIÓN DE PARES CANDIDATOS (6 meses, riesgo fijo 1%)")
    print("=" * 80)
    print(f"{'Par':<12} {'Trades':<8} {'WR':<10} {'PnL Total':<15} {'Max DD':<15} {'Veredicto'}")
    print("-" * 80)
    for r in resultados:
        print(f"{r['Par']:<12} {r['Trades']:<8} {r['Win Rate (%)']:<10} {r['PnL Total ($)']:<15} {r['Max DD (%)']:<15} {r['Veredicto']}")
    print("-" * 80)

    aprobados = [r['Par'] for r in resultados if r['Veredicto'] == 'APROBADO']
    if aprobados:
        print(f"✅ Pares aprobados: {', '.join(aprobados)}")
    else:
        print("❌ Ningún par cumplió los criterios.")
    print("=" * 80)

if __name__ == "__main__":
    main()