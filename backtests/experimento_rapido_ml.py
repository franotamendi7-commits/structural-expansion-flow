import os, sys, pickle, pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta
from scipy.signal import argrelextrema
from scipy.stats import linregress

print("🚀 Experimento rápido (3 meses) con ML\n")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
START_DATE = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
END_DATE   = datetime(2025, 3, 31, 23, 59, tzinfo=timezone.utc)
INITIAL_CAPITAL = 100.0
FIXED_RISK_PCT = 0.01
COMMISSION = 0.0005
SPREAD = 0.0002
SLIPPAGE_ATR_MULT = 0.1

SYMBOL_CONFIG = {
    'BTCUSDT': {'supertrend_multiplier':2.8,'choppiness_neutral_threshold':74.0,'fibonacci_days':30,'tp_ratio':1.5},
    'ETHUSDT': {'supertrend_multiplier':2.6,'choppiness_neutral_threshold':72.0,'fibonacci_days':30,'tp_ratio':1.8},
    'SOLUSDT': {'supertrend_multiplier':2.3,'choppiness_neutral_threshold':70.0,'fibonacci_days':90,'tp_ratio':2.0},
    'XRPUSDT': {'supertrend_multiplier':2.3,'choppiness_neutral_threshold':68.0,'fibonacci_days':60,'tp_ratio':1.8},
    'BNBUSDT': {'supertrend_multiplier':2.8,'choppiness_neutral_threshold':68.0,'fibonacci_days':30,'tp_ratio':1.6},
}

# ─── CARGA FILTRADA DE CACHÉ ───
def load_cache():
    data_all = {}
    for sym in SYMBOLS:
        sym_data = {}
        for tf in ['1d','4h','1h','15m','5m']:
            fname = f"backtest_cache/{sym}_{tf}.csv"
            if not os.path.exists(fname):
                sym_data[tf] = []
                continue
            df = pd.read_csv(fname)
            klines = []
            for _, row in df.iterrows():
                ts = int(row['timestamp'])
                if START_DATE.timestamp()*1000 <= ts <= END_DATE.timestamp()*1000:
                    klines.append({
                        'timestamp': ts,
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume'])
                    })
            sym_data[tf] = klines
        data_all[sym] = sym_data
        print(f"{sym}: {len(sym_data['1d'])}d, {len(sym_data['4h'])}4h, {len(sym_data['1h'])}1h, {len(sym_data['15m'])}15m, {len(sym_data['5m'])}5m")
    return data_all

# ─── CLASES DEL MOTOR (igual a las anteriores) ───
# (Incluyo solo las necesarias para no alargar, son las mismas que en experimento_completo_ml.py)
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
            c = np.array([v['close'] for v in k])
            h = np.array([v['high'] for v in k])
            l = np.array([v['low'] for v in k])
            v = np.array([v['volume'] for v in k])
            o = np.array([v['open'] for v in k])
            ema20 = self._ema(c, 20); ema50 = self._ema(c, 50)
            atr14 = self._atr(h, l, c, 14)
            bb_u, bb_l, bb_s = self._bb(c)
            bb_w = (bb_u - bb_l) / c[-1] if bb_u and bb_l else None
            vol_r = v[-1] / np.mean(v[-20:]) if len(v) >= 20 else None
            ind[tf] = {'ema20':ema20,'ema50':ema50,'atr14':atr14,
                       'bb_upper':bb_u,'bb_lower':bb_l,'bb_width':bb_w,
                       'vol_ratio':vol_r,'close':c[-1]}
        return ind

