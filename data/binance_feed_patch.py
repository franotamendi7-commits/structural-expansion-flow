# Reemplazar la función get_klines actual por esta versión que devuelve lista de listas
import requests
import time

def get_klines(symbol="BTCUSDT", interval="5m", limit=100):
    endpoint = f"/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    data = _try_request(endpoint)
    if data is None:
        return None
    # Devolver la lista de listas raw (sin convertir a dict)
    return data
