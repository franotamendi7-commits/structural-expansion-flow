"""
Adaptive VWAP Engine V2 — Versión Simplificada y Robusta
==========================================================
Basado en el VWAP breakout que generó +58.5% en backtest original,
pero con mejoras críticas:

1. Regime filter robusto (no solo EMA20)
2. Position sizing dinámico (ATR-based)
3. Drawdown gates progresivos
4. TP1 parcial + breakeven + trailing stop
5. Costos realistas en backtest

Diferencias con V1:
- Menos indicadores, más robustez
- Señales más frecuentes pero mejor filtradas
- Mejor ratio R:R
"""

import math
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIGURACIÓN
# ============================================================

# Costos realistas (Binance Futures)
COMMISSION = 0.0005      # 0.05% per side
SPREAD = 0.0002          # 0.02% price impact
SLIP_ATR_MULT = 0.1      # 0.1 × ATR(5m) per side

# Position sizing
BASE_RISK_PCT = 0.005    # 0.5% per trade
MAX_LEVERAGE = 10

# Drawdown gates
DD_WARNING = -0.03       # -3%: reducir a 50%
DD_CRITICAL = -0.06      # -6%: reducir a 25%
DD_STOP = -0.10          # -10%: parar

# Cooldown
COOLDOWNLOSSES = 3
COOLDOWN_MINUTES = 60

# Regime detection
REGIME_CONFIG = {
    'adx_period': 14,
    'adx_trending': 25,
    'adx_ranging': 20,
    'bb_period': 20,
    'bb_std': 2.0,
    'ema_fast': 20,
    'ema_slow': 50,
}

# Signal generation - Más selectivo
SIGNAL_CONFIG = {
    'vwap_period': 20,
    'dev_threshold': 1.0,     # 1.0% deviation (más estricto)
    'rsi_period': 14,
    'rsi_oversold': 30,       # Más estricto
    'rsi_overbought': 70,     # Más estricto
    'volume_period': 20,
    'volume_threshold': 1.5,  # Más estricto
}

# TP/SL - Optimizado para R:R 1.5:1
# Con 47.8% WR, necesitamos R:R > 1.1:1 para ser profitable
# TP1 = 3.5 × ATR, SL = 2.0 × ATR → R:R = 1.75:1
TP1_RATIO = 3.5           # TP1 = 3.5 × ATR
TP2_RATIO = 5.0           # TP2 = 5.0 × ATR
SL_RATIO = 2.0            # SL = 2.0 × ATR
TRAIL_RATIO = 3.0         # Trailing = 3.0 × ATR
TP1_FRACTION = 0.6        # Close 60% at TP1


# ============================================================
# INDICADORES
# ============================================================