class StructureAnalyzer:
    def __init__(self, klines): self.klines = klines
    def analyze(self):
        if not self.klines or len(self.klines) < 50:
            return {'trend':'neutral','bos':False,'choch':False,'recent_high':0,'recent_low':0}
        h = np.array([k['high'] for k in self.klines])
        l = np.array([k['low'] for k in self.klines])
        c = np.array([k['close'] for k in self.klines])
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
        c = np.array([k['close'] for k in self.kl])
        bw = self.ind.get('bb_width'); a = self.ind.get('atr14'); vr = self.ind.get('vol_ratio')
        atr_r = a / c[-1] if a else 0
        slope = linregress(np.arange(20), c[-20:])[0] if len(c)>=20 else 0
        if bw and bw < 0.03 and atr_r < 0.01 and vr and vr < 0.8: return 'compressing'
        if bw and bw > 0.06 and vr and vr > 1.3 and abs(slope) > 0.005: return 'expanding'
        if atr_r > 0.015 and abs(slope) > 0.01: return 'trending'
        if atr_r < 0.008 and abs(slope) < 0.003: return 'ranging'
        return 'neutral'

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
            0.00: fib_low, 0.25: fib_low + rng*0.25,
            0.50: fib_low + rng*0.50, 0.75: fib_low + rng*0.75,
            1.00: fib_high, 1.25: fib_high + rng*0.25,
            1.50: fib_high + rng*0.50, 1.75: fib_high + rng*0.75,
            2.00: fib_high + rng*1.00
        }
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
        highs = np.array([k['high'] for k in klines[-self.period:]])
        lows  = np.array([k['low']  for k in klines[-self.period:]])
        closes = np.array([k['close'] for k in klines[-self.period:]])
        tr = np.maximum(np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1))), np.abs(lows - np.roll(closes, 1)))
        tr[0] = highs[0] - lows[0]
        atr_sum = np.sum(tr); highest = np.max(highs); lowest = np.min(lows)
        total_range = highest - lowest
        if total_range == 0: return 50.0
        ci = 100 * np.log10(atr_sum / total_range) / np.log10(self.period)
        return np.clip(ci, 0, 100)
    def analyze(self, klines):
        ci_value = self.calculate(klines)
        if ci_value is None: return {'value':50.0,'zone':'neutral','tradeable':True}
        if ci_value < self.trend_threshold: return {'value':ci_value,'zone':'trending','tradeable':True}
        elif ci_value > self.neutral_threshold: return {'value':ci_value,'zone':'choppy','tradeable':False}
        else: return {'value':ci_value,'zone':'neutral','tradeable':True}

class WilliamsRTrigger:
    OVERSOLD = -80; OVERBOUGHT = -20
    def __init__(self, period=14): self.period = period
    def _calc(self, klines):
        if len(klines) < self.period: return None
        highs = np.array([k['high'] for k in klines])
        lows  = np.array([k['low']  for k in klines])
        closes = np.array([k['close'] for k in klines])
        hh = np.max(highs[-self.period:]); ll = np.min(lows[-self.period:])
        if hh == ll: return 0.0
        return np.clip(-100 * (hh - closes[-1]) / (hh - ll), -100, 0)
    def analyze(self, klines_5m, klines_15m):
        wr5 = self._calc(klines_5m); wr15 = self._calc(klines_15m)
        if wr5 is None or wr15 is None:
            return {'value_5m':-50,'value_15m':-50,'long_trigger':False,'short_trigger':False}
        prev5 = self._calc(klines_5m[:-1]) if len(klines_5m) >= self.period+1 else wr5
        long_trigger = prev5 is not None and prev5 <= self.OVERSOLD and wr5 > self.OVERSOLD
        short_trigger = prev5 is not None and prev5 >= self.OVERBOUGHT and wr5 < self.OVERBOUGHT
        return {'value_5m':wr5,'value_15m':wr15,'long_trigger':long_trigger,'short_trigger':short_trigger}

