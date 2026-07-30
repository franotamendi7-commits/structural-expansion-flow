#!/usr/bin/env python3
"""
BTC Active Trading Strategy Backtester
Tests multiple strategies on BTCUSDT with REALISTIC costs:
- Taker fee: 0.04% per side (0.08% round trip)
- Spread: 0.02% average
- Funding rate: +0.005% per 8h (conservative)
- Slippage: 0.01% per trade
"""

import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import requests
import time

# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/data/BTCUSDT')
REPORT_DIR = Path('/Users/franciscootamendi/ai-agents-v3/research/reports')
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# Realistic costs
TAKER_FEE = 0.0004          # 0.04% per side
SPREAD = 0.0002             # 0.02% average
FUNDING_RATE = 0.00005      # 0.005% per 8h (longs pay)
SLIPPAGE = 0.0001           # 0.01% per trade

INITIAL_CAPITAL = 10000.0

# Leverage levels to test
LEVERAGES = [1, 2, 3, 5, 7, 10]


def download_btc_1h():
    """Download BTCUSDT 1h data from Binance Futures"""
    print("Downloading BTCUSDT 1h data from Binance...")
    
    all_klines = []
    start_ms = int(datetime(2023, 1, 1).timestamp() * 1000)
    end_ms = int(datetime(2026, 7, 28).timestamp() * 1000)
    current_start = start_ms
    
    while current_start < end_ms:
        params = {
            'symbol': 'BTCUSDT',
            'interval': '1h',
            'startTime': current_start,
            'endTime': end_ms,
            'limit': 1500
        }
        try:
            resp = requests.get('https://fapi.binance.com/fapi/v1/klines', params=params, timeout=30)
            resp.raise_for_status()
            klines = resp.json()
            if not klines:
                break
            all_klines.extend(klines)
            current_start = klines[-1][0] + 3600 * 1000
            time.sleep(0.1)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(1)
            continue
    
    if not all_klines:
        return None
    
    df = pd.DataFrame(all_klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume']:
        df[col] = pd.to_numeric(df[col])
    
    df.set_index('open_time', inplace=True)
    df = df[['open', 'high', 'low', 'close', 'volume', 'quote_volume']]
    df = df[~df.index.duplicated(keep='first')].sort_index()
    
    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    filepath = DATA_DIR / 'BTCUSDT_1h_full.parquet'
    df.to_parquet(filepath, compression='snappy')
    print(f"Saved: {filepath} ({len(df)} rows)")
    return df


def load_btc_data():
    """Load or download BTC data"""
    filepath = DATA_DIR / 'BTCUSDT_1h_full.parquet'
    if filepath.exists():
        df = pd.read_parquet(filepath)
        print(f"Loaded: {filepath} ({len(df)} rows)")
        return df
    return download_btc_1h()


def calculate_costs(position_size_usd, holding_hours=8):
    """Calculate total trading costs for one round trip"""
    taker = position_size_usd * TAKER_FEE * 2  # entry + exit
    spread_cost = position_size_usd * SPREAD
    slippage_cost = position_size_usd * SLIPPAGE
    
    # Funding: paid every 8h while holding
    funding_periods = max(1, int(holding_hours / 8))
    funding_cost = position_size_usd * FUNDING_RATE * funding_periods
    
    total = taker + spread_cost + slippage_cost + funding_cost
    return {
        'taker': taker,
        'spread': spread_cost,
        'slippage': slippage_cost,
        'funding': funding_cost,
        'total': total,
        'pct': total / position_size_usd
    }


# ============================================================
# STRATEGY 1: SCALPING — RSI + Bollinger Band Touch
# ============================================================

def strategy_scalping(data, rsi_period=14, bb_period=20, bb_std=2.0):
    """
    Scalping: Buy when price touches lower BB + RSI < 30, sell when touches upper BB + RSI > 70.
    Quick in/out, targeting 0.2-0.5% moves.
    """
    close = data['close']
    high = data['high']
    low = data['low']
    
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # Bollinger Bands
    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    upper_bb = sma + bb_std * std
    lower_bb = sma - bb_std * std
    
    # Signals
    position = pd.Series(0.0, index=data.index)
    
    in_position = False
    entry_price = 0
    entry_idx = 0
    
    for i in range(bb_period + 1, len(data)):
        if not in_position:
            # Entry: price touches lower BB + RSI < 30 (oversold)
            if close.iloc[i] <= lower_bb.iloc[i] and rsi.iloc[i] < 30:
                in_position = True
                entry_price = close.iloc[i]
                entry_idx = i
                position.iloc[i] = 1
            # Short entry: price touches upper BB + RSI > 70 (overbought)
            elif close.iloc[i] >= upper_bb.iloc[i] and rsi.iloc[i] > 70:
                in_position = True
                entry_price = close.iloc[i]
                entry_idx = i
                position.iloc[i] = -1
        else:
            # Exit conditions
            hold_bars = i - entry_idx
            current_pnl_pct = (close.iloc[i] / entry_price - 1) * (1 if position.iloc[entry_idx] > 0 else -1)
            
            # Take profit at 0.3% or stop loss at 0.2% or max hold 12 bars
            if current_pnl_pct > 0.003 or current_pnl_pct < -0.002 or hold_bars > 12:
                in_position = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = position.iloc[entry_idx]
    
    return position


# ============================================================
# STRATEGY 2: GRID TRADING — Automated buy/sell levels
# ============================================================

def strategy_grid(data, grid_pct=0.5, num_levels=10):
    """
    Grid trading: Place buy/sell orders at fixed intervals.
    Buy at each level down, sell at each level up.
    """
    close = data['close']
    position = pd.Series(0.0, index=data.index)
    
    # Calculate grid levels based on recent price
    lookback = 48  # 48 hours
    current_grid_center = close.iloc[lookback:].mean()
    grid_step = current_grid_center * grid_pct / 100
    
    grid_levels = []
    for i in range(-num_levels, num_levels + 1):
        grid_levels.append(current_grid_center + i * grid_step)
    
    in_position = 0  # -num_levels to +num_levels
    entry_price = current_grid_center
    
    for i in range(lookback, len(data)):
        price = close.iloc[i]
        
        # Calculate which grid level we're at
        level = int((price - current_grid_center) / grid_step)
        level = max(-num_levels, min(num_levels, level))
        
        if level < in_position:
            # Price dropped — buy (increase position)
            in_position = level
            position.iloc[i] = in_position / num_levels
        elif level > in_position:
            # Price rose — sell (decrease position)
            in_position = level
            position.iloc[i] = in_position / num_levels
        else:
            position.iloc[i] = position.iloc[i-1] if i > 0 else 0
    
    return position


# ============================================================
# STRATEGY 3: MOMENTUM — EMA Cross + Volume Confirmation
# ============================================================

def strategy_momentum(data, fast=8, slow=21, vol_mult=1.5):
    """
    Momentum: Buy when fast EMA crosses above slow EMA with volume spike.
    Sell when fast crosses below slow.
    """
    close = data['close']
    volume = data['volume']
    
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    avg_vol = volume.rolling(20).mean()
    
    position = pd.Series(0.0, index=data.index)
    
    # Signal: EMA cross + volume confirmation
    long_signal = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1)) & (volume > vol_mult * avg_vol)
    short_signal = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1)) & (volume > vol_mult * avg_vol)
    
    # Exit: opposite cross or stop loss
    position = pd.Series(0.0, index=data.index)
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(1, len(data)):
        if not in_trade:
            if long_signal.iloc[i]:
                in_trade = True
                entry_price = close.iloc[i]
                direction = 1
            elif short_signal.iloc[i]:
                in_trade = True
                entry_price = close.iloc[i]
                direction = -1
        else:
            # Check exit
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            hold_bars = i - (i - 1)  # simplified
            
            # Exit on: opposite signal, stop loss -1%, take profit +2%, or max hold 24 bars
            if ((direction == 1 and short_signal.iloc[i]) or
                (direction == -1 and long_signal.iloc[i]) or
                pnl_pct < -0.01 or pnl_pct > 0.02):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# STRATEGY 4: MEAN REVERSION — RSI Extreme + Support/Resistance
