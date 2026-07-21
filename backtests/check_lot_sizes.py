import requests

BASE_URL = "https://testnet.binancefuture.com"
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]

print(" Consultando lotes mínimos en Binance Futures (Testnet)...\n")
for symbol in PAIRS:
    try:
        resp = requests.get(f"{BASE_URL}/fapi/v1/exchangeInfo", timeout=10)
        data = resp.json()
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s["filters"]}
                min_qty = filters["MARKET_LOT_SIZE"]["minQty"]
                step_size = filters["MARKET_LOT_SIZE"]["stepSize"]
                print(f"{symbol}:")
                print(f"  Cantidad mínima: {min_qty}")
                print(f"  Incremento:      {step_size}")
                break
    except Exception as e:
        print(f"{symbol}: Error al consultar - {e}")