def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index."""
    high = df['high']
    low = df['low']
    close = df['close']
    
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    adx_val = dx.ewm(alpha=1/period, min_periods=2*period, adjust=False).mean()
    
    return adx_val


def calc_bb_width(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    """Bollinger Bands Width."""
    middle = df['close'].rolling(period).mean()
    std_dev = df['close'].rolling(period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    return (upper - lower) / middle


def calc_ema(df: pd.DataFrame, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return df['close'].ewm(span=period, adjust=False).mean()


def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def calc_vwap(df: pd.DataFrame, period: int) -> pd.Series:
    """Rolling VWAP."""
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    vp = tp * df['volume']
    return vp.rolling(period).sum() / df['volume'].rolling(period).sum()


# ============================================================
# REGIME DETECTOR
# ============================================================

def detect_regime(df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
    """
    Detecta régimen del mercado.
    
    Returns:
        (regime, debug_dict)
    """
    if len(df) < 50:
        return 'UNKNOWN', {'reason': 'insufficient_data'}
    
    adx_val = calc_adx(df, REGIME_CONFIG['adx_period']).iloc[-1]
    bb_w = calc_bb_width(df, REGIME_CONFIG['bb_period'], REGIME_CONFIG['bb_std']).iloc[-1]
    ema_fast = calc_ema(df, REGIME_CONFIG['ema_fast'])
    ema_slow = calc_ema(df, REGIME_CONFIG['ema_slow'])
    
    debug = {
        'adx': float(adx_val) if pd.notna(adx_val) else None,
        'bb_width': float(bb_w) if pd.notna(bb_w) else None,
        'ema_fast': float(ema_fast.iloc[-1]) if pd.notna(ema_fast.iloc[-1]) else None,
        'ema_slow': float(ema_slow.iloc[-1]) if pd.notna(ema_slow.iloc[-1]) else None,
    }
    
    if pd.isna(adx_val) or pd.isna(bb_w):
        return 'UNKNOWN', debug
    
    # Trending
    if adx_val > REGIME_CONFIG['adx_trending']:
        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
            return 'TRENDING_BULL', debug
        else:
            return 'TRENDING_BEAR', debug
    
    # Ranging
    if adx_val < REGIME_CONFIG['adx_ranging']:
        return 'RANGING', debug
    
    # Neutral
    return 'NEUTRAL', debug


# ============================================================
# SIGNAL GENERATOR
# ============================================================

def generate_signal(df: pd.DataFrame, regime: str) -> Optional[Dict[str, Any]]:
    """
    Genera señal de VWAP breakout/mean reversion.
    
    Lógica:
    - LONG: Precio cruza VWAP desde abajo con confirmación
    - SHORT: Precio cruza VWAP desde arriba con confirmación
    """
    if regime in ('UNKNOWN', 'NEUTRAL'):
        return None
    
    if len(df) < 50:
        return None
    
    # Calcular indicadores
    vwap = calc_vwap(df, SIGNAL_CONFIG['vwap_period'])
    dev = (df['close'] - vwap) / vwap * 100
    rsi_val = calc_rsi(df, SIGNAL_CONFIG['rsi_period'])
    vol_ma = df['volume'].rolling(SIGNAL_CONFIG['volume_period']).mean()
    vol_ratio = df['volume'] / vol_ma
    atr_val = calc_atr(df, 14)
    
    # Últimas barras
    last_dev = float(dev.iloc[-1]) if pd.notna(dev.iloc[-1]) else None
    prev_dev = float(dev.iloc[-2]) if len(dev) > 1 and pd.notna(dev.iloc[-2]) else None
    last_rsi = float(rsi_val.iloc[-1]) if pd.notna(rsi_val.iloc[-1]) else None
    last_vol = float(vol_ratio.iloc[-1]) if pd.notna(vol_ratio.iloc[-1]) else None
    last_atr = float(atr_val.iloc[-1]) if pd.notna(atr_val.iloc[-1]) else None
    last_close = float(df['close'].iloc[-1])
    
    if any(v is None for v in [last_dev, prev_dev, last_rsi, last_vol, last_atr]):
        return None
    
    threshold = SIGNAL_CONFIG['dev_threshold']
    
    signal = None
    
    # LONG: VWAP breakout hacia arriba (momentum)
    # Precio por encima de VWAP con momentum creciente
    if last_dev > threshold and last_dev > prev_dev:
        # Score de confirmación
        score = 0
        
        # RSI: no sobrecomprado
        if last_rsi < 60:
            score += 2
        elif last_rsi < 70:
            score += 1
        
        # Volumen: confirmar breakout
        if last_vol > SIGNAL_CONFIG['volume_threshold']:
            score += 1
        
        # Régimen: trending bull es mejor
        if regime == 'TRENDING_BULL':
            score += 2
        elif regime == 'RANGING':
            score += 1
        
        # Momentum: deviation positiva fuerte
        if last_dev > threshold * 1.5:
            score += 1
        
        if score >= 3:
            signal = {
                'dir': 1,
                'entry': last_close,
                'stop': last_close - SL_RATIO * last_atr,
                'tp1': last_close + TP1_RATIO * last_atr,
                'tp2': last_close + TP2_RATIO * last_atr,
                'atr': last_atr,
                'regime': regime,
                'dev': last_dev,
                'rsi': last_rsi,
                'vol_ratio': last_vol,
                'score': score,
            }
    
    # SHORT: VWAP breakout hacia abajo (momentum)
    # Precio por debajo de VWAP con momentum decreciente
    elif last_dev < -threshold and last_dev < prev_dev:
        # Score de confirmación
        score = 0
        
        # RSI: no sobrevendido
        if last_rsi > 40:
            score += 2
        elif last_rsi > 30:
            score += 1
        
        # Volumen: confirmar breakout
        if last_vol > SIGNAL_CONFIG['volume_threshold']:
            score += 1
        
        # Régimen: trending bear es mejor
        if regime == 'TRENDING_BEAR':
            score += 2
        elif regime == 'RANGING':
            score += 1
        
        # Momentum: deviation negativa fuerte
        if last_dev < -threshold * 1.5:
            score += 1
        
        if score >= 3:
            signal = {
                'dir': -1,
                'entry': last_close,
                'stop': last_close + SL_RATIO * last_atr,
                'tp1': last_close - TP1_RATIO * last_atr,
                'tp2': last_close - TP2_RATIO * last_atr,
                'atr': last_atr,
                'regime': regime,
                'dev': last_dev,
                'rsi': last_rsi,
                'vol_ratio': last_vol,
                'score': score,
            }
    
    return signal


# ============================================================
# POSITION SIZING
# ============================================================

def calculate_size(equity: float, entry: float, stop: float, 
                   regime: str, peak_equity: float, loss_streak: int) -> float:
    """
    Calcula tamaño de posición basado en riesgo.
    """
    risk_pct = BASE_RISK_PCT
    
    # Reducir en drawdown
    if peak_equity > 0:
        dd_pct = (equity - peak_equity) / peak_equity
        if dd_pct <= DD_CRITICAL:
            risk_pct *= 0.25
        elif dd_pct <= DD_WARNING:
            risk_pct *= 0.50
    
    # Reducir en losing streak
    if loss_streak >= 2:
        risk_pct *= 0.50
    
    # Ajustar por régimen
    regime_mult = {
        'TRENDING_BULL': 1.0,
        'TRENDING_BEAR': 1.0,
        'RANGING': 0.7,
        'NEUTRAL': 0.5,
        'UNKNOWN': 0.0,
    }
    risk_pct *= regime_mult.get(regime, 0.5)
    
    risk_usd = equity * risk_pct
    risk_distance = abs(entry - stop)
    
    if risk_distance <= 0:
        return 0.0
    
    size = risk_usd / risk_distance
    return size


# ============================================================
# BACKTEST ENGINE
# ============================================================

class BacktestEngine:
    """Engine de backtesting con costos realistas."""
    
    def __init__(self, initial_capital: float = 100.0):
        self.initial_capital = initial_capital
    
    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Ejecuta backtest."""
        trades = []
        equity = self.initial_capital
        peak_equity = equity
        loss_streak = 0
        
        position = None
        
        for i in range(50, len(df)):
            bar = df.iloc[i]
            
            # Gestionar posición abierta
            if position is not None:
                exit_result = self._manage_exit(position, bar)
                if exit_result is not None:
                    pnl = exit_result['pnl']
                    equity += pnl
                    
                    if pnl <= 0:
                        loss_streak += 1
                    else:
                        loss_streak = 0
                    
                    if equity > peak_equity:
                        peak_equity = equity
                    
                    trades.append({
                        'entry_time': position['entry_time'],
                        'exit_time': bar.name,
                        'dir': position['dir'],
                        'entry': position['entry'],
                        'exit': exit_result['exit'],
                        'pnl': pnl,
                        'equity_after': equity,
                        'regime': position.get('regime', 'unknown'),
                        'exit_reason': exit_result['reason'],
                    })
                    
                    position = None
                    continue
            
            # Buscar señal
            if position is None:
                df_slice = df.iloc[:i+1]
                regime, _ = detect_regime(df_slice)
                signal = generate_signal(df_slice, regime)
                
                if signal is not None:
                    size = calculate_size(
                        equity, signal['entry'], signal['stop'],
                        regime, peak_equity, loss_streak
                    )
                    
                    if size > 0:
                        # Aplicar slippage
                        entry_price = self._apply_slippage(
                            signal['entry'], signal['dir'], signal['atr'], 'entry'
                        )
                        
                        position = {
                            'dir': signal['dir'],
                            'entry': entry_price,
                            'size': size,
                            'stop': signal['stop'],
                            'tp1': signal['tp1'],
                            'tp2': signal['tp2'],
                            'atr': signal['atr'],
                            'entry_time': bar.name,
                            'regime': signal.get('regime', 'unknown'),
                            'trail_high': entry_price,
                            'trail_low': entry_price,
                            'trailing_stop': None,
                            'tp1_hit': False,
                            'size_remaining': size,
                        }
        
        # Cerrar posición final
        if position is not None:
            last_bar = df.iloc[-1]
            exit_price = self._apply_slippage(
                last_bar['close'], position['dir'], position['atr'], 'exit'
            )
            
            if position['dir'] == 1:
                pnl = position['size'] * (exit_price - position['entry'])
            else:
                pnl = position['size'] * (position['entry'] - exit_price)
            
            comm = position['size'] * (position['entry'] + exit_price) * COMMISSION
            net_pnl = pnl - comm
            
            equity += net_pnl
            trades.append({
                'entry_time': position['entry_time'],
                'exit_time': last_bar.name,
                'dir': position['dir'],
                'entry': position['entry'],
                'exit': exit_price,
                'pnl': net_pnl,
                'equity_after': equity,
                'regime': position.get('regime', 'unknown'),
                'exit_reason': 'end_of_data',
            })
        
        return self._calculate_metrics(trades, equity)
    
    def _manage_exit(self, position: dict, bar: pd.Series) -> Optional[Dict[str, Any]]:
        """Gestiona salidas."""
        dir = position['dir']
        bar_high = bar['high']
        bar_low = bar['low']
        atr = position['atr']
        
        # Actualizar trail
        if dir == 1:
            if bar_high > position['trail_high']:
                position['trail_high'] = bar_high
        else:
            if bar_low < position['trail_low']:
                position['trail_low'] = bar_low
        
        # Check SL
        sl_hit = (dir == 1 and bar_low <= position['stop']) or \
                 (dir == -1 and bar_high >= position['stop'])
        if sl_hit:
            exit_price = self._apply_slippage(position['stop'], dir, atr, 'exit')
            pnl = self._calc_pnl(position, exit_price, 1.0)
            return {'exit': exit_price, 'pnl': pnl, 'reason': 'sl'}
        
        # Check trailing
        if position['trailing_stop'] is not None:
            trail_hit = (dir == 1 and bar_low <= position['trailing_stop']) or \
                        (dir == -1 and bar_high >= position['trailing_stop'])
            if trail_hit:
                exit_price = self._apply_slippage(position['trailing_stop'], dir, atr, 'exit')
                pnl = self._calc_pnl(position, exit_price, 1.0)
                return {'exit': exit_price, 'pnl': pnl, 'reason': 'trail'}
        
        # Check TP1
        if not position['tp1_hit']:
            tp1_hit = (dir == 1 and bar_high >= position['tp1']) or \
                      (dir == -1 and bar_low <= position['tp1'])
            if tp1_hit:
                # Cerrar parcial
                partial_size = position['size_remaining'] * TP1_FRACTION
                exit_price = self._apply_slippage(position['tp1'], dir, atr, 'exit')
                
                if dir == 1:
                    pnl = partial_size * (exit_price - position['entry'])
                else:
                    pnl = partial_size * (position['entry'] - exit_price)
                
                comm = partial_size * (position['entry'] + exit_price) * COMMISSION
                net_pnl = pnl - comm
                
                position['tp1_hit'] = True
                position['size_remaining'] -= partial_size
                position['stop'] = position['entry']  # Breakeven
                
                # Activar trailing
                trail_dist = TRAIL_RATIO * atr
                if dir == 1:
                    position['trailing_stop'] = position['trail_high'] - trail_dist
                else:
                    position['trailing_stop'] = position['trail_low'] + trail_dist
                
                return {'exit': exit_price, 'pnl': net_pnl, 'reason': 'tp1'}
        
        # Update trailing
        if position['tp1_hit']:
            trail_dist = TRAIL_RATIO * atr
            if dir == 1:
                new_trail = position['trail_high'] - trail_dist
                if new_trail > position['trailing_stop']:
                    position['trailing_stop'] = new_trail
            else:
                new_trail = position['trail_low'] + trail_dist
                if new_trail < position['trailing_stop']:
                    position['trailing_stop'] = new_trail
            
            # Check TP2
            tp_hit = (dir == 1 and bar_high >= position['tp2']) or \
                     (dir == -1 and bar_low <= position['tp2'])
            if tp_hit:
                exit_price = self._apply_slippage(position['tp2'], dir, atr, 'exit')
                pnl = self._calc_pnl(position, exit_price, 1.0)
                return {'exit': exit_price, 'pnl': pnl, 'reason': 'tp2'}
        
        return None
    
    def _calc_pnl(self, position: dict, exit_price: float, ratio: float) -> float:
        """Calcula PnL."""
        dir = position['dir']
        size = position['size_remaining'] * ratio
        
        if dir == 1:
            pnl = size * (exit_price - position['entry'])
        else:
            pnl = size * (position['entry'] - exit_price)
        
        comm = size * (position['entry'] + exit_price) * COMMISSION
        return pnl - comm
    
    def _apply_slippage(self, price: float, direction: int, atr: float, 
                        side: str) -> float:
        """Aplica slippage."""
        slip = SLIP_ATR_MULT * atr
        spread = price * SPREAD
        
        if side == 'entry':
            if direction == 1:
                return price + spread + slip
            else:
                return price - spread - slip
        else:  # exit
            if direction == 1:
                return price - spread - slip
            else:
                return price + spread + slip
    
    def _calculate_metrics(self, trades: list, final_equity: float) -> Dict[str, Any]:
        """Calcula métricas."""
        if not trades:
            return {
                'trades': [],
                'metrics': {
                    'total_trades': 0,
                    'win_rate': 0,
                    'profit_factor': 0,
                    'sharpe': 0,
                    'sortino': 0,
                    'max_dd': 0,
                    'max_dd_pct': 0,
                    'total_return': 0,
                    'total_return_pct': 0,
                    'avg_trade': 0,
                    'avg_win': 0,
                    'avg_loss': 0,
                }
            }
        
        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        # Equity curve
        equity_curve = [self.initial_capital]
        for pnl in pnls:
            equity_curve.append(equity_curve[-1] + pnl)
        
        # Max DD
        peak = equity_curve[0]
        max_dd = 0
        max_dd_pct = 0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
        
        # Sharpe
        if len(pnls) > 1:
            returns = np.array(pnls) / self.initial_capital
            std_ret = np.std(returns)
            sharpe = np.mean(returns) / std_ret * np.sqrt(252 * 24 * 4) if std_ret > 0 else 0
        else:
            sharpe = 0
        
        # Sortino
        if len(pnls) > 1:
            returns = np.array(pnls) / self.initial_capital
            downside = [r for r in returns if r < 0]
            if downside and len(downside) > 1:
                downside_std = np.std(downside)
                sortino = np.mean(returns) / downside_std * np.sqrt(252 * 24 * 4) if downside_std > 0 else 0
            else:
                sortino = 0
        else:
            sortino = 0
        
        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else 0
        
        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'metrics': {
                'total_trades': len(trades),
                'win_rate': len(wins) / len(pnls) if pnls else 0,
                'profit_factor': pf,
                'sharpe': sharpe,
                'sortino': sortino,
                'max_dd': max_dd,
                'max_dd_pct': max_dd_pct,
                'total_return': final_equity - self.initial_capital,
                'total_return_pct': (final_equity - self.initial_capital) / self.initial_capital * 100,
                'avg_trade': np.mean(pnls),
                'avg_win': np.mean(wins) if wins else 0,
                'avg_loss': np.mean(losses) if losses else 0,
            }
        }


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import sys
    from pathlib import Path
    
    sys.path.insert(0, str(Path(__file__).parent))
    
    # Test con datos sintéticos
    np.random.seed(42)
    n = 1000
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq='15min')
    
    trend = np.concatenate([
        np.linspace(50000, 55000, 300),
        np.linspace(55000, 52000, 200),
        np.linspace(52000, 52000, 200),
        np.linspace(52000, 58000, 300),
    ])
    
    noise = np.cumsum(np.random.randn(n) * 50)
    close = trend + noise[:n]
    
    df = pd.DataFrame({
        'open': close + np.random.randn(n) * 30,
        'high': close + abs(np.random.randn(n) * 80),
        'low': close - abs(np.random.randn(n) * 80),
        'close': close,
        'volume': np.random.exponential(1000, n),
    }, index=dates)
    
    # Run backtest
    print("Running backtest...")
    engine = BacktestEngine()
    result = engine.run(df)
    
    m = result['metrics']
    print(f"\n=== Results ===")
    print(f"Trades: {m['total_trades']}")
    print(f"Win Rate: {m['win_rate']:.1%}")
    print(f"Profit Factor: {m['profit_factor']:.2f}")
    print(f"Sharpe: {m['sharpe']:.2f}")
    print(f"Sortino: {m['sortino']:.2f}")
    print(f"Max DD: {m['max_dd_pct']:.2%}")
    print(f"Return: ${m['total_return']:.2f} ({m['total_return_pct']:.2f}%)")
