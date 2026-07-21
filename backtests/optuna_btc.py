#!/usr/bin/env python3
"""
Optimización Bayesiana (Optuna) para BTCUSDT – Walk‑Forward trimestral.
Parámetros: supertrend_multiplier, choppiness_neutral_threshold, fibonacci_days, tp_ratio.
Objetivo: Profit Factor promedio (test folds) – 0.5 * std(Profit Factors).
"""
import sys, os, time, math
import numpy as np
import pandas as pd
import requests
from scipy.signal import argrelextrema
from scipy.stats import linregress
from datetime import datetime, timezone, timedelta

import optuna

# ═══════════════════ CONFIGURACIÓN ═══════════════════
SYMBOL = "BTCUSDT"
DATA_START = datetime(2023, 4, 1, tzinfo=timezone.utc)
DATA_END   = datetime(2026, 6, 17, 23, 59, tzinfo=timezone.utc)
MAX_KLINES = 1000
BASE_URL   = "https://api.binance.com/api/v3/klines"
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01
COMMISSION = 0.0005
SPREAD = 0.0002
SLIPPAGE_ATR_MULT = 0.1

# ═══════════════════ DESCARGA Y CACHÉ ═══════════════════
_cache = {}

def fetch_klines_range(symbol, interval, start_dt, end_dt):
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)
    all_klines = []
    while start_ms < end_ms:
        params = {'symbol':symbol,'interval':interval,'limit':MAX_KLINES,
                  'startTime':start_ms,'endTime':end_ms}
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except Exception as e:
            print(f"Error conexión: {e}"); time.sleep(1); continue
        if resp.status_code != 200:
            print(f"Error {resp.status_code}: {resp.text}"); time.sleep(1); continue
        data = resp.json()
        if not data: break
        batch = [{
            'timestamp': int(k[0]),
            'open': float(k[1]), 'high': float(k[2]),
            'low': float(k[3]), 'close': float(k[4]),
            'volume': float(k[5])
        } for k in data]
        all_klines.extend(batch)
        start_ms = batch[-1]['timestamp'] + 1
        time.sleep(0.3)
    return [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]

def get_cached_data():
    if _cache:
        return _cache
    print("Descargando y cacheando datos de BTCUSDT...")
    _cache['1d']  = fetch_klines_range(SYMBOL, '1d', DATA_START, DATA_END)
    _cache['4h']  = fetch_klines_range(SYMBOL, '4h', DATA_START, DATA_END)
    _cache['1h']  = fetch_klines_range(SYMBOL, '1h', DATA_START, DATA_END)
    _cache['15m'] = fetch_klines_range(SYMBOL, '15m', DATA_START, DATA_END)
    _cache['5m']  = fetch_klines_range(SYMBOL, '5m', DATA_START, DATA_END)
    print("Datos en caché.")
    return _cache

# ═══════════════════ CLASES DEL MOTOR ═══════════════════
SYMBOL_CONFIG = {
    'BTCUSDT': {'supertrend_multiplier':2.8,'choppiness_neutral_threshold':74.0,'fibonacci_days':30,'tp_ratio':1.5},
}

class IndicatorEngine:
    def __init__(self, klines): self.klines = klines
    @staticmethod
    def _ema(s, p):
        if len(s) < p: return None
        m = 2/(p+1); e = sum(s[:p])/p
        for x in s[p:]: e = (x-e)*m + e
        return e
    @staticmethod
    def _atr(h, l, c, p):
        if len(h) < p+1: return None
        tr = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])) for i in range(1, len(h))]
        return sum(tr[-p:])/p
    @staticmethod
    def _bb(c, p=20, n=2):
        if len(c) < p: return None, None, None
        sma = sum(c[-p:])/p
        std = (sum((x-sma)**2 for x in c[-p:])/p)**0.5
        return sma+n*std, sma-n*std, std
    def calculate(self):
        ind = {}
        for tf in ['1d','4h','1h','15m','5m']:
            k = self.klines.get(tf)
            if not k or len(k) < 30: ind[tf] = None; continue
            c = np.array([float(v['close']) for v in k])
            h = np.array([float(v['high']) for v in k])
            l = np.array([float(v['low']) for v in k])
            v = np.array([float(v['volume']) for v in k])
            o = np.array([float(v['open']) for v in k])
            ema20 = self._ema(c, 20); ema50 = self._ema(c, 50)
            atr14 = self._atr(h, l, c, 14)
            bb_u, bb_l, bb_s = self._bb(c)
            bb_w = (bb_u - bb_l) / c[-1] if bb_u and bb_l else None
            vol_r = v[-1] / np.mean(v[-20:]) if len(v) >= 20 else None
            body = abs(c[-1] - o[-1]); tr = h[-1] - l[-1]
            body_p = body / tr if tr > 0 else 0
            uw = h[-1] - max(o[-1], c[-1]); lw = min(o[-1], c[-1]) - l[-1]
            ind[tf] = {'ema20':ema20,'ema50':ema50,'atr14':atr14,
                       'bb_upper':bb_u,'bb_lower':bb_l,'bb_width':bb_w,
                       'vol_ratio':vol_r,'close':c[-1],
                       'body_pct':body_p,'wick_upper_pct':uw/tr if tr>0 else 0,
                       'wick_lower_pct':lw/tr if tr>0 else 0}
        return ind

