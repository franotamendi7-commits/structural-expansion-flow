"""
Adaptive VWAP Engine — Estrategia robusta BTC/USDT
====================================================
Combina VWAP deviation con confirmación multi-indicador:
- Regime detection (ADX + BB Width + ATR ratio + EMA slope)
- Signal generation (VWAP + RSI + Volume + Structure)
- Dynamic risk management (ATR-based sizing + DD gates)
- Self-tuning (parameter rotation based on performance)

Diseñado para evitar overfitting:
- Walk-forward validation
- Múltiples presets (no optimización excesiva)
- Regime-aware (no asume condiciones de mercado constantes)
"""

import math
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIGURACIÓN
# ============================================================

REGIME_CONFIG = {
    'adx_period': 14,
    'adx_trending': 25,
    'adx_ranging': 20,
    'bb_period': 20,
    'bb_std': 2.0,
    'bb_squeeze': 0.03,
    'bb_expand': 0.06,
    'atr_period': 14,
    'atr_lookback': 50,
    'atr_high_vol': 1.5,
    'atr_low_vol': 0.7,
    'ema_fast': 20,
    'ema_slow': 50,
    'ema_slope_bars': 5,
}

SIGNAL_CONFIG = {
    'vwap_base_period': 20,
    'vwap_dev_threshold': 0.5,    # Reducido: 0.5% deviation
    'rsi_period': 14,
    'rsi_oversold': 35,
    'rsi_overbought': 65,
    'volume_ma_period': 20,
    'volume_threshold': 1.2,      # Reducido: 1.2x promedio
    'swing_lookback': 8,
}

RISK_CONFIG = {
    'base_risk_pct': 0.005,
    'max_risk_pct': 0.01,
    'atr_sl_multiplier': 1.5,     # SL más ajustado
    'atr_tp1_multiplier': 2.0,    # TP1 más amplio
    'atr_tp2_multiplier': 3.5,    # TP2 aún más amplio
    'max_leverage': 10,
    'leverage_by_regime': {
        'TRENDING_BULL': 8,
        'TRENDING_BEAR': 8,
        'RANGING_LOW_VOL': 5,
        'RANGING_HIGH_VOL': 3,
        'TRANSITION': 0,
    },
    'dd_warning': -0.03,
    'dd_critical': -0.06,
    'dd_stop': -0.10,
    'cooldown_losses': 3,
    'cooldown_minutes': 60,
}

SELF_TUNING_CONFIG = {
    'rolling_window': 7,
    'min_trades_for_tuning': 10,
    'presets': {
        'AGGRESSIVE': {
            'vwap_dev': 0.8,
            'rsi_oversold': 35,
            'rsi_overbought': 65,
            'volume_threshold': 1.3,
        },
        'BALANCED': {
            'vwap_dev': 1.0,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'volume_threshold': 1.5,
        },
        'CONSERVATIVE': {
            'vwap_dev': 1.5,
            'rsi_oversold': 25,
            'rsi_overbought': 75,
            'volume_threshold': 2.0,
        },
    },
    'performance_threshold': 0.02,
    'rotation_cooldown_days': 3,
}


# ============================================================
# INDICADORES BASE
# ============================================================

