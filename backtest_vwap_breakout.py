#!/usr/bin/env python3
"""
BACKTEST VWAP BREAKOUT — Simula EXACTAMENTE multi_bot.py
Período: Abr-Jul 2026 | Capital: $500 ($100/bot) | 5 pares
Estrategia: VWAP deviation breakout con TP1 parcial + trailing stop
"""

import sys, os, time, math, json
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

# ═══════════════════ CONFIGURACIÓN IDÉNTICA A multi_bot.py ═══════════════════
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
START_DATE = datetime(2026, 4, 1, tzinfo=timezone.utc)
END_DATE   = datetime(2026, 7, 31, 23, 59, tzinfo=timezone.utc)
BASE_URL   = "https://fapi.binance.com/fapi/v1/klines"
MAX_KLINES = 1500

INITIAL_EQUITY_PER_BOT = 100.0
TOTAL_INITIAL_CAPITAL = 500.0

# Costos idénticos a multi_bot.py
COMMISSION = 0.0005          # 0.05% taker por lado
SPREAD = 0.0002              # 0.02% spread fijo
SLIP_ATR_MULT = 0.1          # 0.1 x ATR(5m) slippage por lado
ATR5M_N = 14                 # ATR lookback 5m (para slippage)
ATR15M_N = 14                # ATR lookback 15m (para SL/TP sizing)

# Parámetros de salida idénticos
TP1_FRACTION = 0.6           # cerrar 60% en primer target
TRAIL_MULT = 2.0             # trailing distance = 2.0 x ATR(5m)
TRAIL_ACTIVATE_PCT = 0.3     # activar trailing cuando pos restante 0.3% ITM

# Cooldown idéntico
LOSS_STREAK_TRIGGER = 5
PAUSE_MINUTES = 60

# Anti-fail risk idéntico
DD_THRESHOLDS = [(3, 1.0), (6, 0.75), (10, 0.5), (float('inf'), 0.0)]

# Parámetros por bot (IDÉNTICOS a multi_bot.py)
BOT_CONFIGS = {
    "BTC": {
        "symbol": "BTCUSDT",
        "risk_pct": 0.01,
        "min_order_size": 0.001,
        "multimodal": True,
        "regime_params": {
            "ALCISTA":  dict(vwap_n=12, dev_thr=1.0, sl_mult=2.0, tp_mult=2.5),
            "BAJISTA":  dict(vwap_n=15, dev_thr=1.25, sl_mult=2.5, tp_mult=3.0),
            "LATERAL":  dict(vwap_n=15, dev_thr=0.75, sl_mult=1.5, tp_mult=1.5),
        }
    },
    "ETH": {
        "symbol": "ETHUSDT",
        "risk_pct": 0.01,
        "min_order_size": 0.001,
        "multimodal": False,
        "params": dict(vwap_n=12, dev_thr=1.0, sl_mult=2.0, tp_mult=2.0),
    },
    "SOL": {
        "symbol": "SOLUSDT",
        "risk_pct": 0.012,
        "min_order_size": 0.01,
        "multimodal": False,
        "params": dict(vwap_n=10, dev_thr=1.0, sl_mult=1.5, tp_mult=1.5),
    },
    "XRP": {
        "symbol": "XRPUSDT",
        "risk_pct": 0.012,
        "min_order_size": 0.1,
        "multimodal": False,
        "params": dict(vwap_n=10, dev_thr=1.0, sl_mult=2.0, tp_mult=2.0),
    },
    "BNB": {
        "symbol": "BNBUSDT",
        "risk_pct": 0.012,
        "min_order_size": 0.01,
        "multimodal": False,
        "params": dict(vwap_n=10, dev_thr=1.0, sl_mult=1.5, tp_mult=1.5),
    },
}

EMA_DAILY_N = 20
REGIME_HYSTERESIS = 2

# ═══════════════════ FUNCIONES DE DATOS ═══════════════════
def fetch_klines_range(symbol: str, interval: str, start_dt: datetime, end_dt: datetime) -> List[Dict]:
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    all_klines = []
    while start_ms < end_ms:
        params = {"symbol": symbol, "interval": interval, "limit": MAX_KLINES,
                  "startTime": start_ms, "endTime": end_ms}
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
        except Exception as e:
            print(f"  Error conexión {symbol} {interval}: {e}")
            time.sleep(1)
            continue
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text}")
            time.sleep(1)
            continue
        data = resp.json()
        if not data:
            break
        batch = [{
            'timestamp': int(k[0]),
            'open': float(k[1]), 'high': float(k[2]),
            'low': float(k[3]), 'close': float(k[4]),
            'volume': float(k[5])
        } for k in data]
        all_klines.extend(batch)
        start_ms = batch[-1]['timestamp'] + 1
        time.sleep(0.15)
    return [k for k in all_klines if start_dt.timestamp()*1000 <= k['timestamp'] <= end_dt.timestamp()*1000]