class StructureAnalyzer:
    def __init__(self, klines): self.klines = klines
    def analyze(self):
        if not self.klines or len(self.klines) < 50:
            return {'trend':'neutral','bos':False,'choch':False,'recent_high':0,'recent_low':0}
        h = np.array([float(k['high']) for k in self.klines])
        l = np.array([float(k['low']) for k in self.klines])
        c = np.array([float(k['close']) for k in self.klines])
        sh = argrelextrema(h, np.greater, order=5)[0]
        sl = argrelextrema(l, np.less, order=5)[0]
        swings = sorted([(i, h[i], 'H') for i in sh] + [(i, l[i], 'L') for i in sl], key=lambda x: x[0])
        if len(swings) < 4:
            return {'trend':'neutral','bos':False,'choch':False,'recent_high':max(h),'recent_low':min(l)}
        hs = [s[1] for s in swings if s[2]=='H']; ls = [s[1] for s in swings if s[2]=='L']
        trend = 'neutral'
        if len(hs)>=2 and len(ls)>=2:
            if hs[-1] > hs[-2] and ls[-1] > ls[-2]: trend = 'bullish'
            elif hs[-1] < hs[-2] and ls[-1] < ls[-2]: trend = 'bearish'
        bos = (trend=='bullish' and c[-1] > hs[-1]) or (trend=='bearish' and c[-1] < ls[-1])
        return {'trend':trend,'bos':bos,'choch':False,'recent_high':hs[-1] if hs else max(h),'recent_low':ls[-1] if ls else min(l)}

class MarketPhaseDetector:
    def __init__(self, ind, kl, tf): self.ind = ind; self.kl = kl
    def detect(self):
        if not self.ind or not self.kl or len(self.kl)<30: return 'unknown'
        c = np.array([float(k['close']) for k in self.kl])
        bw = self.ind.get('bb_width'); a = self.ind.get('atr14'); vr = self.ind.get('vol_ratio')
        atr_r = a / c[-1] if a else 0
        slope = linregress(np.arange(20), c[-20:])[0] if len(c)>=20 else 0
        if bw and bw < 0.03 and atr_r < 0.01 and vr and vr < 0.8: return 'compressing'
        if bw and bw > 0.06 and vr and vr > 1.3 and abs(slope) > 0.005: return 'expanding'
        if atr_r > 0.015 and abs(slope) > 0.01: return 'trending'
        if atr_r < 0.008 and abs(slope) < 0.003: return 'ranging'
        return 'neutral'

class PhaseTransitionDetector:
    def __init__(self, klines_1h, klines_15m):
        self.klines_1h = klines_1h; self.klines_15m = klines_15m
    def check_breakout_retest(self, compression_detected: bool) -> dict:
        if not compression_detected:
            return {'breakout_confirmed':False,'retest_valid':False,'direction':'neutral'}
        h1 = self.klines_1h
        if not h1 or len(h1) < 5:
            return {'breakout_confirmed':False,'retest_valid':False,'direction':'neutral'}
        highs_1h = [float(k['high']) for k in h1[-5:]]; lows_1h = [float(k['low']) for k in h1[-5:]]
        range_high = max(highs_1h); range_low = min(lows_1h)
        m15 = self.klines_15m
        if not m15 or len(m15) < 3:
            return {'breakout_confirmed':False,'retest_valid':False,'direction':'neutral'}
        closes_m15 = [float(k['close']) for k in m15[-3:]]; volumes_m15 = [float(k['volume']) for k in m15[-3:]]
        avg_vol = np.mean(volumes_m15) if volumes_m15 else 0
        last_close = closes_m15[-1]; last_vol = volumes_m15[-1]
        breakout_direction = 'neutral'; breakout_confirmed = False
        if last_close > range_high and last_vol > avg_vol * 1.2:
            breakout_direction = 'bullish'; breakout_confirmed = True
        elif last_close < range_low and last_vol > avg_vol * 1.2:
            breakout_direction = 'bearish'; breakout_confirmed = True
        retest_valid = False
        if breakout_confirmed:
            if breakout_direction == 'bullish':
                for i in range(len(m15)-3, len(m15)):
                    low = float(m15[i]['low']); close = float(m15[i]['close'])
                    if low <= range_high and close > range_high: retest_valid = True; break
            else:
                for i in range(len(m15)-3, len(m15)):
                    high = float(m15[i]['high']); close = float(m15[i]['close'])
                    if high >= range_low and close < range_low: retest_valid = True; break
        return {'breakout_confirmed':breakout_confirmed,'retest_valid':retest_valid,'direction':breakout_direction}

