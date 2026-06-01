import requests

def get_ticker(symbol="BTCUSDT"):
    """Devuelve el precio actual de un símbolo usando la API pública de Binance."""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    try:
        resp = requests.get(url, timeout=30)   # ← timeout aumentado a 30s
        if resp.status_code == 200:
            data = resp.json()
            return float(data["price"])
        else:
            print(f"Error get_ticker: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"Excepción get_ticker: {e}")
        return None

def get_klines(symbol="BTCUSDT", interval="5m", limit=100):
    """Devuelve las últimas velas en formato lista de diccionarios."""
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    try:
        resp = requests.get(url, timeout=30)   # ← timeout aumentado a 30s
        if resp.status_code == 200:
            klines = []
            for k in resp.json():
                klines.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5])
                })
            return klines
        else:
            print(f"Error get_klines: {resp.status_code} - {resp.text}")
            return None
    except Exception as e:
        print(f"Excepción get_klines: {e}")
        return None