def fetch_all_data(symbol: str) -> Dict[str, List]:
    print(f"  Descargando {symbol}...")
    return {
        '15m': fetch_klines_range(symbol, '15m', START_DATE, END_DATE),
        '5m':  fetch_klines_range(symbol, '5m',  START_DATE, END_DATE),
        '1h':  fetch_klines_range(symbol, '1h',  START_DATE, END_DATE),
        '1d':  fetch_klines_range(symbol, '1d',  START_DATE, END_DATE),
    }

# ═══════════════════ INDICADORES IDÉNTICOS A multi_bot.py ═══════════════════
def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    out[n-1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i-1] * (n-1) + tr[i]) / n
    return out

def rolling_vwap(df: pd.DataFrame, n: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vp = tp * df["volume"]
    return vp.rolling(n).sum() / df["volume"].rolling(n).sum()

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def compute_1h_vwap_simple(df1h: pd.DataFrame, period: int = 20) -> np.ndarray:
    h, l, c, v = df1h["high"].values, df1h["low"].values, df1h["close"].values, df1h["volume"].values
    tp = (h + l + c) / 3.0
    n = len(df1h)
    vp = tp * v
    csvp = np.cumsum(vp)
    csv = np.cumsum(v)
    vwap = np.full(n, np.nan)
    if period <= n:
        vwap[period-1:] = (csvp[period-1:] - np.concatenate([[0], csvp[:-period]])) / \
                          (csv[period-1:] - np.concatenate([[0], csv[:-period]]))
    return vwap

def get_1h_bias_simple(df1h: pd.DataFrame, vwap_1h: np.ndarray) -> int:
    if df1h.empty or len(vwap_1h) < 2:
        return 0
    c1h = float(df1h["close"].iloc[-2])
    vw1h = float(vwap_1h[-2])
    if not np.isfinite(vw1h) or vw1h <= 0:
        return 0
    return 1 if c1h > vw1h else (-1 if c1h < vw1h else 0)

def detect_regime(df1h: pd.DataFrame) -> Tuple[str, Dict]:
    df = df1h.copy().sort_values("open_time").reset_index(drop=True)
    df["day"] = df["open_time"].dt.floor("D")
    df["h4"]  = df["open_time"].dt.floor("4h")

    daily = df.groupby("day").agg(close_d=("close","last")).reset_index()
    daily["ema20"]      = ema(daily["close_d"], EMA_DAILY_N)
    daily["ema20_y"]    = daily["ema20"].shift(1)
    daily["ema20_y_prev"] = daily["ema20_y"].shift(1)
    daily["rising_y"]  = (daily["ema20_y"] > daily["ema20_y_prev"]).astype(bool)
    daily["falling_y"] = (daily["ema20_y"] < daily["ema20_y_prev"]).astype(bool)

    if len(daily) < 2:
        return "LATERAL", {"reason": "insufficient daily data"}
    last_day = daily["day"].iloc[-1]
    dr = daily[daily["day"] == last_day].iloc[0]

    ema20_y = dr["ema20_y"]
    rising  = bool(dr["rising_y"]) if pd.notna(dr["rising_y"]) else False
    falling = bool(dr["falling_y"]) if pd.notna(dr["falling_y"]) else False

    h4 = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    if len(h4) < 2:
        return "LATERAL", {"reason": "insufficient 4h bars"}
    close_4h = float(h4["close_4h"].iloc[-2])

    if pd.isna(ema20_y):
        raw_regime = "LATERAL"
    elif close_4h > ema20_y and rising:
        raw_regime = "ALCISTA"
    elif close_4h < ema20_y and falling:
        raw_regime = "BAJISTA"
    else:
        raw_regime = "LATERAL"

    # Hysteresis
    h4_full = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4_full["day"] = h4_full["h4"].dt.floor("D")
    h4_full = h4_full.merge(daily[["day","ema20_y","rising_y","falling_y"]], on="day", how="left")
    h4_full["raw"] = "LATERAL"
    up_mask = (h4_full["close_4h"] > h4_full["ema20_y"]) & h4_full["rising_y"]
    dn_mask = (h4_full["close_4h"] < h4_full["ema20_y"]) & h4_full["falling_y"]
    h4_full.loc[up_mask, "raw"] = "ALCISTA"
    h4_full.loc[dn_mask, "raw"] = "BAJISTA"
    h4_full.loc[h4_full["ema20_y"].isna(), "raw"] = "LATERAL"

    raw_series = h4_full["raw"].tolist()
    final = []
    cur = "LATERAL"; pending=None; pc=0
    for v in raw_series:
        if v == cur:
            pending=None; pc=0; final.append(cur)
        else:
            if v == pending: pc += 1
            else: pending=v; pc=1
            if pc >= REGIME_HYSTERESIS:
                cur = pending; pending=None; pc=0
            final.append(cur)
    regime = final[-1] if final else raw_regime

    return regime, {"ema20_daily": float(ema20_y) if pd.notna(ema20_y) else None,
                    "close_4h": close_4h, "rising": rising, "falling": falling,
                    "raw_regime": raw_regime}

# ═══════════════════ SEÑAL VWAP BREAKOUT IDÉNTICA ═══════════════════
def vwap_breakout_signal(df15: pd.DataFrame, vwap_n: int, dev_thr: float,
                         sl_mult: float, tp_mult: float,
                         regime: str = None) -> Optional[Dict]:
    vwap = rolling_vwap(df15, vwap_n)
    dev = (df15["close"] - vwap) / vwap * 100.0
    dev_prev = dev.shift(1)

    if len(dev) < 2:
        return None
    last_dev = float(dev.iloc[-1])
    last_dev_prev = float(dev_prev.iloc[-1]) if pd.notna(dev_prev.iloc[-1]) else None
    if last_dev_prev is None:
        return None

    a15_arr = atr(df15, ATR15M_N)
    a15 = float(a15_arr[-1])
    if not math.isfinite(a15) or a15 <= 0:
        return None

    close = float(df15["close"].iloc[-1])
    thr = dev_thr

    sig = {"atr15": a15, "dev": last_dev}
    if regime:
        sig["regime"] = regime

    # LONG: dev_prev >= -thr AND dev < -thr
    if last_dev_prev >= -thr and last_dev < -thr:
        sig.update({"dir": 1, "entry": close,
                    "stop": close - sl_mult * a15,
                    "tp":   close + tp_mult * a15})
        return sig

    # SHORT: dev_prev <= +thr AND dev > +thr
    if last_dev_prev <= thr and last_dev > thr:
        sig.update({"dir": -1, "entry": close,
                    "stop": close + sl_mult * a15,
                    "tp":   close - tp_mult * a15})
        return sig

    return None

# ═══════════════════ SIZING IDÉNTICO A multi_bot.py ═══════════════════
def compute_size(equity: float, entry_raw: float, stop_raw: float,
                 risk_pct: float, atr15: float = None,
                 close_price: float = None, direction: int = 1,
                 vwap_1h: np.ndarray = None, df1h: pd.DataFrame = None) -> float:
    size_mult = 1.0

    if atr15 is not None and close_price is not None and close_price > 0:
        atr_pct = atr15 / close_price * 100
        if atr_pct > 0.5:
            size_mult *= 1.3
        elif atr_pct < 0.15:
            size_mult *= 0.75

    if vwap_1h is not None and df1h is not None:
        h1_bias = get_1h_bias_simple(df1h, vwap_1h)
        if h1_bias == direction:
            size_mult *= 1.5
        elif h1_bias == -direction:
            size_mult *= 0.67

    adjusted_rp = risk_pct * size_mult
    adjusted_rp = min(adjusted_rp, 0.035)

    risk = adjusted_rp * equity
    if risk <= 0:
        risk = 1.0
    price_range = abs(entry_raw - stop_raw)
    if price_range <= 0:
        return 0.0
    return risk / price_range

def round_size(size: float, min_order_size: float) -> float:
    if size <= 0:
        return 0.0
    step = min_order_size
    if step <= 0:
        return size
    decimals = max(0, -int(math.floor(math.log10(step))))
    rounded = round(size, decimals)
    return rounded if rounded >= step else 0.0

# ═══════════════════ SLIPPAGE IDÉNTICO ═══════════════════
def apply_entry_slippage(entry_raw: float, direction: int, atr5: float) -> float:
    slip = SLIP_ATR_MULT * atr5 if atr5 and math.isfinite(atr5) else 0.0
    if direction == 1:
        return entry_raw * (1 + SPREAD) + slip
    else:
        return entry_raw * (1 - SPREAD) - slip

def apply_exit_slippage(exit_raw: float, direction: int, atr5: float) -> float:
    slip = SLIP_ATR_MULT * atr5 if atr5 and math.isfinite(atr5) else 0.0
    if direction == 1:
        return exit_raw * (1 - SPREAD) - slip
    else:
        return exit_raw * (1 + SPREAD) + slip

# ═══════════════════ SIMULACIÓN DE SALIDA IDÉNTICA ═══════════════════
def simulate_exit(direction: int, entry_fill: float, stop_raw: float, tp_raw: float,
                  tp1: float, size: float, candles_5m: List[Dict], start_idx: int,
                  atr5: float) -> Tuple[float, List, str]:
    """
    Simula salida con TP1 parcial (60%), breakeven, trailing stop (2x ATR), TP final.
    Retorna: (pnl_neto, events, exit_type)
    """
    remaining = size
    sl_active = stop_raw
    trail_high = entry_fill
    trail_low = entry_fill
    trailing_stop = None
    tp1_hit = False
    events = []

    for j in range(start_idx + 1, len(candles_5m)):
        high = candles_5m[j]['high']
        low = candles_5m[j]['low']

        if direction == 1:  # LONG
            if high > trail_high:
                trail_high = high

            # Activar trailing stop cuando pos restante está 0.3% ITM
            if not tp1_hit and high >= entry_fill * (1 + TRAIL_ACTIVATE_PCT / 100):
                trail_dist = TRAIL_MULT * atr5
                trailing_stop = trail_high - trail_dist

            # TP1 hit (60%)
            if not tp1_hit and high >= tp1:
                close_size = size * TP1_FRACTION
                exit_fill = apply_exit_slippage(tp1, direction, atr5)
                gross = close_size * (exit_fill - entry_fill)
                comm = close_size * (entry_fill + exit_fill) * COMMISSION
                net = gross - comm
                events.append({"type": "tp1", "contracts": close_size, "price": exit_fill,
                               "pnl": gross, "comm": comm, "net": net})
                remaining -= close_size
                sl_active = entry_fill  # breakeven
                tp1_hit = True
                # Activar trailing inmediatamente
                trail_dist = TRAIL_MULT * atr5
                trailing_stop = trail_high - trail_dist
                continue

            # Trailing stop update
            if tp1_hit and trailing_stop is not None:
                trail_dist = TRAIL_MULT * atr5
                new_trail = trail_high - trail_dist
                if new_trail > trailing_stop:
                    trailing_stop = new_trail

                # Check trailing hit
                if low <= trailing_stop:
                    exit_fill = apply_exit_slippage(trailing_stop, direction, atr5)
                    gross = remaining * (exit_fill - entry_fill)
                    comm = remaining * (entry_fill + exit_fill) * COMMISSION
                    net = gross - comm
                    events.append({"type": "trail", "contracts": remaining, "price": exit_fill,
                                   "pnl": gross, "comm": comm, "net": net})
                    remaining = 0
                    break

                # Check full TP
                if high >= tp_raw:
                    exit_fill = apply_exit_slippage(tp_raw, direction, atr5)
                    gross = remaining * (exit_fill - entry_fill)
                    comm = remaining * (entry_fill + exit_fill) * COMMISSION
                    net = gross - comm
                    events.append({"type": "tp", "contracts": remaining, "price": exit_fill,
                                   "pnl": gross, "comm": comm, "net": net})
                    remaining = 0
                    break

            # SL check (before TP1)
            if not tp1_hit and low <= sl_active:
                exit_fill = apply_exit_slippage(sl_active, direction, atr5)
                gross = size * (exit_fill - entry_fill)
                comm = size * (entry_fill + exit_fill) * COMMISSION
                net = gross - comm
                events.append({"type": "sl", "contracts": size, "price": exit_fill,
                               "pnl": gross, "comm": comm, "net": net})
                remaining = 0
                break

        else:  # SHORT
            if low < trail_low:
                trail_low = low

            if not tp1_hit and low <= entry_fill * (1 - TRAIL_ACTIVATE_PCT / 100):
                trail_dist = TRAIL_MULT * atr5
                trailing_stop = trail_low + trail_dist

            if not tp1_hit and low <= tp1:
                close_size = size * TP1_FRACTION
                exit_fill = apply_exit_slippage(tp1, direction, atr5)
                gross = close_size * (entry_fill - exit_fill)
                comm = close_size * (entry_fill + exit_fill) * COMMISSION
                net = gross - comm
                events.append({"type": "tp1", "contracts": close_size, "price": exit_fill,
                               "pnl": gross, "comm": comm, "net": net})
                remaining -= close_size
                sl_active = entry_fill
                tp1_hit = True
                trail_dist = TRAIL_MULT * atr5
                trailing_stop = trail_low + trail_dist
                continue

            if tp1_hit and trailing_stop is not None:
                trail_dist = TRAIL_MULT * atr5
                new_trail = trail_low + trail_dist
                if new_trail < trailing_stop:
                    trailing_stop = new_trail

                if high >= trailing_stop:
                    exit_fill = apply_exit_slippage(trailing_stop, direction, atr5)
                    gross = remaining * (entry_fill - exit_fill)
                    comm = remaining * (entry_fill + exit_fill) * COMMISSION
                    net = gross - comm
                    events.append({"type": "trail", "contracts": remaining, "price": exit_fill,
                                   "pnl": gross, "comm": comm, "net": net})
                    remaining = 0
                    break

                if low <= tp_raw:
                    exit_fill = apply_exit_slippage(tp_raw, direction, atr5)
                    gross = remaining * (entry_fill - exit_fill)
                    comm = remaining * (entry_fill + exit_fill) * COMMISSION
                    net = gross - comm
                    events.append({"type": "tp", "contracts": remaining, "price": exit_fill,
                                   "pnl": gross, "comm": comm, "net": net})
                    remaining = 0
                    break

            if not tp1_hit and high >= sl_active:
                exit_fill = apply_exit_slippage(sl_active, direction, atr5)
                gross = size * (entry_fill - exit_fill)
                comm = size * (entry_fill + exit_fill) * COMMISSION
                net = gross - comm
                events.append({"type": "sl", "contracts": size, "price": exit_fill,
                               "pnl": gross, "comm": comm, "net": net})
                remaining = 0
                break

    # Forced exit at end
    if remaining > 0:
        last_price = candles_5m[-1]['close']
        exit_fill = apply_exit_slippage(last_price, direction, atr5)
        if direction == 1:
            gross = remaining * (exit_fill - entry_fill)
        else:
            gross = remaining * (entry_fill - exit_fill)
        comm = remaining * (entry_fill + exit_fill) * COMMISSION
        net = gross - comm
        events.append({"type": "forced", "contracts": remaining, "price": exit_fill,
                       "pnl": gross, "comm": comm, "net": net})

    # Calcular PnL neto total
    entry_comm = entry_fill * size * COMMISSION
    total_net = -entry_comm + sum(e["net"] for e in events)

    # Determinar exit_type
    exit_types = [e["type"] for e in events]
    if "tp" in exit_types:
        exit_type = "tp_reached"
    elif "trail" in exit_types:
        exit_type = "trail_hit"
    elif "tp1" in exit_types:
        exit_type = "tp1_then_sl_or_trail"
    else:
        exit_type = "sl_hit"

    return total_net, events, exit_type

# ═══════════════════ BOT BACKTEST CLASS ═══════════════════
class BotBacktest:
    def __init__(self, name: str, config: Dict, data: Dict):
        self.name = name
        self.config = config
        self.data = data
        self.equity = INITIAL_EQUITY_PER_BOT
        self.peak_equity = INITIAL_EQUITY_PER_BOT
        self.risk_multiplier = 1.0
        self.position = None
        self.loss_streak = 0
        self.cooldown_until = None
        self.trades = []
        self.last_regime = "single"

    def get_risk_multiplier(self) -> float:
        dd_pct = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0
        for threshold, mult in DD_THRESHOLDS:
            if dd_pct < threshold:
                return mult
        return 0.0

    def check_cooldown(self, current_time: datetime) -> bool:
        if self.cooldown_until and current_time < self.cooldown_until:
            return True
        if self.cooldown_until and current_time >= self.cooldown_until:
            self.cooldown_until = None
            self.loss_streak = 0
        return False

    def run(self) -> List[Dict]:
        df15_all = pd.DataFrame(self.data['15m'])
        df5_all = pd.DataFrame(self.data['5m'])
        df1h_all = pd.DataFrame(self.data['1h'])
        df1d_all = pd.DataFrame(self.data['1d'])

        if len(df15_all) < 50 or len(df5_all) < 50 or len(df1h_all) < 50:
            print(f"  {self.name}: datos insuficientes")
            return []

        df15_all['open_time'] = pd.to_datetime(df15_all['timestamp'], unit='ms', utc=True)
        df5_all['open_time'] = pd.to_datetime(df5_all['timestamp'], unit='ms', utc=True)
        df1h_all['open_time'] = pd.to_datetime(df1h_all['timestamp'], unit='ms', utc=True)
        df1d_all['open_time'] = pd.to_datetime(df1d_all['timestamp'], unit='ms', utc=True)

        # Iterar sobre velas de 15m cerradas (empezar desde índice 50 para tener historial)
        for i in range(50, len(df15_all) - 1):
            current_15m_time = df15_all['open_time'].iloc[i]
            current_5m_time = df15_all['open_time'].iloc[i]  # misma vela base

            # Verificar cooldown
            if self.check_cooldown(current_15m_time):
                continue

            # Actualizar risk_multiplier por drawdown
            self.risk_multiplier = self.get_risk_multiplier()
            if self.risk_multiplier == 0.0 and self.position is None:
                break

            # Preparar datos hasta este momento (sin lookahead)
            df15 = df15_all.iloc[:i+1].copy().reset_index(drop=True)
            df5 = df5_all[df5_all['timestamp'] <= df15_all['timestamp'].iloc[i]].copy().reset_index(drop=True)
            df1h = df1h_all[df1h_all['timestamp'] <= df15_all['timestamp'].iloc[i]].copy().reset_index(drop=True)
            df1d = df1d_all[df1d_all['timestamp'] <= df15_all['timestamp'].iloc[i]].copy().reset_index(drop=True)

            if len(df5) < ATR5M_N + 5 or len(df15) < ATR15M_N + 5:
                continue

            # Calcular ATR 5m actual para slippage
            atr5_arr = atr(df5, ATR5M_N)
            atr5 = float(atr5_arr[-1]) if not np.isnan(atr5_arr[-1]) else 0.0

            # Precompute 1h VWAP para sizing multi-TF
            vwap_1h = compute_1h_vwap_simple(df1h) if len(df1h) > 20 else None

            # 1) GESTIONAR POSICIÓN ABIERTA
            if self.position is not None:
                # Buscar vela 5m actual
                current_5m_idx = len(df5) - 1
                bar_high = df5['high'].iloc[current_5m_idx]
                bar_low = df5['low'].iloc[current_5m_idx]

                pnl, events, exit_type = simulate_exit(
                    self.position['dir'], self.position['entry_fill'],
                    self.position['stop_raw'], self.position['tp_raw'],
                    self.position['tp1'], self.position['size'],
                    df5.to_dict('records'), current_5m_idx, atr5
                )

                if pnl != 0 or events:  # posición cerrada
                    self.equity += pnl
                    if self.equity > self.peak_equity:
                        self.peak_equity = self.equity

                    trade_record = {
                        "bot": self.name,
                        "symbol": self.config['symbol'],
                        "entry_time": self.position['entry_time'].isoformat(),
                        "exit_time": current_15m_time.isoformat(),
                        "direction": "LONG" if self.position['dir'] == 1 else "SHORT",
                        "entry": round(self.position['entry_fill'], 6),
                        "exit": round(events[-1]['price'], 6) if events else None,
                        "stop": round(self.position['stop_raw'], 6),
                        "tp": round(self.position['tp_raw'], 6),
                        "tp1": round(self.position['tp1'], 6),
                        "size": round(self.position['size'], 6),
                        "pnl_net": round(pnl, 4),
                        "equity_after": round(self.equity, 2),
                        "hold_minutes": round((current_15m_time - self.position['entry_time']).total_seconds() / 60, 1),
                        "exit_type": exit_type,
                        "regime": self.position.get('regime', 'single'),
                        "dev": self.position.get('dev', 0),
                    }
                    self.trades.append(trade_record)

                    # Actualizar loss streak y cooldown
                    if pnl <= 0:
                        self.loss_streak += 1
                        if self.loss_streak >= LOSS_STREAK_TRIGGER:
                            self.cooldown_until = current_15m_time + timedelta(minutes=PAUSE_MINUTES)
                    else:
                        self.loss_streak = 0

                    self.position = None
                continue

            # 2) BUSCAR SEÑAL NUEVA
            # Determinar parámetros según régimen (solo BTC)
            if self.config.get('multimodal', False):
                regime, _ = detect_regime(df1h)
                self.last_regime = regime
                params = self.config['regime_params'][regime]
            else:
                params = self.config['params']
                regime = "single"

            sig = vwap_breakout_signal(
                df15, vwap_n=params['vwap_n'], dev_thr=params['dev_thr'],
                sl_mult=params['sl_mult'], tp_mult=params['tp_mult'],
                regime=regime
            )

            if sig is None:
                continue

            # Calcular tamaño
            equity = self.equity
            adjusted_risk = self.config['risk_pct'] * self.risk_multiplier
            size = compute_size(
                equity, sig["entry"], sig["stop"], adjusted_risk,
                atr15=sig["atr15"], close_price=sig["entry"],
                direction=sig["dir"], vwap_1h=vwap_1h, df1h=df1h
            )

            size = round_size(size, self.config['min_order_size'])
            if size <= 0:
                continue

            # Aplicar slippage en entrada
            entry_fill = apply_entry_slippage(sig["entry"], sig["dir"], atr5)

            # TP1 parcial (60% del rango TP)
            if sig["dir"] == 1:
                tp1 = sig["entry"] + (sig["tp"] - sig["entry"]) * TP1_FRACTION
            else:
                tp1 = sig["entry"] - (sig["entry"] - sig["tp"]) * TP1_FRACTION

            # Abrir posición
            self.position = {
                "dir": sig["dir"],
                "size": size,
                "entry_fill": entry_fill,
                "stop_raw": sig["stop"],
                "tp_raw": sig["tp"],
                "tp1": tp1,
                "entry_time": current_15m_time,
                "regime": regime,
                "dev": sig.get("dev", 0),
            }

        # Cerrar posición forzada al final si queda abierta
        if self.position is not None:
            df5 = df5_all[df5_all['timestamp'] <= df15_all['timestamp'].iloc[-1]].copy().reset_index(drop=True)
            atr5_arr = atr(df5, ATR5M_N)
            atr5 = float(atr5_arr[-1]) if not np.isnan(atr5_arr[-1]) else 0.0
            pnl, events, exit_type = simulate_exit(
                self.position['dir'], self.position['entry_fill'],
                self.position['stop_raw'], self.position['tp_raw'],
                self.position['tp1'], self.position['size'],
                df5.to_dict('records'), len(df5) - 1, atr5
            )
            self.equity += pnl
            trade_record = {
                "bot": self.name,
                "symbol": self.config['symbol'],
                "entry_time": self.position['entry_time'].isoformat(),
                "exit_time": df15_all['open_time'].iloc[-1].isoformat(),
                "direction": "LONG" if self.position['dir'] == 1 else "SHORT",
                "entry": round(self.position['entry_fill'], 6),
                "exit": round(events[-1]['price'], 6) if events else None,
                "stop": round(self.position['stop_raw'], 6),
                "tp": round(self.position['tp_raw'], 6),
                "tp1": round(self.position['tp1'], 6),
                "size": round(self.position['size'], 6),
                "pnl_net": round(pnl, 4),
                "equity_after": round(self.equity, 2),
                "hold_minutes": round((df15_all['open_time'].iloc[-1] - self.position['entry_time']).total_seconds() / 60, 1),
                "exit_type": exit_type + "_forced",
                "regime": self.position.get('regime', 'single'),
                "dev": self.position.get('dev', 0),
            }
            self.trades.append(trade_record)

        return self.trades

# ═══════════════════ EJECUCIÓN PRINCIPAL ═══════════════════
def main():
    print("=" * 70)
    print("BACKTEST VWAP BREAKOUT — Simulación EXACTA de multi_bot.py")
    print(f"Período: {START_DATE.date()} → {END_DATE.date()}")
    print(f"Capital: ${TOTAL_INITIAL_CAPITAL} (${INITIAL_EQUITY_PER_BOT}/bot × 5)")
    print("=" * 70)

    all_trades = []
    bot_equities = {}

    for bot_name, config in BOT_CONFIGS.items():
        print(f"\n--- {bot_name} ({config['symbol']}) ---")
        data = fetch_all_data(config['symbol'])
        bot = BotBacktest(bot_name, config, data)
        trades = bot.run()
        all_trades.extend(trades)
        bot_equities[bot_name] = bot.equity
        print(f"  Trades: {len(trades)} | Equity final: ${bot.equity:.2f} | PnL: ${bot.equity - INITIAL_EQUITY_PER_BOT:.2f}")

    # Resultados consolidados
    if not all_trades:
        print("\n❌ No se generaron trades")
        return

    df = pd.DataFrame(all_trades)
    df['entry_time'] = pd.to_datetime(df['entry_time'])
    df = df.sort_values('entry_time').reset_index(drop=True)

    # Equity curve portfolio
    df['cum_pnl'] = df['pnl_net'].cumsum()
    portfolio_equity = TOTAL_INITIAL_CAPITAL + df['cum_pnl']
    peak = portfolio_equity.cummax()
    dd = (portfolio_equity - peak) / peak * 100
    max_dd = dd.min()
    max_dd_abs = (peak - portfolio_equity).max()

    total_pnl = df['pnl_net'].sum()
    win_rate = (df['pnl_net'] > 0).mean() * 100
    n_trades = len(df)
    profit_factor = df[df['pnl_net'] > 0]['pnl_net'].sum() / abs(df[df['pnl_net'] <= 0]['pnl_net'].sum()) if (df['pnl_net'] <= 0).any() else float('inf')

    # Sharpe (aprox diario)
    daily_returns = portfolio_equity.pct_change().dropna()
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    # Calmar
    days = (df['entry_time'].iloc[-1] - df['entry_time'].iloc[0]).days
    months = max(days / 30.44, 1)
    monthly_return = ((portfolio_equity.iloc[-1] / TOTAL_INITIAL_CAPITAL) ** (1 / months) - 1) * 100
    annual_return = monthly_return * 12
    calmar = annual_return / abs(max_dd) if max_dd != 0 else float('inf')

    print("\n" + "=" * 70)
    print("RESULTADOS PORTFOLIO (5 BOTS)")
    print("=" * 70)
    print(f"Trades totales:           {n_trades}")
    print(f"Win Rate:                 {win_rate:.1f}%")
    print(f"PnL neto total:           ${total_pnl:.2f}")
    print(f"Capital final:            ${portfolio_equity.iloc[-1]:.2f}")
    print(f"Retorno total:            {(portfolio_equity.iloc[-1]/TOTAL_INITIAL_CAPITAL - 1)*100:.2f}%")
    print(f"Retorno mensual:          {monthly_return:.2f}%")
    print(f"Max Drawdown:             {max_dd:.2f}% (${max_dd_abs:.2f})")
    print(f"Profit Factor:            {profit_factor:.2f}")
    print(f"Sharpe Ratio:             {sharpe:.2f}")
    print(f"Calmar Ratio:             {calmar:.2f}")
    print("-" * 70)
    print("Por bot:")
    for bot_name in BOT_CONFIGS.keys():
        bot_df = df[df['bot'] == bot_name]
        if len(bot_df) > 0:
            bot_wr = (bot_df['pnl_net'] > 0).mean() * 100
            bot_pnl = bot_df['pnl_net'].sum()
            print(f"  {bot_name}: {len(bot_df)} trades | WR {bot_wr:.1f}% | PnL ${bot_pnl:.2f} | Eq ${bot_equities[bot_name]:.2f}")
        else:
            print(f"  {bot_name}: 0 trades | Eq ${bot_equities[bot_name]:.2f}")

    # Guardar CSV
    output_csv = "backtest_vwap_breakout_AprJul2026.csv"
    df.to_csv(output_csv, index=False)
    print(f"\n📁 Guardado: {output_csv} ({len(df)} trades)")

    # Guardar resumen
    summary = {
        "period": f"{START_DATE.date()} to {END_DATE.date()}",
        "initial_capital": TOTAL_INITIAL_CAPITAL,
        "final_equity": float(portfolio_equity.iloc[-1]),
        "total_return_pct": float((portfolio_equity.iloc[-1]/TOTAL_INITIAL_CAPITAL - 1)*100),
        "monthly_return_pct": float(monthly_return),
        "total_trades": int(n_trades),
        "win_rate_pct": float(win_rate),
        "total_pnl": float(total_pnl),
        "max_drawdown_pct": float(max_dd),
        "max_drawdown_abs": float(max_dd_abs),
        "profit_factor": float(profit_factor),
        "sharpe_ratio": float(sharpe),
        "calmar_ratio": float(calmar),
        "per_bot": {bot: {"trades": int((df['bot']==bot).sum()),
                         "win_rate": float((df[df['bot']==bot]['pnl_net']>0).mean()*100) if (df['bot']==bot).any() else 0,
                         "pnl": float(df[df['bot']==bot]['pnl_net'].sum()) if (df['bot']==bot).any() else 0,
                         "final_equity": float(bot_equities[bot])}
                    for bot in BOT_CONFIGS.keys()}
    }
    with open("backtest_vwap_breakout_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print("📁 Guardado: backtest_vwap_breakout_summary.json")

    return df, summary

if __name__ == "__main__":
    main()
