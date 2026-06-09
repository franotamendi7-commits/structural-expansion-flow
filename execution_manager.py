import hashlib
import hmac
import time
import requests
from typing import Dict, Optional

class ExecutionManager:
    """
    Ejecuta órdenes de mercado en Binance Futures (REST, sin librerías externas).
    """
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        self.session = requests.Session()

    def _sign_request(self, params: dict) -> dict:
        params['timestamp'] = int(time.time() * 1000)
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params

    def _send_request(self, method: str, endpoint: str, params: dict) -> dict:
        url = f"{self.base_url}{endpoint}"
        headers = {'X-MBX-APIKEY': self.api_key}
        try:
            if method == 'POST':
                resp = self.session.post(url, headers=headers, data=params, timeout=10)
            else:
                resp = self.session.get(url, headers=headers, params=params, timeout=10)
            return resp.json()
        except requests.exceptions.RequestException as e:
            return {'error': str(e)}

    def execute_signal(self, signal_dict: dict) -> dict:
        """
        Ejecuta una orden de mercado simple.
        signal_dict debe contener: symbol, side, quantity.
        side: 'BUY' o 'SELL'
        quantity: cantidad en contratos (float)
        """
        side = signal_dict.get('side', 'BUY').upper()
        symbol = signal_dict.get('symbol')
        quantity = signal_dict.get('quantity')

        if not all([symbol, quantity]):
            return {'error': 'Missing required fields: symbol, quantity'}

        order_params = self._sign_request({
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity),
            'newOrderRespType': 'RESULT'
        })
        market_resp = self._send_request('POST', '/fapi/v1/order', order_params)

        if 'error' in market_resp:
            return {'error': f"Market order failed: {market_resp.get('msg', market_resp['error'])}"}

        return {
            'order_id': market_resp.get('orderId'),
            'status': market_resp.get('status'),
            'executed_price': float(market_resp.get('avgPrice', 0)),
            'error': None
        }
