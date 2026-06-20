# engine/scalping_engine.py
import sys, os, time, math, json
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from scipy.stats import linregress
from datetime import datetime, timezone, timedelta
from data.binance_feed import get_klines, get_ticker
from system_logger import market_logger, signals_logger, errors_logger
from risk_manager import RiskManager   # <-- NUEVO IMPORT

# ------------------------------------------------------------
# CONFIGURACIÓN POR ACTIVO (FASE 5 – 5 PARES)
# ------------------------------------------------------------
SYMBOL_CONFIG = {
    'BTCUSDT': {
        'supertrend_multiplier': 2.8,
        'choppiness_neutral_threshold': 74.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.5,
    },
    'ETHUSDT': {
        'supertrend_multiplier': 2.6,
        'choppiness_neutral_threshold': 72.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.8,
    },
    'SOLUSDT': {
        'supertrend_multiplier': 2.3,
        'choppiness_neutral_threshold': 70.0,
        'fibonacci_days': 90,
        'tp_ratio': 2.0,
    },
    'XRPUSDT': {
        'supertrend_multiplier': 2.3,
        'choppiness_neutral_threshold': 68.0,
        'fibonacci_days': 60,
        'tp_ratio': 1.8,
    },
    'BNBUSDT': {
        'supertrend_multiplier': 2.8,
        'choppiness_neutral_threshold': 68.0,
        'fibonacci_days': 30,
        'tp_ratio': 1.6,
    },
    'LTCUSDT': {
        'supertrend_multiplier': 2.8,
        'choppiness_neutral_threshold': 65.0,
        'fibonacci_days': 45,
        'tp_ratio': 1.7,
    },
    'DOGEUSDT': {
        'supertrend_multiplier': 2.0,
        'choppiness_neutral_threshold': 60.0,
        'fibonacci_days': 45,
        'tp_ratio': 2.0,
    },
    'LINKUSDT': {
        'supertrend_multiplier': 2.6,
        'choppiness_neutral_threshold': 65.0,
        'fibonacci_days': 45,
        'tp_ratio': 1.9,
    },
    'AVAXUSDT': {
        'supertrend_multiplier': 2.4,
        'choppiness_neutral_threshold': 62.0,
        'fibonacci_days': 60,
        'tp_ratio': 2.0,
    },
    'ADAUSDT': {
        'supertrend_multiplier': 2.5,
        'choppiness_neutral_threshold': 65.0,
        'fibonacci_days': 60,
        'tp_ratio': 1.8,
    },
    'MATICUSDT': {
        'supertrend_multiplier': 2.7,
        'choppiness_neutral_threshold': 63.0,
        'fibonacci_days': 45,
        'tp_ratio': 1.9,
    },
}

# ------------------------------------------------------------
# CLASES AUXILIARES (DataFetcher, IndicatorEngine, etc.)
# ------------------------------------------------------------
class DataFetcher:
    def __init__(self, pair): self.pair = pair
    def fetch(self):
        try:
            return {
                '1d':  get_klines(self.pair, '1d', 200),
                '4h':  get_klines(self.pair, '4h', 100),
                '1h':  get_klines(self.pair, '1h', 100),
                '15m': get_klines(self.pair, '15m', 100),
                '5m':  get_klines(self.pair, '5m', 100)
            }
        except Exception as e:
            errors_logger.error(f"DataFetcher error: {e}")
            return None

class IndicatorEngine:
    def __init__(self, klines): self.klines = klines
    @staticmethod
    def _ema(s, p):
        if len(s) < p: return None
        m = 2/(p+1)
        e = sum(s[:p])/p
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
            if not k or len(k) < 30:
                ind[tf] = None; continue
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
            ind[tf] = {
                'ema20': ema20, 'ema50': ema50, 'atr14': atr14,
                'bb_upper': bb_u, 'bb_lower': bb_l, 'bb_width': bb_w,
                'vol_ratio': vol_r, 'close': c[-1],
                'body_pct': body_p, 'wick_upper_pct': uw/tr if tr>0 else 0,
                'wick_lower_pct': lw/tr if tr>0 else 0
            }
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
        return {'trend': trend, 'bos': bos, 'choch': False,
                'recent_high': hs[-1] if hs else max(h), 'recent_low': ls[-1] if ls else min(l)}

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
            return {'breakout_confirmed': False, 'retest_valid': False, 'direction': 'neutral'}
        h1 = self.klines_1h
        if not h1 or len(h1) < 5: return {'breakout_confirmed': False, 'retest_valid': False, 'direction': 'neutral'}
        highs_1h = [float(k['high']) for k in h1[-5:]]; lows_1h = [float(k['low']) for k in h1[-5:]]
        range_high = max(highs_1h); range_low = min(lows_1h)
        m15 = self.klines_15m
        if not m15 or len(m15) < 3: return {'breakout_confirmed': False, 'retest_valid': False, 'direction': 'neutral'}
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
        return {'breakout_confirmed': breakout_confirmed, 'retest_valid': retest_valid, 'direction': breakout_direction}

