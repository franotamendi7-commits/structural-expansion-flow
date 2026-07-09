"""
TradingView Webhook Receiver — servidor HTTP minimalista.
Recibe alerts de TradingView via POST y las convierte en señales
que el sistema multi_bot puede consumir.

Endpoints:
  POST /webhook  — Recibe alerta TradingView
  GET  /health   — Health check

Formato esperado del payload:
  {
    "symbol": "BTCUSDT",
    "action": "buy" | "sell",
    "price": 65000.0,
    "stop_loss": 64000.0,
    "take_profit": 68000.0,
    "strategy": "scout" | "momentum" | "range" | "structure",
    "confidence": 0.85
  }
"""
import json
import hmac
import hashlib
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "9999"))

latest_signal = None
signal_history = []


def validate_signature(payload: bytes, signature: str) -> bool:
    if not WEBHOOK_SECRET:
        return True
    expected = hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def process_signal(data: dict) -> Optional[dict]:
    symbol = data.get("symbol", "").upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"

    action = data.get("action", "").lower()
    if action == "buy":
        dir_val = 1
    elif action == "sell":
        dir_val = -1
    else:
        logger.warning(f"Unknown action: {action}")
        return None

    signal = {
        "symbol": symbol,
        "dir": dir_val,
        "price": float(data.get("price", 0)),
        "stop_loss": float(data.get("stop_loss", 0)) if data.get("stop_loss") else None,
        "take_profit": float(data.get("take_profit", 0)) if data.get("take_profit") else None,
        "strategy": data.get("strategy", "unknown"),
        "confidence": float(data.get("confidence", 0)),
        "bot_name": data.get("bot_name", data.get("strategy", "webhook")),
        "regime": data.get("regime", "LATERAL"),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    return signal


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        sig = self.headers.get("X-Signature", "")
        if not validate_signature(body, sig):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid signature"}')
            return

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid JSON"}')
            return

        signal = process_signal(data)
        if signal is None:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid signal data"}')
            return

        global latest_signal
        latest_signal = signal
        signal_history.append(signal)
        if len(signal_history) > 1000:
            signal_history.pop(0)

        try:
            from db import save_signal
            save_signal(signal)
        except Exception as e:
            logger.warning(f"DB save failed: {e}")

        logger.info(f"Webhook signal received: {signal['strategy']} {signal['symbol']} "
                    f"{'LONG' if signal['dir']==1 else 'SHORT'} @ {signal['entry']}")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {"status": "ok", "signal": signal}
        self.wfile.write(json.dumps(response).encode())

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "signals_received": len(signal_history),
                "latest_signal": latest_signal,
            }).encode())
        elif self.path == "/signals":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(signal_history[-50:]).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        logger.info(f"Webhook: {args[0]} {args[1]} {args[2]}")


def get_latest_signal() -> Optional[dict]:
    return latest_signal


def get_signal_history(limit: int = 50) -> list:
    return signal_history[-limit:]


def start_webhook_server():
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    logger.info(f"TradingView webhook listening on port {WEBHOOK_PORT}")
    server.serve_forever()


def start_webhook_thread():
    import threading
    t = threading.Thread(target=start_webhook_server, daemon=True, name="tv-webhook")
    t.start()
    logger.info(f"TradingView webhook thread started on port {WEBHOOK_PORT}")
    return t


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Starting TradingView webhook on port {WEBHOOK_PORT}...")
    start_webhook_server()