class SupertrendFilter:
    def __init__(self, period_short=10, period_long=14, multiplier=3.0):
        self.period_short = period_short; self.period_long = period_long; self.multiplier = multiplier
    def _calc(self, klines, period):
        if len(klines) < period + 2: return None, 0.0, 'neutral'
        highs = np.array([k['high'] for k in klines])
        lows  = np.array([k['low']  for k in klines])
        closes = np.array([k['close'] for k in klines])
        hl2 = (highs + lows) / 2
        tr = np.maximum(np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1))), np.abs(lows - np.roll(closes, 1)))
        tr[0] = highs[0] - lows[0]
        atr = pd.Series(tr).ewm(span=period, adjust=False).mean().values
        upper_basic = hl2 + self.multiplier * atr; lower_basic = hl2 - self.multiplier * atr
        n = len(klines); upper = upper_basic.copy(); lower = lower_basic.copy()
        direction = np.ones(n, dtype=int)
        for i in range(1, n):
            if closes[i-1] <= upper[i-1]: upper[i] = min(upper[i], upper[i-1])
            if closes[i-1] >= lower[i-1]: lower[i] = max(lower[i], lower[i-1])
            if closes[i] > upper[i-1]: direction[i] = 1
            elif closes[i] < lower[i-1]: direction[i] = -1
            else: direction[i] = direction[i-1]
        last_dir = direction[-1]; label = 'bullish' if last_dir == 1 else 'bearish'
        return 0, last_dir, label
    def analyze(self, klines_5m, klines_15m, klines_1h):
        _, _, label5 = self._calc(klines_5m, self.period_short)
        _, _, label15 = self._calc(klines_15m, self.period_short)
        _, _, label1h = self._calc(klines_1h, self.period_long)
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
        veto = False; veto_reason = ""
        if not ci_state['tradeable']: veto = True; veto_reason = "CI lateral"
        if not veto:
            if base_direction == 'SHORT' and wr_state['long_trigger']: veto = True; veto_reason = "WR long en short"
            if base_direction == 'LONG' and wr_state['short_trigger']: veto = True; veto_reason = "WR short en long"
        if not veto:
            if base_direction == 'SHORT' and st_state['bias'] == 'bullish' and st_state['aligned']: veto = True; veto_reason = "ST alcista en short"
            if base_direction == 'LONG' and st_state['bias'] == 'bearish' and st_state['aligned']: veto = True; veto_reason = "ST bajista en long"
        return {'veto':veto,'veto_reason':veto_reason,'ci':ci_state,'wr':wr_state,'st':st_state}

class TradeSetupBuilder:
    def __init__(self, entry_price, stop_loss, capital, risk_pct=0.01, trend_h4='neutral',
                 signal_direction='neutral', tp_ratio=1.5, score=85):
        self.entry = entry_price; self.sl = stop_loss; self.capital = capital
        self.base_risk_pct = risk_pct; self.trend_h4 = trend_h4
        self.signal_direction = signal_direction; self.tp_ratio = tp_ratio; self.score = score
    def build(self):
        sl_pct = abs(self.entry - self.sl) / self.entry
        MAX_SL_PCT = 0.018
        if sl_pct > MAX_SL_PCT:
            if self.signal_direction == 'bullish': self.sl = self.entry * (1 - MAX_SL_PCT)
            else: self.sl = self.entry * (1 + MAX_SL_PCT)
            sl_pct = MAX_SL_PCT
        risk_usd = self.capital * self.base_risk_pct
        effective_risk = risk_usd
        if (self.trend_h4 == 'bullish' and self.signal_direction == 'bearish') or \
           (self.trend_h4 == 'bearish' and self.signal_direction == 'bullish'):
            effective_risk *= 0.5
        notional = effective_risk / sl_pct; contracts = notional / self.entry
        if self.entry > self.sl:
            tp1 = self.entry + self.tp_ratio * (self.entry - self.sl)
            tp2 = self.entry + 2 * self.tp_ratio * (self.entry - self.sl)
        else:
            tp1 = self.entry - self.tp_ratio * (self.sl - self.entry)
            tp2 = self.entry - 2 * self.tp_ratio * (self.sl - self.entry)
        return {'entry':self.entry,'sl':self.sl,'tp1':tp1,'tp2':tp2,'contracts':contracts}