class MomentumAnalyzer:
    def __init__(self, indicators): self.ind = indicators
    def analyze(self):
        scores = {}
        w = {'4h':0.5,'1h':0.35,'15m':0.15}
        for tf, wt in w.items():
            if not self.ind.get(tf) or not self.ind[tf]['ema20'] or not self.ind[tf]['ema50']: continue
            d = 1 if self.ind[tf]['ema20'] > self.ind[tf]['ema50'] else -1
            acc = (self.ind[tf]['vol_ratio'] - 1) * 0.5 if self.ind[tf]['vol_ratio'] else 0
            scores[tf] = (d * 0.7 + acc * 0.3) * wt
        total = sum(scores.values()) if scores else 0
        direction = 'bullish' if total > 0.15 else ('bearish' if total < -0.15 else 'neutral')
        return {'score':total,'direction':direction,'strength':abs(total)}

class WeeklyAdaptiveFibonacci:
    def __init__(self, daily_klines, days=30):
        self.levels = None
        self._calculate(daily_klines, days)
    def _calculate(self, klines, days):
        if not klines or len(klines) < 2: self.levels = None; return
        usable_days = min(days, len(klines))
        recent = klines[-usable_days:]
        highs = [k['high'] for k in recent]; lows = [k['low'] for k in recent]
        fib_high = max(highs); fib_low = min(lows)
        if fib_high <= fib_low: self.levels = None; return
        rng = fib_high - fib_low
        self.levels = {
            0.00: fib_low,
            0.25: fib_low + rng*0.25,
            0.50: fib_low + rng*0.50,
            0.75: fib_low + rng*0.75,
            1.00: fib_high,
            1.25: fib_high + rng*0.25,
            1.50: fib_high + rng*0.50,
            1.75: fib_high + rng*0.75,
            2.00: fib_high + rng*1.00
        }
    def get_levels(self): return self.levels
    def get_current_block(self, price):
        if not self.levels: return None
        sorted_levels = sorted(self.levels.items(), key=lambda x: x[1])
        for i in range(len(sorted_levels)-1):
            low_key, low_price = sorted_levels[i]
            high_key, high_price = sorted_levels[i+1]
            if low_price <= price <= high_price:
                return (low_key, low_price, high_key, high_price)
        return None

class PatternDetector:
    @staticmethod
    def is_bearish_engulfing(klines):
        if len(klines) < 2: return False
        prev = klines[-2]; curr = klines[-1]
        return (prev['close'] > prev['open'] and curr['close'] < curr['open'] and
                curr['open'] > prev['close'] and curr['close'] < prev['open'])
    @staticmethod
    def is_bullish_engulfing(klines):
        if len(klines) < 2: return False
        prev = klines[-2]; curr = klines[-1]
        return (prev['close'] < prev['open'] and curr['close'] > curr['open'] and
                curr['open'] < prev['close'] and curr['close'] > prev['open'])

class ChoppinessIndex:
    def __init__(self, period=14, neutral_threshold=70.0):
        self.period = period; self.trend_threshold = 38.2; self.neutral_threshold = neutral_threshold
    def calculate(self, klines):
        if len(klines) < self.period + 1: return None
        highs = np.array([float(k['high']) for k in klines[-self.period:]])
        lows  = np.array([float(k['low'])  for k in klines[-self.period:]])
        closes = np.array([float(k['close']) for k in klines[-self.period:]])
        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1)))
        tr[0] = highs[0] - lows[0]
        atr_sum = np.sum(tr); highest = np.max(highs); lowest = np.min(lows)
        total_range = highest - lowest
        if total_range == 0: return 50.0
        ci = 100 * np.log10(atr_sum / total_range) / np.log10(self.period)
        return np.clip(ci, 0, 100)
    def analyze(self, klines):
        ci_value = self.calculate(klines)
        if ci_value is None: return {'value':50.0,'zone':'neutral','tradeable':True,'description':'CI calc failed'}
        if ci_value < self.trend_threshold: return {'value':ci_value,'zone':'trending','tradeable':True,'description':f"CI={ci_value:.1f} — tendencia fuerte"}
        elif ci_value > self.neutral_threshold: return {'value':ci_value,'zone':'choppy','tradeable':False,'description':f"CI={ci_value:.1f} — lateral/choppy"}
        else: return {'value':ci_value,'zone':'neutral','tradeable':True,'description':f"CI={ci_value:.1f} — zona neutral"}