# ============================================================

def strategy_mean_reversion(data, rsi_period=14, rsi_buy=25, rsi_sell=75):
    """
    Mean reversion: Buy extreme oversold, sell extreme overbought.
    Uses ATR for stop loss and take profit.
    """
    close = data['close']
    high = data['high']
    low = data['low']
    
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # ATR for stops
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    
    position = pd.Series(0.0, index=data.index)
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(30, len(data)):
        if not in_trade:
            # Entry: RSI extreme + price near Bollinger Band
            if rsi.iloc[i] < rsi_buy:
                in_trade = True
                entry_price = close.iloc[i]
                direction = 1
            elif rsi.iloc[i] > rsi_sell:
                in_trade = True
                entry_price = close.iloc[i]
                direction = -1
        else:
            # Exit: RSI returns to 50, or stop loss at 1.5 ATR, or take profit at 1 ATR
            atr_val = atr.iloc[i]
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            
            if (abs(rsi.iloc[i] - 50) < 5 or
                pnl_pct < -(1.5 * atr_val / close.iloc[i]) or
                pnl_pct > (1.0 * atr_val / close.iloc[i])):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# STRATEGY 5: BREAKOUT — Range Break + Volume
# ============================================================

def strategy_breakout(data, lookback=24, vol_mult=2.0):
    """
    Breakout: Identify range, trade breakout with volume confirmation.
    """
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    
    position = pd.Series(0.0, index=data.index)
    avg_vol = volume.rolling(20).mean()
    
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(lookback + 1, len(data)):
        # Calculate range
        range_high = high.iloc[i-lookback:i].max()
        range_low = low.iloc[i-lookback:i].min()
        range_size = (range_high - range_low) / close.iloc[i]
        
        if not in_trade:
            # Breakout up: close above range high + volume spike
            if close.iloc[i] > range_high and volume.iloc[i] > vol_mult * avg_vol.iloc[i]:
                in_trade = True
                entry_price = close.iloc[i]
                direction = 1
            # Breakout down
            elif close.iloc[i] < range_low and volume.iloc[i] > vol_mult * avg_vol.iloc[i]:
                in_trade = True
                entry_price = close.iloc[i]
                direction = -1
        else:
            # Exit: re-enter range, stop loss, or take profit
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            
            # Stop at 1% or TP at 1.5% or exit if price re-enters range
            if (pnl_pct < -0.01 or pnl_pct > 0.015 or
                (direction == 1 and close.iloc[i] < range_low) or
                (direction == -1 and close.iloc[i] > range_high)):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# STRATEGY 6: VWAP + RSI — Institutional Flow
