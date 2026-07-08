#!/usr/bin/env python3
"""
ws_binance_feed.py
==================
WebSocket feed para Binance klines 1h de los 5 pares del STRUCTURAL EXPANSION FLOW.

Reemplaza el polling REST por un stream combinado que recibe klines en tiempo real.
Mantiene en memoria la última vela cerrada por símbolo y la última vela en formación.

USO:
    from ws_binance_feed import BinanceKlineFeed

    feed = BinanceKlineFeed(symbols=['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT'],
                            interval='1h')
    feed.start()   # arranca el thread del WebSocket
    candle = feed.get_latest_candle('BTCUSDT')   # última vela CERRADA
    live   = feed.get_live_candle('BTCUSDT')     # vela en formación

CARACTERÍSTICAS:
- Stream combinado: 1 conexión WebSocket para los 5 pares
- Reconexión automática con backoff exponencial
- Heartbeat: si no llega ningún mensaje en 60s, fuerza reconexión
- Thread daemon: no bloquea el thread principal

DEPENDENCIAS:
    pip install websocket-client
"""

import json
import time
import threading
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

try:
    import websocket
except ImportError:
    raise ImportError("websocket-client no instalado. Ejecutá: pip install websocket-client")

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT']
DEFAULT_INTERVAL = '1h'
WS_BASE_URL = 'wss://stream.binance.com:9443/stream?streams='
RECONNECT_INITIAL_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0
HEARTBEAT_TIMEOUT = 60.0


class BinanceKlineFeed:
    """WebSocket feed para klines de Binance (multi-par, 1 conexión)."""

    def __init__(self,
                 symbols: Optional[List[str]] = None,
                 interval: str = DEFAULT_INTERVAL):
        self.symbols = [s.upper() for s in (symbols or DEFAULT_SYMBOLS)]
        self.interval = interval.lower()
        self._closed_candles: Dict[str, Dict[str, Any]] = {}
        self._live_candles: Dict[str, Dict[str, Any]] = {}
        for s in self.symbols:
            self._closed_candles[s] = None
            self._live_candles[s] = None
        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._last_message_ts = 0.0
        self._reconnect_attempts = 0
        self._lock = threading.Lock()
        self.messages_received = 0
        self.closes_detected = 0
        self.last_error: Optional[str] = None

    def _build_stream_url(self) -> str:
        streams = '/'.join(f"{s.lower()}@kline_{self.interval}" for s in self.symbols)
        return WS_BASE_URL + streams

    def _on_open(self, ws):
        logger.info(f"[WS] Conectado a Binance. Streams: {len(self.symbols)} pares @ {self.interval}")
        with self._lock:
            self._connected = True
            self._reconnect_attempts = 0
            self._last_message_ts = time.time()

    def _on_message(self, ws, message):
        try:
            with self._lock:
                self._last_message_ts = time.time()
                self.messages_received += 1
            data = json.loads(message)
            payload = data.get('data', data)
            if payload.get('e') != 'kline':
                return
            k = payload['k']
            symbol = payload['s']
            candle = {
                'symbol': symbol,
                'interval': k.get('i', self.interval),
                'open_time': k.get('t'),
                'close_time': k.get('T'),
                'open': float(k['o']),
                'high': float(k['h']),
                'low': float(k['l']),
                'close': float(k['c']),
                'volume': float(k['v']),
                'is_closed': bool(k.get('x', False)),
                'datetime': datetime.fromtimestamp(k['t'] / 1000, tz=timezone.utc).isoformat()
            }
            with self._lock:
                self._live_candles[symbol] = candle
                if candle['is_closed']:
                    self._closed_candles[symbol] = candle
                    self.closes_detected += 1
        except Exception as e:
            logger.error(f"[WS] Error procesando mensaje: {e}", exc_info=True)

    def _on_error(self, ws, error):
        with self._lock:
            self._connected = False
            self.last_error = str(error)
        logger.error(f"[WS] Error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        with self._lock:
            self._connected = False
        logger.warning(f"[WS] Cerrado (code={close_status_code}, msg={close_msg}). Reconectando...")

    def _run_loop(self):
        delay = RECONNECT_INITIAL_DELAY
        while self._running:
            try:
                url = self._build_stream_url()
                logger.info(f"[WS] Conectando a {url[:120]}...")
                self._ws = websocket.WebSocketApp(
                    url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self._ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error(f"[WS] Excepción en run_forever: {e}", exc_info=True)
                with self._lock:
                    self._connected = False
                    self.last_error = str(e)
            if not self._running:
                break
            self._reconnect_attempts += 1
            logger.info(f"[WS] Reintento #{self._reconnect_attempts} en {delay:.1f}s")
            time.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    def _heartbeat_loop(self):
        while self._running:
            time.sleep(10)
            with self._lock:
                if self._connected and self._last_message_ts > 0:
                    elapsed = time.time() - self._last_message_ts
                    if elapsed > HEARTBEAT_TIMEOUT:
                        logger.warning(f"[WS] Sin mensajes en {elapsed:.0f}s, forzando reconexión")
                        try:
                            self._ws.close()
                        except Exception:
                            pass

    def start(self):
        if self._running:
            logger.warning("[WS] Ya está corriendo")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name='BinanceKlineFeed', daemon=True)
        self._thread.start()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, name='BinanceKlineFeedHB', daemon=True)
        self._hb_thread.start()
        logger.info(f"[WS] Feed iniciado para {len(self.symbols)} pares @ {self.interval}")

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[WS] Feed detenido")

    def is_alive(self) -> bool:
        with self._lock:
            if not self._connected:
                return False
            if self._last_message_ts == 0:
                return False
            return (time.time() - self._last_message_ts) < HEARTBEAT_TIMEOUT

    def get_latest_candle(self, symbol: str) -> Optional[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            return self._closed_candles.get(symbol)

    def get_live_candle(self, symbol: str) -> Optional[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            return self._live_candles.get(symbol)

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'running': self._running,
                'connected': self._connected,
                'alive': self.is_alive(),
                'symbols': list(self.symbols),
                'interval': self.interval,
                'messages_received': self.messages_received,
                'closes_detected': self.closes_detected,
                'reconnect_attempts': self._reconnect_attempts,
                'last_message_age_s': (time.time() - self._last_message_ts) if self._last_message_ts else None,
                'last_error': self.last_error,
                'closed_candles_ready': {s: (self._closed_candles.get(s) is not None) for s in self.symbols},
            }

    def get_current_price(self, symbol: str) -> Optional[float]:
        live = self.get_live_candle(symbol)
        if live:
            return live['close']
        closed = self.get_latest_candle(symbol)
        return closed['close'] if closed else None


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    print("Iniciando feed de prueba por 60 segundos...")
    feed = BinanceKlineFeed()
    feed.start()
    try:
        for i in range(12):
            time.sleep(5)
            print(f"\n--- t={i*5+5}s ---")
            status = feed.get_status()
            print(f"alive={status['alive']}  msgs={status['messages_received']}  "
                  f"closes={status['closes_detected']}  attempts={status['reconnect_attempts']}")
            for s in feed.symbols:
                live = feed.get_live_candle(s)
                if live:
                    print(f"  {s}: close=${live['close']:.4f}  closed={live['is_closed']}  "
                          f"closed_candle_ready={status['closed_candles_ready'][s]}")
                else:
                    print(f"  {s}: (sin datos aún)")
    except KeyboardInterrupt:
        print("\nDeteniendo...")
    finally:
        feed.stop()
        print("Feed detenido.")
