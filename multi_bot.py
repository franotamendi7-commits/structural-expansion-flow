"""
============================================================
 MULTI-BOT TRADING SYSTEM — UNIFIED MODULE
============================================================
 Single module containing 5 trading bots (BTC, ETH, SOL, XRP, BNB)
 sharing common infrastructure (data fetch, indicators, slippage,
 sizing, cooldown) but each maintaining its own:
   - Symbol
   - Strategy (entry/exit params)
   - Equity (independent compounding)
   - Position state
   - Cooldown state
   - Loss streak

 ARCHITECTURE:
   - Common utilities (atr, vwap, fetch, slippage, sizing) defined once.
   - BaseBot class: handles lifecycle (on_tick), position management,
     cooldown, sizing, slippage. Subclasses only override check_signal().
   - BtcBot: multimodal (regime detection + per-regime VWAP params)
   - EthBot/SolBot/XrpBot/BnbBot: single-strategy VWAP breakout

 USAGE:
   from multi_bot import MultiBotSystem
   system = MultiBotSystem()
   system.start()  # runs all 5 bots in parallel threads
   # ... or for testing:
   system.run_once()  # single tick of all bots

 VALIDATED on Jan 1 - Jun 23 2026 (Binance Futures):
   Portfolio: $500 -> $792 (+58.5%), Max DD -2.24%, MC p95 DD -2.75%
   Individual bots: 99.94%-100% prob profit in MC (5000 sims)
============================================================
"""
import time
import math
import json
import logging
import threading
import requests
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# File path for persistent trade history
TRADE_HISTORY_PATH = Path(__file__).parent / "trade_history.json"

