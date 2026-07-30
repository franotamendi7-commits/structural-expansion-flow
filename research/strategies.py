#!/usr/bin/env python3
"""
Trading Strategies Implementation
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple
from abc import ABC, abstractmethod


class PositionSide(Enum):
    LONG = 1
    SHORT = -1
    FLAT = 0


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: PositionSide
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    net_pnl: float
    commission: float
    slippage: float
    duration_hours: float
    return_pct: float


@dataclass
class BacktestResult:
    strategy_name: str
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return: float
    annual_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    avg_trade_duration_hours: float
    trades_per_year: float
    trades: List[Trade]
    equity_curve: pd.Series
    monthly_returns: pd.Series
    
    def to_dict(self):
        return {
            'strategy_name': self.strategy_name,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'initial_capital': self.initial_capital,
            'final_capital': self.final_capital,
            'total_return': self.total_return,
            'annual_return': self.annual_return,
            'sharpe_ratio': self.sharpe_ratio,
            'sortino_ratio': self.sortino_ratio,
            'max_drawdown': self.max_drawdown,
            'max_drawdown_pct': self.max_drawdown_pct,
            'win_rate': self.win_rate,
            'profit_factor': self.profit_factor,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'avg_win': self.avg_win,
            'avg_loss': self.avg_loss,
            'avg_trade_duration_hours': self.avg_trade_duration_hours,
            'trades_per_year': self.trades_per_year,
        }
    
    def to_markdown(self):
        md = f"""# {self.strategy_name} - {self.symbol} ({self.timeframe})

## Period
{self.start_date} to {self.end_date}

## Performance Metrics (After Costs)

| Metric | Value |
|--------|-------|
| Initial Capital | ${self.initial_capital:,.2f} |
| Final Capital | ${self.final_capital:,.2f} |
| Total Return | {self.total_return:.2f}% |
| Annual Return | {self.annual_return:.2f}% |
| Sharpe Ratio | {self.sharpe_ratio:.2f} |
| Sortino Ratio | {self.sortino_ratio:.2f} |
| Max Drawdown | ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.2f}%) |
| Win Rate | {self.win_rate:.1f}% |
| Profit Factor | {self.profit_factor:.2f} |
| Total Trades | {self.total_trades} |
| Winning Trades | {self.winning_trades} |
| Losing Trades | {self.losing_trades} |
| Avg Win | ${self.avg_win:.2f} |
| Avg Loss | ${self.avg_loss:.2f} |
| Avg Trade Duration | {self.avg_trade_duration_hours:.1f} hours |
| Trades/Year | {self.trades_per_year:.1f} |

