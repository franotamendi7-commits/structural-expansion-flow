"""
Backtest 1 mes (1-May-2026 → 1-Jun-2026) con riesgo fijo 1% y reglas de salida del PaperTrader.
Pares: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT

Reglas de salida:
- TP1: cierra 60% de la posición y mueve SL al breakeven.
- El 40% restante corre hacia TP2.
- Si el precio alcanza el 50% del camino hacia TP1, mueve SL al breakeven (antes de tocar TP1).
- Si toca SL (original o de breakeven), cierra toda la posición.
- Si toca TP2, cierra el 40% restante.
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
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
START_DATE = datetime(2026, 5, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 1, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01

# ---------- Descarga de datos ----------
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
        except:
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

def fetch_historical(symbol, start, end):
    print(f"  {symbol}: descargando datos...")
    k1d  = fetch_klines_range(symbol, '1d', start, end)
    k4h  = fetch_klines_range(symbol, '4h', start, end)
    k1h  = fetch_klines_range(symbol, '1h', start, end)
    k15m = fetch_klines_range(symbol, '15m', start, end)
    k5m  = fetch_klines_range(symbol, '5m', start, end)
    print(f"     -> {len(k1d)}d {len(k4h)}4h {len(k1h)}1h {len(k15m)}15m {len(k5m)}5m")
    return {'1d': k1d, '4h': k4h, '1h': k1h, '15m': k15m, '5m': k5m}

# ---------- Backtest por par con nuevas reglas ----------
def backtest_pair(symbol, capital):
    print(f"\n🔍 Backtesting {symbol}...")
    data = fetch_historical(symbol, START_DATE, END_DATE)
    candles = data['5m']
    if not candles or len(candles) < 100:
        print(f"  {symbol}: datos insuficientes")
        return [], capital

    engine = ScalpingEngine(symbol=symbol, capital=capital, risk_pct=FIXED_RISK_PCT, debug_filters=False)
    trades = []

    original_get_ticker = eng_module.get_ticker
    original_is_session = eng_module.SessionFilter.is_trading_session
    eng_module.SessionFilter.is_trading_session = staticmethod(lambda: True)

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

        engine.capital = capital
        try:
            res = engine.run()
        except:
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
            # Obtener tp2, si no existe en el motor, calcular como 2:1 desde tp1
            tp2 = trade.get('tp2', None)
            if tp2 is None:
                if direction == 'bullish':
                    tp2 = entry + 2 * (tp1 - entry)
                else:
                    tp2 = entry - 2 * (entry - tp1)

            contracts = trade.get('contracts', 0)
            direction = res.get('direction', 'neutral')
            if entry == 0 or contracts == 0:
                continue

            # ---------- Simulación de salida ----------
            # Estado inicial
            position_size = contracts
            part_closed = False
            sl_current = sl
            tp1_hit = False
            tp2_hit = False
            sl_hit = False
            half_tp1_hit = False  # 50% hacia TP1
            exit_price = None
            pnl_parts = []   # guardar pnl parcial

            # Precios objetivo para el 50% del camino a TP1
            if direction == 'bullish':
                half_tp1_price = (entry + tp1) / 2
            else:
                half_tp1_price = (entry + tp1) / 2  # tp1 es más bajo en bajista

            # Recorremos velas futuras
            for j in range(i+1, len(candles)):
                high = candles[j]['high']
                low = candles[j]['low']

                if direction == 'bullish':
                    # Comprobar niveles en orden de importancia
                    if not part_closed:
                        # Mover SL al breakeven si llegó al 50% del TP1
                        if not half_tp1_hit and high >= half_tp1_price:
                            sl_current = entry
                            half_tp1_hit = True
                        # Tocar TP1 -> cierre parcial 60%
                        if high >= tp1:
                            # Cerrar 60% en tp1
                            partial_size = position_size * 0.6
                            pnl_parts.append((tp1 - entry) * partial_size)
                            position_size = position_size * 0.4
                            sl_current = entry   # mover al breakeven
                            part_closed = True
                            tp1_hit = True
                            # Si después de esto el TP2 está por debajo del tp1? No en largos, tp2 > tp1
                        # Tocar SL (original o breakeven)
                        if low <= sl_current:
                            exit_price = sl_current
                            sl_hit = True
                            if part_closed:
                                # Cerrar el resto en SL (que es entry si se movió a breakeven)
                                pnl_parts.append((sl_current - entry) * position_size)
                            else:
                                pnl_parts.append((sl_current - entry) * position_size)
                            break
                    else:
                        # Ya se tomó parcial, SL está en breakeven (entry)
                        if low <= sl_current:   # es entry
                            exit_price = sl_current
                            sl_hit = True
                            pnl_parts.append((entry - entry) * position_size)  # cero
                            break
                        if high >= tp2:
                            exit_price = tp2
                            tp2_hit = True
                            pnl_parts.append((tp2 - entry) * position_size)
                            break

                else:  # dirección bajista
                    if not part_closed:
                        if not half_tp1_hit and low <= half_tp1_price:
                            sl_current = entry
                            half_tp1_hit = True
                        if low <= tp1:
                            partial_size = position_size * 0.6
                            pnl_parts.append((entry - tp1) * partial_size)
                            position_size = position_size * 0.4
                            sl_current = entry
                            part_closed = True
                            tp1_hit = True
                        if high >= sl_current:
                            exit_price = sl_current
                            sl_hit = True
                            if part_closed:
                                pnl_parts.append((entry - sl_current) * position_size)
                            else:
                                pnl_parts.append((entry - sl_current) * position_size)
                            break
                    else:
                        if high >= sl_current:   # entry
                            exit_price = sl_current
                            sl_hit = True
                            pnl_parts.append((entry - entry) * position_size)
                            break
                        if low <= tp2:
                            exit_price = tp2
                            tp2_hit = True
                            pnl_parts.append((entry - tp2) * position_size)
                            break

            # Si no se cerró dentro de las velas, cerrar al último precio
            if not sl_hit and not tp2_hit:
                last_candle = candles[-1]
                exit_price = last_candle['close']
                if part_closed:
                    # cerrar el resto a mercado
                    if direction == 'bullish':
                        pnl_parts.append((exit_price - entry) * position_size)
                    else:
                        pnl_parts.append((entry - exit_price) * position_size)

            # Calcular PnL total y actualizar capital
            total_pnl = sum(pnl_parts)
            capital += total_pnl

            # Determinar tipo de cierre para estadísticas
            if tp2_hit:
                close_type = "TP1_partial_then_TP2"
            elif tp1_hit and (sl_hit or not tp2_hit):
                # parcial en TP1, luego SL (breakeven) o cierre al final
                if sl_hit:
                    close_type = "TP1_partial_then_BE"
                else:
                    close_type = "TP1_partial_then_market_close"
            else:
                # nunca tocó TP1
                if half_tp1_hit and sl_hit and sl_current == entry:
                    close_type = "BE_after_50%"
                else:
                    close_type = "SL"

            # Registrar el trade con toda la información
            trades.append({
                'symbol': symbol,
                'entry_time': datetime.fromtimestamp(candles[i]['timestamp']/1000, tz=timezone.utc).isoformat(),
                'direction': direction,
                'entry': entry,
                'sl_original': sl,
                'tp1': tp1,
                'tp2': tp2,
                'exit_price': exit_price,
                'pnl': total_pnl,
                'contracts': contracts,
                'close_type': close_type,
                'tp1_hit': tp1_hit,
                'tp2_hit': tp2_hit,
                'half_tp1_hit': half_tp1_hit
            })

    # Restaurar sesión
    eng_module.SessionFilter.is_trading_session = original_is_session
    print(f"  {symbol}: TRADES={len(trades)} | Capital final=${capital:,.2f}")
    return trades, capital

# ========== MAIN ==========
def main():
    all_trades = []
    capital = INITIAL_CAPITAL

    for sym in SYMBOLS:
        trades, capital = backtest_pair(sym, capital)
        all_trades.extend(trades)

    if all_trades:
        df = pd.DataFrame(all_trades)
        csv_name = 'backtest_1month_new_rules.csv'
        df.to_csv(csv_name, index=False)
        print(f"\n📁 {len(df)} trades guardados en {csv_name}")

        # Métricas generales
        win_rate = (df['pnl'] > 0).mean() * 100
        total_pnl = df['pnl'].sum()
        curve = (INITIAL_CAPITAL + df['pnl'].cumsum()).values
        peak = np.maximum.accumulate(curve)
        max_dd = (peak - curve).max()
        final_capital = capital

        print("\n========== RESUMEN BACKTEST 1 MES (NUEVAS REGLAS) ==========")
        print(f"Trades totales:      {len(df)}")
        print(f"Win Rate:            {win_rate:.1f}%")
        print(f"PnL total:           ${total_pnl:,.2f}")
        print(f"Drawdown máximo:     ${max_dd:,.2f} ({max_dd/peak.max()*100:.1f}% del pico)")
        print(f"Capital final:       ${final_capital:,.2f}")

        # Desglose de tipos de cierre
        print("\n--- Tipos de cierre ---")
        tp1_tp2 = df[df['close_type'] == 'TP1_partial_then_TP2']
        tp1_be  = df[df['close_type'] == 'TP1_partial_then_BE']
        be_50   = df[df['close_type'] == 'BE_after_50%']
        sl      = df[df['close_type'] == 'SL']
        other   = df[~df['close_type'].isin(['TP1_partial_then_TP2', 'TP1_partial_then_BE', 'BE_after_50%', 'SL'])]

        print(f"Cierre parcial en TP1 y luego TP2: {len(tp1_tp2)} trades")
        print(f"Cierre parcial en TP1 y luego SL (breakeven): {len(tp1_be)} trades")
        print(f"SL movido a breakeven al 50% y cerrado en BE: {len(be_50)} trades")
        print(f"Cierre completo por SL (sin tocar TP1): {len(sl)} trades")
        if len(other) > 0:
            print(f"Otros cierres: {len(other)} trades ({', '.join(other['close_type'].unique())})")

    else:
        print("\nNo se generaron trades.")

if __name__ == "__main__":
    main()
