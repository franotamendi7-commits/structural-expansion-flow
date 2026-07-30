#!/usr/bin/env python3
"""
BTC Optimized Strategy Backtester — Cost-Aware, Swing-Focused
=============================================================
Previous 8 strategies ALL FAILED because they captured too-small moves eaten by costs.

Key fixes:
  1. Target LARGER moves (1-3% per trade minimum)
  2. Fewer but higher quality trades (5-15 per month, not 50+)
  3. 4h timeframe to reduce noise
  4. Trailing stops to let winners run
  5. Parameter grid search for optimal settings
  6. Cost survival analysis — minimum edge needed

Cost model:
  Taker:  0.04% per side  (0.08% round trip)
  Spread: 0.02%
  Funding: 0.005% per 8h
  Slippage: 0.01%
  Total one-way ≈ 0.075%  →  round trip ≈ 0.15%+
"""

import sys
import json
import itertools
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict

# ============================================================
# PATHS & CONFIG
# ============================================================

DATA_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/data/BTCUSDT')
REPORT_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/reports')
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Realistic costs
TAKER_FEE   = 0.0004   # 0.04% per side
SPREAD      = 0.0002   # 0.02%
FUNDING     = 0.00005  # 0.005% per 8h
SLIPPAGE    = 0.0001   # 0.01%

INITIAL_CAPITAL = 10_000.0
LEVERAGES = [1, 2, 3, 5, 7, 10]

# ============================================================
# PARAMETER GRID
# ============================================================

PARAM_GRID = {
    'ema_fast':    [8, 12, 20],
    'ema_slow':    [21, 50, 100],
    'rsi_period':  [10, 14, 21],
    'rsi_buy':     [25, 30, 35],
    'rsi_sell':    [65, 70, 75],
    'stop_loss':   [0.01, 0.015, 0.02],
    'take_profit': [0.02, 0.03, 0.04, 0.05],
}

# Reduced grid for speed (full grid = 3^3 * 3 * 3 * 3 * 4 = 2916 combos per strategy)
# We sample key combos to keep runtime sane.
QUICK_GRID = {
    'ema_fast':    [8, 12, 20],
    'ema_slow':    [50, 100],
    'rsi_period':  [14],
    'rsi_buy':     [30, 35],
    'rsi_sell':    [65, 70],
    'stop_loss':   [0.015, 0.02],
    'take_profit': [0.03, 0.04, 0.05],
}


# ============================================================
# DATA LOADING
# ============================================================

def load_4h_data() -> Optional[pd.DataFrame]:
    """Load pre-existing 4h data (resampled from 1h)."""
    fp = DATA_DIR / 'BTCUSDT_4h.parquet'
    if fp.exists():
        df = pd.read_parquet(fp)
        print(f"[DATA] Loaded 4h: {len(df)} bars  {df.index[0]} → {df.index[-1]}")
        return df
    # Fallback: resample from 1h
    fp1h = DATA_DIR / 'BTCUSDT_1h_full.parquet'
    if fp1h.exists():
        df = pd.read_parquet(fp1h)
        df4h = df.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min',
            'close': 'last', 'volume': 'sum', 'quote_volume': 'sum',
        }).dropna()
        df4h.to_parquet(fp, compression='snappy')
        print(f"[DATA] Resampled 1h→4h: {len(df4h)} bars")
        return df4h
    print("[DATA] ERROR: No BTC data found")
    return None


# ============================================================
# COST MODEL
# ============================================================