class WilliamsRTrigger:
    OVERSOLD = -80; OVERBOUGHT = -20
    def __init__(self, period=14): self.period = period
    def _calc(self, klines):
        if len(klines) < self.period: return None
        highs = np.array([float(k['high']) for k in klines])
        lows  = np.array([float(k['low'])  for k in klines])
        closes = np.array([float(k['close']) for k in klines])
        hh = np.max(highs[-self.period:]); ll = np.min(lows[-self.period:])
        if hh == ll: return 0.0
        return np.clip(-100 * (hh - closes[-1]) / (hh - ll), -100, 0)
    def analyze(self, klines_5m, klines_15m):
        wr5 = self._calc(klines_5m); wr15 = self._calc(klines_15m)
        if wr5 is None or wr15 is None:
            return {'value_5m':-50,'value_15m':-50,'long_trigger':False,'short_trigger':False,'cross_strength':0.0,'description':'WR calc failed'}
        prev5 = self._calc(klines_5m[:-1]) if len(klines_5m) >= self.period+1 else wr5
        long_trigger = prev5 is not None and prev5 <= self.OVERSOLD and wr5 > self.OVERSOLD
        short_trigger = prev5 is not None and prev5 >= self.OVERBOUGHT and wr5 < self.OVERBOUGHT
        cross_strength = abs(wr5 - (self.OVERSOLD if long_trigger else self.OVERBOUGHT)) / 20 if (long_trigger or short_trigger) else 0.0
        desc = f"WR 5M={wr5:.1f}, 15M={wr15:.1f}"
        if long_trigger: desc += " — LONG trigger"
        if short_trigger: desc += " — SHORT trigger"
        return {'value_5m':wr5,'value_15m':wr15,'long_trigger':long_trigger,'short_trigger':short_trigger,'cross_strength':cross_strength,'description':desc}

class SupertrendFilter:
    def __init__(self, period_short=10, period_long=14, multiplier=3.0):
        self.period_short = period_short; self.period_long = period_long; self.multiplier = multiplier
    def _calc(self, klines, period):
        if len(klines) < period + 2: return None, 0.0, 'neutral'
        highs = np.array([float(k['high']) for k in klines])
        lows  = np.array([float(k['low'])  for k in klines])
        closes = np.array([float(k['close']) for k in klines])
        hl2 = (highs + lows) / 2
        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1)))
        tr[0] = highs[0] - lows[0]
        atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
        upper_basic = hl2 + self.multiplier * atr; lower_basic = hl2 - self.multiplier * atr
        n = len(klines); upper = upper_basic.copy(); lower = lower_basic.copy()
        direction = np.ones(n, dtype=int); st_line = np.zeros(n)
        for i in range(1, n):
            if closes[i-1] <= upper[i-1]: upper[i] = min(upper[i], upper[i-1])
            else: upper[i] = upper[i]
            if closes[i-1] >= lower[i-1]: lower[i] = max(lower[i], lower[i-1])
            else: lower[i] = lower[i]
            if closes[i] > upper[i-1]: direction[i] = 1
            elif closes[i] < lower[i-1]: direction[i] = -1
            else: direction[i] = direction[i-1]
            st_line[i] = lower[i] if direction[i] == 1 else upper[i]
        last_dir = direction[-1]; label = 'bullish' if last_dir == 1 else 'bearish'
        return st_line[-1], last_dir, label
    def analyze(self, klines_5m, klines_15m, klines_1h):
        _, dir5, label5 = self._calc(klines_5m, self.period_short)
        _, dir15, label15 = self._calc(klines_15m, self.period_short)
        _, dir1h, label1h = self._calc(klines_1h, self.period_long)
        if dir5 is None or dir15 is None or dir1h is None:
            return {'direction_5m':'neutral','direction_15m':'neutral','direction_1h':'neutral','aligned':False,'bias':'mixed','strength':0.0}
        dirs = [label5, label15, label1h]
        bull = dirs.count('bullish'); bear = dirs.count('bearish')
        if bull == 3: bias, aligned, strength = 'bullish', True, 1.0
        elif bear == 3: bias, aligned, strength = 'bearish', True, 1.0
        elif bull == 2: bias, aligned, strength = 'bullish', False, 0.67
        elif bear == 2: bias, aligned, strength = 'bearish', False, 0.67
        else: bias, aligned, strength = 'mixed', False, 0.0
        return {'direction_5m':label5,'direction_15m':label15,'direction_1h':label1h,'aligned':aligned,'bias':bias,'strength':strength}

class EnhancedFilterManager:
    def __init__(self, choppiness_threshold=70.0, supertrend_multiplier=3.0):
        self.ci = ChoppinessIndex(neutral_threshold=choppiness_threshold)
        self.wr = WilliamsRTrigger()
        self.st = SupertrendFilter(multiplier=supertrend_multiplier)
    def evaluate(self, klines_5m, klines_15m, klines_1h, base_direction):
        ci_state = self.ci.analyze(klines_15m)
        wr_state = self.wr.analyze(klines_5m, klines_15m)
        st_state = self.st.analyze(klines_5m, klines_15m, klines_1h)
        veto = False; veto_reason = ""; add_score = 0
        if not ci_state['tradeable']: veto = True; veto_reason = f"CI={ci_state['value']:.1f} lateral"
        elif ci_state['zone'] == 'trending': add_score += 1 if base_direction == 'LONG' else -1
        if not veto:
            if base_direction == 'SHORT' and wr_state['short_trigger']: add_score += 1
            elif base_direction == 'LONG' and wr_state['long_trigger']: add_score += 1
            elif base_direction == 'SHORT' and wr_state['long_trigger']: veto = True; veto_reason = "WR long trigger en short"
            elif base_direction == 'LONG' and wr_state['short_trigger']: veto = True; veto_reason = "WR short trigger en long"
        if not veto:
            if base_direction == 'SHORT' and st_state['bias'] == 'bearish' and st_state['aligned']: add_score += 1
            elif base_direction == 'LONG' and st_state['bias'] == 'bullish' and st_state['aligned']: add_score += 1
            elif base_direction == 'SHORT' and st_state['bias'] == 'bullish' and st_state['aligned']: veto = True; veto_reason = "ST 3/3 alcista en short"
            elif base_direction == 'LONG' and st_state['bias'] == 'bearish' and st_state['aligned']: veto = True; veto_reason = "ST 3/3 bajista en long"
        return {'veto':veto,'veto_reason':veto_reason,'add_score':add_score,'ci':ci_state,'wr':wr_state,'st':st_state}

