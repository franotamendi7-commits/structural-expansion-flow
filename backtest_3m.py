#!/usr/bin/env python3
import numpy as np, pandas as pd, sys, os
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data.binance_feed import get_klines
from engine.scalping_engine import (
    SYMBOL_CONFIG, ChoppinessIndex, SupertrendFilter,
    PatternDetector, WeeklyAdaptiveFibonacci,
    TradeSetupBuilder
)

ACTIVE_PAIRS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
START = datetime.now(timezone.utc) - timedelta(days=90)
END   = datetime.now(timezone.utc)
INITIAL_BALANCE = 100.0
RISK_PCT = 0.01; COMM = 0.0005; SPREAD = 0.0002; SLIPPAGE = 0.001

def apply_slippage(price, side):
    return price*(1+SLIPPAGE) if side=='LONG' else price*(1-SLIPPAGE)

def fetch_klines(sym, interval, start, end):
    k = get_klines(sym, interval, limit=1000)
    if not k: return []
    df = pd.DataFrame(k)
    df['ts'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df[(df['ts']>=start)&(df['ts']<=end)].to_dict('records')

class Backtest:
    def __init__(self):
        self.balance = INITIAL_BALANCE; self.peak = INITIAL_BALANCE; self.trades = []
    def run(self):
        print(f"BACKTEST 3M | {START.date()} → {END.date()}")
        for pair in ACTIVE_PAIRS:
            d = fetch_klines(pair,'1d',START,END)
            h4 = fetch_klines(pair,'4h',START,END)
            if len(h4)<50: continue
            for i in range(50,len(h4)):
                cur = h4[i]; cur_ts = pd.to_datetime(cur['timestamp'], unit='ms', utc=True)
                win4 = h4[i-49:i+1]
                winD = [k for k in d if pd.to_datetime(k['timestamp'], unit='ms', utc=True)<=cur_ts][-30:]
                if len(winD)<10: continue
                fib = WeeklyAdaptiveFibonacci(winD,30)
                pr = float(cur['close']); fb = fib.get_current_block(pr)
                if not fb: continue
                be = PatternDetector.is_bearish_engulfing(win4)
                bu = PatternDetector.is_bullish_engulfing(win4)
                sig = dir = None
                if fb and be: sig, dir = 'SHORT','bearish'
                elif fb and bu: sig, dir = 'LONG','bullish'
                if not sig: continue
                cfg = SYMBOL_CONFIG[pair]
                ci = ChoppinessIndex(neutral_threshold=cfg['choppiness_neutral_threshold']).analyze(win4)
                if not ci['tradeable']: continue
                st = SupertrendFilter(multiplier=cfg['supertrend_multiplier'])
                _,_,lab = st._calc(win4,14)
                if dir=='bullish' and lab!='bullish': continue
                if dir=='bearish' and lab!='bearish': continue
                entry = pr
                sl = max([float(k['high']) for k in win4[-10:]])*1.002 if sig=='SHORT' else min([float(k['low']) for k in win4[-10:]])*0.998
                # CORRECCIÓN: argumentos con nombre
                b = TradeSetupBuilder(
                    entry_price=entry,
                    stop_loss=sl,
                    capital=self.balance,
                    risk_pct=RISK_PCT,
                    trend_h4='neutral',
                    signal_direction=dir,
                    tp_ratio=cfg['tp_ratio'],
                    score=70
                ).build()
                if not b: continue
                entry_ex = apply_slippage(b['entry'],sig)
                sl_ex, tp1_ex, tp2_ex = b['sl'], b['tp1'], b['tp2']
                ctr = b['contracts']; exit_pr = reason = None
                for j in range(i+1,len(h4)):
                    hi = float(h4[j]['high']); lo = float(h4[j]['low'])
                    if dir=='bearish':
                        if lo<=tp1_ex: exit_pr,reason = tp1_ex,'TP1'; break
                        if hi>=sl_ex: exit_pr,reason = sl_ex,'SL'; break
                    else:
                        if hi>=tp1_ex: exit_pr,reason = tp1_ex,'TP1'; break
                        if lo<=sl_ex: exit_pr,reason = sl_ex,'SL'; break
                if not exit_pr:
                    exit_pr,reason = float(h4[-1]['close']),'EOD'
                pnl = (entry_ex-exit_pr)*ctr if dir=='bearish' else (exit_pr-entry_ex)*ctr
                pnl -= (entry_ex*ctr*COMM)+(exit_pr*ctr*COMM)
                self.balance += pnl
                if self.balance>self.peak: self.peak=self.balance
                self.trades.append({'time':cur_ts,'pair':pair,'signal':sig,'entry':entry_ex,'sl':sl_ex,'tp1':tp1_ex,'tp2':tp2_ex,'exit':exit_pr,'pnl':pnl,'reason':reason,'contracts':ctr,'balance':self.balance})
        self.report()

    def report(self):
        df=pd.DataFrame(self.trades)
        if df.empty: print("Sin trades"); return
        df['pnl']=df['pnl'].astype(float)
        total=df['pnl'].sum(); wr=len(df[df['pnl']>0])/len(df)*100
        df['cummax']=df['balance'].cummax(); df['dd']=(df['balance']-df['cummax'])/df['cummax']*100
        maxdd=df['dd'].min(); final=self.balance
        print(f"Trades:{len(df)} | WR:{wr:.1f}% | PnL:${total:.2f} | DD:{maxdd:.1f}% | Final:${final:.2f}")
        df.to_csv("backtest_results.csv",index=False)
        print("Guardado en backtest_results.csv")
        print(df[['time','pair','signal','entry','exit','pnl','reason']].tail(10).to_string(index=False))

if __name__=="__main__":
    Backtest().run()
