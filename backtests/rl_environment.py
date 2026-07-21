#!/usr/bin/env python3
"""
Entorno de Reinforcement Learning para optimizar la gestión de salidas.
Compatible con Gymnasium para usar con stable-baselines3.
"""

import numpy as np
import gymnasium as gym
from gymnasium import spaces

class ExitEnv(gym.Env):
    """
    Entorno de trading simplificado para RL.
    El agente debe decidir cómo gestionar la salida de una posición ya abierta.
    """
    def __init__(self, candles, direction, entry_price, sl_original, tp1, contracts,
                 commission=0.0005, slippage=0.001):
        super().__init__()
        self.candles = candles
        self.direction = direction
        self.entry = entry_price
        self.sl_original = sl_original
        self.tp1 = tp1
        self.contracts = contracts
        self.commission = commission
        self.slippage = slippage

        # Espacio de observación (8 features)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(8,), dtype=np.float32)
        # Espacio de acción (4 acciones discretas)
        self.action_space = spaces.Discrete(4)

        # Calcular tp2 y half_target
        if direction == 'bullish':
            self.tp_distance = tp1 - entry_price
            self.tp2 = entry_price + 2 * self.tp_distance
            self.half_target = entry_price + 0.5 * self.tp_distance
        else:
            self.tp_distance = entry_price - tp1
            self.tp2 = entry_price - 2 * self.tp_distance
            self.half_target = entry_price - 0.5 * self.tp_distance

        self.current_step = 0
        self.remaining = contracts
        self.breakeven_moved = False
        self.partial_done = False
        self.total_pnl = 0.0
        self.done = False

    def _current_price(self):
        return self.candles[self.current_step]['close']

    def _high_low(self):
        c = self.candles[self.current_step]
        return c['high'], c['low']

    def _state(self):
        price = self._current_price()
        if self.direction == 'bullish':
            pnl_pct = (price - self.entry) / self.entry
            dist_tp = (self.tp1 - price) / price if price > 0 else 0
            dist_sl = (price - self.sl) / price if price > 0 else 0
        else:
            pnl_pct = (self.entry - price) / self.entry
            dist_tp = (price - self.tp1) / price if price > 0 else 0
            dist_sl = (self.sl - price) / price if price > 0 else 0

        high, low = self._high_low()
        atr = (high - low) / price if price > 0 else 0.02
        ci = 50.0

        state = np.array([
            pnl_pct,
            atr,
            ci / 100.0,
            dist_tp,
            dist_sl,
            self.current_step / max(len(self.candles), 1),
            float(self.breakeven_moved),
            float(self.partial_done),
        ], dtype=np.float32)
        return state

    def step(self, action):
        if self.done:
            return self._state(), 0, True, False, {}

        price = self._current_price()
        high, low = self._high_low()
        reward = 0.0

        # 1. MANTENER
        if action == 0:
            pass
        # 2. SUBIR_STOP
        elif action == 1:
            if self.direction == 'bullish':
                new_sl = self.sl + 0.25 * (price - self.sl)
                if new_sl > self.sl:
                    self.sl = new_sl
            else:
                new_sl = self.sl - 0.25 * (self.sl - price)
                if new_sl < self.sl:
                    self.sl = new_sl
        # 3. CIERRE_PARCIAL_30%
        elif action == 2:
            if not self.partial_done and self.remaining > 0:
                close_contracts = self.remaining * 0.3
                if self.direction == 'bullish':
                    pnl = (price - self.entry) * close_contracts
                else:
                    pnl = (self.entry - price) * close_contracts
                comm = price * close_contracts * self.commission
                self.total_pnl += pnl - comm
                self.remaining -= close_contracts
                reward = pnl - comm
                if not self.breakeven_moved:
                    self.sl = self.entry
                    self.breakeven_moved = True
        # 4. CIERRE_TOTAL
        elif action == 3:
            if self.remaining > 0:
                if self.direction == 'bullish':
                    pnl = (price - self.entry) * self.remaining
                else:
                    pnl = (self.entry - price) * self.remaining
                comm = price * self.remaining * self.commission
                self.total_pnl += pnl - comm
                self.remaining = 0
                self.done = True
                reward = pnl - comm

        # Verificar SL/TP
        if not self.done:
            if self.direction == 'bullish':
                if low <= self.sl:
                    exit_price = self.sl - self.slippage * self.entry
                    pnl = (exit_price - self.entry) * self.remaining
                    comm = exit_price * self.remaining * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = 0
                    self.done = True
                elif high >= self.tp1 and not self.partial_done:
                    close_contracts = self.contracts * 0.6
                    pnl = (self.tp1 - self.entry) * close_contracts
                    comm = self.tp1 * close_contracts * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = self.contracts * 0.4
                    self.partial_done = True
                    self.sl = self.entry
                    self.breakeven_moved = True
                elif high >= self.tp2 and self.partial_done:
                    pnl = (self.tp2 - self.entry) * self.remaining
                    comm = self.tp2 * self.remaining * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = 0
                    self.done = True
            else:
                if high >= self.sl:
                    exit_price = self.sl + self.slippage * self.entry
                    pnl = (self.entry - exit_price) * self.remaining
                    comm = exit_price * self.remaining * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = 0
                    self.done = True
                elif low <= self.tp1 and not self.partial_done:
                    close_contracts = self.contracts * 0.6
                    pnl = (self.entry - self.tp1) * close_contracts
                    comm = self.tp1 * close_contracts * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = self.contracts * 0.4
                    self.partial_done = True
                    self.sl = self.entry
                    self.breakeven_moved = True
                elif low <= self.tp2 and self.partial_done:
                    pnl = (self.entry - self.tp2) * self.remaining
                    comm = self.tp2 * self.remaining * self.commission
                    self.total_pnl += pnl - comm
                    self.remaining = 0
                    self.done = True

        if self.current_step >= len(self.candles) - 1:
            if self.remaining > 0:
                last_price = self.candles[-1]['close']
                if self.direction == 'bullish':
                    pnl = (last_price - self.entry) * self.remaining
                else:
                    pnl = (self.entry - last_price) * self.remaining
                comm = last_price * self.remaining * self.commission
                self.total_pnl += pnl - comm
                self.remaining = 0
            self.done = True

        self.current_step += 1
        if self.done:
            reward = self.total_pnl

        # Gymnasium requiere 5 valores de retorno
        info = {}
        terminated = self.done
        truncated = False
        return self._state(), reward, terminated, truncated, info

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        self.remaining = self.contracts
        self.sl = self.sl_original
        self.breakeven_moved = False
        self.partial_done = False
        self.total_pnl = 0.0
        self.done = False
        info = {}
        return self._state(), info

    def render(self, mode='human'):
        pass   # no necesitamos visualización