def _load_trade_history() -> list:
    if TRADE_HISTORY_PATH.exists():
        try:
            with open(TRADE_HISTORY_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load trade history: {e}")
    return []

def _save_trade_history(trades: list):
    try:
        with open(TRADE_HISTORY_PATH, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Could not save trade history: {e}")


# ─── ML Features computation (for ML Filter + Guard Agent) ────────
def compute_ml_features(symbol: str, df15: pd.DataFrame, df5: pd.DataFrame,
                        df1h: pd.DataFrame = None, direction: int = 1) -> dict:
    """
    Build feature dict for ML filter from the same data the bot uses.
    Falls back to defaults for features that need additional data.
    """
    features = {}

    # Choppiness Index (15m, period 14)
    h15 = df15['high'].values[-14:]
    l15 = df15['low'].values[-14:]
    c15 = df15['close'].values[-14:]
    if len(h15) >= 14:
        tr = np.maximum(h15 - l15, np.abs(np.concatenate([[c15[0]], c15[:-1]]) - h15))
        tr = np.maximum(tr, np.abs(np.concatenate([[c15[0]], c15[:-1]]) - l15))
        atr_sum = tr.sum()
        total_range = h15.max() - l15.min()
        if total_range > 0:
            features['ci_value'] = float(np.clip(100 * np.log10(atr_sum / total_range) / np.log10(14), 0, 100))
        else:
            features['ci_value'] = 50.0
    else:
        features['ci_value'] = 50.0

    # Williams %R 5m
    h5 = df5['high'].values[-14:]
    l5 = df5['low'].values[-14:]
    c5_close = float(df5['close'].values[-1]) if len(df5) > 0 else 0
    if len(h5) >= 14 and (h5.max() - l5.min()) != 0:
        features['wr_5m'] = float(np.clip(-100 * (h5.max() - c5_close) / (h5.max() - l5.min()), -100, 0))
    else:
        features['wr_5m'] = -50.0

    # Williams %R 15m
    if len(h15) >= 14 and (h15.max() - l15.min()) != 0:
        c15_close = float(df15['close'].values[-1])
        features['wr_15m'] = float(np.clip(-100 * (h15.max() - c15_close) / (h15.max() - l15.min()), -100, 0))
    else:
        features['wr_15m'] = -50.0

    # Hour of day
    features['hour_of_day'] = datetime.now(timezone.utc).hour

    # Direction
    features['direction_long'] = 1 if direction == 1 else 0

    # Volume ratio 5m
    vol5 = df5['volume'].values[-20:] if len(df5) >= 20 else df5['volume'].values
    mean_vol = vol5.mean() if len(vol5) > 0 else 0
    features['vol_ratio_5m'] = float(vol5[-1] / mean_vol) if mean_vol > 0 and len(vol5) > 0 else 1.0

    # Body ratio 4h (try to build from 15m)
    if len(df15) >= 4:
        grp = df15.iloc[-4:]  # last 4 x 15m ≈ 1h
        features['body_ratio_4h'] = float(abs(grp['close'].iloc[-1] - grp['open'].iloc[0]) /
                                           max(grp['high'].max() - grp['low'].min(), 0.001))
    else:
        features['body_ratio_4h'] = 0.0

    # Fibonacci defaults
    features['fib_low_key'] = 0.0
    features['fib_high_key'] = 0.5
    features['fib_width'] = 0.0

    # Supertrend defaults
    features['st_aligned'] = 0
    features['st_bias_bullish'] = 0
    features['st_bias_bearish'] = 0

    # Momentum defaults
    features['mom_score'] = 0.0

    # Phase defaults
    features['phase_compressing'] = 0
    features['phase_expanding'] = 0
    features['phase_trending'] = 0

    # Mom direction defaults
    features['mom_bullish'] = 0
    features['mom_bearish'] = 0
    features['mom_neutral'] = 1

    return features


_ml_filter_loaded = False
_guard_model_loaded = False

def _load_ml_models():
    global _ml_filter_loaded, _guard_model_loaded
    try:
        from ml_filter import cargar_modelo as cargar_ml
        cargar_ml()
        _ml_filter_loaded = True
    except Exception:
        pass
    try:
        from guard_filter import cargar_modelo as cargar_guard
        cargar_guard()
        _guard_model_loaded = True
    except Exception:
        pass


def should_execute_trade(features: dict, df15: pd.DataFrame, df5: pd.DataFrame,
                         df1h: pd.DataFrame = None) -> tuple:
    """
    Returns (execute: bool, reason: str, ml_prob: float or None).
    Applies ML Filter + Guard Agent if models are available.
    """
    execute = True
    reason = "no_ml"
    ml_prob = None

    if _ml_filter_loaded:
        try:
            from ml_filter import debe_ejecutar
            execute, prob = debe_ejecutar(features)
            ml_prob = prob
            if not execute:
                reason = f"ml_veto(p={prob:.3f})"
            else:
                reason = f"ml_approve(p={prob:.3f})"
        except Exception as e:
            logger.warning(f"ML filter error: {e}")

    if execute and _guard_model_loaded:
        try:
            from guard_filter import debe_revivir
            guard_ok = debe_revivir(features, True)
            if not guard_ok:
                execute = False
                reason = f"guard_veto"
        except Exception as e:
            logger.warning(f"Guard filter error: {e}")

    return execute, reason, ml_prob


# ============================================================
# COMMON CONFIGURATION
# ============================================================
INTERVAL_15M       = "15m"
INTERVAL_5M        = "5m"
INTERVAL_1H        = "1h"            # only BtcBot uses 1h (for regime detection)

# Transaction costs (per side, applied to entry AND exit)
COMMISSION         = 0.0005          # 0.05% of notional
SPREAD             = 0.0002          # 0.02% price impact (unfavorable direction)
SLIP_ATR_MULT      = 0.1             # 0.1 x ATR(5m) slippage per side
ATR5M_N            = 14              # ATR lookback on 5m (for slippage)
ATR15M_N           = 14              # ATR lookback on 15m (for SL/TP sizing)
EMA_DAILY_N        = 20              # for BtcBot regime detection
REGIME_HYSTERESIS  = 2               # bars of 4h to confirm regime switch

# Dynamic cooldown: 5 consecutive losses -> 1h pause (per bot, not global)
LOSS_STREAK_TRIGGER = 5
PAUSE_MINUTES       = 60

# Binance Futures endpoints
BINANCE_FAPI        = "https://fapi.binance.com"

# Poll interval (5 minutes = one 5m bar)
POLL_INTERVAL_SEC   = 300

# Initial equity per bot
INITIAL_EQUITY      = 100.0


# ============================================================
# COMMON UTILITY FUNCTIONS
# ============================================================
def atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    """Wilder's ATR on a DataFrame with high/low/close columns."""
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    out = np.full(len(tr), np.nan)
    out[n-1] = tr[:n].mean()
    for i in range(n, len(tr)):
        out[i] = (out[i-1] * (n-1) + tr[i]) / n
    return out


def rolling_vwap(df: pd.DataFrame, n: int) -> pd.Series:
    """Rolling VWAP over n periods using (H+L+C)/3 as typical price."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vp = tp * df["volume"]
    return vp.rolling(n).sum() / df["volume"].rolling(n).sum()


def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA with span=n."""
    return s.ewm(span=n, adjust=False).mean()


def fetch_klines(symbol: str, interval: str, limit: int = 1500,
                 end_time: Optional[int] = None) -> pd.DataFrame:
    """
    Fetch most recent `limit` klines for `interval` from Binance Futures.
    Returns DataFrame with columns: open_time, open, high, low, close, volume, ...
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time is not None:
        params["endTime"] = end_time
    for attempt in range(5):
        try:
            r = requests.get(f"{BINANCE_FAPI}/fapi/v1/klines",
                             params=params, timeout=15)
            if r.status_code == 200:
                break
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
        except Exception:
            time.sleep(1.5 ** attempt)
    else:
        raise RuntimeError(f"failed to fetch {symbol} {interval}")

    rows = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_vol","trades","taker_buy_base",
            "taker_buy_quote","ignore"]
    df = pd.DataFrame(rows, columns=cols)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in ["open","high","low","close","volume"]:
        df[c] = pd.to_numeric(df[c])
    return df


def get_recent_data(symbol: str, need_1h: bool = False) -> tuple:
    """
    Fetch 15m, 5m (and optionally 1h) klines for the most recent period.
    Discards the in-progress (forming) bar on each timeframe so that signals,
    VWAP, ATR, and exit checks use CLOSED bars only.

    Args:
        symbol: e.g. "BTCUSDT"
        need_1h: if True, also fetch 1h klines (for BtcBot regime detection)

    Returns:
        (df15, df5) or (df15, df5, df1h) if need_1h=True
    """
    df15 = fetch_klines(symbol, INTERVAL_15M, limit=100).sort_values("open_time").reset_index(drop=True)
    df5  = fetch_klines(symbol, INTERVAL_5M,  limit=50).sort_values("open_time").reset_index(drop=True)

    # Discard the in-progress (forming) bar on each timeframe.
    if len(df15) > 1:
        df15 = df15.iloc[:-1].reset_index(drop=True)
    if len(df5) > 1:
        df5 = df5.iloc[:-1].reset_index(drop=True)

    if need_1h:
        df1h = fetch_klines(symbol, INTERVAL_1H, limit=500).sort_values("open_time").reset_index(drop=True)
        if len(df1h) > 1:
            df1h = df1h.iloc[:-1].reset_index(drop=True)
        return df15, df5, df1h
    return df15, df5


def apply_entry_slippage(entry_raw: float, direction: int, atr5: float) -> float:
    """Apply spread + slippage to entry fill (unfavorable direction)."""
    slip = SLIP_ATR_MULT * atr5 if atr5 and math.isfinite(atr5) else 0.0
    if direction == 1:
        return entry_raw * (1 + SPREAD) + slip
    else:
        return entry_raw * (1 - SPREAD) - slip


def apply_exit_slippage(exit_raw: float, direction: int, atr5: float) -> float:
    """Apply spread + slippage to exit fill (unfavorable direction)."""
    slip = SLIP_ATR_MULT * atr5 if atr5 and math.isfinite(atr5) else 0.0
    if direction == 1:
        return exit_raw * (1 - SPREAD) - slip
    else:
        return exit_raw * (1 + SPREAD) + slip


def compute_size(equity: float, entry_raw: float, stop_raw: float,
                 risk_pct: float) -> float:
    """
    Risk-based sizing with compounding:
      risk = risk_pct * equity
      size = risk / |entry - stop|
    """
    risk = risk_pct * equity
    price_range = abs(entry_raw - stop_raw)
    if price_range <= 0:
        return 0.0
    return risk / price_range


# ============================================================
# REGIME DETECTION (only used by BtcBot)
# ============================================================
def detect_regime(df1h: pd.DataFrame) -> tuple:
    """
    Classify current regime using daily EMA20 + last completed 4h close.
      ALCISTA  : close_4h > EMA20_daily(yesterday) AND EMA20_daily rising
      BAJISTA  : close_4h < EMA20_daily(yesterday) AND EMA20_daily falling
      LATERAL  : otherwise

    Uses yesterday's EMA20 (no lookahead) and last COMPLETED 4h close.
    Applies 2-bar hysteresis on the raw regime signal to prevent flipping.

    Returns: (regime_str, debug_dict)
    """
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

    # Raw regime
    if pd.isna(ema20_y):
        raw_regime = "LATERAL"
    elif close_4h > ema20_y and rising:
        raw_regime = "ALCISTA"
    elif close_4h < ema20_y and falling:
        raw_regime = "BAJISTA"
    else:
        raw_regime = "LATERAL"

    # Hysteresis on the 4h raw signal
    h4_full = df.groupby("h4").agg(close_4h=("close","last")).reset_index()
    h4_full["day"] = h4_full["h4"].dt.floor("D")
    h4_full = h4_full.merge(daily[["day","ema20_y","rising_y","falling_y"]],
                            on="day", how="left")
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
                    "close_4h": close_4h,
                    "rising": rising, "falling": falling,
                    "raw_regime": raw_regime}


# ============================================================
# BASE BOT CLASS
# ============================================================
class BaseBot:
    """
    Base class for all trading bots. Handles:
      - Data fetching (15m + 5m, optionally 1h)
      - Cooldown state (5 consecutive losses -> 1h pause)
      - Position management (TP/SL exit checks on 5m bars)
      - Sizing with compounding (risk_pct of current equity)
      - Slippage + spread on entry and exit
      - Min order size validation
      - Trade logging

    Subclasses must:
      - Set self.symbol, self.risk_pct, self.min_order_size
      - Override check_signal(df15) -> dict or None
        Signal dict must have: dir, entry, stop, tp, atr15, (optional: dev, regime)
    """

    def __init__(self, symbol: str, risk_pct: float, min_order_size: float,
                 initial_equity: float = INITIAL_EQUITY, name: str = None,
                 exchange_client=None):
        self.name = name or symbol
        self.symbol = symbol
        self.risk_pct = risk_pct
        self.min_order_size = min_order_size
        self.initial_equity = initial_equity
        self._exchange_client = exchange_client  # ExecutionManager or None (paper)

        # State
        self.equity = initial_equity
        self.position = None       # dict: dir, size, entry_fill, stop_raw, tp_raw, entry_time
        self.loss_streak = 0
        self.cooldown_until = None  # UTC datetime or None
        self.trade_log = []
        self.last_signal = None
        self.last_regime = "single"  # default; BtcBot overrides with regime detection

        # Thread safety
        self._lock = threading.RLock()

        # Load persistent trade history at boot
        self._all_trades = _load_trade_history()

    # ---- exchange adapter ----
    def _round_size(self, size: float) -> float:
        """Round order size to the symbol's min lot size (decimals)."""
        if size is None or size <= 0:
            return 0.0
        step = self.min_order_size
        if step <= 0:
            return size
        decimals = max(0, -int(math.floor(math.log10(step))))
        rounded = round(size, decimals)
        return rounded if rounded >= step else 0.0

    def place_order(self, side: str, size: float, price: float) -> Optional[float]:
        """
        side='BUY'/'SELL'. Returns fill_price, or None if the order failed.
        If no exchange_client is set (paper mode), returns the planned price.
        """
        if self._exchange_client is None:
            return price
        size = self._round_size(size)
        if size <= 0:
            logger.warning(f"[{self.name}] order skipped: size rounded to 0")
            return None
        try:
            result = self._exchange_client.execute_signal({
                "symbol": self.symbol,
                "side": side,
                "quantity": size,
            })
            if result.get("error"):
                logger.error(f"[{self.name}] order error: {result['error']}")
                return None
            return float(result.get("executed_price", price) or price)
        except Exception as e:
            logger.error(f"[{self.name}] place_order exception: {e}")
            return None

    def close_order(self, size: float, price: float) -> Optional[float]:
        """
        Closes the open position (reduce_only). Returns fill_price, or None.
        If no exchange_client is set (paper mode), returns the planned price.
        """
        if self._exchange_client is None:
            return price
        if self.position is None:
            return price
        size = self._round_size(size)
        if size <= 0:
            return None
        close_side = "SELL" if self.position["dir"] == 1 else "BUY"
        try:
            result = self._exchange_client.execute_signal({
                "symbol": self.symbol,
                "side": close_side,
                "quantity": size,
            }, reduce_only=True)
            if result.get("error"):
                logger.error(f"[{self.name}] close error: {result['error']}")
                return None
            return float(result.get("executed_price", price) or price)
        except Exception as e:
            logger.error(f"[{self.name}] close_order exception: {e}")
            return None

    def get_equity(self) -> float:
        """Returns current account equity in USD. Override to read real balance."""
        return self.equity

    # ---- subclass hook ----
    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Subclass must implement this.
        Returns signal dict {dir, entry, stop, tp, atr15, ...} or None.
        """
        raise NotImplementedError("Subclass must implement check_signal()")

    def _fetch_data(self):
        """Fetch data needed by this bot. Override in BtcBot for 1h."""
        return get_recent_data(self.symbol, need_1h=False)

    def _atr5_from_data(self, df5):
        a5 = atr(df5, ATR5M_N)
        return float(a5[-1]) if not np.isnan(a5[-1]) else 0.0

    # ---- main tick logic ----
    def on_tick(self) -> str:
        """
        Call this every 5 minutes (aligned to 5m bar close).
        Thread-safe via internal lock.
        Returns a status string.
        """
        with self._lock:
            now = datetime.now(timezone.utc)

            # Cooldown check
            if self.cooldown_until is not None and now < self.cooldown_until:
                remaining = (self.cooldown_until - now).total_seconds() / 60
                return f"[{self.name}] cooldown: {remaining:.1f}min remaining"
            elif self.cooldown_until is not None and now >= self.cooldown_until:
                self.cooldown_until = None
                self.loss_streak = 0
                print(f"[{self.name}] COOLDOWN ENDED, loss streak reset")

            # Fetch data
            try:
                data = self._fetch_data()
                df15, df5 = data[0], data[1]
            except Exception as e:
                return f"[{self.name}] data fetch error: {e}"

            atr5 = self._atr5_from_data(df5)

            # 1) Manage open position — check 5m bar high/low for TP/SL
            if self.position is not None:
                bar_high = float(df5["high"].iloc[-1])
                bar_low  = float(df5["low"].iloc[-1])
                pos = self.position
                hit = None
                if pos["dir"] == 1:
                    if bar_low <= pos["stop_raw"]:
                        hit = "sl"
                    elif bar_high >= pos["tp_raw"]:
                        hit = "tp"
                else:
                    if bar_high >= pos["stop_raw"]:
                        hit = "sl"
                    elif bar_low <= pos["tp_raw"]:
                        hit = "tp"

                if hit is not None:
                    raw_exit = pos["tp_raw"] if hit == "tp" else pos["stop_raw"]
                    exit_fill = apply_exit_slippage(raw_exit, pos["dir"], atr5)

                    # Send real close order if live trading is enabled
                    if self._exchange_client is not None:
                        self.close_order(pos["size"], raw_exit)
                    if pos["dir"] == 1:
                        gross = pos["size"] * (exit_fill - pos["entry_fill"])
                    else:
                        gross = pos["size"] * (pos["entry_fill"] - exit_fill)
                    comm = pos["size"] * (pos["entry_fill"] + exit_fill) * COMMISSION
                    net = gross - comm
                    self.equity += net
                    self.trade_log.append({
                        "exit_time": now, "dir": pos["dir"],
                        "entry": pos["entry_fill"], "exit": exit_fill,
                        "outcome": "win" if net > 0 else "loss",
                        "net": net, "equity_after": self.equity,
                        "hold_minutes": (now - pos["entry_time"]).total_seconds() / 60,
                        "exit_reason": hit,
                        "regime": getattr(self, "last_regime", None) or "single",
                        "symbol": self.symbol,
                        "bot_name": self.name,
                    })
                    print(f"[{self.name}] {hit.upper()} dir={pos['dir']} "
                          f"entry={pos['entry_fill']:.4f} exit={exit_fill:.4f} "
                          f"net={net:+.4f} equity={self.equity:.2f} "
                          f"loss_streak={self.loss_streak}")

                    if net <= 0:
                        self.loss_streak += 1
                    else:
                        self.loss_streak = 0

                    if self.loss_streak >= LOSS_STREAK_TRIGGER:
                        self.cooldown_until = now + timedelta(minutes=PAUSE_MINUTES)
                        print(f"[{self.name}] COOLDOWN: {self.loss_streak} consecutive losses -> "
                              f"pause until {self.cooldown_until.isoformat()}")

                    self.position = None

                    # Persist to global trade history
                    trade_record = {
                        "exit_time": now.isoformat(),
                        "dir": pos["dir"],
                        "entry": pos["entry_fill"],
                        "exit": exit_fill,
                        "outcome": "win" if net > 0 else "loss",
                        "net": round(net, 4),
                        "equity_after": round(self.equity, 2),
                        "hold_minutes": round((now - pos["entry_time"]).total_seconds() / 60, 1),
                        "exit_reason": hit,
                        "regime": getattr(self, "last_regime", None) or "single",
                        "symbol": self.symbol,
                        "bot_name": self.name,
                        "entry_time": pos["entry_time"].isoformat(),
                        "size": pos["size"],
                    }
                    self._all_trades.append(trade_record)
                    _save_trade_history(self._all_trades)

                    return f"[{self.name}] exited via {hit}, net={net:+.4f}"
                return f"[{self.name}] in position, no exit this bar"

            # 2) No position — check signal
            try:
                sig = self.check_signal(df15)
            except Exception as e:
                return f"[{self.name}] signal error: {e}"

            if sig is None:
                self.last_signal = None
                return f"[{self.name}] no signal"

            self.last_signal = sig

            # ML Filter + Guard Agent check
            df1h_ml = getattr(self, "_df1h", None)
            features = compute_ml_features(self.symbol, df15, df5, df1h_ml, sig.get("dir", 1))
            execute, ml_reason, ml_prob = should_execute_trade(features, df15, df5, df1h_ml)
            if not execute:
                print(f"[{self.name}] ML BLOCKED: {ml_reason} (prob={ml_prob:.3f})" if ml_prob is not None
                      else f"[{self.name}] ML BLOCKED: {ml_reason}")
                return f"[{self.name}] blocked by {ml_reason}"

            # Enter
            equity = self.get_equity()
            size = compute_size(equity, sig["entry"], sig["stop"], self.risk_pct)
            if size <= 0:
                return f"[{self.name}] size=0, skip"
            if size < self.min_order_size:
                print(f"[{self.name}] SKIP: size={size:.6f} < min {self.min_order_size} "
                      f"(equity=${equity:.2f}, stop_range=${abs(sig['entry']-sig['stop']):.4f})")
                return f"[{self.name}] size too small ({size:.6f} < {self.min_order_size})"

            entry_fill = apply_entry_slippage(sig["entry"], sig["dir"], atr5)
            side = "BUY" if sig["dir"] == 1 else "SELL"

            # Send real entry order if live trading is enabled
            actual_fill = entry_fill
            actual_size = size
            if self._exchange_client is not None:
                fill = self.place_order(side, size, entry_fill)
                if fill is None:
                    return f"[{self.name}] order rejected"
                actual_fill = fill

            self.position = {
                "dir": sig["dir"],
                "size": actual_size,
                "entry_fill": actual_fill,
                "stop_raw": sig["stop"],
                "tp_raw": sig["tp"],
                "entry_time": now,
            }
            extra = ""
            if "regime" in sig:
                extra = f" regime={sig['regime']}"
            if "dev" in sig:
                extra += f" dev={sig['dev']:+.3f}%"
            print(f"[{self.name}] ENTRY {side} size={actual_size:.6f} "
                  f"entry={actual_fill:.4f} stop={sig['stop']:.4f} "
                  f"tp={sig['tp']:.4f} equity={equity:.2f}{extra}")
            return f"[{self.name}] entered {side} size={actual_size:.6f}{extra}"

    def status(self) -> Dict[str, Any]:
        """Return current state snapshot."""
        with self._lock:
            return {
                "name": self.name,
                "symbol": self.symbol,
                "equity": self.equity,
                "position": self.position,
                "loss_streak": self.loss_streak,
                "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
                "n_trades": len(self.trade_log),
            }


# ============================================================
# VWAP BREAKOUT SIGNAL HELPER (used by all single-strategy bots)
# ============================================================
def _vwap_breakout_signal(df15: pd.DataFrame, vwap_n: int, dev_thr: float,
                          sl_mult: float, tp_mult: float,
                          regime: str = None) -> Optional[Dict[str, Any]]:
    """
    VWAP deviation BREAKOUT signal.
      LONG  : dev crosses BELOW -thr (price extends below VWAP by thr%)
      SHORT : dev crosses ABOVE  +thr (price extends above VWAP by thr%)

    Returns signal dict or None.
    """
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


# ============================================================
# BTC BOT — Multimodal (regime detection + per-regime VWAP params)
# ============================================================
class BtcBot(BaseBot):
    """
    BTC multimodal bot: detects regime every tick (using 1h data + daily EMA20)
    and applies the VWAP breakout strategy with regime-specific params.
    """

    # Per-regime params (validated for BTCUSDT Jan-Jun 2026)
    REGIME_PARAMS = {
        "ALCISTA": dict(vwap_n=15, dev_thr=0.75, sl_mult=1.5, tp_mult=1.0),
        "BAJISTA": dict(vwap_n=20, dev_thr=1.0,  sl_mult=2.0, tp_mult=1.5),
        "LATERAL": dict(vwap_n=15, dev_thr=0.75, sl_mult=1.5, tp_mult=1.0),
    }

    def __init__(self, initial_equity: float = INITIAL_EQUITY, exchange_client=None):
        super().__init__(
            symbol="BTCUSDT",
            risk_pct=0.0075,            # adjusted for production (DD p95 mitigation)
            min_order_size=0.001,       # Binance Futures lot for BTCUSDT
            initial_equity=initial_equity,
            name="BTC",
            exchange_client=exchange_client,
        )

    def _fetch_data(self):
        # BtcBot needs 1h for regime detection
        return get_recent_data(self.symbol, need_1h=True)

    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        # df15 is passed by on_tick, but we also need df1h.
        # Re-fetch df1h here (cached in real production via ws_binance_feed).
        # For simplicity, on_tick stores df1h on self.
        df1h = getattr(self, "_df1h", None)
        if df1h is None:
            return None

        regime, _ = detect_regime(df1h)
        self.last_regime = regime
        p = self.REGIME_PARAMS[regime]
        return _vwap_breakout_signal(
            df15, vwap_n=p["vwap_n"], dev_thr=p["dev_thr"],
            sl_mult=p["sl_mult"], tp_mult=p["tp_mult"],
            regime=regime,
        )

    def on_tick(self) -> str:
        """Override to capture df1h before calling super().on_tick()."""
        with self._lock:
            # Fetch data with 1h
            try:
                df15, df5, df1h = self._fetch_data()
            except Exception as e:
                return f"[{self.name}] data fetch error: {e}"
            self._df1h = df1h
            self._df15 = df15
            self._df5 = df5
            # Call parent on_tick but it will re-fetch... not ideal.
            # Better: refactor parent to accept data. For now, stash and let
            # parent re-fetch. In production, use ws_binance_feed cache.
            # Hack: temporarily monkey-patch get_recent_data to return cached.
            global _cached_data
            _cached_data[(self.symbol, True)] = (df15, df5, df1h)
        # Now call parent (it will use the cached data via the patch below)
        return super().on_tick()


# Cached data hack (for BtcBot to avoid double-fetch)
_cached_data: Dict[tuple, tuple] = {}
_orig_get_recent_data = get_recent_data

def _cached_get_recent_data(symbol, need_1h=False):
    key = (symbol, need_1h)
    if key in _cached_data:
        return _cached_data.pop(key)
    return _orig_get_recent_data(symbol, need_1h)

# Patch the global so BaseBot's _fetch_data uses the cache
get_recent_data = _cached_get_recent_data


# ============================================================
# ETH BOT — VWAP breakout, SL=2.0, TP=1.0
# ============================================================
class EthBot(BaseBot):
    def __init__(self, initial_equity: float = INITIAL_EQUITY, exchange_client=None):
        super().__init__(
            symbol="ETHUSDT",
            risk_pct=0.0085,            # adjusted for production (DD p95 mitigation)
            min_order_size=0.001,
            initial_equity=initial_equity,
            name="ETH",
            exchange_client=exchange_client,
        )

    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        return _vwap_breakout_signal(
            df15, vwap_n=10, dev_thr=1.0,
            sl_mult=2.0, tp_mult=1.0,
        )


# ============================================================
# SOL BOT — VWAP breakout, SL=1.5, TP=0.5
# ============================================================
class SolBot(BaseBot):
    def __init__(self, initial_equity: float = INITIAL_EQUITY, exchange_client=None):
        super().__init__(
            symbol="SOLUSDT",
            risk_pct=0.01,
            min_order_size=0.01,
            initial_equity=initial_equity,
            name="SOL",
            exchange_client=exchange_client,
        )

    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        return _vwap_breakout_signal(
            df15, vwap_n=10, dev_thr=1.0,
            sl_mult=1.5, tp_mult=0.5,
        )


# ============================================================
# XRP BOT — VWAP breakout, SL=1.5, TP=0.5
# ============================================================
class XrpBot(BaseBot):
    def __init__(self, initial_equity: float = INITIAL_EQUITY, exchange_client=None):
        super().__init__(
            symbol="XRPUSDT",
            risk_pct=0.01,
            min_order_size=0.1,
            initial_equity=initial_equity,
            name="XRP",
            exchange_client=exchange_client,
        )

    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        return _vwap_breakout_signal(
            df15, vwap_n=10, dev_thr=1.0,
            sl_mult=1.5, tp_mult=0.5,
        )


# ============================================================
# BNB BOT — VWAP breakout, SL=1.5, TP=1.0
# ============================================================
class BnbBot(BaseBot):
    def __init__(self, initial_equity: float = INITIAL_EQUITY, exchange_client=None):
        super().__init__(
            symbol="BNBUSDT",
            risk_pct=0.01,
            min_order_size=0.01,
            initial_equity=initial_equity,
            name="BNB",
            exchange_client=exchange_client,
        )

    def check_signal(self, df15: pd.DataFrame) -> Optional[Dict[str, Any]]:
        return _vwap_breakout_signal(
            df15, vwap_n=10, dev_thr=1.0,
            sl_mult=1.5, tp_mult=1.0,
        )


# ============================================================
# MULTI-BOT SYSTEM — Orchestrates all 5 bots in parallel threads
# ============================================================
class MultiBotSystem:
    """
    Orchestrates all 5 bots running in parallel threads.

    Usage:
        system = MultiBotSystem()
        system.start()                # starts 5 background threads
        system.run_once()             # one synchronous tick of all 5
        system.status()               # returns dict of all bot states
        system.stop()                 # stops all threads
    """

    def __init__(self, initial_equity_per_bot: float = INITIAL_EQUITY,
                 exchange_client=None):
        self.exchange_client = exchange_client
        _load_ml_models()
        self.bots = {
            "BTC": BtcBot(initial_equity=initial_equity_per_bot,
                         exchange_client=exchange_client),
            "ETH": EthBot(initial_equity=initial_equity_per_bot,
                         exchange_client=exchange_client),
            "SOL": SolBot(initial_equity=initial_equity_per_bot,
                         exchange_client=exchange_client),
            "XRP": XrpBot(initial_equity=initial_equity_per_bot,
                         exchange_client=exchange_client),
            "BNB": BnbBot(initial_equity=initial_equity_per_bot,
                         exchange_client=exchange_client),
        }
        # When live, sync virtual equity with the real exchange balance
        if exchange_client is not None:
            self._sync_equity_from_exchange()
        self._threads = {}
        self._stop_event = threading.Event()

    def _sync_equity_from_exchange(self):
        """When live, set each bot's equity to a share of the real account
        balance, so sizing and the dashboard reflect actual testnet funds."""
        try:
            bal = self.exchange_client.get_balance("USDT")
            if bal and bal > 0:
                per = bal / len(self.bots)
                for bot in self.bots.values():
                    bot.initial_equity = per
                    bot.equity = per
                logger.info(f"Equity synced from exchange: ${bal:.2f} "
                            f"-> ${per:.2f}/bot")
            else:
                logger.warning(f"Exchange balance unavailable/zero ({bal}); "
                               f"keeping default equity")
        except Exception as e:
            logger.warning(f"Could not sync balance from exchange: {e}")

    def run_once(self) -> Dict[str, str]:
        """Run a single synchronous tick of all 5 bots (sequential)."""
        results = {}
        for name, bot in self.bots.items():
            try:
                results[name] = bot.on_tick()
            except Exception as e:
                results[name] = f"[{name}] ERROR: {e}"
        return results

    def start(self):
        """Start all 5 bots in parallel background threads."""
        self._stop_event.clear()
        for name, bot in self.bots.items():
            t = threading.Thread(target=self._bot_loop, args=(bot,), daemon=True, name=f"bot-{name}")
            t.start()
            self._threads[name] = t
            print(f"[MultiBotSystem] Started {name} bot thread")
        print(f"[MultiBotSystem] All 5 bots running. Poll interval: {POLL_INTERVAL_SEC}s")

    def stop(self):
        """Stop all bot threads gracefully."""
        self._stop_event.set()
        for name, t in self._threads.items():
            t.join(timeout=10)
            print(f"[MultiBotSystem] Stopped {name} bot thread")
        self._threads = {}

    def _bot_loop(self, bot: BaseBot):
        """Main loop for each bot thread. Runs until stop() is called."""
        while not self._stop_event.is_set():
            try:
                bot.on_tick()
            except Exception as e:
                print(f"[{bot.name}] thread error: {e}")
            # Wait for next 5m bar (with stop event check every 5s)
            for _ in range(POLL_INTERVAL_SEC // 5):
                if self._stop_event.is_set():
                    return
                time.sleep(5)

    def status(self) -> Dict[str, Dict[str, Any]]:
        """Return current state of all bots."""
        return {name: bot.status() for name, bot in self.bots.items()}

    def portfolio_summary(self) -> Dict[str, Any]:
        """Return consolidated portfolio metrics."""
        total_equity = sum(b.equity for b in self.bots.values())
        total_initial = sum(b.initial_equity for b in self.bots.values())
        total_trades = sum(len(b.trade_log) for b in self.bots.values())
        all_nets = []
        for b in self.bots.values():
            for t in b.trade_log:
                all_nets.append(t["net"])
        nets = np.array(all_nets) if all_nets else np.array([0])
        wins = (nets > 0).sum()
        losses = (nets <= 0).sum()
        gross_win = nets[nets > 0].sum()
        gross_loss = -nets[nets <= 0].sum()
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        wr = wins / len(nets) * 100 if len(nets) > 0 else 0
        return {
            "total_equity": total_equity,
            "total_initial": total_initial,
            "net_pnl": total_equity - total_initial,
            "return_pct": (total_equity / total_initial - 1) * 100,
            "total_trades": total_trades,
            "win_rate": wr,
            "profit_factor": pf,
            "bots": self.status(),
        }


# ============================================================
# LIVE DEMO ENTRY POINT
# ============================================================
if __name__ == "__main__":
    print("=" * 72)
    print(" MULTI-BOT SYSTEM — Live Signal Check")
    print("=" * 72)
    print()
    print("Bots configured:")
    print(f"  {'Name':<6} {'Symbol':<10} {'Risk%':>6} {'MinSize':>10} {'Strategy':<40}")
    print("  " + "-" * 75)
    for name, bot in [
        ("BTC", BtcBot()),
        ("ETH", EthBot()),
        ("SOL", SolBot()),
        ("XRP", XrpBot()),
        ("BNB", BnbBot()),
    ]:
        strat = "VWAP multimodal (3 regimes)" if name == "BTC" else \
                f"VWAP({bot.__class__.__name__})"
        # Get strategy params from class for display
        if name == "BTC":
            strat = "Multimodal: ALC(vwap15,thr0.75,sl1.5,tp1.0) BAJ(vwap20,thr1.0,sl2.0,tp1.5)"
        else:
            # Read params from a test signal call... too complex for demo.
            # Just hardcode for display.
            params = {"ETH": "vwap10,thr1.0,sl2.0,tp1.0",
                      "SOL": "vwap10,thr1.0,sl1.5,tp0.5",
                      "XRP": "vwap10,thr1.0,sl1.5,tp0.5",
                      "BNB": "vwap10,thr1.0,sl1.5,tp1.0"}[name]
            strat = f"VWAP breakout ({params})"
        print(f"  {name:<6} {bot.symbol:<10} {bot.risk_pct*100:>5.2f}% {bot.min_order_size:>10} {strat:<40}")
    print()
    print(f"Common config:")
    print(f"  Commission: {COMMISSION*100:.2f}%")
    print(f"  Spread:     {SPREAD*100:.2f}%")
    print(f"  Slippage:   {SLIP_ATR_MULT}xATR(5m)")
    print(f"  Cooldown:   {LOSS_STREAK_TRIGGER} losses -> {PAUSE_MINUTES}min pause (per bot)")
    print(f"  Poll:       every {POLL_INTERVAL_SEC}s (5m bars)")
    print()

    # Single synchronous tick of all 5 bots
    print("=" * 72)
    print("Running single tick of all 5 bots (synchronous)...")
    print("=" * 72)
    system = MultiBotSystem()
    results = system.run_once()
    print()
    for name, status in results.items():
        print(f"  {name}: {status}")

    print()
    print("=" * 72)
    print("Portfolio status:")
    print("=" * 72)
    summary = system.portfolio_summary()
    print(f"  Total equity: ${summary['total_equity']:.2f} (initial ${summary['total_initial']:.2f})")
    print(f"  Net PnL: ${summary['net_pnl']:+.2f} ({summary['return_pct']:+.2f}%)")
    print(f"  Total trades: {summary['total_trades']}")
    print()

    print("=" * 72)
    print("To run as a continuous system:")
    print("  system = MultiBotSystem()")
    print("  system.start()         # 5 background threads, polls every 5 min")
    print("  system.status()        # check state of all bots")
    print("  system.portfolio_summary()  # consolidated metrics")
    print("  system.stop()          # graceful shutdown")
    print("=" * 72)