class SessionFilter:
    @staticmethod
    def is_trading_session(): return True  # 24/7 para backtest

class TradeSetupBuilder:
    def __init__(self, entry_price, stop_loss, capital, risk_pct=0.01, leverage=20,
                 trend_h4='neutral', signal_direction='neutral', tp_ratio=1.5, score=85):
        self.entry = entry_price; self.sl = stop_loss; self.capital = capital
        self.base_risk_pct = risk_pct; self.leverage = leverage
        self.trend_h4 = trend_h4; self.signal_direction = signal_direction
        self.tp_ratio = tp_ratio; self.score = score
    def _adjusted_risk_pct(self):
        if self.score >= 90: return min(self.base_risk_pct * 1.5, 0.015)
        elif self.score >= 80: return self.base_risk_pct
        elif self.score >= 70: return self.base_risk_pct * 0.75
        elif self.score >= 30: return self.base_risk_pct * 0.5
        else: return 0.0
    def build(self):
        sl_pct = abs(self.entry - self.sl) / self.entry
        MAX_SL_PCT = 0.018
        if sl_pct > MAX_SL_PCT:
            if self.signal_direction == 'bullish': self.sl = self.entry * (1 - MAX_SL_PCT)
            else: self.sl = self.entry * (1 + MAX_SL_PCT)
            sl_pct = MAX_SL_PCT
        adjusted_risk = self._adjusted_risk_pct()
        if adjusted_risk == 0.0: return None
        risk_usd = self.capital * adjusted_risk
        effective_risk = risk_usd; contrarian = False
        if (self.trend_h4 == 'bullish' and self.signal_direction == 'bearish') or \
           (self.trend_h4 == 'bearish' and self.signal_direction == 'bullish'):
            effective_risk *= 0.5; contrarian = True
        notional = effective_risk / sl_pct; contracts = notional / self.entry
        if self.entry > self.sl:
            tp1 = self.entry + self.tp_ratio * (self.entry - self.sl)
            tp2 = self.entry + 2 * self.tp_ratio * (self.entry - self.sl)
        else:
            tp1 = self.entry - self.tp_ratio * (self.sl - self.entry)
            tp2 = self.entry - 2 * self.tp_ratio * (self.sl - self.entry)
        return {'entry':self.entry,'sl':self.sl,'tp1':tp1,'tp2':tp2,
                'contracts':contracts}