# ============================================================

def strategy_vwap_rsi(data, rsi_period=14, vwap_period=24):
    """
    VWAP + RSI: Trade when price deviates from VWAP with RSI confirmation.
    """
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    
    # VWAP (rolling)
    typical_price = (high + low + close) / 3
    vwap = (typical_price * volume).rolling(vwap_period).sum() / volume.rolling(vwap_period).sum()
    
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    position = pd.Series(0.0, index=data.index)
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(max(vwap_period, rsi_period) + 1, len(data)):
        if not in_trade:
            # Buy: price below VWAP + RSI < 35 (oversold relative to VWAP)
            if close.iloc[i] < vwap.iloc[i] * 0.998 and rsi.iloc[i] < 35:
                in_trade = True
                entry_price = close.iloc[i]
                direction = 1
            # Sell: price above VWAP + RSI > 65
            elif close.iloc[i] > vwap.iloc[i] * 1.002 and rsi.iloc[i] > 65:
                in_trade = True
                entry_price = close.iloc[i]
                direction = -1
        else:
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            
            # Exit: back to VWAP, stop loss -0.8%, take profit +1.2%
            if (abs(close.iloc[i] - vwap.iloc[i]) / vwap.iloc[i] < 0.001 or
                pnl_pct < -0.008 or pnl_pct > 0.012):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# STRATEGY 7: VOLATILITY BREAKOUT — ATR Expansion
