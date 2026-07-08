#!/usr/bin/env python3
"""
ETH Adaptive Trading Agent con ML + Fibonacci 9 niveles (RECALIBRADO)
====================================================================
Agente adaptativo para ETHUSDT. Opera según régimen de mercado
(RANGO/TENDENCIA/RUPTURA) con filtro ML integrado.
"""

from __future__ import annotations
import time, logging, sys, os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
import numpy as np, pandas as pd, requests

# ─── CONFIG ───
SYMBOL = "ETHUSDT"
START_DATE = "2026-01-01"
END_DATE = "2026-06-23"
TIMEFRAMES = ("1h","15m","5m")
COMMISSION_PCT = 0.05
SPREAD_PCT = 0.02
SLIPPAGE_ATR_MULT = 0.1
RISK_PER_TRADE = 0.01
INITIAL_EQUITY = 10000.0
ADX_PERIOD, BB_PERIOD, BB_STD = 14, 20, 2.0
EMA_PERIOD, ATR_PERIOD, VOLUME_MA_PERIOD = 20, 14, 20
FIB_WINDOW = 168
FIB_LEVELS = [0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0]
ML_THRESHOLD = 0.50  # recalibrado ETH

logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eth_adaptive")

# ─── FETCHER ───
class BinanceFetcher:
    BASE_URL = "https://api.binance.com"
    KLINES = "/api/v3/klines"
    def __init__(self, delay=0.2, retries=3):
        self.sess = requests.Session(); self.sess.headers.update({"User-Agent":"ETHAdaptive/1.0"})
        self.delay = delay; self.retries = retries
    @staticmethod
    def to_ms(s): return int(datetime.strptime(s,"%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()*1000)
    def _get(self, params):
        for attempt in range(1,self.retries+1):
            try:
                r = self.sess.get(f"{self.BASE_URL}{self.KLINES}", params=params, timeout=15)
                if r.status_code==429: time.sleep(2**attempt); continue
                if r.status_code>=500: time.sleep(2**attempt); continue
                r.raise_for_status(); return r.json()
            except: time.sleep(2**attempt)
        raise RuntimeError("Falla requests")
    def fetch(self, symbol, interval, start_ms, end_ms):
        data=[]; cur=start_ms
        while cur<end_ms:
            raw=self._get({"symbol":symbol,"interval":interval,"startTime":cur,"endTime":end_ms,"limit":1000})
            if not raw: break
            for k in raw: data.append({"timestamp":k[0],"open":float(k[1]),"high":float(k[2]),"low":float(k[3]),"close":float(k[4]),"volume":float(k[5])})
            cur=raw[-1][0]+1; time.sleep(self.delay)
            if len(raw)<1000: break
        logger.info("  %s %s: %d velas",symbol,interval,len(data))
        return data
    def fetch_all(self, symbol, start, end, tfs=TIMEFRAMES):
        ms_s, ms_e = self.to_ms(start), self.to_ms(end)
        return {tf:self.fetch(symbol,tf,ms_s,ms_e) for tf in tfs}

def candles_to_df(candles):
    if not candles: return pd.DataFrame(columns=["open","high","low","close","volume"])
    df=pd.DataFrame(candles); df["datetime"]=pd.to_datetime(df["timestamp"],unit="ms",utc=True)
    return df.set_index("datetime").sort_index()[["open","high","low","close","volume"]]

# ─── INDICADORES ───
def ema(s,p): return s.ewm(span=p,adjust=False).mean()
def atr(df,p=14):
    h,l,c=df["high"],df["low"],df["close"]; pc=c.shift(1)
    tr=pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    return tr.rolling(p,min_periods=p).mean()
def adx(df,p=14):
    h,l,c=df["high"],df["low"],df["close"]; up=h.diff(); dn=-l.diff()
    p_dm=pd.Series(np.where((up>dn)&(up>0),up,0.0),index=df.index)
    m_dm=pd.Series(np.where((dn>up)&(dn>0),dn,0.0),index=df.index)
    tr=pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    at=tr.ewm(alpha=1/p,min_periods=p,adjust=False).mean()
    pdi=100*(p_dm.ewm(alpha=1/p,min_periods=p,adjust=False).mean()/at.replace(0,np.nan))
    mdi=100*(m_dm.ewm(alpha=1/p,min_periods=p,adjust=False).mean()/at.replace(0,np.nan))
    dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,np.nan)
    return dx.ewm(alpha=1/p,min_periods=2*p,adjust=False).mean()
def bollinger_width(df,p=20,n=2.0):
    mid=df["close"].rolling(p,min_periods=p).mean(); std=df["close"].rolling(p,min_periods=p).std()
    return (mid+n*std-(mid-n*std))/mid.replace(0,np.nan)
def volume_ratio(df,p=20):
    ma=df["volume"].rolling(p,min_periods=p).mean(); return df["volume"]/ma.replace(0,np.nan)
def ema_slope(s,lb=3): return s.diff(lb)

# ─── FIBONACCI (9 niveles, ventana 168 velas 1h) ───
def weekly_adaptive_fibonacci(df_1h, idx):
    if idx < 10: return None, None, 0.0
    start = max(0, idx - FIB_WINDOW)
    sub = df_1h.iloc[start:idx+1]
    if sub.empty: return None, None, 0.0
    sw_high = sub["high"].max(); sw_low = sub["low"].min()
    if sw_high <= sw_low: return None, None, 0.0
    rng = sw_high - sw_low
    levels = {lvl: sw_low + rng*lvl for lvl in FIB_LEVELS}
    price = sub["close"].iloc[-1]
    fib_low_key, fib_high_key = None, None
    for lvl in sorted(levels.keys()):
        if levels[lvl] <= price: fib_low_key = lvl
        if levels[lvl] >= price and fib_high_key is None: fib_high_key = lvl
    if fib_low_key is None: fib_low_key = 0.0
    if fib_high_key is None: fib_high_key = 2.0
    fib_width = (sw_high - sw_low) / sw_low if sw_low != 0 else 0.0
    return fib_low_key, fib_high_key, fib_width

# ─── REGIME DETECTOR ───
class RegimeDetector:
    def __init__(self, adx_rng=20.0, adx_trd=25.0, bb_rng=0.04, bb_brk=0.06, vol_brk=1.5, slope_lb=3):
        self.adx_rng=adx_rng; self.adx_trd=adx_trd; self.bb_rng=bb_rng; self.bb_brk=bb_brk
        self.vol_brk=vol_brk; self.slope_lb=slope_lb
    def detect(self, df_1h):
        adx_v=adx(df_1h,ADX_PERIOD); bb_w=bollinger_width(df_1h,BB_PERIOD,BB_STD)
        vol_r=volume_ratio(df_1h,VOLUME_MA_PERIOD); sl=ema_slope(ema(df_1h["close"],EMA_PERIOD),self.slope_lb)
        reg=pd.Series("INDEFINIDO",index=df_1h.index,dtype=object)
        reg[(adx_v>self.adx_trd)&(sl>0)]="TENDENCIA_ALCISTA"
        reg[(adx_v>self.adx_trd)&(sl<0)]="TENDENCIA_BAJISTA"
        reg[(adx_v<self.adx_rng)&(bb_w<self.bb_rng)]="RANGO"
        reg[(bb_w>self.bb_brk)&(vol_r>self.vol_brk)]="RUPTURA"
        return reg

# ─── SEÑALES ───
@dataclass
class Signal:
    timestamp: pd.Timestamp; direction: int; entry_price: float
    stop_loss: float; take_profit: float; regime: str; strategy: str; atr_at_signal: float=0.0

class MicroStrategies:
    def __init__(self):
        self.rng_lb=5; self.rng_tp=0.5; self.trd_min=0.2; self.trd_max=0.3
        self.trd_tp=2.0; self.brk_lb=6; self.brk_vol=1.5; self.brk_tp=1.5
    def range_signal(self,df,idx,regime,atr_v):
        if idx<self.rng_lb+2: return None
        sub=df.iloc[:idx+1]; c=sub["close"].iloc[-1]; h=sub["high"].iloc[-1]; l=sub["low"].iloc[-1]; ts=sub.index[-1]
        sh=sub["high"].iloc[-self.rng_lb-1:-1].max(); sl=sub["low"].iloc[-self.rng_lb-1:-1].min()
        if pd.isna(sh) or pd.isna(sl) or sh==sl: return None
        tol=0.002*c; rw=sh-sl
        if abs(l-sl)<tol and c>sl:
            e=c; stop=sh; tp=e+self.rng_tp*rw
            if tp>e and stop>e: return Signal(ts,1,e,stop,tp,regime,"range",atr_v)
        if abs(h-sh)<tol and c<sh:
            e=c; stop=sl; tp=e-self.rng_tp*rw
            if tp<e and stop<e: return Signal(ts,-1,e,stop,tp,regime,"range",atr_v)
        return None
    def trend_signal(self,df,idx,regime,atr_v):
        if idx<5 or pd.isna(atr_v) or atr_v==0: return None
        sub=df.iloc[:idx+1]; c=sub["close"].iloc[-1]; ts=sub.index[-1]
        if regime=="TENDENCIA_ALCISTA":
            sa=sub["low"].iloc[-10:-1].min()
            if pd.isna(sa): return None
            rh=sub["high"].iloc[-20:-1].max()
            if pd.isna(rh): return None
            pb=(rh-c)/atr_v
            if not (self.trd_min<=pb<=self.trd_max): return None
            e=c; stop=sa; tp=e+self.trd_tp*atr_v
            if tp>e and stop<e: return Signal(ts,1,e,stop,tp,regime,"trend",atr_v)
        elif regime=="TENDENCIA_BAJISTA":
            sa=sub["high"].iloc[-10:-1].max()
            if pd.isna(sa): return None
            rl=sub["low"].iloc[-20:-1].min()
            if pd.isna(rl): return None
            pb=(c-rl)/atr_v
            if not (self.trd_min<=pb<=self.trd_max): return None
            e=c; stop=sa; tp=e-self.trd_tp*atr_v
            if tp<e and stop>e: return Signal(ts,-1,e,stop,tp,regime,"trend",atr_v)
        return None
    def breakout_signal(self,df,idx,regime,atr_v,vol_r):
        if idx<self.brk_lb+1 or pd.isna(vol_r) or vol_r<self.brk_vol: return None
        sub=df.iloc[:idx+1]; c=sub["close"].iloc[-1]; ts=sub.index[-1]
        rh=sub["high"].iloc[-self.brk_lb-1:-1].max(); rl=sub["low"].iloc[-self.brk_lb-1:-1].min()
        if pd.isna(rh) or pd.isna(rl) or rh==rl: return None
        rc=(rh+rl)/2; rw=rh-rl
        if c>rh:
            e=c; stop=rc; tp=e+self.brk_tp*rw
            if tp>e and stop<e: return Signal(ts,1,e,stop,tp,regime,"breakout",atr_v)
        if c<rl:
            e=c; stop=rc; tp=e-self.brk_tp*rw
            if tp<e and stop>e: return Signal(ts,-1,e,stop,tp,regime,"breakout",atr_v)
        return None
    def generate(self,df,idx,regime,atr_v,vol_r):
        if regime=="RANGO": return self.range_signal(df,idx,regime,atr_v)
        if regime in ("TENDENCIA_ALCISTA","TENDENCIA_BAJISTA"): return self.trend_signal(df,idx,regime,atr_v)
        if regime=="RUPTURA": return self.breakout_signal(df,idx,regime,atr_v,vol_r)
        return None

# ─── POSITION MANAGER ───
@dataclass
class Position:
    signal: Signal; entry_time: pd.Timestamp; entry_price: float; stop_loss: float
    take_profit: float; size: float; direction: int; regime_at_entry: str

@dataclass
class ClosedTrade:
    entry_time: pd.Timestamp; exit_time: pd.Timestamp; direction: int
    entry_price: float; exit_price: float; stop_loss: float; take_profit: float
    size: float; pnl_abs: float; pnl_pct: float; exit_reason: str
    regime_at_entry: str; strategy: str

class PositionManager:
    def __init__(self, max_bars=96):
        self.max_bars=max_bars
    def check_exit(self, pos, df_15m, current_time, current_regime):
        fut = df_15m.index[df_15m.index>pos.entry_time][:self.max_bars]
        if len(fut)==0: return None, None
        for ts in fut:
            h,l,c = df_15m.loc[ts,"high"],df_15m.loc[ts,"low"],df_15m.loc[ts,"close"]
            if pos.direction==1:
                if l<=pos.stop_loss: return self._close(pos,pos.stop_loss,ts,"sl"),None
                if h>=pos.take_profit: return self._close(pos,pos.take_profit,ts,"tp"),None
            else:
                if h>=pos.stop_loss: return self._close(pos,pos.stop_loss,ts,"sl"),None
                if l<=pos.take_profit: return self._close(pos,pos.take_profit,ts,"tp"),None
            if ts>=current_time: break
        if len(fut)>0:
            lts = fut[-1]; lp = df_15m.loc[lts,"close"]
            return self._close(pos,lp,lts,"timeout"),None
        return None, None
    def check_reversal(self, pos, current_regime, df_1h, idx, atr_v):
        if pos.direction==1 and current_regime=="TENDENCIA_BAJISTA":
            ns = Signal(df_1h.index[idx],-1,df_1h["close"].iloc[idx],
                        df_1h["high"].iloc[-10:-1].max(),
                        df_1h["close"].iloc[idx]-2*atr_v,current_regime,"reversal",atr_v)
            return True, ns
        if pos.direction==-1 and current_regime=="TENDENCIA_ALCISTA":
            ns = Signal(df_1h.index[idx],1,df_1h["close"].iloc[idx],
                        df_1h["low"].iloc[-10:-1].min(),
                        df_1h["close"].iloc[idx]+2*atr_v,current_regime,"reversal",atr_v)
            return True, ns
        return False, None
    def _close(self, pos, exit_price, exit_time, reason):
        slip = SLIPPAGE_ATR_MULT * pos.signal.atr_at_signal
        spread = SPREAD_PCT/100.0 * pos.entry_price
        if pos.direction==1:
            eff = exit_price - slip - spread
            pnl_abs = (eff - pos.entry_price) * pos.size
        else:
            eff = exit_price + slip + spread
            pnl_abs = (pos.entry_price - eff) * pos.size
        comm = (COMMISSION_PCT/100.0) * (pos.entry_price + eff) * pos.size
        pnl_abs -= comm
        return ClosedTrade(pos.entry_time,exit_time,pos.direction,pos.entry_price,eff,
                           pos.stop_loss,pos.take_profit,pos.size,pnl_abs,pnl_abs/INITIAL_EQUITY,
                           reason,pos.regime_at_entry,pos.signal.strategy)

# ─── FEATURES ML ───
class FeatureExtractor:
    @staticmethod
    def extract(df_1h, idx, direction, regime, atr_val, vol_ratio):
        fib_low, fib_high, fib_width = weekly_adaptive_fibonacci(df_1h, idx)
        # phase dummy
        phase_trending = 1 if regime in ("TENDENCIA_ALCISTA","TENDENCIA_BAJISTA") else 0
        phase_ranging  = 1 if regime=="RANGO" else 0
        phase_neutral  = 1 if regime=="INDEFINIDO" else 0
        # choppiness proxy (simple)
        ci = bollinger_width(df_1h.iloc[:idx+1],14,2.0).iloc[-1]
        if pd.isna(ci): ci = 0.04
        ci_value = max(0, min(100, 50 + (0.04 - ci)*2000))
        # williams %R proxy
        sub14 = df_1h.iloc[max(0,idx-14):idx+1]
        hh, ll = sub14["high"].max(), sub14["low"].min()
        wr5 = -50.0; wr15 = -50.0
        if hh != ll:
            wr_val = -100 * (hh - sub14["close"].iloc[-1]) / (hh - ll)
            wr5 = max(-100.0, min(0.0, wr_val))
            wr15 = wr5
        # supertrend dummy
        st_aligned = 1 if regime in ("TENDENCIA_ALCISTA","TENDENCIA_BAJISTA") else 0
        st_bull = 1 if regime=="TENDENCIA_ALCISTA" else 0
        st_bear = 1 if regime=="TENDENCIA_BAJISTA" else 0
        # momentum
        roc = (df_1h["close"].iloc[idx] - df_1h["close"].iloc[max(0,idx-14)]) / df_1h["close"].iloc[max(0,idx-14)] * 100
        mom_score = np.tanh(roc / 5)
        mom_bull = 1 if mom_score > 0.1 else 0
        mom_bear = 1 if mom_score < -0.1 else 0
        # body ratio 4h (usamos 1h como proxy)
        body = abs(df_1h["close"].iloc[idx] - df_1h["open"].iloc[idx])
        tr_bar = df_1h["high"].iloc[idx] - df_1h["low"].iloc[idx]
        body_ratio_4h = body / tr_bar if tr_bar > 0 else 0.0
        hour = df_1h.index[idx].hour
        dir_long = 1 if direction == 1 else 0

        return {
            "fib_low_key": fib_low if fib_low is not None else 0.0,
            "fib_high_key": fib_high if fib_high is not None else 0.0,
            "fib_width": fib_width,
            "phase_trending": phase_trending, "phase_ranging": phase_ranging, "phase_neutral": phase_neutral,
            "ci_value": ci_value,
            "wr_5m": wr5, "wr_15m": wr15,
            "st_aligned": st_aligned, "st_bias_bullish": st_bull, "st_bias_bearish": st_bear,
            "mom_score": mom_score, "mom_bullish": mom_bull, "mom_bearish": mom_bear,
            "vol_ratio_5m": vol_ratio if not pd.isna(vol_ratio) else 1.0,
            "body_ratio_4h": body_ratio_4h,
            "hour_of_day": hour,
            "direction_long": dir_long
        }

    @staticmethod
    def heuristic_prob(features, direction, regime):
        score = 0.50
        if features["st_aligned"]: score += 0.15
        if (direction == 1 and features["mom_score"] > 0.05) or (direction == -1 and features["mom_score"] < -0.05): score += 0.10
        if regime in ("TENDENCIA_ALCISTA","TENDENCIA_BAJISTA"): score += 0.15
        elif regime == "RANGO": score -= 0.02
        if direction == 1 and features["wr_5m"] < -80: score += 0.05
        if direction == -1 and features["wr_5m"] > -20: score += 0.05
        if features["body_ratio_4h"] > 0.6: score += 0.05
        if features["vol_ratio_5m"] > 1.5: score += 0.05
        fw = features.get("fib_width", 0.0)
        if 0.02 <= fw <= 0.15: score += 0.05
        elif fw > 0.20: score -= 0.03
        return score

# ─── BACKTESTER ───
class AdaptiveBacktester:
    def __init__(self, cooldown=12, use_ml=True):
        self.regime_detector = RegimeDetector()
        self.strategies = MicroStrategies()
        self.pos_mgr = PositionManager()
        self.equity = INITIAL_EQUITY
        self.cooldown = cooldown
        self.last_close = -cooldown - 1
        self.use_ml = use_ml
        self.ml_model = None
        self.feature_names = None
        if use_ml:
            try:
                import joblib
                self.ml_model = joblib.load("ml_model.pkl") if os.path.exists("ml_model.pkl") else None
                if self.ml_model is not None and hasattr(self.ml_model, "feature_names_in_"):
                    self.feature_names = self.ml_model.feature_names_in_
            except: pass

    def _ml_approve(self, features, direction, regime):
        if self.ml_model is not None and self.feature_names is not None:
            try:
                X = pd.DataFrame([features])[self.feature_names].fillna(0.0)
                prob = self.ml_model.predict_proba(X)[0,1]
                return prob >= ML_THRESHOLD, prob
            except: pass
        score = FeatureExtractor.heuristic_prob(features, direction, regime)
        return score >= ML_THRESHOLD, score

    def _compute_size(self, entry, stop):
        risk = self.equity * RISK_PER_TRADE
        pr = abs(entry - stop)
        return risk / pr if pr > 0 else 0.0

    def _apply_costs(self, price, atr_v, direction):
        slip = SLIPPAGE_ATR_MULT * atr_v
        spread = SPREAD_PCT / 100.0 * price
        return price + slip + spread if direction == 1 else price - slip - spread

    def run(self, data):
        df_1h = candles_to_df(data["1h"]); df_15m = candles_to_df(data["15m"])
        if df_1h.empty or df_15m.empty: return [], pd.DataFrame()
        logger.info("Backtest ETH adaptativo %d velas 1h...", len(df_1h))
        regimes = self.regime_detector.detect(df_1h)
        atr_1h = atr(df_1h, ATR_PERIOD)
        vol_r = volume_ratio(df_1h, VOLUME_MA_PERIOD)
        trades = []; pos = None; signals_eval = 0; signals_accepted = 0

        for i in range(len(df_1h)):
            ts = df_1h.index[i]; cr = regimes.iloc[i]
            av = atr_1h.iloc[i] if not pd.isna(atr_1h.iloc[i]) else 0.0
            vr = vol_r.iloc[i]

            if pos is not None:
                ct, _ = self.pos_mgr.check_exit(pos, df_15m, ts, cr)
                if ct is not None:
                    trades.append(ct); self.equity += ct.pnl_abs; pos = None; self.last_close = i
                else:
                    rev, ns = self.pos_mgr.check_reversal(pos, cr, df_1h, i, av)
                    if rev and ns is not None:
                        ct = self.pos_mgr._close(pos, df_1h["close"].iloc[i], ts, "reversal")
                        trades.append(ct); self.equity += ct.pnl_abs; self.last_close = i
                        sz = self._compute_size(ns.entry_price, ns.stop_loss)
                        if sz > 0:
                            ep = self._apply_costs(ns.entry_price, av, ns.direction)
                            pos = Position(signal=ns, entry_time=ts, entry_price=ep,
                                           stop_loss=ns.stop_loss, take_profit=ns.take_profit,
                                           size=sz, direction=ns.direction, regime_at_entry=cr)

            if pos is None and (i - self.last_close) >= self.cooldown:
                sig = self.strategies.generate(df_1h, i, cr, av, vr)
                if sig is not None:
                    signals_eval += 1
                    features = FeatureExtractor.extract(df_1h, i, sig.direction, cr, av, vr)
                    approved, prob = self._ml_approve(features, sig.direction, cr)
                    if approved:
                        signals_accepted += 1
                        sz = self._compute_size(sig.entry_price, sig.stop_loss)
                        if sz > 0:
                            ep = self._apply_costs(sig.entry_price, av, sig.direction)
                            pos = Position(signal=sig, entry_time=ts, entry_price=ep,
                                           stop_loss=sig.stop_loss, take_profit=sig.take_profit,
                                           size=sz, direction=sig.direction, regime_at_entry=cr)

            if (i+1)%500==0: logger.info("  %d/%d | Equity: $%.2f | Trades: %d",i+1,len(df_1h),self.equity,len(trades))

        if pos is not None:
            ct = self.pos_mgr._close(pos, df_1h["close"].iloc[-1], df_1h.index[-1], "end_of_data")
            trades.append(ct); self.equity += ct.pnl_abs

        logger.info("Backtest ETH ML completado: %d trades, %d/%d señales aceptadas",
                    len(trades), signals_accepted, signals_eval)
        return trades, regimes

# ─── MÉTRICAS Y REPORTE ───
def metrics(trades, days):
    if not trades: return {"num":0,"tpm":0,"pf":0,"wr":0,"dd":0,"pnl":0}
    pnls = np.array([t.pnl_abs for t in trades])
    wins = pnls[pnls>0]; losses = pnls[pnls<0]
    gp = wins.sum() if len(wins)>0 else 0.0; gl = abs(losses.sum()) if len(losses)>0 else 0.0
    pf = gp/gl if gl>0 else (99.0 if gp>0 else 0.0)
    wr = len(wins)/len(trades); tpnl = float(pnls.sum())
    eq = INITIAL_EQUITY + np.cumsum(pnls); rm = np.maximum.accumulate(eq)
    dd = float(abs((eq - rm).min()))
    return {"num":len(trades),"tpm":round(len(trades)/(days/30),2),"pf":round(pf,4),
            "wr":round(wr,4),"dd_abs":round(dd,2),"dd_pct":round(dd/rm.max(),4) if rm.max()>0 else 0,
            "pnl":round(tpnl,2),"final":round(float(eq[-1]),2)}

def report(met, trades, regimes):
    print("\n"+"="*72); print("  🤖 ETH ADAPTIVE AGENT ML (RECALIBRADO) - BACKTEST")
    print("="*72); print(f"  Período: {START_DATE} → {END_DATE}")
    print(f"  Equity inicial: ${INITIAL_EQUITY:,.2f} | Risk: {RISK_PER_TRADE:.1%}")
    print("="*72)
    print(f"  Trades: {met['num']} | Trades/mes: {met['tpm']}")
    print(f"  Profit Factor: {met['pf']:.4f} | Win Rate: {met['wr']:.1%}")
    print(f"  Max DD: ${met['dd_abs']:,.2f} ({met['dd_pct']:.1%})")
    print(f"  PnL: ${met['pnl']:,.2f} | Equity final: ${met['final']:,.2f}")
    if trades:
        rc={}; rp={}
        for t in trades:
            rc[t.regime_at_entry]=rc.get(t.regime_at_entry,0)+1
            rp[t.regime_at_entry]=rp.get(t.regime_at_entry,0)+t.pnl_abs
        print("\n  POR RÉGIMEN:")
        for r in sorted(rc): print(f"    {r:25s}: {rc[r]:>4d} trades, PnL=${rp[r]:>12,.2f}")
        sc={}; sp={}
        for t in trades:
            sc[t.strategy]=sc.get(t.strategy,0)+1
            sp[t.strategy]=sp.get(t.strategy,0)+t.pnl_abs
        print("\n  POR ESTRATEGIA:")
        for s in sorted(sc): print(f"    {s:15s}: {sc[s]:>4d} trades, PnL=${sp[s]:>12,.2f}")
    print("="*72)
    pf_ok=met['pf']>1.5; fq_ok=met['tpm']>=20; dd_ok=met['dd_pct']<0.20
    print(f"  PF>1.5: {'✅' if pf_ok else '❌'} ({met['pf']:.3f})")
    print(f"  ≥20/mes: {'✅' if fq_ok else '❌'} ({met['tpm']})")
    print(f"  DD<20%: {'✅' if dd_ok else '❌'} ({met['dd_pct']:.1%})")
    print("="*72+"\n")

def main():
    print("\n"+"#"*72); print("  🤖 ETH ADAPTIVE AGENT ML"); print("#"*72)
    fetcher = BinanceFetcher(); data = fetcher.fetch_all(SYMBOL, START_DATE, END_DATE)
    print(f"\n✅ Datos: {sum(len(v) for v in data.values())} velas")
    print("🔄 Backtest con ML...")
    bt = AdaptiveBacktester(use_ml=True)
    trades, regimes = bt.run(data)
    sd = datetime.strptime(START_DATE,"%Y-%m-%d"); ed = datetime.strptime(END_DATE,"%Y-%m-%d")
    met = metrics(trades, (ed-sd).days)
    report(met, trades, regimes)
    if trades:
        pd.DataFrame([{ "entry":t.entry_time,"exit":t.exit_time,"dir":t.direction,
                        "entry_p":t.entry_price,"exit_p":t.exit_price,"pnl":t.pnl_abs,
                        "regime":t.regime_at_entry,"strategy":t.strategy,"reason":t.exit_reason}
                      for t in trades]).to_csv("eth_adaptive_ml_trades.csv",index=False)
        print("💾 Trades guardados en eth_adaptive_ml_trades.csv")

if __name__=="__main__":
    main()
