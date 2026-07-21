"""
Backtesting rápido multi‑par (1 mes RELAJADO): 27‑Abr‑2026 al 27‑May‑2026.
Parámetros de riesgo relajados:
- Límite diario: 5% del capital
- Rachas: reducir al 50% con 4 pérdidas, kill switch con 7
- Drawdown: igual (10%→50% riesgo, 15%→25% riesgo)
Incluye parche de sesión siempre activa.
"""
import sys, os, time
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
import requests
from datetime import datetime, date, timedelta, timezone
from engine.scalping_engine import ScalpingEngine, DataFetcher
import engine.scalping_engine as eng_module

# ========== CONFIGURACIÓN ==========
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
START_DATE = datetime(2026, 4, 27, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 5, 27, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
BASE_RISK_PCT = 0.01
MAX_DAILY_LOSS_PCT = 0.05            # ← 5 % (antes 3 %)
MAX_CONSECUTIVE_LOSSES = 7           # ← kill switch en 7 (antes 5)

# ========== GESTIÓN DINÁMICA DE RIESGO (RELAJADA) ==========
class DynamicRiskManager:
    def __init__(self, base_risk_pct=0.01, max_daily_loss_pct=0.05, max_consecutive_losses=7):
        self.base_risk_pct = base_risk_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.daily_pnl = 0.0
        self.last_day = None
        self.consecutive_losses = 0

    def get_adjusted_risk(self, capital, peak_capital):
        # Factor drawdown (sin cambios)
        drawdown = 1.0 - (capital / peak_capital) if peak_capital > 0 else 0
        if drawdown > 0.15:
            factor_dd = 0.25
        elif drawdown > 0.10:
            factor_dd = 0.5
        else:
            factor_dd = 1.0

        # Factor racha de pérdidas (RELAJADO)
        if self.consecutive_losses >= self.max_consecutive_losses:
            return 0.0                     # kill switch con 7
        elif self.consecutive_losses >= 4: # ← reducción al 50% con 4 (antes 3)
            factor_loss = 0.5
        else:
            factor_loss = 1.0

        # Factor límite diario (RELAJADO a 5% del capital)
        if self.daily_pnl < 0 and abs(self.daily_pnl) >= capital * self.max_daily_loss_pct:
            factor_daily = 0.0
        else:
            factor_daily = 1.0

        return self.base_risk_pct * factor_dd * factor_loss * factor_daily

    def register_trade(self, pnl, trade_date=None):
        if trade_date is None:
            trade_date = date.today()
        if self.last_day != trade_date:
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.last_day = trade_date

        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def reset_if_new_day(self, check_date):
        if self.last_day is None or self.last_day != check_date:
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.last_day = check_date

# ========== DESCARGA DE UN RANGO EXACTO ==========
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

# ========== BACKTEST PARA UN PAR ==========
def backtest_pair(symbol, capital, risk_manager, peak_capital):
    print(f"\n🔍 Backtesting {symbol}...")
    data = fetch_historical(symbol, START_DATE, END_DATE)
    candles = data['5m']
    if not candles or len(candles) < 100:
        print(f"  {symbol}: datos insuficientes")
        return [], capital, peak_capital

    first_date = datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).date()
    risk_manager.reset_if_new_day(first_date)

    engine = ScalpingEngine(symbol=symbol, capital=capital, risk_pct=BASE_RISK_PCT, debug_filters=False)
    trades = []

    original_get_ticker = eng_module.get_ticker

    # 👇 PARCHE QUE FUERZA SESIÓN DE TRADING SIEMPRE ACTIVA EN BACKTEST
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

        dyn_risk_pct = risk_manager.get_adjusted_risk(capital, peak_capital)
        if dyn_risk_pct == 0.0:
            continue

        original_fetch = DataFetcher.fetch
        DataFetcher.fetch = lambda self, t=truncated: t

        current_price = candles[i]['close']
        eng_module.get_ticker = lambda symbol=None, price=current_price: price

        engine.capital = capital
        engine.risk_pct = dyn_risk_pct

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

                exit_dt = datetime.fromtimestamp(candles[j]['timestamp']/1000, tz=timezone.utc)
                risk_manager.register_trade(pnl, trade_date=exit_dt.date())

                if capital > peak_capital:
                    peak_capital = capital

                trades.append({
                    'symbol': symbol,
                    'entry_time': datetime.fromtimestamp(candles[i]['timestamp']/1000, tz=timezone.utc).isoformat(),
                    'exit_time': exit_dt.isoformat(),
                    'signal': direction,
                    'entry': entry,
                    'exit': exit_price,
                    'pnl': pnl,
                    'reason': exit_reason,
                    'risk_pct_used': dyn_risk_pct,
                    'contracts': contracts,
                    'consecutive_losses': risk_manager.consecutive_losses,
                    'daily_pnl': risk_manager.daily_pnl
                })

    # 👇 RESTAURAR EL FILTRO DE SESIÓN ORIGINAL
    eng_module.SessionFilter.is_trading_session = original_is_session

    print(f"  {symbol}: TRADES={len(trades)} | Capital final=${capital:,.2f} | Peak=${peak_capital:,.2f}")
    return trades, capital, peak_capital

# ========== MAIN ==========
def main():
    all_trades = []
    capital = INITIAL_CAPITAL
    peak_capital = INITIAL_CAPITAL

    risk_manager = DynamicRiskManager(base_risk_pct=BASE_RISK_PCT,
                                      max_daily_loss_pct=MAX_DAILY_LOSS_PCT,
                                      max_consecutive_losses=MAX_CONSECUTIVE_LOSSES)

    for sym in SYMBOLS:
        trades, capital, peak_capital = backtest_pair(sym, capital, risk_manager, peak_capital)
        all_trades.extend(trades)

    if all_trades:
        df = pd.DataFrame(all_trades)
        csv_name = 'backtest_1month_relaxed.csv'   # ← nombre específico
        df.to_csv(csv_name, index=False)
        print(f"\n📁 {len(df)} trades guardados en {csv_name}")

        win_rate = len(df[df['pnl'] > 0]) / len(df) * 100
        avg_pnl = df['pnl'].mean()
        total_pnl = df['pnl'].sum()
        df['capital_curve'] = INITIAL_CAPITAL + df['pnl'].cumsum()
        max_dd = (df['capital_curve'].cummax() - df['capital_curve']).max()
        print(f"Win rate: {win_rate:.1f}%")
        print(f"PnL promedio: ${avg_pnl:,.2f}")
        print(f"PnL total: ${total_pnl:,.2f}")
        print(f"Máximo drawdown (capital): ${max_dd:,.2f}")
        print(f"Capital final: ${capital:,.2f}")
        print(f"Peak capital: ${peak_capital:,.2f}")
    else:
        print("\nNo se generaron trades en ningún par.")

if __name__ == "__main__":
    main()