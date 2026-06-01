import threading
import time
import psutil
import websocket
import json
from datetime import datetime
from system_logger import errors_logger

class SystemMonitor:
    def __init__(self):
        self.ws = None
        self.thread = None
        self.running = False
        self.last_tick_time = None
        self.last_price = None
        self.reconnect_count = 0
        self.latency_ms = 0
        self.status = "DISCONNECTED"
        self.start_time = datetime.utcnow()

    def _on_message(self, ws, message):
        tick_time = datetime.utcnow()
        data = json.loads(message)
        self.last_price = float(data['c'])
        self.last_tick_time = tick_time
        event_time = data['E'] / 1000.0
        now_ts = tick_time.timestamp()
        self.latency_ms = (now_ts - event_time) * 1000
        self.status = "CONNECTED"

    def _on_error(self, ws, error):
        self.status = "ERROR"
        errors_logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        self.status = "DISCONNECTED"
        self.reconnect_count += 1
        errors_logger.warning(f"WebSocket cerrado (reconexiones: {self.reconnect_count})")

    def _on_open(self, ws):
        self.status = "CONNECTED"
        errors_logger.info("WebSocket conectado a Binance")

    def _run(self):
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    "wss://stream.binance.com:9443/ws/btcusdt@ticker",
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                    on_open=self._on_open
                )
                self.ws.run_forever()
            except Exception as e:
                errors_logger.error(f"Monitor WebSocket exception: {e}")
                self.status = "ERROR"
                time.sleep(5)

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)

    def get_metrics(self):
        """Devuelve un diccionario con TODAS las métricas necesarias."""
        process = psutil.Process()
        mem_total = psutil.virtual_memory()

        # Edad del tick en milisegundos
        if self.last_tick_time:
            last_tick_age_ms = (datetime.utcnow() - self.last_tick_time).total_seconds() * 1000
        else:
            last_tick_age_ms = None

        return {
            'websocket_status': self.status,
            'reconnect_count': self.reconnect_count,
            'last_tick_time': self.last_tick_time.strftime('%H:%M:%S.%f')[:-3] if self.last_tick_time else 'N/A',
            'last_tick_age_ms': last_tick_age_ms,
            'latencia_efectiva_ms': f"{self.latency_ms:.1f}" if self.latency_ms < 5000 else "N/A",
            'last_price': self.last_price,
            'uptime_sec': (datetime.utcnow() - self.start_time).total_seconds(),
            'cpu_percent': psutil.cpu_percent(interval=0.1),
            'ram_total_percent': mem_total.percent,
            'ram_process_mb': process.memory_info().rss / (1024 * 1024),
            'ram_process_percent': process.memory_percent(),
        }