class MomentumAnalyzer:
    def __init__(self, indicators): self.ind = indicators
    def analyze(self):
        scores = {}
        w = {'4h':0.5, '1h':0.35, '15m':0.15}
        for tf, wt in w.items():
            if not self.ind.get(tf) or not self.ind[tf]['ema20'] or not self.ind[tf]['ema50']:
                continue
            d = 1 if self.ind[tf]['ema20'] > self.ind[tf]['ema50'] else -1
            acc = (self.ind[tf]['vol_ratio'] - 1) * 0.5 if self.ind[tf]['vol_ratio'] else 0
            scores[tf] = (d * 0.7 + acc * 0.3) * wt
        total = sum(scores.values()) if scores else 0
        direction = 'bullish' if total > 0.15 else ('bearish' if total < -0.15 else 'neutral')
        return {'score': total, 'direction': direction, 'strength': abs(total)}

class WeeklyAdaptiveFibonacci:
    def __init__(self, daily_klines, days=30):
        self.levels = None
        self._calculate(daily_klines, days)
    def _calculate(self, klines, days):
        if not klines or len(klines) < 2:
            self.levels = None; return
        usable_days = min(days, len(klines))
        recent = klines[-usable_days:]
        highs = [k['high'] for k in recent]
        lows = [k['low'] for k in recent]
        fib_high = max(highs); fib_low = min(lows)
        if fib_high <= fib_low: self.levels = None; return
        rng = fib_high - fib_low
        self.levels = {
            0.00: fib_low, 0.25: fib_low+rng*0.25, 0.50: fib_low+rng*0.50,
            0.75: fib_low+rng*0.75, 1.00: fib_high, 1.25: fib_high+rng*0.25,
            1.50: fib_high+rng*0.50, 1.75: fib_high+rng*0.75, 2.00: fib_high+rng*1.00
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

# ------------------------------------------------------------
# FILTROS
# ------------------------------------------------------------
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
        atr_sum = np.sum(tr)
        highest = np.max(highs); lowest = np.min(lows)
        total_range = highest - lowest
        if total_range == 0: return 50.0
        ci = 100 * np.log10(atr_sum / total_range) / np.log10(self.period)
        return np.clip(ci, 0, 100)
    def analyze(self, klines):
        ci_value = self.calculate(klines)
        if ci_value is None: return {'value': 50.0, 'zone': 'neutral', 'tradeable': True, 'description': 'CI calculation failed'}
        if ci_value < self.trend_threshold: return {'value': ci_value, 'zone': 'trending', 'tradeable': True, 'description': f"CI={ci_value:.1f} — tendencia fuerte"}
        elif ci_value > self.neutral_threshold: return {'value': ci_value, 'zone': 'choppy', 'tradeable': False, 'description': f"CI={ci_value:.1f} — lateral/choppy"}
        else: return {'value': ci_value, 'zone': 'neutral', 'tradeable': True, 'description': f"CI={ci_value:.1f} — zona neutral"}

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
            return {'value_5m': -50, 'value_15m': -50, 'long_trigger': False, 'short_trigger': False, 'cross_strength': 0.0, 'description': 'WR calculation failed'}
        prev5 = self._calc(klines_5m[:-1]) if len(klines_5m) >= self.period+1 else wr5
        long_trigger = prev5 is not None and prev5 <= self.OVERSOLD and wr5 > self.OVERSOLD
        short_trigger = prev5 is not None and prev5 >= self.OVERBOUGHT and wr5 < self.OVERBOUGHT
        cross_strength = abs(wr5 - (self.OVERSOLD if long_trigger else self.OVERBOUGHT)) / 20 if (long_trigger or short_trigger) else 0.0
        desc = f"WR 5M={wr5:.1f}, 15M={wr15:.1f}"
        if long_trigger: desc += " — LONG trigger"
        if short_trigger: desc += " — SHORT trigger"
        return {'value_5m': wr5, 'value_15m': wr15, 'long_trigger': long_trigger, 'short_trigger': short_trigger, 'cross_strength': cross_strength, 'description': desc}

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
            return {'direction_5m': 'neutral', 'direction_15m': 'neutral', 'direction_1h': 'neutral', 'aligned': False, 'bias': 'mixed', 'strength': 0.0}
        dirs = [label5, label15, label1h]
        bull = dirs.count('bullish'); bear = dirs.count('bearish')
        if bull == 3: bias, aligned, strength = 'bullish', True, 1.0
        elif bear == 3: bias, aligned, strength = 'bearish', True, 1.0
        elif bull == 2: bias, aligned, strength = 'bullish', False, 0.67
        elif bear == 2: bias, aligned, strength = 'bearish', False, 0.67
        else: bias, aligned, strength = 'mixed', False, 0.0
        return {'direction_5m': label5, 'direction_15m': label15, 'direction_1h': label1h, 'aligned': aligned, 'bias': bias, 'strength': strength}

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
        if not ci_state['tradeable']: veto = True; veto_reason = f"CI={ci_state['value']:.1f} lateral (choppy)"
        elif ci_state['zone'] == 'trending': add_score += 1 if base_direction == 'LONG' else -1
        if not veto:
            if base_direction == 'SHORT' and wr_state['short_trigger']: add_score += 1
            elif base_direction == 'LONG' and wr_state['long_trigger']: add_score += 1
            elif base_direction == 'SHORT' and wr_state['long_trigger']: veto = True; veto_reason = "WilliamsR long trigger en short"
            elif base_direction == 'LONG' and wr_state['short_trigger']: veto = True; veto_reason = "WilliamsR short trigger en long"
        if not veto:
            if base_direction == 'SHORT' and st_state['bias'] == 'bearish' and st_state['aligned']: add_score += 1
            elif base_direction == 'LONG' and st_state['bias'] == 'bullish' and st_state['aligned']: add_score += 1
            elif base_direction == 'SHORT' and st_state['bias'] == 'bullish' and st_state['aligned']: veto = True; veto_reason = "Supertrend 3/3 alcista en short"
            elif base_direction == 'LONG' and st_state['bias'] == 'bearish' and st_state['aligned']: veto = True; veto_reason = "Supertrend 3/3 bajista en long"
        return {'veto': veto, 'veto_reason': veto_reason, 'add_score': add_score, 'ci': ci_state, 'wr': wr_state, 'st': st_state}

class SessionFilter:
    @staticmethod
    def is_trading_session(): return True   # 24/7

# ------------------------------------------------------------
# TRADE SETUP BUILDER
# ------------------------------------------------------------
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
        liq_price = self.entry * (1 - 1/self.leverage) if self.entry > self.sl else self.entry * (1 + 1/self.leverage)
        if self.entry > self.sl:
            tp1 = self.entry + self.tp_ratio * (self.entry - self.sl)
            tp2 = self.entry + 2 * self.tp_ratio * (self.entry - self.sl)
        else:
            tp1 = self.entry - self.tp_ratio * (self.sl - self.entry)
            tp2 = self.entry - 2 * self.tp_ratio * (self.sl - self.entry)
        return {'entry': self.entry, 'sl': self.sl, 'tp1': tp1, 'tp2': tp2,
                'risk_pct': sl_pct * 100, 'risk_usd': effective_risk,
                'notional': notional, 'contracts': contracts,
                'liq_price': liq_price, 'contrarian': contrarian}

# ------------------------------------------------------------
# MOTOR UNIFICADO (FASE 5 – 5 PARES) CON RISK MANAGER DINÁMICO
# ------------------------------------------------------------
class ScalpingEngine:
    def __init__(self, symbol, capital=100.0, risk_pct=0.01, debug_filters=True):
        if symbol not in SYMBOL_CONFIG:
            raise ValueError(f"Símbolo {symbol} no configurado. Usar: {list(SYMBOL_CONFIG.keys())}")
        self.symbol = symbol
        self.capital = capital
        self.risk_pct = risk_pct
        self.config = SYMBOL_CONFIG[symbol]
        self.filter_manager = EnhancedFilterManager(
            choppiness_threshold=self.config['choppiness_neutral_threshold'],
            supertrend_multiplier=self.config['supertrend_multiplier']
        )
        self.debug_filters = debug_filters
        self.risk_manager = RiskManager()   # <-- NUEVO: risk manager dinámico

    def _calculate_dynamic_score(self, direction, fib_block, enhanced, mom, klines_4h, klines_15m):
        score = 70
        if klines_4h and len(klines_4h) >= 2:
            curr = klines_4h[-1]
            body = abs(float(curr['close']) - float(curr['open']))
            tr = float(curr['high']) - float(curr['low'])
            body_ratio = body / tr if tr > 0 else 0
            if body_ratio > 0.4: score += 15
        if klines_15m and len(klines_15m) >= 3:
            vols = [float(k['volume']) for k in klines_15m[-3:]]
            if len(vols) == 3 and vols[-1] > vols[-2] > vols[-3]:
                score += 15
        st_state = enhanced.get('st', {})
        if st_state.get('aligned') and st_state.get('bias') == direction.lower():
            score += 10
        ci_value = enhanced.get('ci', {}).get('value', 50)
        if ci_value is not None and ci_value < 60:
            score += 10
        mom_direction = mom.get('direction', 'neutral')
        mom_strength = mom.get('strength', 0)
        if mom_direction == direction.lower():
            score += min(mom_strength * 20, 20)
        return max(0, min(100, int(round(score))))

    def run(self):
        try:
            klines = DataFetcher(self.symbol).fetch()
            if klines is None or any(klines[tf] is None for tf in ['5m', '15m', '1h', '4h', '1d']):
                return self._empty_result('Network error')
            indicators = IndicatorEngine(klines).calculate()
            if not indicators.get('4h'): return self._empty_result('Insufficient data')
            struct_h4 = StructureAnalyzer(klines['4h']).analyze()
            phase_h1 = MarketPhaseDetector(indicators['1h'], klines['1h'], '1h').detect()
            mom = MomentumAnalyzer(indicators).analyze()
            try:
                vol_5m = np.array([float(k['volume']) for k in klines['5m']])
                vol_ratio = vol_5m[-1] / np.mean(vol_5m[-20:]) if len(vol_5m) >= 20 else 1.0
            except TypeError:
                return self._empty_result('Data error (volume)')
            fib = WeeklyAdaptiveFibonacci(klines['1d'], days=self.config['fibonacci_days'])
            current_price = get_ticker(self.symbol) or indicators['15m']['close']
            fib_block = fib.get_current_block(current_price)
            if not fib_block:
                return self._empty_result('No Fibonacci block')

            signal = 'WAIT'; direction = 'neutral'; setup_state = 'FORMING'

            # FASE 5: solo engulfing de 4h, sin confirmación de 15m
            if PatternDetector.is_bearish_engulfing(klines['4h']):
                signal = 'SHORT'; direction = 'bearish'; setup_state = 'EXECUTE'
            elif PatternDetector.is_bullish_engulfing(klines['4h']):
                signal = 'LONG'; direction = 'bullish'; setup_state = 'EXECUTE'

            if phase_h1 in ('ranging', 'neutral'): signal = 'WAIT'; setup_state = 'INVALID'

            enhanced = {'veto': False, 'add_score': 0, 'ci': {}, 'wr': {}, 'st': {}}
            if signal in ('LONG', 'SHORT'):
                enhanced = self.filter_manager.evaluate(klines['5m'], klines['15m'], klines['1h'], signal)
                if enhanced['veto']:
                    if self.debug_filters: print(f"[DEBUG] VETO: {enhanced['veto_reason']}")
                    signal = 'WAIT'; setup_state = 'INVALID'

            # Auditor de rechazo
            if signal == 'WAIT':
                reasons = {
                    "fib_block": fib_block is not None,
                    "bearish_engulfing_4h": PatternDetector.is_bearish_engulfing(klines['4h']) if klines.get('4h') else None,
                    "bullish_engulfing_4h": PatternDetector.is_bullish_engulfing(klines['4h']) if klines.get('4h') else None,
                    "phase": phase_h1,
                    "ci": enhanced.get('ci', {}).get('value', '?'),
                    "wr": enhanced.get('wr', {}).get('value_5m', '?'),
                    "st": enhanced.get('st', {}).get('bias', '?'),
                    "veto": enhanced.get('veto', False),
                    "veto_reason": enhanced.get('veto_reason', '')
                }
                _log_rejection(self.symbol, reasons, f"Fib={fib_block[0]}-{fib_block[2]}")

            trade = None; entry = None; dynamic_score = 0
            if signal in ('LONG', 'SHORT'):
                entry = indicators['5m']['close'] if indicators['5m'] else (indicators['15m']['close'] if indicators['15m'] else indicators['1h']['close'])
                if fib_block:
                    if signal == 'SHORT':
                        last_high = max([float(k['high']) for k in klines['15m'][-10:]])
                        sl = last_high * 1.002
                    else:
                        last_low = min([float(k['low']) for k in klines['15m'][-10:]])
                        sl = last_low * 0.998
                else:
                    atr1h = self._calc_atr(klines['1h'], 14) or entry * 0.005
                    sl = entry + atr1h * 2 if direction == 'bearish' else entry - atr1h * 2

                dynamic_score = self._calculate_dynamic_score(
                    direction=direction, fib_block=fib_block, enhanced=enhanced,
                    mom=mom, klines_4h=klines['4h'], klines_15m=klines['15m']
                )

                # ─── RIESGO DINÁMICO ───
                atr_val = indicators['1h']['atr14'] if indicators.get('1h') and indicators['1h'].get('atr14') else entry * 0.005
                dynamic_risk_pct = self.risk_manager.get_risk_pct(dynamic_score, atr_val, entry)
                # ────────────────────────

                trade = TradeSetupBuilder(
                    entry, sl, capital=self.capital, risk_pct=dynamic_risk_pct,
                    trend_h4=struct_h4.get('trend', 'neutral'),
                    signal_direction=direction,
                    tp_ratio=self.config['tp_ratio'],
                    score=dynamic_score
                ).build()

            fib_label = f"{fib_block[0]}-{fib_block[2]}" if fib_block else "?"
            explanation = f"Fib={fib_label} Sig={signal} Phase={phase_h1} CI={enhanced['ci'].get('value','?')} WR={enhanced['wr'].get('value_5m','?')} ST={enhanced['st'].get('bias','?')} Veto={enhanced['veto']}"
            return {
                'signal': signal, 'score': dynamic_score if signal != 'WAIT' else 0,
                'direction': direction, 'setup_state': setup_state,
                'agent_scores': {'scanner':85, 'risk':80, 'technical':90, 'momentum':85, 'guard':80},
                'weighted_confidence': dynamic_score if signal != 'WAIT' else 50,
                'trend_h4': struct_h4.get('trend','neutral'),
                'market_phase': phase_h1, 'vol_ratio': vol_ratio,
                'explanation': explanation, 'trade': trade,
                'ci': enhanced['ci'], 'wr': enhanced['wr'], 'st': enhanced['st'],
                'veto': enhanced['veto'], 'veto_reason': enhanced.get('veto_reason','')
            }
        except Exception as e:
            errors_logger.exception("Error en análisis")
            return self._empty_result(f'Error: {e}')

    def _empty_result(self, explanation=''):
        return {'signal':'WAIT','setup_state':'INVALID','explanation':explanation,
                'direction':'neutral','trade':None,'score':0}
    def _calc_atr(self, klines, period=14):
        if len(klines) < period + 1: return None
        highs = np.array([float(k['high']) for k in klines])
        lows  = np.array([float(k['low'])  for k in klines])
        closes = np.array([float(k['close']) for k in klines])
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(highs))]
        return np.mean(tr[-period:])

# ------------------------------------------------------------
# AUDITOR DE RECHAZO
# ------------------------------------------------------------
REJECTION_LOG_FILE = "rejection_log.json"
def _log_rejection(symbol, reasons, explanation):
    try:
        logs = []
        if os.path.exists(REJECTION_LOG_FILE):
            with open(REJECTION_LOG_FILE, "r") as f:
                try: logs = json.load(f)
                except: logs = []
        entry = {"timestamp": datetime.now().isoformat(), "symbol": symbol, "reasons": reasons, "explanation": explanation}
        logs.append(entry)
        if len(logs) > 1000: logs = logs[-1000:]
        with open(REJECTION_LOG_FILE, "w") as f:
            json.dump(logs, f, indent=2)
    except Exception as e:
        print(f"[RejectionLog] Error escribiendo: {e}")