# ─── MOTOR CON FEATURES ───
class ScalpingEngineFull:
    def __init__(self, symbol, config, capital=100.0, risk_pct=0.01, phase_mode='strict'):
        self.symbol = symbol; self.capital = capital; self.risk_pct = risk_pct
        self.config = config; self.phase_mode = phase_mode
        self.filter_manager = EnhancedFilterManager(
            choppiness_threshold=config['choppiness_neutral_threshold'],
            supertrend_multiplier=config['supertrend_multiplier']
        )

    def run(self, klines, current_price, current_ts):
        indicators = IndicatorEngine(klines).calculate()
        if not indicators.get('4h'): return None
        struct_h4 = StructureAnalyzer(klines['4h']).analyze()
        phase_h1 = MarketPhaseDetector(indicators['1h'], klines['1h'], '1h').detect()
        phase_h4 = MarketPhaseDetector(indicators['4h'], klines['4h'], '4h').detect()
        mom = MomentumAnalyzer(indicators).analyze()
        fib = WeeklyAdaptiveFibonacci(klines['1d'], days=self.config['fibonacci_days'])
        fib_block = fib.get_current_block(current_price)
        if fib_block is None: return None

        signal = 'WAIT'; direction = 'neutral'
        if PatternDetector.is_bearish_engulfing(klines['4h']):
            signal = 'SHORT'; direction = 'bearish'
        elif PatternDetector.is_bullish_engulfing(klines['4h']):
            signal = 'LONG'; direction = 'bullish'

        if signal != 'WAIT':
            if self.phase_mode == 'strict':
                if phase_h1 in ('ranging', 'neutral'):
                    signal = 'WAIT'
            elif self.phase_mode == 'flexible':
                ci_val = indicators.get('1h', {}).get('bb_width', 0.03)
                if phase_h1 in ('ranging', 'neutral') and (ci_val is None or ci_val > 0.03):
                    signal = 'WAIT'

        if signal == 'WAIT':
            return None

        enhanced = self.filter_manager.evaluate(klines['5m'], klines['15m'], klines['1h'], signal)
        if enhanced['veto']: return None

        entry = indicators['5m']['close'] if indicators['5m'] else indicators['15m']['close'] if indicators['15m'] else indicators['1h']['close']
        if signal == 'SHORT':
            last_high = max([float(k['high']) for k in klines['15m'][-10:]])
            sl = last_high * 1.002
        else:
            last_low = min([float(k['low']) for k in klines['15m'][-10:]])
            sl = last_low * 0.998

        trade = TradeSetupBuilder(
            entry, sl, capital=self.capital, risk_pct=self.risk_pct,
            trend_h4=struct_h4.get('trend','neutral'), signal_direction=direction,
            tp_ratio=self.config['tp_ratio'], score=85
        ).build()

        # features
        k4h = klines['4h']
        body_ratio_4h = 0.0
        if k4h and len(k4h) >= 1:
            last4 = k4h[-1]
            tr4 = float(last4['high']) - float(last4['low'])
            if tr4 > 0:
                body_ratio_4h = abs(float(last4['close']) - float(last4['open'])) / tr4

        vol_5m = np.array([float(k['volume']) for k in klines['5m']])
        vol_ratio_5m = vol_5m[-1] / np.mean(vol_5m[-20:]) if len(vol_5m) >= 20 else 1.0

        features = {
            'fib_low_key': fib_block[0] if fib_block else None,
            'fib_high_key': fib_block[2] if fib_block else None,
            'fib_width': (fib_block[3] - fib_block[1]) / fib_block[1] if fib_block and fib_block[1] != 0 else 0.0,
            'phase_h1': phase_h1,
            'ci_value': enhanced['ci'].get('value', 50.0),
            'wr_5m': enhanced['wr'].get('value_5m', -50.0),
            'wr_15m': enhanced['wr'].get('value_15m', -50.0),
            'st_aligned': 1 if enhanced['st'].get('aligned') else 0,
            'st_bias_bullish': 1 if enhanced['st'].get('bias') == 'bullish' else 0,
            'st_bias_bearish': 1 if enhanced['st'].get('bias') == 'bearish' else 0,
            'mom_score': mom['score'],
            'mom_direction': mom['direction'],
            'vol_ratio_5m': vol_ratio_5m,
            'body_ratio_4h': body_ratio_4h,
            'hour_of_day': datetime.fromtimestamp(current_ts/1000, tz=timezone.utc).hour,
            'direction_long': 1 if direction == 'LONG' else 0
        }
        return trade, features, direction, enhanced

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
                sl_active = entry_real; breakeven_moved = True
            if not partial_done and high >= tp1:
                close_contracts = contracts_total * 0.6
                pnl = (tp1 - entry_real) * close_contracts
                comm = tp1 * close_contracts * COMMISSION
                events.append({"pnl":pnl,"comm":comm})
                remaining = contracts_total * 0.4; sl_active = entry_real
                breakeven_moved = True; partial_done = True
            if partial_done and high >= tp2:
                pnl = (tp2 - entry_real) * remaining
                comm = tp2 * remaining * COMMISSION
                events.append({"pnl":pnl,"comm":comm})
                remaining = 0; break
            if low <= sl_active:
                exit_price = sl_active - slippage_exit
                if not partial_done:
                    pnl = (exit_price - entry_real) * contracts_total
                    comm = exit_price * contracts_total * COMMISSION
                    events.append({"pnl":pnl,"comm":comm})
                else:
                    pnl = (exit_price - entry_real) * remaining
                    comm = exit_price * remaining * COMMISSION
                    events.append({"pnl":pnl,"comm":comm})
                remaining = 0; break
        else:
            if not breakeven_moved and low <= half_target:
                sl_active = entry_real; breakeven_moved = True
            if not partial_done and low <= tp1:
                close_contracts = contracts_total * 0.6
                pnl = (entry_real - tp1) * close_contracts
                comm = tp1 * close_contracts * COMMISSION
                events.append({"pnl":pnl,"comm":comm})
                remaining = contracts_total * 0.4; sl_active = entry_real
                breakeven_moved = True; partial_done = True
            if partial_done and low <= tp2:
                pnl = (entry_real - tp2) * remaining
                comm = tp2 * remaining * COMMISSION
                events.append({"pnl":pnl,"comm":comm})
                remaining = 0; break
            if high >= sl_active:
                exit_price = sl_active + slippage_exit
                if not partial_done:
                    pnl = (entry_real - exit_price) * contracts_total
                    comm = exit_price * contracts_total * COMMISSION
                    events.append({"pnl":pnl,"comm":comm})
                else:
                    pnl = (entry_real - exit_price) * remaining
                    comm = exit_price * remaining * COMMISSION
                    events.append({"pnl":pnl,"comm":comm})
                remaining = 0; break
    if remaining > 0:
        last_price = candles[-1]['close']
        pnl = (last_price - entry_real) * remaining if direction == 'bullish' else (entry_real - last_price) * remaining
        comm = last_price * remaining * COMMISSION
        events.append({"pnl":pnl,"comm":comm})
    commission_entry = entry_real * contracts_total * COMMISSION
    net = -commission_entry
    for ev in events:
        if 'pnl' in ev:
            net += ev['pnl'] - ev['comm']
    return net