class ScalpingEngine:
    def __init__(self, symbol, capital=100.0, risk_pct=0.01, debug_filters=False):
        if symbol not in SYMBOL_CONFIG:
            raise ValueError(f"Símbolo {symbol} no configurado")
        self.symbol = symbol; self.capital = capital; self.risk_pct = risk_pct
        self.config = SYMBOL_CONFIG[symbol]
        self.filter_manager = EnhancedFilterManager(
            choppiness_threshold=self.config['choppiness_neutral_threshold'],
            supertrend_multiplier=self.config['supertrend_multiplier']
        )
        self.debug_filters = debug_filters

    def _calculate_dynamic_score(self, direction, fib_block, enhanced, mom, klines_4h, klines_15m):
        score = 70
        if klines_4h and len(klines_4h) >= 2:
            curr = klines_4h[-1]; body = abs(float(curr['close']) - float(curr['open']))
            tr = float(curr['high']) - float(curr['low'])
            body_ratio = body / tr if tr > 0 else 0
            if body_ratio > 0.4: score += 15
        if klines_15m and len(klines_15m) >= 3:
            vols = [float(k['volume']) for k in klines_15m[-3:]]
            if len(vols) == 3 and vols[-1] > vols[-2] > vols[-3]: score += 15
        st_state = enhanced.get('st', {})
        if st_state.get('aligned') and st_state.get('bias') == direction.lower(): score += 10
        ci_value = enhanced.get('ci', {}).get('value', 50)
        if ci_value is not None and ci_value < 60: score += 10
        mom_direction = mom.get('direction', 'neutral'); mom_strength = mom.get('strength', 0)
        if mom_direction == direction.lower(): score += min(mom_strength * 20, 20)
        return max(0, min(100, int(round(score))))

    def run(self, klines, current_price, current_ts=None):
        try:
            indicators = IndicatorEngine(klines).calculate()
            if not indicators.get('4h'): return self._empty_result('Insufficient data')
            struct_h4 = StructureAnalyzer(klines['4h']).analyze()
            struct_h1 = StructureAnalyzer(klines['1h']).analyze()
            phase_h1 = MarketPhaseDetector(indicators['1h'], klines['1h'], '1h').detect()
            phase_h4 = MarketPhaseDetector(indicators['4h'], klines['4h'], '4h').detect()
            compression = (phase_h4 == 'compressing' or phase_h1 == 'compressing')
            mom = MomentumAnalyzer(indicators).analyze()
            vol_5m = np.array([float(k['volume']) for k in klines['5m']])
            vol_ratio = vol_5m[-1] / np.mean(vol_5m[-20:]) if len(vol_5m) >= 20 else 1.0
            breakout_retest = PhaseTransitionDetector(klines['1h'], klines['15m']).check_breakout_retest(compression)
            fib_days = self.config['fibonacci_days']
            fib = WeeklyAdaptiveFibonacci(klines['1d'], days=fib_days)
            fib_block = fib.get_current_block(current_price)

            if fib_block is None:
                return self._empty_result('No Fibonacci block')

            signal = 'WAIT'; direction = 'neutral'; setup_state = 'FORMING'
            if PatternDetector.is_bearish_engulfing(klines['4h']):
                signal = 'SHORT'; direction = 'bearish'; setup_state = 'EXECUTE'
            elif PatternDetector.is_bullish_engulfing(klines['4h']):
                signal = 'LONG'; direction = 'bullish'; setup_state = 'EXECUTE'

            if phase_h1 in ('ranging', 'neutral'): signal = 'WAIT'; setup_state = 'INVALID'
            if signal in ('LONG', 'SHORT') and not SessionFilter.is_trading_session():
                if self.debug_filters: print("[DEBUG] VETO sesión")
                signal = 'WAIT'; setup_state = 'INVALID'
            enhanced = {'veto':False,'add_score':0,'ci':{},'wr':{},'st':{}}
            if signal in ('LONG', 'SHORT'):
                enhanced = self.filter_manager.evaluate(klines['5m'], klines['15m'], klines['1h'], signal)
                if enhanced['veto']:
                    if self.debug_filters: print(f"[DEBUG] VETO: {enhanced['veto_reason']}")
                    signal = 'WAIT'; setup_state = 'INVALID'

            trade = None; entry = None; dynamic_score = 0
            if signal in ('LONG', 'SHORT'):
                entry = indicators['5m']['close'] if indicators['5m'] else (indicators['15m']['close'] if indicators['15m'] else indicators['1h']['close'])
                if signal == 'SHORT':
                    last_high = max([float(k['high']) for k in klines['15m'][-10:]])
                    sl = last_high * 1.002
                else:
                    last_low = min([float(k['low']) for k in klines['15m'][-10:]])
                    sl = last_low * 0.998

                dynamic_score = self._calculate_dynamic_score(
                    direction=direction, fib_block=fib_block, enhanced=enhanced,
                    mom=mom, klines_4h=klines['4h'], klines_15m=klines['15m']
                )
                trade = TradeSetupBuilder(
                    entry, sl, capital=self.capital, risk_pct=self.risk_pct,
                    trend_h4=struct_h4.get('trend','neutral'),
                    signal_direction=direction,
                    tp_ratio=self.config['tp_ratio'],
                    score=dynamic_score
                ).build()

            fib_label = f"{fib_block[0]}-{fib_block[2]}" if fib_block else "?"
            explanation = f"Fib={fib_label} Sig={signal} Phase={phase_h1} CI={enhanced['ci'].get('value','?')} WR={enhanced['wr'].get('value_5m','?')} ST={enhanced['st'].get('bias','?')} Veto={enhanced['veto']}"
            return {
                'signal': signal, 'score': dynamic_score if signal != 'WAIT' else 0,
                'direction': direction, 'setup_state': setup_state,
                'trade': trade,
                'explanation': explanation,
                'enhanced': enhanced
            }
        except Exception as e:
            return self._empty_result(f'Error: {e}')

    def _empty_result(self, explanation=''):
        return {'signal':'WAIT','setup_state':'INVALID','explanation':explanation,
                'direction':'neutral','trade':None,'score':0,'enhanced':{}}

    def _calc_atr(self, klines, period=14):
        if len(klines) < period + 1: return None
        highs = np.array([float(k['high']) for k in klines])
        lows = np.array([float(k['low'])  for k in klines])
        closes = np.array([float(k['close']) for k in klines])
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(highs))]
        return np.mean(tr[-period:])

