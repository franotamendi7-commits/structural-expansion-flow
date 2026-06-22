import hashlib
import hmac
import time
import requests
from typing import Dict, Optional

class ExecutionManager:
    """
    Ejecuta órdenes de mercado en Binance Futures (REST, sin librerías externas).
    Extrae el precio real de ejecución usando avgPrice o fills.
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

    def set_isolated_margin(self, symbol):
        """Establece el modo de margen aislado para el símbolo. No interrumpe la ejecución si falla."""
        try:
            params = self._sign_request({
                'symbol': symbol,
                'marginType': 'ISOLATED'
            })
            resp = self._send_request('POST', '/fapi/v1/marginType', params)
            if resp.get('code') == 200 or resp.get('msg') == 'success':
                return True
            elif resp.get('retCode') == 0 or resp.get('code') == -4046:
                return True
            else:
                print(f"[ExecutionManager] Aviso: no se pudo confirmar margen aislado: {resp}")
                return False
        except Exception as e:
            print(f"[ExecutionManager] Excepción al activar margen aislado: {e}")
            return False

    def _extract_price(self, resp: dict) -> float:
        """Extrae el precio de ejecución de una respuesta de orden (inicial o polling)."""
        # 1) Intentar desde fills (viene en respuestas FULL cuando la orden se ejecuta de inmediato)
        fills = resp.get('fills', [])
        if fills:
            total_qty = sum(float(f['qty']) for f in fills)
            total_cost = sum(float(f['price']) * float(f['qty']) for f in fills)
            if total_qty > 0:
                return total_cost / total_qty
        # 2) Usar avgPrice (viene en el endpoint de consulta de orden)
        avg_price = resp.get('avgPrice')
        if avg_price is not None and float(avg_price) > 0:
            return float(avg_price)
        # 3) Último recurso: price (puede ser 0 en mercado)
        return float(resp.get('price', 0))

    def _poll_order(self, symbol: str, order_id: int, timeout: float = 5.0) -> Optional[dict]:
        """Espera hasta que la orden esté FILLED y devuelve la respuesta completa."""
        start = time.time()
        while time.time() - start < timeout:
            params = self._sign_request({
                'symbol': symbol,
                'orderId': str(order_id)
            })
            resp = self._send_request('GET', '/fapi/v1/order', params)
            if isinstance(resp, dict) and resp.get('status') == 'FILLED':
                return resp
            time.sleep(0.5)
        return resp

    def execute_signal(self, signal_dict: dict, reduce_only: bool = False) -> dict:
        """
        Ejecuta una orden de mercado.
        signal_dict: {symbol, side, quantity}
        reduce_only=True para cerrar posiciones.
        Devuelve order_id, status, executed_price (precio real), error.
        """
        self.set_isolated_margin(signal_dict.get("symbol"))

        side = signal_dict.get('side', 'BUY').upper()
        symbol = signal_dict.get('symbol')
        quantity = signal_dict.get('quantity')

        if not all([symbol, quantity]):
            return {'error': 'Missing required fields: symbol, quantity'}

        # Usamos FULL para obtener fills si la ejecución es instantánea
        order_params = self._sign_request({
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': str(quantity),
            'reduceOnly': 'true' if reduce_only else 'false',
            'newOrderRespType': 'FULL'
        })
        initial_resp = self._send_request('POST', '/fapi/v1/order', order_params)

        if 'error' in initial_resp:
            return {'error': f"Market order failed: {initial_resp.get('msg', initial_resp['error'])}"}

        order_id = initial_resp.get('orderId')

        # Si la respuesta inicial ya tiene estado FILLED, extraemos el precio de inmediato
        if initial_resp.get('status') == 'FILLED':
            price = self._extract_price(initial_resp)
            return {
                'order_id': order_id,
                'status': 'FILLED',
                'executed_price': round(price, 4),
                'error': None
            }

        # Si no, hacemos polling hasta que esté FILLED
        final_resp = self._poll_order(symbol, order_id)
        if isinstance(final_resp, dict) and final_resp.get('status') == 'FILLED':
            price = self._extract_price(final_resp)
            return {
                'order_id': order_id,
                'status': 'FILLED',
                'executed_price': round(price, 4),
                'error': None
            }
        else:
            return {
                'order_id': order_id,
                'status': final_resp.get('status', 'UNKNOWN') if final_resp else 'UNKNOWN',
                'executed_price': 0.0,
                'error': 'Order not filled within timeout'
            }

    def get_balance(self, asset="USDT"):
        """Obtiene el balance de un activo en la cuenta de futuros."""
        try:
            params = self._sign_request({})
            resp = self._send_request('GET', '/fapi/v2/balance', params)
            if isinstance(resp, list):
                for item in resp:
                    if item.get('asset') == asset:
                        return float(item.get('balance', 0.0))
            return 0.0
        except Exception as e:
            print(f"[ExecutionManager] Error al obtener balance: {e}")
            return 0.0