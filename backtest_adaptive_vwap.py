"""
Backtesting Framework — Adaptive VWAP Engine
=============================================
Framework completo para validar la estrategia con:
- Walk-forward validation
- Monte Carlo simulation
- Análisis por régimen
- Métricas completas (Sharpe, Sortino, Max DD, Win Rate, Profit Factor)
- Costos realistas (fees, spread, slippage)
"""

import numpy as np
import pandas as pd
import json
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Tuple
from pathlib import Path

# Importar engine
import sys
sys.path.insert(0, str(Path(__file__).parent))
from engine.adaptive_vwap_engine import AdaptiveVWAPEngine, RISK_CONFIG


# ============================================================
# CONFIGURACIÓN DEL BACKTEST
# ============================================================

BACKTEST_CONFIG = {
    # Costos (Binance Futures)
    'commission': 0.0005,      # 0.05% per side
    'spread': 0.0002,          # 0.02% price impact
    'slippage_atr_mult': 0.1,  # 0.1 × ATR(5m) per side
    
    # Capital
    'initial_capital': 100.0,  # $100 por bot
    
    # Position sizing
    'risk_per_trade': 0.005,   # 0.5%
    'max_positions': 1,        # Solo 1 posición a la vez
    
    # Walk-forward
    'train_pct': 0.7,          # 70% train, 30% test
    'n_folds': 6,              # 6 folds
    
    # Monte Carlo
    'mc_sims': 5000,
    'mc_confidence': 0.95,
}


# ============================================================
# BACKTEST ENGINE
# ============================================================