# ═══════════════════ SIMULADOR DE SALIDAS (con slippage ATR en stops) ═══════════════════
def simulate_exit(direction, entry_real, sl_original, tp1, contracts_total, candles, start_idx, atr_5m):
    slippage_exit = SLIPPAGE_ATR_MULT * atr_5m

    if direction == 'bullish':
        tp_distance = tp1 - entry_real
        tp2 = entry_real + 2 * tp_distance
        half_target = entry_real + 0.5 * tp_distance
    else:
        tp_distance = entry_real - tp1
        tp2 = entry_real - 2 * tp_distance
        half_target = entry_real - 0.5 * tp_distance

    remaining = contracts_total; sl_active = sl_original
    breakeven_moved = False; partial_done = False; events = []

    for j in range(start_idx+1, len(candles)):
        high = candles[j]['high']; low = candles[j]['low']
        if direction == 'bullish':
            if not breakeven_moved and high >= half_target:
                sl_active = entry_real
                breakeven_moved = True
                events.append(f"be_50%@{j}")
            if not partial_done and high >= tp1:
                close_contracts = contracts_total * 0.6
                pnl = (tp1 - entry_real) * close_contracts
                comm = tp1 * close_contracts * COMMISSION
                events.append({"type":"tp1","contracts":close_contracts,"price":tp1,"pnl":pnl,"comm":comm})
                remaining = contracts_total * 0.4
                sl_active = entry_real
                breakeven_moved = True; partial_done = True
            if partial_done and high >= tp2:
                pnl = (tp2 - entry_real) * remaining
                comm = tp2 * remaining * COMMISSION
                events.append({"type":"tp2","contracts":remaining,"price":tp2,"pnl":pnl,"comm":comm})
                remaining = 0; break
            if low <= sl_active:
                exit_price = sl_active - slippage_exit
                if not partial_done:
                    pnl = (exit_price - entry_real) * contracts_total
                    comm = exit_price * contracts_total * COMMISSION
                    events.append({"type":"sl_full","contracts":contracts_total,"price":exit_price,"pnl":pnl,"comm":comm})
                else:
                    pnl = (exit_price - entry_real) * remaining
                    comm = exit_price * remaining * COMMISSION
                    events.append({"type":"sl_rem","contracts":remaining,"price":exit_price,"pnl":pnl,"comm":comm})
                remaining = 0; break
        else:  # bearish
            if not breakeven_moved and low <= half_target:
                sl_active = entry_real
                breakeven_moved = True
                events.append(f"be_50%@{j}")
            if not partial_done and low <= tp1:
                close_contracts = contracts_total * 0.6
                pnl = (entry_real - tp1) * close_contracts
                comm = tp1 * close_contracts * COMMISSION
                events.append({"type":"tp1","contracts":close_contracts,"price":tp1,"pnl":pnl,"comm":comm})
                remaining = contracts_total * 0.4
                sl_active = entry_real
                breakeven_moved = True; partial_done = True
            if partial_done and low <= tp2:
                pnl = (entry_real - tp2) * remaining
                comm = tp2 * remaining * COMMISSION
                events.append({"type":"tp2","contracts":remaining,"price":tp2,"pnl":pnl,"comm":comm})
                remaining = 0; break
            if high >= sl_active:
                exit_price = sl_active + slippage_exit
                if not partial_done:
                    pnl = (entry_real - exit_price) * contracts_total
                    comm = exit_price * contracts_total * COMMISSION
                    events.append({"type":"sl_full","contracts":contracts_total,"price":exit_price,"pnl":pnl,"comm":comm})
                else:
                    pnl = (entry_real - exit_price) * remaining
                    comm = exit_price * remaining * COMMISSION
                    events.append({"type":"sl_rem","contracts":remaining,"price":exit_price,"pnl":pnl,"comm":comm})
                remaining = 0; break
    if remaining > 0:
        last_price = candles[-1]['close']
        pnl = (last_price - entry_real) * remaining if direction == 'bullish' else (entry_real - last_price) * remaining
        comm = last_price * remaining * COMMISSION
        events.append({"type":"forced","contracts":remaining,"price":last_price,"pnl":pnl,"comm":comm})

    commission_entry = entry_real * contracts_total * COMMISSION
    net = -commission_entry
    for ev in events:
        if isinstance(ev, dict) and 'pnl' in ev:
            net += ev['pnl'] - ev['comm']
    exit_type = 'tp2_reached' if any(e.get('type')=='tp2' for e in events if isinstance(e,dict)) else \
                'tp1_partial_then_sl' if any(e.get('type')=='tp1' for e in events if isinstance(e,dict)) else 'full_sl'
    return net, events, exit_type