def run_backtest_full(data_all, phase_mode):
    capital = INITIAL_CAPITAL
    trades_list = []
    for sym in SYMBOLS:
        data = data_all[sym]
        candles_1h = data['1h']
        if len(candles_1h) < 10: continue
        engine = ScalpingEngineFull(sym, SYMBOL_CONFIG[sym], capital=capital,
                                    risk_pct=FIXED_RISK_PCT, phase_mode=phase_mode)
        for i in range(10, len(candles_1h)):
            ts = candles_1h[i]['timestamp']
            trunc = {
                '5m':  [k for k in data['5m']  if k['timestamp'] <= ts],
                '15m': [k for k in data['15m'] if k['timestamp'] <= ts],
                '1h':  candles_1h[:i+1],
                '4h':  [k for k in data['4h']  if k['timestamp'] <= ts],
                '1d':  [k for k in data['1d']  if k['timestamp'] <= ts],
            }
            current_price = candles_1h[i]['close']
            result = engine.run(trunc, current_price, ts)
            if result is None: continue
            trade, features, direction, enhanced = result
            entry = trade['entry']; sl = trade['sl']; tp1 = trade['tp1']
            contracts = trade['contracts']
            start_idx_5m = next((idx for idx, c in enumerate(data['5m']) if c['timestamp'] >= ts), len(data['5m'])-1)
            atr_5m = 0.001 * current_price
            slippage_entry = SLIPPAGE_ATR_MULT * atr_5m
            if direction == 'bullish':
                entry_real = entry * (1 + SPREAD) + slippage_entry
            else:
                entry_real = entry * (1 - SPREAD) - slippage_entry
            pnl = simulate_exit(direction, entry_real, sl, tp1, contracts, data['5m'], start_idx_5m, atr_5m)
            capital += pnl
            trade_record = {
                'symbol': sym,
                'entry_time': datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat(),
                'direction': direction,
                'entry_signal': entry,
                'entry_real': entry_real,
                'sl_original': sl,
                'tp1': tp1,
                'contracts': contracts,
                'pnl_neto': pnl,
                'exit_events': '',
                'exit_type': '',
                'explanation': '',
                'score': 85,
                **features
            }
            trades_list.append(trade_record)
    df = pd.DataFrame(trades_list)
    return df, capital

