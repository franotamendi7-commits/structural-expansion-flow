"""Verify Binance Futures testnet API connection."""
import sys
import toml
import hashlib
import hmac
import time
import requests
from urllib.parse import urlencode
from pathlib import Path


def load_keys():
    secrets_path = Path(__file__).parent / ".streamlit" / "secrets.toml"
    secrets = toml.load(secrets_path)
    return secrets["BINANCE_API_KEY"], secrets["BINANCE_SECRET_KEY"]


def sign_request(params: dict, secret: str) -> dict:
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["signature"] = signature
    return params


def get_account_balance(api_key: str, secret_key: str) -> dict:
    base_url = "https://testnet.binancefuture.com"
    endpoint = "/fapi/v2/account"

    params = {
        "timestamp": int(time.time() * 1000),
        "recvWindow": 10000,
    }
    params = sign_request(params, secret_key)

    headers = {"X-MBX-APIKEY": api_key}
    url = f"{base_url}{endpoint}?{urlencode(params)}"

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def main():
    print("=" * 50)
    print("BINANCE FUTURES TESTNET - VERIFICACION DE API")
    print("=" * 50)

    try:
        api_key, secret_key = load_keys()
        print(f"\nAPI Key cargada: ...{api_key[-8:]}")
        print(f"Secret Key cargada: ...{secret_key[-8:]}")
    except Exception as e:
        print(f"\nERROR cargando secrets.toml: {e}")
        sys.exit(1)

    print("\nConectando a fapi.binancefuture.com (testnet)...")
    try:
        data = get_account_balance(api_key, secret_key)
    except requests.exceptions.HTTPError as e:
        print(f"\nERROR HTTP: {e}")
        print(f"Response: {e.response.text if e.response else 'N/A'}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR de conexion: {e}")
        sys.exit(1)

    total_balance = float(data.get("totalWalletBalance", 0))
    available = float(data.get("availableBalance", 0))
    unrealized = float(data.get("totalUnrealizedProfit", 0))

    print(f"\n{'='*50}")
    print(f"  Balance Total:    ${total_balance:,.2f}")
    print(f"  Disponible:       ${available:,.2f}")
    print(f"  PnL No Realizado: ${unrealized:,.2f}")
    print(f"{'='*50}")

    if total_balance > 0:
        print("\nCONEXION EXITOSA - API funcional")
    else:
        print("\nADVERTENCIA: Balance es 0 - verificar keys")


if __name__ == "__main__":
    main()