## Monthly Returns
"""
        if len(self.monthly_returns) > 0:
            md += "| Month | Return |\n|-------|--------|\n"
            for idx, val in self.monthly_returns.items():
                md += f"| {idx.strftime('%Y-%m')} | {val:.2f}% |\n"
        
        return md


class Strategy(ABC):
    """Base strategy class"""
    
    def __init__(self, name: str, params: dict = None):
        self.name = name
        self.params = params or {}
    
    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """Generate trading signals: 1=long, -1=short, 0=flat"""
        pass
    
    def get_params_str(self) -> str:
        return ", ".join([f"{k}={v}" for k, v in self.params.items()])


# ============================================================
# MOMENTUM STRATEGIES
# ============================================================

class EMACrossover(Strategy):
    """EMA Crossover Strategy"""
    
    def __init__(self, fast_period: int = 5, slow_period: int = 20):
        super().__init__("EMA_Crossover", {"fast": fast_period, "slow": slow_period})
        self.fast_period = fast_period
        self.slow_period = slow_period
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        ema_fast = close.ewm(span=self.fast_period, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow_period, adjust=False).mean()
        
        signals = pd.Series(0, index=data.index)
        signals[ema_fast > ema_slow] = 1
        signals[ema_fast < ema_slow] = -1
        
        # Only change position on crossover
        signals = signals.diff().fillna(0)
        signals[signals > 0] = 1
        signals[signals < 0] = -1
        
        # Convert to position (1=long, -1=short, 0=flat)
        position = signals.cumsum().clip(-1, 1)
        return position


class MACDStrategy(Strategy):
    """MACD Crossover Strategy"""
    
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        super().__init__("MACD", {"fast": fast, "slow": slow, "signal": signal})
        self.fast = fast
        self.slow = slow
        self.signal = signal
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        
        ema_fast = close.ewm(span=self.fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=self.signal, adjust=False).mean()
        histogram = macd - signal_line
        
        signals = pd.Series(0, index=data.index)
        signals[histogram > 0] = 1
        signals[histogram < 0] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class RSIMomentum(Strategy):
    """RSI Momentum Strategy"""
    
    def __init__(self, period: int = 14, overbought: int = 70, oversold: int = 30):
        super().__init__("RSI_Momentum", {"period": period, "overbought": overbought, "oversold": oversold})
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        signals = pd.Series(0, index=data.index)
        signals[rsi < self.oversold] = 1
        signals[rsi > self.overbought] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class RateOfChange(Strategy):
    """Rate of Change Momentum"""
    
    def __init__(self, period: int = 20, threshold: float = 0.02):
        super().__init__("Rate_of_Change", {"period": period, "threshold": threshold})
        self.period = period
        self.threshold = threshold
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        roc = close.pct_change(self.period)
        
        signals = pd.Series(0, index=data.index)
        signals[roc > self.threshold] = 1
        signals[roc < -self.threshold] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


# ============================================================
# MEAN REVERSION STRATEGIES
# ============================================================

class BollingerBandsMeanReversion(Strategy):
    """Bollinger Bands Mean Reversion"""
    
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        super().__init__("Bollinger_Bands_MR", {"period": period, "std_dev": std_dev})
        self.period = period
        self.std_dev = std_dev
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        
        sma = close.rolling(window=self.period).mean()
        std = close.rolling(window=self.period).std()
        upper = sma + self.std_dev * std
        lower = sma - self.std_dev * std
        
        signals = pd.Series(0, index=data.index)
        signals[close < lower] = 1  # Buy at lower band
        signals[close > upper] = -1  # Sell at upper band
        
        # Exit at middle band
        signals[(close > sma) & (signals.shift(1) == 1)] = 0
        signals[(close < sma) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class RSIMEanReversion(Strategy):
    """RSI Mean Reversion (extreme levels)"""
    
    def __init__(self, period: int = 14, overbought: int = 80, oversold: int = 20):
        super().__init__("RSI_Mean_Reversion", {"period": period, "overbought": overbought, "oversold": oversold})
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        signals = pd.Series(0, index=data.index)
        signals[rsi < self.oversold] = 1
        signals[rsi > self.overbought] = -1
        
        # Exit at 50
        signals[(rsi > 50) & (signals.shift(1) == 1)] = 0
        signals[(rsi < 50) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class ZScoreMeanReversion(Strategy):
    """Z-Score Mean Reversion"""
    
    def __init__(self, period: int = 20, entry_z: float = 2.0, exit_z: float = 0.5):
        super().__init__("ZScore_Mean_Reversion", {"period": period, "entry_z": entry_z, "exit_z": exit_z})
        self.period = period
        self.entry_z = entry_z
        self.exit_z = exit_z
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        
        mean = close.rolling(window=self.period).mean()
        std = close.rolling(window=self.period).std()
        zscore = (close - mean) / std
        
        signals = pd.Series(0, index=data.index)
        signals[zscore < -self.entry_z] = 1
        signals[zscore > self.entry_z] = -1
        
        # Exit at exit_z
        signals[(zscore > -self.exit_z) & (signals.shift(1) == 1)] = 0
        signals[(zscore < self.exit_z) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


# ============================================================
# BREAKOUT STRATEGIES
# ============================================================

class DonchianChannelBreakout(Strategy):
    """Donchian Channel Breakout"""
    
    def __init__(self, period: int = 20, exit_period: int = 10):
        super().__init__("Donchian_Breakout", {"period": period, "exit_period": exit_period})
        self.period = period
        self.exit_period = exit_period
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        
        upper = high.rolling(window=self.period).max()
        lower = low.rolling(window=self.period).min()
        exit_upper = high.rolling(window=self.exit_period).max()
        exit_lower = low.rolling(window=self.exit_period).min()
        
        signals = pd.Series(0, index=data.index)
        signals[close > upper.shift(1)] = 1
        signals[close < lower.shift(1)] = -1
        
        # Exit signals
        signals[(close < exit_lower.shift(1)) & (signals.shift(1) == 1)] = 0
        signals[(close > exit_upper.shift(1)) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class KeltnerChannelBreakout(Strategy):
    """Keltner Channel Breakout"""
    
    def __init__(self, period: int = 20, atr_period: int = 10, multiplier: float = 2.0):
        super().__init__("Keltner_Breakout", {"period": period, "atr_period": atr_period, "multiplier": multiplier})
        self.period = period
        self.atr_period = atr_period
        self.multiplier = multiplier
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        
        # ATR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.atr_period).mean()
        
        # Keltner Channels
        ema = close.ewm(span=self.period, adjust=False).mean()
        upper = ema + self.multiplier * atr
        lower = ema - self.multiplier * atr
        
        signals = pd.Series(0, index=data.index)
        signals[close > upper.shift(1)] = 1
        signals[close < lower.shift(1)] = -1
        
        # Exit at EMA
        signals[(close < ema.shift(1)) & (signals.shift(1) == 1)] = 0
        signals[(close > ema.shift(1)) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class VolumeBreakout(Strategy):
    """Volume Breakout Strategy"""
    
    def __init__(self, period: int = 20, volume_mult: float = 2.0):
        super().__init__("Volume_Breakout", {"period": period, "volume_mult": volume_mult})
        self.period = period
        self.volume_mult = volume_mult
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        volume = data['volume']
        
        # Price breakout
        high = data['high'].rolling(window=self.period).max()
        low = data['low'].rolling(window=self.period).min()
        
        # Volume breakout
        avg_volume = volume.rolling(window=self.period).mean()
        volume_spike = volume > self.volume_mult * avg_volume
        
        signals = pd.Series(0, index=data.index)
        signals[(close > high.shift(1)) & volume_spike] = 1
        signals[(close < low.shift(1)) & volume_spike] = -1
        
        # Exit at opposite breakout
        signals[(close < low.shift(1)) & (signals.shift(1) == 1)] = 0
        signals[(close > high.shift(1)) & (signals.shift(1) == -1)] = 0
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


# ============================================================
# TREND FOLLOWING STRATEGIES
# ============================================================

class Supertrend(Strategy):
    """Supertrend Strategy"""
    
    def __init__(self, period: int = 10, multiplier: float = 3.0):
        super().__init__("Supertrend", {"period": period, "multiplier": multiplier})
        self.period = period
        self.multiplier = multiplier
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        
        # ATR
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=self.period).mean()
        
        # Supertrend calculation
        hl2 = (high + low) / 2
        upper_band = hl2 + self.multiplier * atr
        lower_band = hl2 - self.multiplier * atr
        
        supertrend = pd.Series(index=data.index, dtype=float)
        direction = pd.Series(index=data.index, dtype=int)
        
        for i in range(len(data)):
            if i == 0:
                supertrend.iloc[i] = lower_band.iloc[i]
                direction.iloc[i] = 1
            else:
                if close.iloc[i] > supertrend.iloc[i-1]:
                    direction.iloc[i] = 1
                elif close.iloc[i] < supertrend.iloc[i-1]:
                    direction.iloc[i] = -1
                else:
                    direction.iloc[i] = direction.iloc[i-1]
                
                if direction.iloc[i] == 1:
                    supertrend.iloc[i] = max(lower_band.iloc[i], supertrend.iloc[i-1])
                else:
                    supertrend.iloc[i] = min(upper_band.iloc[i], supertrend.iloc[i-1])
        
        signals = pd.Series(0, index=data.index)
        signals[direction == 1] = 1
        signals[direction == -1] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class IchimokuCloud(Strategy):
    """Ichimoku Cloud Strategy"""
    
    def __init__(self, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
        super().__init__("Ichimoku_Cloud", {"tenkan": tenkan, "kijun": kijun, "senkou_b": senkou_b})
        self.tenkan = tenkan
        self.kijun = kijun
        self.senkou_b = senkou_b
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        
        # Tenkan-sen (Conversion Line)
        tenkan_high = high.rolling(window=self.tenkan).max()
        tenkan_low = low.rolling(window=self.tenkan).min()
        tenkan = (tenkan_high + tenkan_low) / 2
        
        # Kijun-sen (Base Line)
        kijun_high = high.rolling(window=self.kijun).max()
        kijun_low = low.rolling(window=self.kijun).min()
        kijun = (kijun_high + kijun_low) / 2
        
        # Senkou Span A (Leading Span A)
        senkou_a = ((tenkan + kijun) / 2).shift(self.kijun)
        
        # Senkou Span B (Leading Span B)
        senkou_b_high = high.rolling(window=self.senkou_b).max()
        senkou_b_low = low.rolling(window=self.senkou_b).min()
        senkou_b = ((senkou_b_high + senkou_b_low) / 2).shift(self.kijun)
        
        # Chikou Span (Lagging Span)
        chikou = close.shift(-self.kijun)
        
        # Signals: Price above cloud = long, below cloud = short
        cloud_top = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
        cloud_bottom = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)
        
        signals = pd.Series(0, index=data.index)
        signals[(close > cloud_top) & (tenkan > kijun)] = 1
        signals[(close < cloud_bottom) & (tenkan < kijun)] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


class ParabolicSAR(Strategy):
    """Parabolic SAR Strategy"""
    
    def __init__(self, af_start: float = 0.02, af_increment: float = 0.02, af_max: float = 0.2):
        super().__init__("Parabolic_SAR", {"af_start": af_start, "af_increment": af_increment, "af_max": af_max})
        self.af_start = af_start
        self.af_increment = af_increment
        self.af_max = af_max
    
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        
        # Initialize
        sar = pd.Series(index=data.index, dtype=float)
        ep = pd.Series(index=data.index, dtype=float)  # Extreme Point
        af = pd.Series(index=data.index, dtype=float)
        trend = pd.Series(index=data.index, dtype=int)  # 1 = up, -1 = down
        
        # Initial values
        sar.iloc[0] = low.iloc[0]
        ep.iloc[0] = high.iloc[0]
        af.iloc[0] = self.af_start
        trend.iloc[0] = 1
        
        for i in range(1, len(data)):
            if trend.iloc[i-1] == 1:  # Uptrend
                sar.iloc[i] = sar.iloc[i-1] + af.iloc[i-1] * (ep.iloc[i-1] - sar.iloc[i-1])
                sar.iloc[i] = min(sar.iloc[i], low.iloc[i-1], low.iloc[i-2] if i > 1 else low.iloc[i-1])
                
                if low.iloc[i] < sar.iloc[i]:
                    trend.iloc[i] = -1
                    sar.iloc[i] = ep.iloc[i-1]
                    ep.iloc[i] = low.iloc[i]
                    af.iloc[i] = self.af_start
                else:
                    trend.iloc[i] = 1
                    if high.iloc[i] > ep.iloc[i-1]:
                        ep.iloc[i] = high.iloc[i]
                        af.iloc[i] = min(af.iloc[i-1] + self.af_increment, self.af_max)
                    else:
                        ep.iloc[i] = ep.iloc[i-1]
                        af.iloc[i] = af.iloc[i-1]
            else:  # Downtrend
                sar.iloc[i] = sar.iloc[i-1] - af.iloc[i-1] * (sar.iloc[i-1] - ep.iloc[i-1])
                sar.iloc[i] = max(sar.iloc[i], high.iloc[i-1], high.iloc[i-2] if i > 1 else high.iloc[i-1])
                
                if high.iloc[i] > sar.iloc[i]:
                    trend.iloc[i] = 1
                    sar.iloc[i] = ep.iloc[i-1]
                    ep.iloc[i] = high.iloc[i]
                    af.iloc[i] = self.af_start
                else:
                    trend.iloc[i] = -1
                    if low.iloc[i] < ep.iloc[i-1]:
                        ep.iloc[i] = low.iloc[i]
                        af.iloc[i] = min(af.iloc[i-1] + self.af_increment, self.af_max)
                    else:
                        ep.iloc[i] = ep.iloc[i-1]
                        af.iloc[i] = af.iloc[i-1]
        
        signals = pd.Series(0, index=data.index)
        signals[trend == 1] = 1
        signals[trend == -1] = -1
        
        position = signals.diff().fillna(0)
        position[position > 0] = 1
        position[position < 0] = -1
        position = position.cumsum().clip(-1, 1)
        
        return position


# ============================================================
# STRATEGY REGISTRY
# ============================================================

def get_all_strategies() -> List[Strategy]:
    """Get all strategy instances with default parameters"""
    return [
        # Momentum
        EMACrossover(5, 20),
        EMACrossover(10, 50),
        EMACrossover(20, 100),
        MACDStrategy(12, 26, 9),
        RSIMomentum(14, 70, 30),
        RateOfChange(20, 0.02),
        
        # Mean Reversion
        BollingerBandsMeanReversion(20, 2.0),
        RSIMEanReversion(14, 80, 20),
        ZScoreMeanReversion(20, 2.0, 0.5),
        
        # Breakout
        DonchianChannelBreakout(20, 10),
        KeltnerChannelBreakout(20, 10, 2.0),
        VolumeBreakout(20, 2.0),
        
        # Trend Following
        Supertrend(10, 3.0),
        IchimokuCloud(9, 26, 52),
        ParabolicSAR(0.02, 0.02, 0.2),
    ]


def get_strategy_by_name(name: str) -> Strategy:
    """Get strategy by name"""
    strategies = {s.name: s for s in get_all_strategies()}
    return strategies.get(name)


if __name__ == '__main__':
    # Test strategies
    strategies = get_all_strategies()
    print(f"Total strategies: {len(strategies)}")
    for s in strategies:
        print(f"  {s.name}: {s.get_params_str()}")