# ═══════════════════ FUNCIÓN DE BACKTEST POR PERÍODO ═══════════════════
def backtest_periodo(start_dt, end_dt, params):
    cache = get_cached_data()
    lookback = timedelta(days=120)
    data_start = start_dt - lookback
    data = {}
    for tf in ['1d','4h','1h','15m','5m']:
        data[tf] = [k for k in cache[tf] if k['timestamp'] >= int(data_start.timestamp()*1000) 
                    and k['timestamp'] <= int(end_dt.timestamp()*1000)]

    candles_1h = data['1h']
    if len(candles_1h) < 10:
        return 0.0, pd.DataFrame()

    orig_config = SYMBOL_CONFIG[SYMBOL].copy()
    SYMBOL_CONFIG[SYMBOL] = {
        'supertrend_multiplier': params['supertrend_multiplier'],
        'choppiness_neutral_threshold': params['choppiness_neutral_threshold'],
        'fibonacci_days': int(params['fibonacci_days']),
        'tp_ratio': params['tp_ratio']
    }

    capital = INITIAL_CAPITAL
    trades = []
    engine = ScalpingEngine(symbol=SYMBOL, capital=capital, risk_pct=FIXED_RISK_PCT)

    start_idx = 0
    for i, c in enumerate(candles_1h):
        if c['timestamp'] >= int(start_dt.timestamp()*1000):
            start_idx = i
            break
    if start_idx < 10:
        start_idx = 10

    for i in range(start_idx, len(candles_1h)):
        ts = candles_1h[i]['timestamp']
        trunc = {
            '5m':  [k for k in data['5m']  if k['timestamp'] <= ts],
            '15m': [k for k in data['15m'] if k['timestamp'] <= ts],
            '1h':  candles_1h[:i+1],
            '4h':  [k for k in data['4h']  if k['timestamp'] <= ts],
            '1d':  [k for k in data['1d']  if k['timestamp'] <= ts],
        }
        current_price = candles_1h[i]['close']
        engine.capital = capital
        res = engine.run(trunc, current_price, current_ts=ts)

        if res['setup_state'] == 'EXECUTE' and res['trade'] is not None:
            trade = res['trade']
            entry = trade['entry']; sl = trade['sl']; tp1 = trade['tp1']
            contracts = trade['contracts']
            direction = res['direction']

            start_idx_5m = next((idx for idx, c in enumerate(data['5m']) if c['timestamp'] >= ts), len(data['5m'])-1)
            atr_5m = engine._calc_atr(data['5m'][:start_idx_5m+1], period=14) or 0.0
            slippage_entry = SLIPPAGE_ATR_MULT * atr_5m

            if direction == 'bullish':
                entry_real = entry * (1 + SPREAD) + slippage_entry
            else:
                entry_real = entry * (1 - SPREAD) - slippage_entry

            pnl_neto, events, exit_type = simulate_exit(
                direction, entry_real, sl, tp1, contracts, data['5m'], start_idx_5m, atr_5m
            )
            capital += pnl_neto
            trades.append({
                'pnl_neto': pnl_neto,
                'exit_type': exit_type
            })

    SYMBOL_CONFIG[SYMBOL] = orig_config

    df = pd.DataFrame(trades)
    if df.empty:
        return 0.0, df

    gross_profit = df[df['pnl_neto'] > 0]['pnl_neto'].sum()
    gross_loss   = abs(df[df['pnl_neto'] <= 0]['pnl_neto'].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else 10.0
    return pf, df

# ═══════════════════ WALK‑FORWARD FOLDS ═══════════════════
def generar_folds():
    folds = []
    start = datetime(2023, 7, 1, tzinfo=timezone.utc)
    end = datetime(2023, 9, 30, 23, 59, tzinfo=timezone.utc)
    while start < datetime(2026, 6, 17, tzinfo=timezone.utc):
        if end > datetime(2026, 6, 17, tzinfo=timezone.utc):
            end = datetime(2026, 6, 17, 23, 59, tzinfo=timezone.utc)
        folds.append((start, end))
        start = end + timedelta(seconds=1)
        if start.month in [1,2,3]:
            end = datetime(start.year, 3, 31, 23, 59, tzinfo=timezone.utc)
        elif start.month in [4,5,6]:
            end = datetime(start.year, 6, 30, 23, 59, tzinfo=timezone.utc)
        elif start.month in [7,8,9]:
            end = datetime(start.year, 9, 30, 23, 59, tzinfo=timezone.utc)
        else:
            end = datetime(start.year, 12, 31, 23, 59, tzinfo=timezone.utc)
    return folds

# ═══════════════════ FUNCIÓN OBJETIVO ═══════════════════
folds = generar_folds()

def objective(trial):
    params = {
        'supertrend_multiplier': trial.suggest_float('supertrend_multiplier', 1.5, 4.0),
        'choppiness_neutral_threshold': trial.suggest_float('choppiness_neutral_threshold', 55, 80),
        'fibonacci_days': trial.suggest_int('fibonacci_days', 20, 120),
        'tp_ratio': trial.suggest_float('tp_ratio', 1.2, 3.0)
    }
    pfs = []
    for (fold_start, fold_end) in folds:
        pf, _ = backtest_periodo(fold_start, fold_end, params)
        pfs.append(pf)
    pfs = np.array(pfs)
    return np.mean(pfs) - 0.5 * np.std(pfs)

# ═══════════════════ MAIN ═══════════════════
if __name__ == "__main__":
    get_cached_data()

    study = optuna.create_study(direction='maximize')
    study.optimize(objective, n_trials=50, show_progress_bar=True)

    print("\nMejores parámetros:")
    for k, v in study.best_params.items():
        print(f"  {k}: {v}")
    print(f"Mejor valor objetivo: {study.best_value:.4f}")
