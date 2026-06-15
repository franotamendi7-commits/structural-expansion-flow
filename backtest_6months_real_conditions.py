"""
Backtest 6 meses (1-Dic-2025 → 1-Jun-2026) con reglas de salida reales:
- TP1 parcial (60%), SL al breakeven, resto a TP2
- SL al breakeven cuando el precio alcanza el 50% del camino al TP1
- Comisión taker 0.05% en entrada y cada salida
- Spread estimado 0.02% aplicado al precio de entrada
"""
import sys, os, time
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, date, timezone
from engine.scalping_engine import ScalpingEngine, DataFetcher
import engine.scalping_engine as eng_module

# ---------- CONFIGURACIÓN ----------
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
START_DATE = datetime(2025, 12, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 6, 1, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01

COMMISSION_RATE = 0.0005
SPREAD_PCT = 0.0002

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
            print(f"Error de conexión: {e}")
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

def simulate_exit(direction, entry_real, sl_original, tp1, contracts_total, candles, start_idx):
    if direction == 'bullish':
        tp_distance = tp1 - entry_real
        tp2 = entry_real + 2 * tp_distance
        half_target = entry_real + 0.5 * tp_distance
    else:
        tp_distance = entry_real - tp1
        tp2 = entry_real - 2 * tp_distance
        half_target = entry_real - 0.5 * tp_distance

    remaining_contracts = contracts_total
    sl_active = sl_original
    breakeven_moved = False
    partial_done = False
    events = []

    for j in range(start_idx+1, len(candles)):
        high = candles[j]['high']
        low = candles[j]['low']

        if direction == 'bullish':
            if not breakeven_moved and high >= half_target:
                sl_active = entry_real
                breakeven_moved = True
                events.append(f"breakeven_50% en vela {j}")

            if not partial_done and high >= tp1:
                close_contracts = contracts_total * 0.6
                exit_price = tp1
                pnl_partial = (exit_price - entry_real) * close_contracts
                commission_exit = exit_price * close_contracts * COMMISSION_RATE
                events.append({
                    "type": "tp1_partial",
                    "contracts": close_contracts,
                    "exit_price": exit_price,
                    "pnl_gross": pnl_partial,
                    "commission": commission_exit
                })
                remaining_contracts = contracts_total * 0.4
                sl_active = entry_real
                breakeven_moved = True
                partial_done = True

            if partial_done and high >= tp2:
                exit_price = tp2
                pnl_rem = (exit_price - entry_real) * remaining_contracts
                commission_exit = exit_price * remaining_contracts * COMMISSION_RATE
                events.append({
                    "type": "tp2",
                    "contracts": remaining_contracts,
                    "exit_price": exit_price,
                    "pnl_gross": pnl_rem,
                    "commission": commission_exit
                })
                remaining_contracts = 0
                break

            if low <= sl_active:
                exit_price = sl_active
                if not partial_done:
                    pnl_full = (exit_price - entry_real) * contracts_total
                    commission_exit = exit_price * contracts_total * COMMISSION_RATE
                    events.append({
                        "type": "sl_full",
                        "contracts": contracts_total,
                        "exit_price": exit_price,
                        "pnl_gross": pnl_full,
                        "commission": commission_exit
                    })
                    remaining_contracts = 0
                else:
                    pnl_rem = (exit_price - entry_real) * remaining_contracts
                    commission_exit = exit_price * remaining_contracts * COMMISSION_RATE
                    events.append({
                        "type": "sl_remaining",
                        "contracts": remaining_contracts,
                        "exit_price": exit_price,
                        "pnl_gross": pnl_rem,
                        "commission": commission_exit
                    })
                    remaining_contracts = 0
                break

        else:
            if not breakeven_moved and low <= half_target:
                sl_active = entry_real
                breakeven_moved = True
                events.append(f"breakeven_50% en vela {j}")

            if not partial_done and low <= tp1:
                close_contracts = contracts_total * 0.6
                exit_price = tp1
                pnl_partial = (entry_real - exit_price) * close_contracts
                commission_exit = exit_price * close_contracts * COMMISSION_RATE
                events.append({
                    "type": "tp1_partial",
                    "contracts": close_contracts,
                    "exit_price": exit_price,
                    "pnl_gross": pnl_partial,
                    "commission": commission_exit
                })
                remaining_contracts = contracts_total * 0.4
                sl_active = entry_real
                breakeven_moved = True
                partial_done = True

            if partial_done and low <= tp2:
                exit_price = tp2
                pnl_rem = (entry_real - exit_price) * remaining_contracts
                commission_exit = exit_price * remaining_contracts * COMMISSION_RATE
                events.append({
                    "type": "tp2",
                    "contracts": remaining_contracts,
                    "exit_price": exit_price,
                    "pnl_gross": pnl_rem,
                    "commission": commission_exit
                })
                remaining_contracts = 0
                break

            if high >= sl_active:
                exit_price = sl_active
                if not partial_done:
                    pnl_full = (entry_real - exit_price) * contracts_total
                    commission_exit = exit_price * contracts_total * COMMISSION_RATE
                    events.append({
                        "type": "sl_full",
                        "contracts": contracts_total,
                        "exit_price": exit_price,
                        "pnl_gross": pnl_full,
                        "commission": commission_exit
                    })
                    remaining_contracts = 0
                else:
                    pnl_rem = (entry_real - exit_price) * remaining_contracts
                    commission_exit = exit_price * remaining_contracts * COMMISSION_RATE
                    events.append({
                        "type": "sl_remaining",
                        "contracts": remaining_contracts,
                        "exit_price": exit_price,
                        "pnl_gross": pnl_rem,
                        "commission": commission_exit
                    })
                    remaining_contracts = 0
                break

    if remaining_contracts > 0:
        last_price = candles[-1]['close']
        events.append({
            "type": "forced_close",
            "contracts": remaining_contracts,
            "exit_price": last_price,
            "pnl_gross": 0
        })
        if direction == 'bullish':
            pnl_f = (last_price - entry_real) * remaining_contracts
        else:
            pnl_f = (entry_real - last_price) * remaining_contracts
        events[-1]['pnl_gross'] = pnl_f
        commission_exit = last_price * remaining_contracts * COMMISSION_RATE
        events[-1]['commission'] = commission_exit

    notional_entry = entry_real * contracts_total
    commission_entry = notional_entry * COMMISSION_RATE

    pnl_neto = -commission_entry
    for ev in events:
        if isinstance(ev, dict) and 'pnl_gross' in ev:
            pnl_neto += ev['pnl_gross'] - ev['commission']

    if any(isinstance(ev, dict) and ev.get('type') == 'tp2' for ev in events):
        exit_type = 'tp2_reached'
    elif any(isinstance(ev, dict) and ev.get('type') == 'tp1_partial' for ev in events):
        exit_type = 'tp1_partial_then_sl'
    else:
        exit_type = 'full_sl'

    return pnl_neto, events, exit_type

def backtest_pair(symbol, capital):
    print(f"\n🔍 Backtesting {symbol}...")
    data = fetch_historical(symbol, START_DATE, END_DATE)
    candles = data['5m']
    if not candles or len(candles) < 100:
        print(f"  {symbol}: datos insuficientes")
        return [], capital

    engine = ScalpingEngine(symbol=symbol, capital=capital, risk_pct=FIXED_RISK_PCT, debug_filters=False)
    trades = []
    stats = {'tp2_reached': 0, 'tp1_partial_then_sl': 0, 'full_sl': 0}

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

            if direction == 'bullish':
                entry_real = entry * (1 + SPREAD_PCT)
            else:
                entry_real = entry * (1 - SPREAD_PCT)

            pnl_neto, events, exit_type = simulate_exit(
                direction, entry_real, sl, tp1, contracts, candles, i
            )

            capital += pnl_neto

            trades.append({
                'symbol': symbol,
                'entry_time': datetime.fromtimestamp(candles[i]['timestamp']/1000, tz=timezone.utc).isoformat(),
                'direction': direction,
                'entry_signal': entry,
                'entry_real': round(entry_real, 6),
                'sl_original': sl,
                'tp1': tp1,
                'contracts': contracts,
                'pnl_neto': round(pnl_neto, 4),
                'exit_events': str(events),
                'exit_type': exit_type
            })

            stats[exit_type] += 1

    eng_module.SessionFilter.is_trading_session = original_is_session
    print(f"  {symbol}: TRADES={len(trades)} | Capital final=${capital:,.2f}")
    return trades, capital, stats

def main():
    all_trades = []
    capital = INITIAL_CAPITAL
    total_stats = {'tp2_reached': 0, 'tp1_partial_then_sl': 0, 'full_sl': 0}

    for sym in SYMBOLS:
        trades, capital, stats = backtest_pair(sym, capital)
        all_trades.extend(trades)
        for k in total_stats:
            total_stats[k] += stats[k]

    if all_trades:
        df = pd.DataFrame(all_trades)
        csv_name = 'backtest_6months_real_conditions.csv'
        df.to_csv(csv_name, index=False)
        print(f"\n📁 {len(df)} trades guardados en {csv_name}")

        win_rate = (df['pnl_neto'] > 0).mean() * 100
        total_pnl = df['pnl_neto'].sum()
        df['capital_curve'] = INITIAL_CAPITAL + df['pnl_neto'].cumsum()
        peak_capital = df['capital_curve'].max()
        max_dd = (df['capital_curve'].cummax() - df['capital_curve']).max()
        max_dd_pct = (max_dd / peak_capital * 100) if peak_capital > 0 else 0

        print("\n========== RESUMEN 6 MESES (CONDICIONES REALES) ==========")
        print(f"Trades totales:            {len(df)}")
        print(f"Win Rate:                  {win_rate:.1f}%")
        print(f"PnL neto total:            ${total_pnl:,.2f}")
        print(f"Drawdown máximo:           ${max_dd:,.2f} ({max_dd_pct:.1f}% del pico)")
        print(f"Capital final:             ${capital:,.2f}")
        print(f"Peak capital:              ${peak_capital:,.2f}")
        print("------------------------------------------------------------")
        print("Desglose de salidas:")
        print(f"  TP2 alcanzado (parcial + remanente): {total_stats['tp2_reached']}")
        print(f"  TP1 parcial, luego SL/breakeven:     {total_stats['tp1_partial_then_sl']}")
        print(f"  SL completo (sin parcial):           {total_stats['full_sl']}")
    else:
        print("\nNo se generaron trades.")

if __name__ == "__main__":
    main()