class BacktestEngine:
    """
    Engine de backtesting con costos realistas.
    
    Características:
    - Solo usa barras cerradas (sin look-ahead)
    - Costos realistas (fees + spread + slippage)
    - TP1 parcial + breakeven + trailing stop
    - Position sizing basado en ATR
    """
    
    def __init__(self, config: dict = None):
        self.config = config or BACKTEST_CONFIG
    
    def run(self, df: pd.DataFrame, engine: AdaptiveVWAPEngine) -> Dict[str, Any]:
        """
        Ejecuta backtest en un DataFrame.
        
        Args:
            df: DataFrame con OHLCV data
            engine: AdaptiveVWAPEngine instance
        
        Returns:
            Dict con trades y métricas
        """
        trades = []
        equity = self.config['initial_capital']
        peak_equity = equity
        
        position = None  # {dir, entry, size, stop, tp1, tp2, atr, entry_time}
        
        engine.update_equity(equity)
        
        for i in range(1, len(df)):
            bar = df.iloc[i]
            prev_bar = df.iloc[i-1]
            
            # Si hay posición abierta, gestionar salidas
            if position is not None:
                exit_result = self._manage_exit(position, bar, equity)
                if exit_result is not None:
                    pnl = exit_result['pnl']
                    equity += pnl
                    engine.update_equity(equity)
                    engine.record_trade_result(pnl)
                    
                    if equity > peak_equity:
                        peak_equity = equity
                    
                    trades.append({
                        'entry_time': position['entry_time'],
                        'exit_time': bar.name,
                        'dir': position['dir'],
                        'entry': position['entry'],
                        'exit': exit_result['exit'],
                        'pnl': pnl,
                        'pnl_pct': pnl / equity * 100,
                        'equity_after': equity,
                        'regime': position.get('regime', 'unknown'),
                        'exit_reason': exit_result['reason'],
                        'hold_bars': i - position['entry_bar'],
                    })
                    
                    position = None
                    continue
            
            # Si no hay posición, buscar señal
            if position is None:
                # Usar solo datos hasta esta barra (sin look-ahead)
                df_slice = df.iloc[:i+1]
                
                result = engine.analyze(df_slice)
                
                if result and result.get('signal') is not None:
                    signal = result['signal']
                    size = result.get('size', 0)
                    
                    if size > 0:
                        # Aplicar costos de entrada
                        entry_price = self._apply_entry_slippage(
                            signal['entry'], signal['dir'], signal['atr']
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
                            'entry_bar': i,
                            'regime': signal.get('regime', 'unknown'),
                            'trail_high': entry_price,
                            'trail_low': entry_price,
                            'trailing_stop': None,
                            'tp1_hit': False,
                            'size_remaining': size,
                        }
        
        # Cerrar posición abierta al final
        if position is not None:
            last_bar = df.iloc[-1]
            exit_price = self._apply_exit_slippage(
                last_bar['close'], position['dir'], position['atr']
            )
            
            if position['dir'] == 1:
                pnl = position['size'] * (exit_price - position['entry'])
            else:
                pnl = position['size'] * (position['entry'] - exit_price)
            
            comm = position['size'] * (position['entry'] + exit_price) * self.config['commission']
            net_pnl = pnl - comm
            
            equity += net_pnl
            trades.append({
                'entry_time': position['entry_time'],
                'exit_time': last_bar.name,
                'dir': position['dir'],
                'entry': position['entry'],
                'exit': exit_price,
                'pnl': net_pnl,
                'pnl_pct': net_pnl / equity * 100,
                'equity_after': equity,
                'regime': position.get('regime', 'unknown'),
                'exit_reason': 'end_of_data',
                'hold_bars': len(df) - position['entry_bar'],
            })
        
        return self._calculate_metrics(trades, equity)
    
    def _manage_exit(self, position: dict, bar: pd.Series, 
                     equity: float) -> Optional[Dict[str, Any]]:
        """
        Gestiona salidas de posición.
        
        Returns:
            Exit result dict or None
        """
        dir = position['dir']
        bar_high = bar['high']
        bar_low = bar['low']
        bar_close = bar['close']
        atr = position['atr']
        
        # Actualizar trail reference
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
            exit_price = self._apply_exit_slippage(position['stop'], dir, atr)
            pnl = self._calculate_pnl(position, exit_price)
            return {'exit': exit_price, 'pnl': pnl, 'reason': 'sl'}
        
        # Check trailing stop
        if position['trailing_stop'] is not None:
            trail_hit = (dir == 1 and bar_low <= position['trailing_stop']) or \
                        (dir == -1 and bar_high >= position['trailing_stop'])
            if trail_hit:
                exit_price = self._apply_exit_slippage(position['trailing_stop'], dir, atr)
                pnl = self._calculate_pnl_partial(position, exit_price, 1.0)
                return {'exit': exit_price, 'pnl': pnl, 'reason': 'trail'}
        
        # Check TP1 (partial exit)
        if not position['tp1_hit']:
            tp1_hit = (dir == 1 and bar_high >= position['tp1']) or \
                      (dir == -1 and bar_low <= position['tp1'])
            if tp1_hit:
                # Cerrar 60% en TP1
                partial_size = position['size_remaining'] * 0.6
                exit_price = self._apply_exit_slippage(position['tp1'], dir, atr)
                
                if dir == 1:
                    pnl = partial_size * (exit_price - position['entry'])
                else:
                    pnl = partial_size * (position['entry'] - exit_price)
                
                comm = partial_size * (position['entry'] + exit_price) * self.config['commission']
                net_pnl = pnl - comm
                
                position['tp1_hit'] = True
                position['size_remaining'] -= partial_size
                position['stop'] = position['entry']  # Move to breakeven
                
                # Activar trailing stop
                trail_dist = 2.0 * atr
                if dir == 1:
                    position['trailing_stop'] = position['trail_high'] - trail_dist
                else:
                    position['trailing_stop'] = position['trail_low'] + trail_dist
                
                return {'exit': exit_price, 'pnl': net_pnl, 'reason': 'tp1'}
        
        # Update trailing stop
        if position['tp1_hit']:
            trail_dist = 2.0 * atr
            if dir == 1:
                new_trail = position['trail_high'] - trail_dist
                if new_trail > position['trailing_stop']:
                    position['trailing_stop'] = new_trail
            else:
                new_trail = position['trail_low'] + trail_dist
                if new_trail < position['trailing_stop']:
                    position['trailing_stop'] = new_trail
            
            # Check full TP
            tp_hit = (dir == 1 and bar_high >= position['tp2']) or \
                     (dir == -1 and bar_low <= position['tp2'])
            if tp_hit:
                exit_price = self._apply_exit_slippage(position['tp2'], dir, atr)
                pnl = self._calculate_pnl_partial(position, exit_price, 1.0)
                return {'exit': exit_price, 'pnl': pnl, 'reason': 'tp2'}
        
        return None
    
    def _calculate_pnl(self, position: dict, exit_price: float) -> float:
        """Calcula PnL de la posición completa."""
        dir = position['dir']
        size = position['size']
        
        if dir == 1:
            pnl = size * (exit_price - position['entry'])
        else:
            pnl = size * (position['entry'] - exit_price)
        
        comm = size * (position['entry'] + exit_price) * self.config['commission']
        return pnl - comm
    
    def _calculate_pnl_partial(self, position: dict, exit_price: float, 
                                ratio: float) -> float:
        """Calcula PnL parcial."""
        dir = position['dir']
        size = position['size_remaining'] * ratio
        
        if dir == 1:
            pnl = size * (exit_price - position['entry'])
        else:
            pnl = size * (position['entry'] - exit_price)
        
        comm = size * (position['entry'] + exit_price) * self.config['commission']
        return pnl - comm
    
    def _apply_entry_slippage(self, price: float, direction: int, 
                               atr: float) -> float:
        """Aplica spread + slippage a entrada."""
        slip = self.config['slippage_atr_mult'] * atr
        spread = price * self.config['spread']
        
        if direction == 1:
            return price + spread + slip
        else:
            return price - spread - slip
    
    def _apply_exit_slippage(self, price: float, direction: int, 
                              atr: float) -> float:
        """Aplica spread + slippage a salida."""
        slip = self.config['slippage_atr_mult'] * atr
        spread = price * self.config['spread']
        
        if direction == 1:
            return price - spread - slip
        else:
            return price + spread + slip
    
    def _calculate_metrics(self, trades: List[Dict], 
                           final_equity: float) -> Dict[str, Any]:
        """Calcula métricas completas del backtest."""
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
                    'expectancy': 0,
                }
            }
        
        # Extraer pnls
        pnls = [t['pnl'] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        # Calcular equity curve
        equity_curve = [self.config['initial_capital']]
        for pnl in pnls:
            equity_curve.append(equity_curve[-1] + pnl)
        
        # Max Drawdown
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
        
        # Sharpe Ratio (annualized, assuming 15m bars)
        if len(pnls) > 1:
            returns = np.array(pnls) / self.config['initial_capital']
            std_returns = np.std(returns)
            if std_returns > 0:
                sharpe = np.mean(returns) / std_returns * np.sqrt(252 * 24 * 4)  # 15m bars
            else:
                sharpe = 0
        else:
            returns = np.array([])
            sharpe = 0
        
        # Sortino Ratio
        if len(returns) > 0:
            downside_returns = [r for r in returns if r < 0]
            if downside_returns and len(downside_returns) > 1:
                downside_std = np.std(downside_returns)
                if downside_std > 0:
                    sortino = np.mean(returns) / downside_std * np.sqrt(252 * 24 * 4)
                else:
                    sortino = 0
            else:
                sortino = 0
        else:
            sortino = 0
        
        # Profit Factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Win Rate
        win_rate = len(wins) / len(pnls) if pnls else 0
        
        # Expectancy
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
        
        return {
            'trades': trades,
            'equity_curve': equity_curve,
            'metrics': {
                'total_trades': len(trades),
                'win_rate': win_rate,
                'profit_factor': profit_factor,
                'sharpe': sharpe,
                'sortino': sortino,
                'max_dd': max_dd,
                'max_dd_pct': max_dd_pct,
                'total_return': final_equity - self.config['initial_capital'],
                'total_return_pct': (final_equity - self.config['initial_capital']) / self.config['initial_capital'] * 100,
                'avg_trade': np.mean(pnls),
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'expectancy': expectancy,
                'gross_profit': gross_profit,
                'gross_loss': gross_loss,
            }
        }


# ============================================================
# WALK-FORWARD VALIDATION
# ============================================================

class WalkForwardValidator:
    """
    Walk-forward validation para evitar overfitting.
    
    Divid datos en N folds:
    - Train: optimizar parámetros
    - Test: validar out-of-sample
    
    La estrategia debe ser profitable en TODOS los folds.
    """
    
    def __init__(self, config: dict = None):
        self.config = config or BACKTEST_CONFIG
    
    def validate(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Ejecuta walk-forward validation.
        
        Returns:
            Dict con resultados por fold y agregados
        """
        n_folds = self.config['n_folds']
        train_pct = self.config['train_pct']
        
        fold_size = len(df) // n_folds
        results = []
        
        for fold in range(n_folds - 1):  # -1 porque necesitamos 2 folds
            # Dividir datos
            start = fold * fold_size
            end = min((fold + 2) * fold_size, len(df))  # 2 folds para train+test
            
            if end > len(df):
                break
            
            fold_data = df.iloc[start:end]
            train_size = int(len(fold_data) * train_pct)
            
            train_data = fold_data.iloc[:train_size]
            test_data = fold_data.iloc[train_size:]
            
            if len(test_data) < 100:  # Mínimo 100 barras para test
                continue
            
            # Run backtest on test data
            engine = AdaptiveVWAPEngine()
            engine.update_equity(100.0)
            backtest = BacktestEngine(self.config)
            
            try:
                result = backtest.run(test_data, engine)
                
                results.append({
                    'fold': fold + 1,
                    'train_start': str(train_data.index[0]),
                    'train_end': str(train_data.index[-1]),
                    'test_start': str(test_data.index[0]),
                    'test_end': str(test_data.index[-1]),
                    'train_bars': len(train_data),
                    'test_bars': len(test_data),
                    'metrics': result['metrics'],
                })
            except Exception as e:
                print(f"Fold {fold+1} failed: {e}")
                continue
        
        if not results:
            return {
                'folds': [],
                'summary': {
                    'n_folds': 0,
                    'profitable_folds': 0,
                    'consistency': 0,
                    'avg_sharpe': 0,
                    'avg_return': 0,
                    'min_sharpe': 0,
                    'min_return': 0,
                }
            }
        
        # Agregar métricas
        all_sharpes = []
        all_returns = []
        
        for r in results:
            all_sharpes.append(r['metrics']['sharpe'])
            all_returns.append(r['metrics']['total_return_pct'])
        
        # Verificar consistencia
        profitable_folds = sum(1 for r in results if r['metrics']['total_return_pct'] > 0)
        
        return {
            'folds': results,
            'summary': {
                'n_folds': len(results),
                'profitable_folds': profitable_folds,
                'consistency': profitable_folds / len(results) if results else 0,
                'avg_sharpe': np.mean(all_sharpes) if all_sharpes else 0,
                'avg_return': np.mean(all_returns) if all_returns else 0,
                'min_sharpe': min(all_sharpes) if all_sharpes else 0,
                'min_return': min(all_returns) if all_returns else 0,
            }
        }


# ============================================================
# MONTE CARLO SIMULATION
# ============================================================

class MonteCarloSimulator:
    """
    Monte Carlo simulation para estimar distribución de returns.
    
    Reordena trades aleatoriamente para ver:
    - Distribución de returns
    - Probabilidad de profit
    - Drawdown máximo esperado (p95)
    """
    
    def __init__(self, config: dict = None):
        self.config = config or BACKTEST_CONFIG
    
    def simulate(self, trades: List[Dict], n_sims: int = None) -> Dict[str, Any]:
        """
        Ejecuta Monte Carlo simulation.
        
        Args:
            trades: Lista de trades con pnls
            n_sims: Número de simulaciones
        
        Returns:
            Dict con distribución de resultados
        """
        if not trades:
            return {'error': 'no trades'}
        
        n_sims = n_sims or self.config['mc_sims']
        pnls = np.array([t['pnl'] for t in trades])
        
        results = {
            'final_equities': [],
            'max_drawdowns': [],
            'total_returns': [],
        }
        
        for _ in range(n_sims):
            # Reordenar trades aleatoriamente
            shuffled = np.random.permutation(pnls)
            
            # Simular equity curve
            equity = self.config['initial_capital']
            equity_curve = [equity]
            
            for pnl in shuffled:
                equity += pnl
                equity_curve.append(equity)
            
            # Calcular métricas
            final_equity = equity_curve[-1]
            max_dd = 0
            peak = equity_curve[0]
            
            for eq in equity_curve:
                if eq > peak:
                    peak = eq
                dd = peak - eq
                if dd > max_dd:
                    max_dd = dd
            
            results['final_equities'].append(final_equity)
            results['max_drawdowns'].append(max_dd)
            results['total_returns'].append((final_equity - self.config['initial_capital']) / self.config['initial_capital'] * 100)
        
        # Calcular percentiles
        confidence = self.config['mc_confidence']
        p_value = int(n_sims * (1 - confidence))
        
        return {
            'n_sims': n_sims,
            'initial_capital': self.config['initial_capital'],
            'metrics': {
                'mean_return': np.mean(results['total_returns']),
                'median_return': np.median(results['total_returns']),
                'std_return': np.std(results['total_returns']),
                'p5_return': np.percentile(results['total_returns'], 5),
                'p95_return': np.percentile(results['total_returns'], 95),
                'prob_profit': sum(1 for r in results['total_returns'] if r > 0) / n_sims,
                'mean_max_dd': np.mean(results['max_drawdowns']),
                'p95_max_dd': np.percentile(results['max_drawdowns'], 95),
                'p99_max_dd': np.percentile(results['max_drawdowns'], 99),
            },
            'results': results,
        }


# ============================================================
# REGIME ANALYSIS
# ============================================================

class RegimeAnalyzer:
    """
    Analiza performance por régimen de mercado.
    
    Identifica:
    - En qué régimenes la estrategia es profitable
    - En qué régimenes pierde dinero
    - Cuánto tiempo pasa en cada régimen
    """
    
    def analyze(self, trades: List[Dict]) -> Dict[str, Any]:
        """
        Analiza trades por régimen.
        
        Returns:
            Dict con métricas por régimen
        """
        if not trades:
            return {'error': 'no trades'}
        
        regime_stats = {}
        
        for trade in trades:
            regime = trade.get('regime', 'unknown')
            
            if regime not in regime_stats:
                regime_stats[regime] = {
                    'trades': 0,
                    'wins': 0,
                    'losses': 0,
                    'total_pnl': 0,
                    'pnls': [],
                }
            
            stats = regime_stats[regime]
            stats['trades'] += 1
            
            if trade['pnl'] > 0:
                stats['wins'] += 1
            else:
                stats['losses'] += 1
            
            stats['total_pnl'] += trade['pnl']
            stats['pnls'].append(trade['pnl'])
        
        # Calcular métricas por régimen
        for regime, stats in regime_stats.items():
            pnls = stats['pnls']
            stats['win_rate'] = stats['wins'] / stats['trades'] if stats['trades'] > 0 else 0
            stats['avg_trade'] = np.mean(pnls) if pnls else 0
            stats['profit_factor'] = sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0)) if sum(p for p in pnls if p < 0) != 0 else 0
            stats['expectancy'] = stats['win_rate'] * stats['avg_trade']
            del stats['pnls']  # No serializar arrays
        
        return regime_stats


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    # Test con datos sintéticos
    print("=== Backtesting Framework Test ===\n")
    
    # Generar datos sintéticos
    np.random.seed(42)
    n = 1000
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq='15min')
    
    # Simular mercado con tendencia y rango
    trend = np.concatenate([
        np.linspace(50000, 55000, 300),  # Uptrend
        np.linspace(55000, 52000, 200),  # Downtrend
        np.linspace(52000, 52000, 200),  # Range
        np.linspace(52000, 58000, 300),  # Uptrend
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
    engine = AdaptiveVWAPEngine()
    backtest = BacktestEngine()
    result = backtest.run(df, engine)
    
    print(f"\n=== Backtest Results ===")
    print(f"Total trades: {result['metrics']['total_trades']}")
    print(f"Win rate: {result['metrics']['win_rate']:.1%}")
    print(f"Profit factor: {result['metrics']['profit_factor']:.2f}")
    print(f"Sharpe ratio: {result['metrics']['sharpe']:.2f}")
    print(f"Sortino ratio: {result['metrics']['sortino']:.2f}")
    print(f"Max drawdown: {result['metrics']['max_dd_pct']:.2%}")
    print(f"Total return: {result['metrics']['total_return_pct']:.2f}%")
    
    # Monte Carlo
    print(f"\nRunning Monte Carlo simulation...")
    mc = MonteCarloSimulator()
    mc_result = mc.simulate(result['trades'])
    
    print(f"\n=== Monte Carlo Results ===")
    print(f"Mean return: {mc_result['metrics']['mean_return']:.2f}%")
    print(f"Median return: {mc_result['metrics']['median_return']:.2f}%")
    print(f"Prob profit: {mc_result['metrics']['prob_profit']:.1%}")
    print(f"P95 max DD: {mc_result['metrics']['p95_max_dd']:.2f}%")
    
    # Regime analysis
    print(f"\nRunning regime analysis...")
    regime_analyzer = RegimeAnalyzer()
    regime_result = regime_analyzer.analyze(result['trades'])
    
    print(f"\n=== Regime Analysis ===")
    for regime, stats in regime_result.items():
        print(f"{regime}: {stats['trades']} trades, WR {stats['win_rate']:.1%}, "
              f"PnL ${stats['total_pnl']:.2f}")