def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index."""
    high = df['high']
    low = df['low']
    close = df['close']
    
    plus_dm = high.diff()
    minus_dm = -low.diff()
    
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # Smoothed averages
    atr = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1/period, min_periods=period, adjust=False).mean() / atr
    
    # DX and ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
    adx_val = dx.ewm(alpha=1/period, min_periods=2*period, adjust=False).mean()
    
    return adx_val


def bollinger_width(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    """Bollinger Bands Width (upper - lower) / middle."""
    middle = df['close'].rolling(period).mean()
    std_dev = df['close'].rolling(period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    return (upper - lower) / middle


def atr_ratio(df: pd.DataFrame, period: int = 14, lookback: int = 50) -> pd.Series:
    """ATR actual / ATR histórico (promedio de lookback barras)."""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    atr_current = tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    atr_historical = atr_current.rolling(lookback).mean()
    
    return atr_current / atr_historical.replace(0, np.nan)


def ema_slope(df: pd.DataFrame, period: int = 20, bars: int = 5) -> pd.Series:
    """Pendiente de la EMA en las últimas N barras (normalizada por precio)."""
    ema_val = df['close'].ewm(span=period, adjust=False).mean()
    slope = ema_val.diff(bars) / ema_val.shift(bars) * 100
    return slope


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def rolling_vwap(df: pd.DataFrame, period: int) -> pd.Series:
    """Rolling VWAP over n periods."""
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    vp = tp * df['volume']
    return vp.rolling(period).sum() / df['volume'].rolling(period).sum()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


# ============================================================
# REGIME DETECTOR
# ============================================================

class RegimeDetector:
    """
    Detecta el régimen actual del mercado usando múltiples indicadores.
    
    Regímenes:
    - TRENDING_BULL: ADX > 25, EMA20 > EMA50, slope > 0
    - TRENDING_BEAR: ADX > 25, EMA20 < EMA50, slope < 0
    - RANGING_LOW_VOL: ADX < 20, BB width < 3%, ATR ratio < 0.7
    - RANGING_HIGH_VOL: ADX < 20, BB width > 6%, ATR ratio > 1.5
    - TRANSITION: Todo lo demás (no operar)
    """
    
    def __init__(self, config: dict = None):
        self.config = config or REGIME_CONFIG
    
    def detect(self, df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
        """
        Detecta el régimen actual.
        
        Returns:
            (regime_str, debug_dict)
        """
        if len(df) < self.config['atr_lookback']:
            return 'TRANSITION', {'reason': 'insufficient_data'}
        
        # Calcular indicadores
        adx_val = adx(df, self.config['adx_period']).iloc[-1]
        bb_w = bollinger_width(df, self.config['bb_period'], self.config['bb_std']).iloc[-1]
        atr_r = atr_ratio(df, self.config['atr_period'], self.config['atr_lookback']).iloc[-1]
        ema_f = df['close'].ewm(span=self.config['ema_fast'], adjust=False).mean()
        ema_s = df['close'].ewm(span=self.config['ema_slow'], adjust=False).mean()
        slope = ema_slope(df, self.config['ema_fast'], self.config['ema_slope_bars']).iloc[-1]
        
        debug = {
            'adx': float(adx_val) if pd.notna(adx_val) else None,
            'bb_width': float(bb_w) if pd.notna(bb_w) else None,
            'atr_ratio': float(atr_r) if pd.notna(atr_r) else None,
            'ema_slope': float(slope) if pd.notna(slope) else None,
            'ema_fast': float(ema_f.iloc[-1]) if pd.notna(ema_f.iloc[-1]) else None,
            'ema_slow': float(ema_s.iloc[-1]) if pd.notna(ema_s.iloc[-1]) else None,
        }
        
        # Clasificar régimen
        if pd.isna(adx_val) or pd.isna(bb_w) or pd.isna(atr_r):
            return 'TRANSITION', debug
        
        # Trending
        if adx_val > self.config['adx_trending']:
            if ema_f.iloc[-1] > ema_s.iloc[-1] and slope > 0:
                return 'TRENDING_BULL', debug
            elif ema_f.iloc[-1] < ema_s.iloc[-1] and slope < 0:
                return 'TRENDING_BEAR', debug
            else:
                return 'TRANSITION', debug
        
        # Ranging
        if adx_val < self.config['adx_ranging']:
            if bb_w < self.config['bb_squeeze'] and atr_r < self.config['atr_low_vol']:
                return 'RANGING_LOW_VOL', debug
            elif bb_w > self.config['bb_expand'] and atr_r > self.config['atr_high_vol']:
                return 'RANGING_HIGH_VOL', debug
            else:
                return 'TRANSITION', debug
        
        # Zona neutral (ADX entre 20-25)
        return 'TRANSITION', debug


# ============================================================
# SIGNAL GENERATOR
# ============================================================

class SignalGenerator:
    """
    Genera señales de trading usando VWAP + confirmación multi-indicador.
    
    Señal LONG:
    1. VWAP deviation < -threshold (precio se desvía hacia abajo)
    2. RSI < oversold (sobreventa)
    3. Volume > threshold × promedio (confirmación)
    4. Precio en zona de soporte (swing low)
    5. Régimen favorable (no TRANSITION)
    
    Señal SHORT:
    1. VWAP deviation > +threshold (precio se desvía hacia arriba)
    2. RSI > overbought (sobrecompra)
    3. Volume > threshold × promedio (confirmación)
    4. Precio en zona de resistencia (swing high)
    5. Régimen favorable (no TRANSITION)
    """
    
    def __init__(self, config: dict = None):
        self.config = config or SIGNAL_CONFIG
    
    def generate(self, df: pd.DataFrame, regime: str, 
                 preset: dict = None) -> Optional[Dict[str, Any]]:
        """
        Genera señal de trading.
        
        Lógica relajada:
        - VWAP deviation es la señal PRIMARIA
        - RSI, Volume y Structure son CONFIRMACIONES (al menos 1 required)
        - Esto genera más señales manteniendo calidad
        
        Returns:
            Signal dict or None
        """
        if regime == 'TRANSITION':
            return None
        
        if len(df) < max(self.config['vwap_base_period'], 
                         self.config['rsi_period'],
                         self.config['volume_ma_period'],
                         self.config['swing_lookback'] * 2):
            return None
        
        # Usar preset o configuración default
        cfg = preset or self.config
        
        # Calcular indicadores
        vwap = rolling_vwap(df, self.config['vwap_base_period'])
        dev = (df['close'] - vwap) / vwap * 100
        
        rsi_val = rsi(df, self.config['rsi_period'])
        
        vol_ma = df['volume'].rolling(self.config['volume_ma_period']).mean()
        vol_ratio = df['volume'] / vol_ma
        
        atr_val = atr(df, 14)
        
        # Swing points
        lookback = self.config['swing_lookback']
        swing_high = df['high'].rolling(lookback * 2).max()
        swing_low = df['low'].rolling(lookback * 2).min()
        
        # Última barra cerrada (no la que está formándose)
        last_dev = float(dev.iloc[-1]) if pd.notna(dev.iloc[-1]) else None
        prev_dev = float(dev.iloc[-2]) if len(dev) > 1 and pd.notna(dev.iloc[-2]) else None
        last_rsi = float(rsi_val.iloc[-1]) if pd.notna(rsi_val.iloc[-1]) else None
        last_vol_ratio = float(vol_ratio.iloc[-1]) if pd.notna(vol_ratio.iloc[-1]) else None
        last_atr = float(atr_val.iloc[-1]) if pd.notna(atr_val.iloc[-1]) else None
        last_close = float(df['close'].iloc[-1])
        last_swing_high = float(swing_high.iloc[-1]) if pd.notna(swing_high.iloc[-1]) else None
        last_swing_low = float(swing_low.iloc[-1]) if pd.notna(swing_low.iloc[-1]) else None
        
        if any(v is None for v in [last_dev, prev_dev, last_rsi, last_vol_ratio, 
                                    last_atr, last_swing_high, last_swing_low]):
            return None
        
        # Detectar señal - Mean Reversion
        signal = None
        
        # LONG: Precio por debajo de VWAP + RSI oversold + Volume
        # Mean reversion: esperamos que el precio suba de vuelta al VWAP
        if last_dev < -cfg['vwap_dev']:
            # Score de reversión
            score = 0
            if last_rsi < cfg['rsi_oversold']:
                score += 2  # RSI fuerte
            elif last_rsi < 45:
                score += 1  # RSI moderado
            
            if last_vol_ratio > cfg['volume_threshold']:
                score += 1  # Volumen confirma
            
            if last_close <= last_swing_low * 1.005:
                score += 1  # En zona de soporte
            
            # Necesita score >= 2 para entrar
            if score >= 2:
                signal = {
                    'dir': 1,
                    'entry': last_close,
                    'stop': last_close - RISK_CONFIG['atr_sl_multiplier'] * last_atr,
                    'tp1': last_close + RISK_CONFIG['atr_tp1_multiplier'] * last_atr,
                    'tp2': last_close + RISK_CONFIG['atr_tp2_multiplier'] * last_atr,
                    'atr': last_atr,
                    'regime': regime,
                    'dev': last_dev,
                    'rsi': last_rsi,
                    'vol_ratio': last_vol_ratio,
                    'score': score,
                }
        
        # SHORT: Precio por encima de VWAP + RSI overbought + Volume
        elif last_dev > cfg['vwap_dev']:
            # Score de reversión
            score = 0
            if last_rsi > cfg['rsi_overbought']:
                score += 2  # RSI fuerte
            elif last_rsi > 55:
                score += 1  # RSI moderado
            
            if last_vol_ratio > cfg['volume_threshold']:
                score += 1  # Volumen confirma
            
            if last_close >= last_swing_high * 0.995:
                score += 1  # En zona de resistencia
            
            # Necesita score >= 2 para entrar
            if score >= 2:
                signal = {
                    'dir': -1,
                    'entry': last_close,
                    'stop': last_close + RISK_CONFIG['atr_sl_multiplier'] * last_atr,
                    'tp1': last_close - RISK_CONFIG['atr_tp1_multiplier'] * last_atr,
                    'tp2': last_close - RISK_CONFIG['atr_tp2_multiplier'] * last_atr,
                    'atr': last_atr,
                    'regime': regime,
                    'dev': last_dev,
                    'rsi': last_rsi,
                    'vol_ratio': last_vol_ratio,
                    'score': score,
                }
        
        return signal


# ============================================================
# RISK MANAGER (EXTENSIÓN)
# ============================================================

class AdaptiveRiskManager:
    """
    Risk manager dinámico con:
    - Position sizing basado en ATR
    - Drawdown gates progresivos
    - Cooldown por pérdidas consecutivas
    - Leverage dinámico por régimen
    """
    
    def __init__(self, config: dict = None):
        self.config = config or RISK_CONFIG
        self.equity = 0.0
        self.peak_equity = 0.0
        self.loss_streak = 0
        self.cooldown_until = None
        self.last_trade_time = None
    
    def update_equity(self, new_equity: float):
        """Actualiza equity y tracking."""
        self.equity = new_equity
        if new_equity > self.peak_equity:
            self.peak_equity = new_equity
    
    def record_trade(self, pnl: float, trade_time: datetime):
        """Registra resultado de trade."""
        if pnl <= 0:
            self.loss_streak += 1
            if self.loss_streak >= self.config['cooldown_losses']:
                self.cooldown_until = trade_time + timedelta(minutes=self.config['cooldown_minutes'])
        else:
            self.loss_streak = 0
            self.cooldown_until = None
        
        self.last_trade_time = trade_time
    
    def can_trade(self) -> Tuple[bool, str]:
        """Verifica si se puede operar."""
        now = datetime.now(timezone.utc)
        
        # Cooldown check
        if self.cooldown_until and now < self.cooldown_until:
            remaining = (self.cooldown_until - now).total_seconds() / 60
            return False, f"cooldown: {remaining:.1f}min remaining"
        
        # Drawdown check
        if self.peak_equity > 0:
            dd_pct = (self.equity - self.peak_equity) / self.peak_equity
            if dd_pct <= self.config['dd_stop']:
                return False, f"drawdown {dd_pct:.2%} exceeded stop limit"
        
        return True, "ok"
    
    def get_size_multiplier(self, regime: str) -> float:
        """Obtiene multiplicador de tamaño según régimen."""
        leverage = self.config['leverage_by_regime'].get(regime, 0)
        if leverage == 0:
            return 0.0
        
        # Reducir tamaño en drawdown
        if self.peak_equity > 0:
            dd_pct = (self.equity - self.peak_equity) / self.peak_equity
            
            if dd_pct <= self.config['dd_critical']:
                return 0.25  # 25% del tamaño normal
            elif dd_pct <= self.config['dd_warning']:
                return 0.50  # 50% del tamaño normal
        
        # Reducir tamaño en losing streak
        if self.loss_streak >= 2:
            return 0.50
        
        return 1.0
    
    def calculate_position_size(self, equity: float, entry: float, 
                                 stop: float, regime: str) -> float:
        """Calcula tamaño de posición basado en ATR y riesgo."""
        risk_pct = self.config['base_risk_pct']
        
        # Ajustar por régimen
        size_mult = self.get_size_multiplier(regime)
        if size_mult == 0:
            return 0.0
        
        risk_usd = equity * risk_pct * size_mult
        risk_distance = abs(entry - stop)
        
        if risk_distance <= 0:
            return 0.0
        
        size = risk_usd / risk_distance
        return size
    
    def get_leverage(self, regime: str) -> int:
        """Obtiene leverage según régimen."""
        return self.config['leverage_by_regime'].get(regime, 0)


# ============================================================
# SELF-TUNING MODULE
# ============================================================

class SelfTuner:
    """
    Módulo de auto-ajuste que rota entre presets según performance.
    
    Presets:
    - AGGRESSIVE: VWAP dev 0.8%, RSI 35/65, Volume 1.3x
    - BALANCED: VWAP dev 1.0%, RSI 30/70, Volume 1.5x
    - CONSERVATIVE: VWAP dev 1.5%, RSI 25/75, Volume 2.0x
    """
    
    def __init__(self, config: dict = None):
        self.config = config or SELF_TUNING_CONFIG
        self.current_preset = 'BALANCED'
        self.last_rotation = None
        self.trade_history = []  # (timestamp, pnl)
    
    def record_trade(self, pnl: float, timestamp: datetime):
        """Registra trade para evaluación."""
        self.trade_history.append((timestamp, pnl))
        
        # Mantener solo últimos 100 trades
        if len(self.trade_history) > 100:
            self.trade_history = self.trade_history[-100:]
    
    def get_current_preset(self) -> dict:
        """Obtiene el preset actual."""
        return self.config['presets'][self.current_preset]
    
    def evaluate_and_rotate(self) -> str:
        """
        Evalúa performance reciente y rota preset si es necesario.
        
        Returns:
            Mensaje de rotación o "no rotation"
        """
        now = datetime.now(timezone.utc)
        
        # Verificar cooldown de rotación
        if self.last_rotation:
            days_since = (now - self.last_rotation).total_seconds() / 86400
            if days_since < self.config['rotation_cooldown_days']:
                return "no rotation (cooldown)"
        
        # Evaluar performance de últimos 7 días
        week_ago = now - timedelta(days=self.config['rolling_window'])
        recent_trades = [(t, p) for t, p in self.trade_history if t >= week_ago]
        
        if len(recent_trades) < self.config['min_trades_for_tuning']:
            return f"no rotation (only {len(recent_trades)} trades, need {self.config['min_trades_for_tuning']})"
        
        # Calcular return
        total_pnl = sum(p for _, p in recent_trades)
        total_pnl_pct = total_pnl / 100  # Asumiendo capital base de $100
        
        # Lógica de rotación
        if total_pnl_pct < -0.01:  # Pérdida > 1%
            if self.current_preset != 'CONSERVATIVE':
                old = self.current_preset
                self.current_preset = 'CONSERVATIVE'
                self.last_rotation = now
                return f"rotated {old} -> CONSERVATIVE (return: {total_pnl_pct:.2%})"
        elif total_pnl_pct > 0.03:  # Ganancia > 3%
            if self.current_preset != 'AGGRESSIVE':
                old = self.current_preset
                self.current_preset = 'AGGRESSIVE'
                self.last_rotation = now
                return f"rotated {old} -> AGGRESSIVE (return: {total_pnl_pct:.2%})"
        
        return f"no rotation (return: {total_pnl_pct:.2%}, preset: {self.current_preset})"


# ============================================================
# ADAPTIVE VWAP ENGINE (MAIN)
# ============================================================

class AdaptiveVWAPEngine:
    """
    Engine principal que combina todos los módulos.
    
    Uso:
        engine = AdaptiveVWAPEngine()
        signal = engine.analyze(df)
        if signal:
            # Ejecutar trade
            pass
    """
    
    def __init__(self):
        self.regime_detector = RegimeDetector()
        self.signal_generator = SignalGenerator()
        self.risk_manager = AdaptiveRiskManager()
        self.self_tuner = SelfTuner()
    
    def analyze(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """
        Analiza mercado y genera señal si existe.
        
        Args:
            df: DataFrame con OHLCV data (15m recommended)
        
        Returns:
            Signal dict or None
        """
        # 1. Detectar régimen
        regime, regime_debug = self.regime_detector.detect(df)
        
        # 2. Verificar si se puede operar
        can_trade, reason = self.risk_manager.can_trade()
        if not can_trade:
            return {
                'signal': None,
                'regime': regime,
                'regime_debug': regime_debug,
                'can_trade': False,
                'reason': reason,
            }
        
        # 3. Obtener preset actual
        preset = self.self_tuner.get_current_preset()
        
        # 4. Generar señal
        signal = self.signal_generator.generate(df, regime, preset)
        
        if signal is None:
            return {
                'signal': None,
                'regime': regime,
                'regime_debug': regime_debug,
                'can_trade': True,
                'preset': self.self_tuner.current_preset,
            }
        
        # 5. Calcular tamaño de posición
        equity = self.risk_manager.equity or 100.0  # Default $100
        size = self.risk_manager.calculate_position_size(
            equity, signal['entry'], signal['stop'], regime
        )
        
        if size <= 0:
            return {
                'signal': None,
                'regime': regime,
                'regime_debug': regime_debug,
                'can_trade': True,
                'reason': 'size=0',
            }
        
        # 6. Obtener leverage
        leverage = self.risk_manager.get_leverage(regime)
        
        return {
            'signal': signal,
            'regime': regime,
            'regime_debug': regime_debug,
            'can_trade': True,
            'size': size,
            'leverage': leverage,
            'preset': self.self_tuner.current_preset,
        }
    
    def record_trade_result(self, pnl: float):
        """Registra resultado de trade para self-tuning."""
        now = datetime.now(timezone.utc)
        self.risk_manager.record_trade(pnl, now)
        self.self_tuner.record_trade(pnl, now)
    
    def update_equity(self, equity: float):
        """Actualiza equity del risk manager."""
        self.risk_manager.update_equity(equity)
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene estado actual del engine."""
        return {
            'equity': self.risk_manager.equity,
            'peak_equity': self.risk_manager.peak_equity,
            'loss_streak': self.risk_manager.loss_streak,
            'cooldown_until': self.risk_manager.cooldown_until.isoformat() if self.risk_manager.cooldown_until else None,
            'current_preset': self.self_tuner.current_preset,
            'last_rotation': self.self_tuner.last_rotation.isoformat() if self.self_tuner.last_rotation else None,
        }


# ============================================================
# TESTING
# ============================================================

if __name__ == "__main__":
    # Test básico con datos sintéticos
    np.random.seed(42)
    
    # Generar datos sintéticos
    n = 200
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq='15min')
    close = 50000 + np.cumsum(np.random.randn(n) * 100)
    df = pd.DataFrame({
        'open': close + np.random.randn(n) * 50,
        'high': close + abs(np.random.randn(n) * 100),
        'low': close - abs(np.random.randn(n) * 100),
        'close': close,
        'volume': np.random.exponential(1000, n),
    }, index=dates)
    
    # Test regime detector
    detector = RegimeDetector()
    regime, debug = detector.detect(df)
    print(f"Regime: {regime}")
    print(f"Debug: {debug}")
    
    # Test signal generator
    generator = SignalGenerator()
    signal = generator.generate(df, regime)
    print(f"Signal: {signal}")
    
    # Test engine
    engine = AdaptiveVWAPEngine()
    engine.update_equity(100.0)
    result = engine.analyze(df)
    print(f"Engine result: {result}")