# ─── MAIN ───
print("📂 Cargando datos...")
data_all = load_cache()

with open('ml_model.pkl', 'rb') as f:
    model = pickle.load(f)
features_ok = model.feature_names_in_

modos = ['strict', 'flexible', 'free']
resultados_sin_ml = {}
resultados_con_ml = {}

for modo in modos:
    print(f"\n🔬 Ejecutando {modo} (con features)...")
    df, _ = run_backtest_full(data_all, modo)
    if df.empty:
        print("   Sin trades.")
        continue
    df.to_csv(f'backtest_{modo}.csv', index=False)
    wr = (df['pnl_neto'] > 0).mean()
    pnl = df['pnl_neto'].sum()
    curve = (100 + df['pnl_neto'].cumsum()).values
    peak = np.maximum.accumulate(curve)
    dd = (peak - curve).max()
    resultados_sin_ml[modo] = {'trades': len(df), 'wr': wr, 'pnl': pnl, 'dd': dd}
    print(f"   Sin ML: Trades={len(df)}, WR={wr:.2%}, PnL=${pnl:.2f}, DD=${dd:.2f}")

    # ML
    phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
    mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
    df_ml = pd.concat([df, phase_dummies, mom_dummies], axis=1)
    df_ml.drop(['phase_h1','mom_direction'], axis=1, inplace=True)
    cols_evitar = ['symbol','entry_time','direction','entry_signal','entry_real',
                   'sl_original','tp1','contracts','pnl_neto','exit_events',
                   'exit_type','explanation','score']
    feature_cols = [c for c in df_ml.columns if c not in cols_evitar]
    X = df_ml[feature_cols].fillna(0)
    for col in features_ok:
        if col not in X.columns:
            X[col] = 0.0
    X = X[features_ok]
    prob = model.predict_proba(X)[:,1]
    filtro = prob > 0.55
    df_filtrado = df[filtro]
    if df_filtrado.empty:
        print("   Con ML: vetó todos.")
        resultados_con_ml[modo] = {'trades': 0, 'wr': 0, 'pnl': 0, 'dd': 0}
    else:
        wr_ml = (df_filtrado['pnl_neto'] > 0).mean()
        pnl_ml = df_filtrado['pnl_neto'].sum()
        curve_ml = (100 + df_filtrado['pnl_neto'].cumsum()).values
        dd_ml = (np.maximum.accumulate(curve_ml) - curve_ml).max()
        resultados_con_ml[modo] = {'trades': len(df_filtrado), 'wr': wr_ml, 'pnl': pnl_ml, 'dd': dd_ml}
        print(f"   Con ML: Trades={len(df_filtrado)}, WR={wr_ml:.2%}, PnL=${pnl_ml:.2f}, DD=${dd_ml:.2f}")

print("\n📊 COMPARATIVA FINAL (3 meses)")
print("Modo      | Sin ML               | Con ML")
for modo in modos:
    s = resultados_sin_ml.get(modo)
    c = resultados_con_ml.get(modo)
    if s and c:
        print(f"{modo:10s} | {s['trades']:3d} {s['wr']:.2%} ${s['pnl']:.2f} DD${s['dd']:.2f} | {c['trades']:3d} {c['wr']:.2%} ${c['pnl']:.2f} DD${c['dd']:.2f}")
