import requests
import time

BASE_URLS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api3.binance.com",
]

def _try_request(url, timeout=30):
    """Prueba una URL y devuelve la respuesta si tiene éxito."""
    for base in BASE_URLS:
        full_url = base + url
        try:
            resp = requests.get(full_url, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            continue
    return None

def get_ticker(symbol="BTCUSDT"):
    endpoint = f"/api/v3/ticker/price?symbol={symbol.upper()}"
    data = _try_request(endpoint)
    if data:
        return float(data["price"])
    return None

def get_klines(symbol="BTCUSDT", interval="5m", limit=100):
    endpoint = f"/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    klines_raw = _try_request(endpoint)
    if klines_raw is None:
        return None
    klines = []
    for k in klines_raw:
        klines.append({
            "timestamp": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5])
        })
    return klines