# ============================================================

def strategy_volatility(data, atr_period=14, atr_mult=1.5):
    """
    Volatility: Trade breakouts when ATR expands significantly.
    """
    close = data['close']
    high = data['high']
    low = data['low']
    
    # ATR
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    atr_ma = atr.rolling(50).mean()
    
    position = pd.Series(0.0, index=data.index)
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(60, len(data)):
        if not in_trade:
            # ATR expansion: current ATR > 1.5x average ATR
            if atr.iloc[i] > atr_mult * atr_ma.iloc[i]:
                # Direction based on recent momentum
                momentum = close.iloc[i] / close.iloc[i-5] - 1
                if momentum > 0.005:  # Bullish momentum
                    in_trade = True
                    entry_price = close.iloc[i]
                    direction = 1
                elif momentum < -0.005:  # Bearish momentum
                    in_trade = True
                    entry_price = close.iloc[i]
                    direction = -1
        else:
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            
            # Exit: ATR contracts, stop loss, or take profit
            if (atr.iloc[i] < atr_ma.iloc[i] or
                pnl_pct < -0.012 or pnl_pct > 0.018):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# STRATEGY 8: MULTI-TF — 4h Trend + 1h Entry
# ============================================================

def strategy_multitf(data, trend_ema=50, entry_rsi=14):
    """
    Multi-timeframe: Use EMA trend for direction, RSI for entry timing.
    """
    close = data['close']
    
    # Trend EMA (simulating higher TF)
    ema_trend = close.ewm(span=trend_ema, adjust=False).mean()
    
    # RSI for entry
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(entry_rsi).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(entry_rsi).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    position = pd.Series(0.0, index=data.index)
    in_trade = False
    entry_price = 0
    direction = 0
    
    for i in range(trend_ema + 1, len(data)):
        if not in_trade:
            # Long: uptrend + RSI pullback
            if close.iloc[i] > ema_trend.iloc[i] and rsi.iloc[i] < 40:
                in_trade = True
                entry_price = close.iloc[i]
                direction = 1
            # Short: downtrend + RSI bounce
            elif close.iloc[i] < ema_trend.iloc[i] and rsi.iloc[i] > 60:
                in_trade = True
                entry_price = close.iloc[i]
                direction = -1
        else:
            pnl_pct = (close.iloc[i] / entry_price - 1) * direction
            
            # Exit: trend reversal, stop loss, take profit
            if ((direction == 1 and close.iloc[i] < ema_trend.iloc[i]) or
                (direction == -1 and close.iloc[i] > ema_trend.iloc[i]) or
                pnl_pct < -0.015 or pnl_pct > 0.025):
                in_trade = False
                position.iloc[i] = 0
            else:
                position.iloc[i] = direction
    
    return position


# ============================================================
# BACKTEST ENGINE
# ============================================================