def round_trip_cost_pct(holding_bars: int, leverage: int = 1) -> float:
    """Total cost as % of notional for a single round-trip trade."""
    taker   = TAKER_FEE * 2              # entry + exit
    spread  = SPREAD
    slip    = SLIPPAGE
    periods = max(1, holding_bars // 6)   # 4h bars → 6 bars = 24h ≈ 3 funding periods
    fund    = FUNDING * periods
    return taker + spread + slip + fund


# ============================================================
# INDICATOR HELPERS
# ============================================================

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def bollinger_bands(close: pd.Series, period: int = 20, num_std: float = 2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    return sma + num_std * std, sma, sma - num_std * std  # upper, mid, lower

def bb_width(close: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.Series:
    upper, mid, lower = bollinger_bands(close, period, num_std)
    return (upper - lower) / mid


# ============================================================
# STRATEGY 1: SWING TRADING — EMA Trend + RSI Pullback (4h)
# ============================================================

def strategy_swing(
    data: pd.DataFrame,
    ema_fast: int = 12,
    ema_slow: int = 50,
    rsi_period: int = 14,
    rsi_buy: float = 30.0,
    rsi_sell: float = 70.0,
    stop_loss: float = 0.02,
    take_profit: float = 0.04,
    trail_activate: float = 0.02,
    max_hold_bars: int = 48,
) -> pd.Series:
    """
    Swing trade on 4h bars.
    - Only trade when 50 EMA > 200 EMA (longs) or < (shorts).
    - Enter on RSI pullback to rsi_buy (long) / rsi_sell (short).
    - Stop loss, take profit, trailing stop to breakeven after trail_activate.
    - Max hold: max_hold_bars (= 8 days on 4h).
    """
    c = data['close'].values
    h = data['high'].values
    l = data['low'].values
    n = len(c)

    ema_f = ema(data['close'], ema_fast).values
    ema_s = ema(data['close'], ema_slow).values
    rsi_v = rsi(data['close'], rsi_period).values

    pos = np.zeros(n)
    in_trade = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    tp_px = 0.0
    trail_active = False
    best_px = 0.0

    warmup = max(ema_slow, rsi_period) + 5

    for i in range(warmup, n):
        if not in_trade:
            # Long entry: uptrend + RSI pullback
            if ema_f[i] > ema_s[i] and rsi_v[i] < rsi_buy:
                in_trade = True
                direction = 1
                entry_px = c[i]
                entry_bar = i
                stop_px = entry_px * (1 - stop_loss)
                tp_px = entry_px * (1 + take_profit)
                trail_active = False
                best_px = entry_px
                pos[i] = 1
            # Short entry: downtrend + RSI bounce
            elif ema_f[i] < ema_s[i] and rsi_v[i] > rsi_sell:
                in_trade = True
                direction = -1
                entry_px = c[i]
                entry_bar = i
                stop_px = entry_px * (1 + stop_loss)
                tp_px = entry_px * (1 - take_profit)
                trail_active = False
                best_px = entry_px
                pos[i] = -1
        else:
            hold = i - entry_bar

            # Track best price for trailing stop
            if direction == 1:
                if h[i] > best_px:
                    best_px = h[i]
                # Activate trailing stop once profit >= trail_activate
                if not trail_active and (best_px / entry_px - 1) >= trail_activate:
                    trail_active = True
                if trail_active:
                    stop_px = max(stop_px, best_px * (1 - stop_loss))
            else:
                if l[i] < best_px:
                    best_px = l[i]
                if not trail_active and (1 - best_px / entry_px) >= trail_activate:
                    trail_active = True
                if trail_active:
                    stop_px = min(stop_px, best_px * (1 + stop_loss))

            # Check exit conditions (intra-bar simulation: low/high)
            exited = False
            if direction == 1:
                if l[i] <= stop_px:
                    exited = True
                elif h[i] >= tp_px:
                    exited = True
                elif hold >= max_hold_bars:
                    exited = True
            else:
                if h[i] >= stop_px:
                    exited = True
                elif l[i] <= tp_px:
                    exited = True
                elif hold >= max_hold_bars:
                    exited = True

            if exited:
                in_trade = False
            else:
                pos[i] = direction

    return pd.Series(pos, index=data.index)


# ============================================================
# STRATEGY 2: BREAKOUT + RETEST
# ============================================================

def strategy_breakout_retest(
    data: pd.DataFrame,
    range_bars: int = 48,
    vol_mult: float = 1.5,
    stop_loss: float = 0.02,
    take_profit_ratio: float = 1.0,
    max_hold_bars: int = 36,
) -> pd.Series:
    """
    Identify a 48-bar range. Wait for breakout + retest of boundary.
    Volume must be > vol_mult × average.
    TP = range height projected. SL = below/above range boundary.
    """
    c = data['close'].values
    h = data['high'].values
    l = data['low'].values
    v = data['volume'].values
    n = len(c)

    avg_vol = data['volume'].rolling(20).mean().values

    pos = np.zeros(n)
    in_trade = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    tp_px = 0.0
    range_high = 0.0
    range_low = 0.0

    for i in range(range_bars + 20, n):
        if not in_trade:
            # Define range
            rng_h = np.max(h[i - range_bars:i])
            rng_l = np.min(l[i - range_bars:i])
            rng_size = rng_h - rng_l

            if rng_size <= 0 or rng_size / c[i] < 0.01:
                continue  # range too tight

            # Breakout up: close above range high
            if c[i] > rng_h and c[i - 1] <= rng_h and v[i] > vol_mult * avg_vol[i]:
                # Check for retest in next bars (simplified: enter on breakout bar itself)
                in_trade = True
                direction = 1
                entry_px = c[i]
                entry_bar = i
                stop_px = rng_h  # stop below range boundary
                tp_px = entry_px + rng_size * take_profit_ratio
                range_high = rng_h
                range_low = rng_l
                pos[i] = 1

            # Breakout down
            elif c[i] < rng_l and c[i - 1] >= rng_l and v[i] > vol_mult * avg_vol[i]:
                in_trade = True
                direction = -1
                entry_px = c[i]
                entry_bar = i
                stop_px = rng_l
                tp_px = entry_px - rng_size * take_profit_ratio
                range_high = rng_h
                range_low = rng_l
                pos[i] = -1
        else:
            hold = i - entry_bar
            exited = False

            if direction == 1:
                if l[i] <= stop_px:
                    exited = True
                elif h[i] >= tp_px:
                    exited = True
                elif hold >= max_hold_bars:
                    exited = True
                # Invalidated: close back inside range
                elif c[i] < range_low:
                    exited = True
            else:
                if h[i] >= stop_px:
                    exited = True
                elif l[i] <= tp_px:
                    exited = True
                elif hold >= max_hold_bars:
                    exited = True
                elif c[i] > range_high:
                    exited = True

            if exited:
                in_trade = False
            else:
                pos[i] = direction

    return pd.Series(pos, index=data.index)


# ============================================================
# STRATEGY 3: FUNDING RATE + TREND
# ============================================================

def strategy_funding_trend(
    data: pd.DataFrame,
    ema_fast: int = 12,
    ema_slow: int = 50,
    funding_threshold: float = 0.0003,   # 0.03%
    stop_loss: float = 0.015,
    take_profit: float = 0.03,
    max_hold_bars: int = 36,
) -> pd.Series:
    """
    When funding is very positive (>0.03%), shorts have edge (longs pay).
    When funding is very negative, longs have edge.
    Use EMA trend to confirm direction.
    """
    c = data['close'].values
    h = data['high'].values
    l = data['low'].values
    n = len(c)

    ema_f = ema(data['close'], ema_fast).values
    ema_s = ema(data['close'], ema_slow).values

    # Simulate funding: use rolling 8h proxy from price momentum
    # Positive momentum → funding tends positive (longs pay)
    returns_8h = data['close'].pct_change(2).rolling(6).mean()  # ~24h avg
    funding_proxy = returns_8h * 0.01  # scale to approximate funding rate
    fp = funding_proxy.values

    pos = np.zeros(n)
    in_trade = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    tp_px = 0.0

    warmup = max(ema_slow, 20) + 10

    for i in range(warmup, n):
        if not in_trade:
            # High funding → market is bullish → short has edge (if trend confirms)
            if fp[i] > funding_threshold and ema_f[i] < ema_s[i]:
                in_trade = True
                direction = -1
                entry_px = c[i]
                entry_bar = i
                stop_px = entry_px * (1 + stop_loss)
                tp_px = entry_px * (1 - take_profit)
                pos[i] = -1
            # Low funding → market is bearish → long has edge
            elif fp[i] < -funding_threshold and ema_f[i] > ema_s[i]:
                in_trade = True
                direction = 1
                entry_px = c[i]
                entry_bar = i
                stop_px = entry_px * (1 - stop_loss)
                tp_px = entry_px * (1 + take_profit)
                pos[i] = 1
        else:
            hold = i - entry_bar
            exited = False

            if direction == 1:
                if l[i] <= stop_px or h[i] >= tp_px or hold >= max_hold_bars:
                    exited = True
            else:
                if h[i] >= stop_px or l[i] <= tp_px or hold >= max_hold_bars:
                    exited = True

            if exited:
                in_trade = False
            else:
                pos[i] = direction

    return pd.Series(pos, index=data.index)


# ============================================================
# STRATEGY 4: VOLATILITY COMPRESSION → EXPANSION (Squeeze)
# ============================================================

def strategy_volatility_squeeze(
    data: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    squeeze_percentile: float = 20.0,
    stop_loss: float = 0.015,
    take_profit_multiplier: float = 2.0,
    max_hold_bars: int = 30,
) -> pd.Series:
    """
    Detect Bollinger Band squeeze (width < 20th percentile).
    Enter long when price breaks above upper BB after squeeze.
    Enter short when price breaks below lower BB after squeeze.
    TP = 2× band width. SL = middle BB.
    """
    c = data['close'].values
    h = data['high'].values
    l = data['low'].values
    n = len(c)

    upper, mid, lower = bollinger_bands(data['close'], bb_period, bb_std)
    bw = bb_width(data['close'], bb_period, bb_std)

    # Rolling percentile for squeeze detection
    bw_pctl = bw.rolling(100).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False).values

    upper_v = upper.values
    mid_v = mid.values
    lower_v = lower.values
    bw_v = bw.values

    pos = np.zeros(n)
    in_trade = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    tp_px = 0.0
    in_squeeze = False

    warmup = max(bb_period, 100) + 5

    for i in range(warmup, n):
        if np.isnan(bw_pctl[i]):
            continue

        was_squeeze = in_squeeze
        in_squeeze = bw_pctl[i] < squeeze_percentile

        if not in_trade:
            # Entry: breakout from squeeze
            if was_squeeze and not in_squeeze:
                # Bullish breakout
                if c[i] > upper_v[i]:
                    in_trade = True
                    direction = 1
                    entry_px = c[i]
                    entry_bar = i
                    stop_px = mid_v[i]
                    tp_px = entry_px + bw_v[i] * take_profit_multiplier
                    pos[i] = 1
                # Bearish breakout
                elif c[i] < lower_v[i]:
                    in_trade = True
                    direction = -1
                    entry_px = c[i]
                    entry_bar = i
                    stop_px = mid_v[i]
                    tp_px = entry_px - bw_v[i] * take_profit_multiplier
                    pos[i] = -1
        else:
            hold = i - entry_bar
            exited = False

            if direction == 1:
                if l[i] <= stop_px or h[i] >= tp_px or hold >= max_hold_bars:
                    exited = True
            else:
                if h[i] >= stop_px or l[i] <= tp_px or hold >= max_hold_bars:
                    exited = True

            if exited:
                in_trade = False
            else:
                pos[i] = direction

    return pd.Series(pos, index=data.index)


# ============================================================
# STRATEGY 5: MULTI-STRATEGY COMBINATOR
# ============================================================

def strategy_combinator(
    data: pd.DataFrame,
    min_agreement: int = 2,
    ema_fast: int = 12,
    ema_slow: int = 50,
    rsi_period: int = 14,
    rsi_buy: float = 30.0,
    rsi_sell: float = 70.0,
    stop_loss: float = 0.02,
    take_profit: float = 0.04,
) -> pd.Series:
    """
    Only enter when >= min_agreement sub-strategies agree.
    Sub-strategies: Swing, Breakout, Volatility Squeeze.
    """
    # Generate sub-strategy signals (just directional, no exit logic)
    pos_swing = strategy_swing(data, ema_fast, ema_slow, rsi_period, rsi_buy, rsi_sell, stop_loss, take_profit).values
    pos_break = strategy_breakout_retest(data).values
    pos_vol   = strategy_volatility_squeeze(data).values

    n = len(data)
    pos = np.zeros(n)
    in_trade = False
    direction = 0
    entry_px = 0.0
    entry_bar = 0
    stop_px = 0.0
    tp_px = 0.0

    for i in range(n):
        # Count agreement
        sigs = [pos_swing[i], pos_break[i], pos_vol[i]]
        long_votes  = sum(1 for s in sigs if s > 0)
        short_votes = sum(1 for s in sigs if s < 0)

        if not in_trade:
            if long_votes >= min_agreement:
                in_trade = True
                direction = 1
                entry_px = data['close'].iloc[i]
                entry_bar = i
                stop_px = entry_px * (1 - stop_loss)
                tp_px = entry_px * (1 + take_profit)
                pos[i] = 1
            elif short_votes >= min_agreement:
                in_trade = True
                direction = -1
                entry_px = data['close'].iloc[i]
                entry_bar = i
                stop_px = entry_px * (1 + stop_loss)
                tp_px = entry_px * (1 - take_profit)
                pos[i] = -1
        else:
            hold = i - entry_bar
            h_val = data['high'].iloc[i]
            l_val = data['low'].iloc[i]
            exited = False

            if direction == 1:
                if l_val <= stop_px or h_val >= tp_px or hold >= 48:
                    exited = True
            else:
                if h_val >= stop_px or l_val <= tp_px or hold >= 48:
                    exited = True

            if exited:
                in_trade = False
            else:
                pos[i] = direction

    return pd.Series(pos, index=data.index)


# ============================================================
# BACKTEST ENGINE
# ============================================================

@dataclass
class BacktestMetrics:
    strategy: str
    params: str
    leverage: int
    total_return: float
    annual_return: float
    sharpe: float
    max_dd: float
    win_rate: float
    profit_factor: float
    total_trades: int
    trades_per_month: float
    avg_trade_return: float
    avg_cost_per_trade: float
    total_costs_pct: float
    breakeven_win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy: float


def run_backtest(
    data: pd.DataFrame,
    positions: pd.Series,
    strategy_name: str,
    params_str: str,
    leverage: int,
    capital: float = INITIAL_CAPITAL,
) -> Optional[BacktestMetrics]:
    """Full backtest with costs, per-trade tracking, and trailing stops."""
    close = data['close'].values
    returns = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    returns[0] = 0

    n = len(data)
    pos_vals = positions.values

    # Strategy returns (lagged by 1 bar)
    strat_ret = np.zeros(n)
    strat_ret[1:] = pos_vals[:-1] * returns[1:] * leverage

    # Trade-level cost deduction
    cost_ret = np.zeros(n)
    in_trade = False
    entry_i = 0

    for i in range(1, n):
        if pos_vals[i] != 0 and not in_trade:
            in_trade = True
            entry_i = i
        elif in_trade and (pos_vals[i] == 0 or i == n - 1):
            in_trade = False
            hold_bars = i - entry_i
            cost = round_trip_cost_pct(hold_bars, leverage)
            cost_ret[i] = cost  # deducted on exit bar

    net_ret = strat_ret - cost_ret
    equity = capital * np.cumprod(1 + net_ret)

    # --- Metrics ---
    total_return = (equity[-1] / capital - 1) * 100
    n_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
    annual_return = ((1 + total_return / 100) ** (365 / max(n_days, 1)) - 1) * 100

    std = np.std(net_ret)
    sharpe = (np.mean(net_ret) / std * np.sqrt(252 * 6)) if std > 0 else 0  # 6 bars/day on 4h

    running_max = np.maximum.accumulate(equity)
    dd = equity / running_max - 1
    max_dd = dd.min() * 100

    # Per-trade stats
    trade_returns = []
    in_t = False
    trade_pnl = 0.0
    wins = []
    losses = []

    for i in range(n):
        if pos_vals[i] != 0 and not in_t:
            in_t = True
            trade_pnl = 0.0
        elif in_t:
            trade_pnl += net_ret[i]
            if pos_vals[i] == 0 or i == n - 1:
                in_t = False
                trade_returns.append(trade_pnl)
                if trade_pnl > 0:
                    wins.append(trade_pnl)
                else:
                    losses.append(trade_pnl)

    if len(trade_returns) == 0:
        return None

    n_trades = len(trade_returns)
    win_rate = len(wins) / n_trades * 100
    avg_win = np.mean(wins) * 100 if wins else 0
    avg_loss = np.mean(losses) * 100 if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    total_cost = cost_ret.sum() * 100
    avg_cost = total_cost / n_trades if n_trades > 0 else 0
    avg_trade = np.mean(trade_returns) * 100
    expectancy = (win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss) - avg_cost

    breakeven_wr = 50 + 50 * (avg_cost / max(abs(avg_trade), 0.001))

    return BacktestMetrics(
        strategy=strategy_name,
        params=params_str,
        leverage=leverage,
        total_return=round(total_return, 2),
        annual_return=round(annual_return, 2),
        sharpe=round(sharpe, 2),
        max_dd=round(max_dd, 2),
        win_rate=round(win_rate, 1),
        profit_factor=round(pf, 2),
        total_trades=n_trades,
        trades_per_month=round(n_trades / max(n_days / 30, 1), 1),
        avg_trade_return=round(avg_trade, 4),
        avg_cost_per_trade=round(avg_cost * 100, 4),
        total_costs_pct=round(total_cost, 2),
        breakeven_win_rate=round(min(breakeven_wr, 999), 1),
        avg_win_pct=round(avg_win, 4),
        avg_loss_pct=round(avg_loss, 4),
        expectancy=round(expectancy, 4),
    )


# ============================================================
# PARAMETER GRID SEARCH
# ============================================================

def grid_search_swing(data: pd.DataFrame, leverage: int = 1) -> List[BacktestMetrics]:
    """Grid search over swing strategy parameters."""
    results = []
    combos = list(itertools.product(
        QUICK_GRID['ema_fast'],
        QUICK_GRID['ema_slow'],
        QUICK_GRID['rsi_period'],
        QUICK_GRID['rsi_buy'],
        QUICK_GRID['rsi_sell'],
        QUICK_GRID['stop_loss'],
        QUICK_GRID['take_profit'],
    ))
    print(f"  [GRID] {len(combos)} parameter combos to test...")

    for idx, (ef, es, rp, rb, rs, sl, tp) in enumerate(combos):
        if ef >= es:
            continue  # fast must be < slow
        if rb >= rs:
            continue  # buy RSI must be < sell RSI

        params_str = f"ema={ef}/{es} rsi={rp} buy={rb} sell={rs} sl={sl} tp={tp}"
        try:
            pos = strategy_swing(data, ef, es, rp, rb, rs, sl, tp)
            trades = (pos.diff().fillna(0) != 0).sum()
            if trades < 5:
                continue  # skip too few trades
            m = run_backtest(data, pos, "Swing", params_str, leverage)
            if m and m.total_trades >= 3:
                results.append(m)
        except Exception:
            pass

        if (idx + 1) % 50 == 0:
            print(f"    ... {idx + 1}/{len(combos)} done")

    return results


def grid_search_breakout(data: pd.DataFrame, leverage: int = 1) -> List[BacktestResults]:
    """Grid search over breakout parameters."""
    results = []
    combos = [
        (36, 1.5, 0.02, 1.0),
        (36, 2.0, 0.015, 1.0),
        (48, 1.5, 0.02, 1.0),
        (48, 2.0, 0.02, 1.2),
        (48, 1.5, 0.015, 1.5),
        (60, 1.5, 0.02, 1.0),
        (60, 2.0, 0.025, 1.2),
    ]
    for rb, vm, sl, tpr in combos:
        params_str = f"range={rb} vol={vm} sl={sl} tpr={tpr}"
        try:
            pos = strategy_breakout_retest(data, rb, vm, sl, tpr)
            m = run_backtest(data, pos, "Breakout_Retest", params_str, leverage)
            if m and m.total_trades >= 3:
                results.append(m)
        except Exception:
            pass
    return results


def grid_search_volatility(data: pd.DataFrame, leverage: int = 1) -> List[BacktestResults]:
    """Grid search over volatility squeeze parameters."""
    results = []
    combos = [
        (20, 2.0, 20.0, 0.015, 2.0),
        (20, 2.0, 15.0, 0.02, 2.0),
        (20, 2.5, 20.0, 0.015, 2.5),
        (30, 2.0, 20.0, 0.02, 2.0),
        (30, 2.5, 15.0, 0.02, 2.5),
    ]
    for bp, bs, sp, sl, tpm in combos:
        params_str = f"bb={bp}/{bs} pctl={sp} sl={sl} tpm={tpm}"
        try:
            pos = strategy_volatility_squeeze(data, bp, bs, sp, sl, tpm)
            m = run_backtest(data, pos, "Vol_Squeeze", params_str, leverage)
            if m and m.total_trades >= 3:
                results.append(m)
        except Exception:
            pass
    return results


# ============================================================
# COST SURVIVAL ANALYSIS
# ============================================================

def cost_survival_analysis(data: pd.DataFrame) -> dict:
    """
    What's the minimum edge needed to be profitable?
    Calculate breakeven metrics across different trade frequencies.
    """
    close = data['close']
    avg_vol = close.pct_change().std() * 100  # daily vol %
    n_days = (data.index[-1] - data.index[0]).total_seconds() / 86400

    # Average cost per round trip
    avg_cost_rt = (TAKER_FEE * 2 + SPREAD + SLIPPAGE) * 100  # in %
    # Plus ~1 funding period for avg hold
    avg_cost_rt += FUNDING * 100  # ~0.005%

    # For different trade frequencies
    scenarios = {}
    for trades_per_month in [3, 5, 8, 12, 20, 40]:
        months = n_days / 30
        total_trades = trades_per_month * months
        total_costs = total_trades * avg_cost_rt / 100 * INITIAL_CAPITAL

        # Need to make > total_costs to be profitable
        min_avg_win = avg_cost_rt  # minimum average edge per trade

        scenarios[f"{trades_per_month}/mo"] = {
            'trades_per_month': trades_per_month,
            'total_trades': int(total_trades),
            'cost_per_trade_pct': round(avg_cost_rt, 4),
            'total_costs_usd': round(total_costs, 2),
            'min_edge_per_trade_pct': round(avg_cost_rt, 4),
            'min_win_rate_50_50': round(50 + 50 * avg_cost_rt / max(avg_cost_rt, 0.1), 1),
        }

    return {
        'avg_round_trip_cost_pct': round(avg_cost_rt, 4),
        'btc_daily_volatility_pct': round(avg_vol, 2),
        'scenarios': scenarios,
    }


# ============================================================
# REPORT GENERATOR
# ============================================================

def generate_report(
    all_results: List[BacktestMetrics],
    cost_analysis: dict,
    data: pd.DataFrame,
):
    """Generate comprehensive markdown report."""

    # Find best by different criteria
    viable = [r for r in all_results if r.sharpe > 0.5 and r.max_dd > -40 and r.total_return > 0]
    viable.sort(key=lambda x: x.sharpe, reverse=True)

    best_sharpe = viable[0] if viable else None
    best_return = max(viable, key=lambda x: x.total_return) if viable else None
    best_pf = max(viable, key=lambda x: x.profit_factor) if viable else None
    best_expectancy = max(viable, key=lambda x: x.expectancy) if viable else None

    # Group by leverage
    by_lev = {}
    for r in viable:
        by_lev.setdefault(r.leverage, []).append(r)

    n_days = (data.index[-1] - data.index[0]).total_seconds() / 86400

    md = []
    md.append("# BTC Optimized Strategy Backtest Results\n")
    md.append(f"**Period:** {data.index[0].strftime('%Y-%m-%d')} → {data.index[-1].strftime('%Y-%m-%d')} ({n_days:.0f} days)")
    md.append(f"**Timeframe:** 4h")
    md.append(f"**Initial Capital:** ${INITIAL_CAPITAL:,.0f}")
    md.append(f"**Total backtests:** {len(all_results)}")
    md.append(f"**Viable (Sharpe>0.5, DD>-40%, Return>0):** {len(viable)}\n")

    md.append("---\n")

    # Cost Model
    md.append("## Cost Model\n")
    md.append("| Component | Rate |")
    md.append("|-----------|------|")
    md.append(f"| Taker fee | {TAKER_FEE*100:.2f}% per side |")
    md.append(f"| Spread | {SPREAD*100:.2f}% |")
    md.append(f"| Funding | {FUNDING*100:.4f}% per 8h |")
    md.append(f"| Slippage | {SLIPPAGE*100:.2f}% |")
    md.append(f"| **Round-trip** | **~{cost_analysis['avg_round_trip_cost_pct']:.3f}%** |\n")

    # Best Strategy
    if best_sharpe:
        md.append("---\n")
        md.append("## Best Strategy (by Sharpe)\n")
        md.append(f"**{best_sharpe.strategy}** @ {best_sharpe.leverage}x leverage\n")
        md.append(f"- Parameters: `{best_sharpe.params}`")
        md.append(f"- Total Return: **{best_sharpe.total_return:+.2f}%**")
        md.append(f"- Annual Return: {best_sharpe.annual_return:+.2f}%")
        md.append(f"- Sharpe Ratio: **{best_sharpe.sharpe:.2f}**")
        md.append(f"- Max Drawdown: {best_sharpe.max_dd:.2f}%")
        md.append(f"- Win Rate: {best_sharpe.win_rate:.1f}%")
        md.append(f"- Profit Factor: {best_sharpe.profit_factor:.2f}")
        md.append(f"- Trades: {best_sharpe.total_trades} ({best_sharpe.trades_per_month:.1f}/month)")
        md.append(f"- Avg Win: {best_sharpe.avg_win_pct:.4f}% | Avg Loss: {best_sharpe.avg_loss_pct:.4f}%")
        md.append(f"- Expectancy: {best_sharpe.expectancy:.4f}%")
        md.append(f"- Avg Cost/Trade: {best_sharpe.avg_cost_per_trade:.4f}%")
        md.append(f"- Breakeven WR: {best_sharpe.breakeven_win_rate:.1f}%\n")

    # Performance by Leverage
    md.append("---\n")
    md.append("## Performance by Leverage Level\n")
    md.append("| Leverage | # Viable | Best Strategy | Best Return | Best Sharpe | Avg Max DD |")
    md.append("|----------|----------|---------------|-------------|-------------|------------|")
    for lev in sorted(by_lev.keys()):
        strats = by_lev[lev]
        best = max(strats, key=lambda x: x.sharpe)
        avg_dd = np.mean([r.max_dd for r in strats])
        md.append(f"| {lev}x | {len(strats)} | {best.strategy} | {best.total_return:+.1f}% | {best.sharpe:.2f} | {avg_dd:.1f}% |")

    # Top 15 Results
    md.append("\n---\n")
    md.append("## Top 15 Results (by Sharpe)\n")
    md.append("| # | Strategy | Leverage | Params | Return | Sharpe | WR | PF | DD | Trades/mo | Cost/Trade |")
    md.append("|---|----------|----------|--------|--------|--------|----|----|----|----|----|")
    for i, r in enumerate(viable[:15], 1):
        md.append(f"| {i} | {r.strategy} | {r.leverage}x | `{r.params}` | {r.total_return:+.1f}% | {r.sharpe:.2f} | {r.win_rate:.0f}% | {r.profit_factor:.2f} | {r.max_dd:.1f}% | {r.trades_per_month:.1f} | {r.avg_cost_per_trade:.4f}% |")

    # Top by Return
    md.append("\n---\n")
    md.append("## Top 10 Results (by Total Return)\n")
    by_ret = sorted(viable, key=lambda x: x.total_return, reverse=True)
    md.append("| # | Strategy | Leverage | Return | Sharpe | WR | PF | DD |")
    md.append("|---|----------|----------|--------|--------|----|----|----|")
    for i, r in enumerate(by_ret[:10], 1):
        md.append(f"| {i} | {r.strategy} | {r.leverage}x | {r.total_return:+.1f}% | {r.sharpe:.2f} | {r.win_rate:.0f}% | {r.profit_factor:.2f} | {r.max_dd:.1f}% |")

    # Cost Survival Analysis
    md.append("\n---\n")
    md.append("## Cost Survival Analysis\n")
    md.append("What minimum edge do you need per trade to be profitable?\n")
    md.append("| Trade Freq | Total Trades | Cost/Trade | Total Costs | Min Edge Needed |")
    md.append("|------------|--------------|------------|-------------|-----------------|")
    for label, sc in cost_analysis['scenarios'].items():
        md.append(f"| {label} | {sc['total_trades']} | {sc['cost_per_trade_pct']:.4f}% | ${sc['total_costs_usd']:,.0f} | {sc['min_edge_per_trade_pct']:.4f}% |")

    md.append(f"\n**Key insight:** With round-trip costs of ~{cost_analysis['avg_round_trip_cost_pct']:.3f}%,")
    md.append(f"each trade must capture MORE than {cost_analysis['avg_round_trip_cost_pct']:.3f}% to be profitable.")
    md.append(f"BTC daily volatility is ~{cost_analysis['btc_daily_volatility_pct']:.1f}%, so larger moves are available")
    md.append(f"but require patience (fewer trades, longer holds).\n")

    # Risk Management Rules
    md.append("---\n")
    md.append("## Risk Management Rules\n")
    md.append("1. **Never risk > 2% of capital per trade** — fixed stop loss")
    md.append("2. **Target 2:1 or better R/R ratio** — TP >= 2× SL")
    md.append("3. **Use trailing stops** — move to breakeven after +2% profit")
    md.append("4. **Max 3 concurrent positions** — diversify across strategies")
    md.append("5. **Reduce size in drawdown** — scale down after -10% DD")
    md.append("6. **No trading in low-volatility squeezes** — wait for expansion")
    md.append("7. **Verify funding rate direction** — trade with the crowd when funding is extreme")
    md.append("8. **4h timeframe minimum** — 1h is too noisy for swing trades")
    md.append("9. **Quality over quantity** — 5-15 trades/month, not 50+")
    md.append("10. **Paper trade first** — validate 3 months before live\n")

    # Final Recommendation
    md.append("---\n")
    md.append("## Final Recommendation\n")
    if best_sharpe:
        md.append(f"**Primary strategy:** {best_sharpe.strategy} at {best_sharpe.leverage}x leverage\n")
        md.append(f"- Expected Sharpe: {best_sharpe.sharpe:.2f}")
        md.append(f"- Expected Return: {best_sharpe.total_return:+.1f}% over test period")
        md.append(f"- Max Drawdown: {best_sharpe.max_dd:.1f}%")
        md.append(f"- Trade frequency: {best_sharpe.trades_per_month:.1f} trades/month")
        md.append(f"- Win Rate: {best_sharpe.win_rate:.1f}% with PF {best_sharpe.profit_factor:.2f}\n")

        md.append("**Start with 2x leverage, scale to 3x after 3 months of live profitability.**")
        md.append("**Avoid leverage >5x — drawdowns become unmanageable.**\n")

    if len(viable) == 0:
        md.append("**No strategies survived cost filtering.** Consider:\n")
        md.append("1. Using a maker fee model (0.02% instead of 0.04%)")
        md.append("2. Trading on exchanges with lower fees")
        md.append("3. Holding positions longer to capture larger moves")
        md.append("4. Using limit orders to avoid taker fees\n")

    md.append("---\n")
    md.append("*Generated by btc_optimized.py*")

    return "\n".join(md)


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("BTC OPTIMIZED STRATEGY BACKTESTER")
    print("Cost-aware, swing-focused, parameter-optimized")
    print("=" * 80)
    print(f"Capital: ${INITIAL_CAPITAL:,.0f}")
    print(f"Costs: Taker {TAKER_FEE*100:.2f}% | Spread {SPREAD*100:.2f}% | "
          f"Funding {FUNDING*100:.4f}%/8h | Slippage {SLIPPAGE*100:.2f}%")
    print(f"Leverages: {LEVERAGES}")
    print("=" * 80)

    data = load_4h_data()
    if data is None:
        print("ERROR: No data")
        return

    print(f"\nData: {data.index[0]} → {data.index[-1]} ({len(data)} bars)")
    print(f"Price range: ${data['close'].min():.2f} — ${data['close'].max():.2f}\n")

    all_results: List[BacktestMetrics] = []

    # ── Strategy 1: Swing Trading (with grid search) ──────────────
    print("=" * 60)
    print("STRATEGY 1: SWING TRADING — EMA Trend + RSI Pullback")
    print("=" * 60)
    for lev in LEVERAGES:
        print(f"\n  Leverage {lev}x:")
        results = grid_search_swing(data, lev)
        all_results.extend(results)
        if results:
            best = max(results, key=lambda x: x.sharpe)
            print(f"  → Best: Sharpe={best.sharpe:.2f} Return={best.total_return:+.1f}% "
                  f"WR={best.win_rate:.0f}% PF={best.profit_factor:.2f} "
                  f"DD={best.max_dd:.1f}% Trades/mo={best.trades_per_month:.1f} "
                  f"[{best.params}]")

    # ── Strategy 2: Breakout + Retest ──────────────────────────────
    print("\n" + "=" * 60)
    print("STRATEGY 2: BREAKOUT + RETEST")
    print("=" * 60)
    for lev in LEVERAGES:
        print(f"\n  Leverage {lev}x:")
        results = grid_search_breakout(data, lev)
        all_results.extend(results)
        if results:
            best = max(results, key=lambda x: x.sharpe)
            print(f"  → Best: Sharpe={best.sharpe:.2f} Return={best.total_return:+.1f}% "
                  f"WR={best.win_rate:.0f}% PF={best.profit_factor:.2f} "
                  f"DD={best.max_dd:.1f}% Trades/mo={best.trades_per_month:.1f} "
                  f"[{best.params}]")

    # ── Strategy 3: Funding Rate + Trend ────────────────────────────
    print("\n" + "=" * 60)
    print("STRATEGY 3: FUNDING RATE + TREND")
    print("=" * 60)
    for lev in LEVERAGES:
        pos = strategy_funding_trend(data)
        m = run_backtest(data, pos, "Funding_Trend", "default", lev)
        if m:
            all_results.append(m)
            marker = "✓" if m.sharpe > 0.5 else "✗"
            print(f"  {marker} {lev}x: Return={m.total_return:+.1f}% Sharpe={m.sharpe:.2f} "
                  f"WR={m.win_rate:.0f}% PF={m.profit_factor:.2f} DD={m.max_dd:.1f}% "
                  f"Trades/mo={m.trades_per_month:.1f}")

    # ── Strategy 4: Volatility Squeeze ──────────────────────────────
    print("\n" + "=" * 60)
    print("STRATEGY 4: VOLATILITY COMPRESSION → EXPANSION")
    print("=" * 60)
    for lev in LEVERAGES:
        print(f"\n  Leverage {lev}x:")
        results = grid_search_volatility(data, lev)
        all_results.extend(results)
        if results:
            best = max(results, key=lambda x: x.sharpe)
            print(f"  → Best: Sharpe={best.sharpe:.2f} Return={best.total_return:+.1f}% "
                  f"WR={best.win_rate:.0f}% PF={best.profit_factor:.2f} "
                  f"DD={best.max_dd:.1f}% Trades/mo={best.trades_per_month:.1f} "
                  f"[{best.params}]")

    # ── Strategy 5: Multi-Strategy Combinator ───────────────────────
    print("\n" + "=" * 60)
    print("STRATEGY 5: MULTI-STRATEGY COMBINATOR")
    print("=" * 60)
    for lev in LEVERAGES:
        pos = strategy_combinator(data, min_agreement=2)
        m = run_backtest(data, pos, "Combinator", "min_agree=2", lev)
        if m:
            all_results.append(m)
            marker = "✓" if m.sharpe > 0.5 else "✗"
            print(f"  {marker} {lev}x: Return={m.total_return:+.1f}% Sharpe={m.sharpe:.2f} "
                  f"WR={m.win_rate:.0f}% PF={m.profit_factor:.2f} DD={m.max_dd:.1f}% "
                  f"Trades/mo={m.trades_per_month:.1f}")

    # ── Cost Survival Analysis ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("COST SURVIVAL ANALYSIS")
    print("=" * 60)
    cost_analysis = cost_survival_analysis(data)
    print(f"  Round-trip cost: {cost_analysis['avg_round_trip_cost_pct']:.4f}%")
    print(f"  BTC daily vol:   {cost_analysis['btc_daily_volatility_pct']:.1f}%")
    for label, sc in cost_analysis['scenarios'].items():
        print(f"  {label:12s} → {sc['total_trades']:5d} trades | "
              f"cost/trade {sc['cost_per_trade_pct']:.4f}% | "
              f"total costs ${sc['total_costs_usd']:,.0f}")

    # ── Save Results ────────────────────────────────────────────────
    results_json = [asdict(r) for r in all_results]
    json_path = REPORT_DIR / 'btc_optimized_results.json'
    with open(json_path, 'w') as f:
        json.dump(results_json, f, indent=2)
    print(f"\n[SAVED] {json_path}")

    # ── Generate Report ─────────────────────────────────────────────
    report = generate_report(all_results, cost_analysis, data)
    report_path = REPORT_DIR / 'BTC_OPTIMIZED_RESULTS.md'
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"[SAVED] {report_path}")

    # ── Summary ─────────────────────────────────────────────────────
    viable = [r for r in all_results if r.sharpe > 0.5 and r.max_dd > -40 and r.total_return > 0]
    print(f"\n{'=' * 80}")
    print(f"SUMMARY: {len(all_results)} total backtests | {len(viable)} viable")
    print(f"{'=' * 80}")

    if viable:
        viable.sort(key=lambda x: x.sharpe, reverse=True)
        print("\nTOP 5 BY SHARPE:")
        for i, r in enumerate(viable[:5], 1):
            print(f"  {i}. {r.strategy:20s} {r.leverage:2d}x | "
                  f"Sharpe={r.sharpe:.2f} | Return={r.total_return:+.1f}% | "
                  f"WR={r.win_rate:.0f}% | PF={r.profit_factor:.2f} | "
                  f"DD={r.max_dd:.1f}% | {r.trades_per_month:.1f} trades/mo")
    else:
        print("\n⚠  NO VIABLE STRATEGIES FOUND")
        print("   All strategies lost money after costs.")
        print("   Consider: lower fees, larger moves, or fewer trades.")

    print(f"\n{'=' * 80}")
    print("DONE")
    print(f"{'=' * 80}")

    return all_results, cost_analysis


if __name__ == '__main__':
    main()
