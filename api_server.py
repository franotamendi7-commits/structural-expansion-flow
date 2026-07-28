"""
API REST pública para copy trading + track record.
Sirve signal_log.json + último estado de los bots.
"""
import json, os, http.server, time, logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SIGNAL_LOG = Path(__file__).parent / "signal_log.json"
TRADE_HIST = Path(__file__).parent / "trade_history.json"
PORT = 8585
API_KEY = os.environ.get("API_SERVER_KEY", "")

def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except:
            return []
    return []

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if API_KEY:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {API_KEY}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"unauthorized"}')
                return

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/last_signal":
            signals = load_json(SIGNAL_LOG)
            last = signals[-1] if signals else {"status": "no signals yet"}
            self._json(last)

        elif path == "/api/track_record":
            signals = load_json(SIGNAL_LOG)
            trades = [s for s in signals if s.get("type") == "exit"]
            wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
            total = len(trades)
            pnls = [t.get("pnl", 0) for t in trades]
            self._json({
                "total_trades": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 2) if total else 0,
                "total_pnl": round(sum(pnls), 2),
                "signals": signals[-100:],
            })

        elif path == "/api/bot_status":
            trades = load_json(TRADE_HIST)
            self._json({
                "bot": "VWAP Breakout",
                "pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"],
                "total_trades": len(trades),
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })

        elif path == "/health":
            self._json({"status": "ok"})

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode())

    def log_message(self, format, *args):
        logger.info(f"API: {self.client_address[0]} {args[0]} {args[1]} {args[2]}")

print(f"API server on :{PORT}")
print(f"  GET /api/last_signal  → last trade signal")
print(f"  GET /api/track_record → full track record")
print(f"  GET /api/bot_status   → bot info")
print(f"  GET /health           → health check")
http.server.HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