def backtest_strategy(data, positions, strategy_name, leverage, capital=INITIAL_CAPITAL):
    """Run backtest with realistic costs"""
    
    close = data['close'].copy()
    returns = close.pct_change().fillna(0)
    
    # Strategy returns with leverage
    strat_returns = positions.shift(1).fillna(0) * returns * leverage
    
    # Calculate costs for each trade
    trade_changes = positions.diff().fillna(0)
    trade_costs = pd.Series(0.0, index=data.index)
    
    in_trade = False
    entry_idx = 0
    
    for i in range(1, len(data)):
        if positions.iloc[i] != 0 and not in_trade:
            in_trade = True
            entry_idx = i
        elif in_trade and (positions.iloc[i] == 0 or i == len(data) - 1):
            in_trade = False
            hold_hours = (i - entry_idx)
            pos_size = abs(positions.iloc[entry_idx]) * capital * leverage
            costs = calculate_costs(pos_size, hold_hours)
            trade_costs.iloc[i] = costs['total'] / capital
    
    # Net returns
    net_returns = strat_returns - trade_costs
    
    # Equity curve
    equity = capital * (1 + net_returns).cumprod()
    
    # Metrics
    total_return = (equity.iloc[-1] / capital - 1) * 100
    
    n_days = (data.index[-1] - data.index[0]).total_seconds() / 86400
    annual_return = ((1 + total_return / 100) ** (365 / max(n_days, 1)) - 1) * 100
    
    if net_returns.std() != 0:
        sharpe = (net_returns.mean() / net_returns.std()) * np.sqrt(252 * 24)
    else:
        sharpe = 0
    
    rolling_max = equity.expanding().max()
    drawdown = equity / rolling_max - 1
    max_dd = drawdown.min() * 100
    
    # Count trades
    trade_returns = []
    in_trade = False
    trade_pnl = 0
    
    for i in range(len(positions)):
        if positions.iloc[i] != 0 and not in_trade:
            in_trade = True
            trade_pnl = 0
        elif in_trade:
            trade_pnl += net_returns.iloc[i]
            if positions.iloc[i] == 0 or i == len(positions) - 1:
                in_trade = False
                trade_returns.append(trade_pnl)
    
    if len(trade_returns) == 0:
        return None
    
    winning = sum(1 for r in trade_returns if r > 0)
    losing = sum(1 for r in trade_returns if r <= 0)
    win_rate = (winning / len(trade_returns)) * 100
    
    gross_profit = sum(r for r in trade_returns if r > 0)
    gross_loss = abs(sum(r for r in trade_returns if r <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # Break-even analysis
    avg_trade_return = np.mean(trade_returns) * 100
    total_cost_pct = trade_costs.sum() * 100
    avg_cost_per_trade = total_cost_pct / len(trade_returns) if len(trade_returns) > 0 else 0
    
    return {
        'strategy': strategy_name,
        'leverage': leverage,
        'total_return': round(total_return, 2),
        'annual_return': round(annual_return, 2),
        'sharpe': round(sharpe, 2),
        'max_dd': round(max_dd, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2),
        'total_trades': len(trade_returns),
        'trades_per_month': round(len(trade_returns) / (n_days / 30), 1),
        'avg_trade_return': round(avg_trade_return, 4),
        'avg_cost_per_trade': round(avg_cost_per_trade, 4),
        'total_costs': round(total_cost_pct, 2),
        'breakeven_win_rate': round(50 + (50 * avg_cost_per_trade / max(avg_trade_return, 0.001)), 1),
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("BTC ACTIVE TRADING STRATEGY BACKTESTER")
    print("=" * 80)
    print(f"Capital: ${INITIAL_CAPITAL:,.2f}")
    print(f"Costs: Taker {TAKER_FEE*100:.2f}% | Spread {SPREAD*100:.2f}% | Funding {FUNDING_RATE*100:.4f}%/8h | Slippage {SLIPPAGE*100:.2f}%")
    print(f"Leverages: {LEVERAGES}")
    print("=" * 80)
    
    # Load data
    data = load_btc_data()
    if data is None:
        print("ERROR: No data available")
        return
    
    print(f"\nData: {data.index[0]} to {data.index[-1]} ({len(data)} bars)")
    print(f"Price range: ${data['close'].min():.2f} - ${data['close'].max():.2f}")
    
    # Define strategies
    strategies = {
        'Scalping_RSI_BB': strategy_scalping,
        'Grid_Trading': strategy_grid,
        'Momentum_EMA': strategy_momentum,
        'Mean_Reversion_RSI': strategy_mean_reversion,
        'Breakout_Range': strategy_breakout,
        'VWAP_RSI': strategy_vwap_rsi,
        'Volatility_ATR': strategy_volatility,
        'MultiTF_Trend': strategy_multitf,
    }
    
    all_results = []
    
    for strat_name, strat_func in strategies.items():
        print(f"\n{'='*60}")
        print(f"Strategy: {strat_name}")
        print(f"{'='*60}")
        
        try:
            positions = strat_func(data)
            trade_count = (positions.diff().fillna(0) != 0).sum()
            print(f"  Raw signals: {trade_count} position changes")
            
            for leverage in LEVERAGES:
                result = backtest_strategy(data, positions, strat_name, leverage)
                if result:
                    all_results.append(result)
                    
                    marker = "✅" if result['sharpe'] > 1.0 and result['max_dd'] > -30 else "❌"
                    print(f"  {marker} {leverage}x: Return={result['total_return']:+.1f}% Sharpe={result['sharpe']:.2f} "
                          f"WR={result['win_rate']:.0f}% PF={result['profit_factor']:.2f} "
                          f"DD={result['max_dd']:.1f}% Trades/mo={result['trades_per_month']:.1f} "
                          f"Costs={result['total_costs']:.2f}%")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    # Save all results
    results_path = REPORT_DIR / 'btc_active_trading_results.json'
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    # Print rankings
    print("\n" + "=" * 80)
    print("RANKINGS")
    print("=" * 80)
    
    # Filter viable
    viable = [r for r in all_results if r['sharpe'] > 0.5 and r['max_dd'] > -30 and r['total_return'] > 0]
    viable.sort(key=lambda x: x['sharpe'], reverse=True)
    
    print(f"\nViable strategies: {len(viable)} / {len(all_results)}")
    
    print("\nTOP 10 BY SHARPE:")
    print("-" * 100)
    for i, r in enumerate(viable[:10], 1):
        print(f"{i:2d}. {r['strategy']:20s} {r['leverage']:2d}x | "
              f"Return={r['total_return']:+8.1f}% | Sharpe={r['sharpe']:6.2f} | "
              f"WR={r['win_rate']:5.1f}% | PF={r['profit_factor']:5.2f} | "
              f"DD={r['max_dd']:6.1f}% | Trades/mo={r['trades_per_month']:5.1f} | "
              f"Costs={r['total_costs']:5.2f}%")
    
    print("\nTOP 10 BY RETURN:")
    print("-" * 100)
    by_return = sorted(viable, key=lambda x: x['total_return'], reverse=True)
    for i, r in enumerate(by_return[:10], 1):
        print(f"{i:2d}. {r['strategy']:20s} {r['leverage']:2d}x | "
              f"Return={r['total_return']:+8.1f}% | Sharpe={r['sharpe']:6.2f} | "
              f"WR={r['win_rate']:5.1f}% | DD={r['max_dd']:6.1f}%")
    
    print("\nTOP 10 BY PROFIT FACTOR:")
    print("-" * 100)
    by_pf = sorted(viable, key=lambda x: x['profit_factor'], reverse=True)
    for i, r in enumerate(by_pf[:10], 1):
        print(f"{i:2d}. {r['strategy']:20s} {r['leverage']:2d}x | "
              f"PF={r['profit_factor']:6.2f} | Return={r['total_return']:+8.1f}% | "
              f"Sharpe={r['sharpe']:6.2f} | WR={r['win_rate']:5.1f}%")
    
    # Break-even analysis
    print("\n" + "=" * 80)
    print("BREAK-EVEN ANALYSIS")
    print("=" * 80)
    for r in viable[:5]:
        print(f"\n{r['strategy']} @ {r['leverage']}x:")
        print(f"  Avg return per trade: {r['avg_trade_return']:.4f}%")
        print(f"  Avg cost per trade:   {r['avg_cost_per_trade']:.4f}%")
        print(f"  Net per trade:        {r['avg_trade_return'] - r['avg_cost_per_trade']:.4f}%")
        print(f"  Required WR to profit: {r['breakeven_win_rate']:.1f}%")
    
    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == '__main__':
    main()
