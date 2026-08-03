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
            elif method == 'DELETE':
                resp = self.session.delete(url, headers=headers, params=params, timeout=10)
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

    def _poll_order(self, symbol: str, order_id: int, timeout: float = 10.0) -> Optional[dict]:
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

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        """Cancel a pending order on Binance."""
        try:
            params = self._sign_request({
                'symbol': symbol,
                'orderId': str(order_id)
            })
            resp = self._send_request('DELETE', '/fapi/v1/order', params)
            return resp
        except Exception as e:
            return {'error': str(e)}

    def get_open_orders(self, symbol: str = None) -> list:
        """Get all open (pending) orders, optionally filtered by symbol."""
        try:
            params = self._sign_request({})
            if symbol:
                params['symbol'] = symbol
            resp = self._send_request('GET', '/fapi/v1/openOrders', params)
            if isinstance(resp, list):
                return resp
            return []
        except Exception as e:
            return []

    def get_position(self, symbol: str) -> Optional[dict]:
        """Get actual position from exchange for a symbol."""
        try:
            params = self._sign_request({'symbol': symbol})
            resp = self._send_request('GET', '/fapi/v2/positionRisk', params)
            if isinstance(resp, list):
                for pos in resp:
                    if pos.get('symbol') == symbol:
                        amt = float(pos.get('positionAmt', 0))
                        if amt != 0:
                            return {
                                'symbol': symbol,
                                'side': 'LONG' if amt > 0 else 'SHORT',
                                'quantity': abs(amt),
                                'entry_price': float(pos.get('entryPrice', 0)),
                                'unrealized_pnl': float(pos.get('unRealizedProfit', 0)),
                            }
            return None
        except Exception as e:
            return None

    def execute_signal(self, signal_dict: dict, reduce_only: bool = False, 
                      order_type: str = 'MARKET', price: float = None) -> dict:
        """
        Ejecuta una orden con soporte para Post-Only (Maker).
        
        signal_dict: {symbol, side, quantity}
        reduce_only=True para cerrar posiciones.
        order_type: 'MARKET' (taker) o 'LIMIT_MAKER' (post-only)
        price: precio para órdenes limit (requerido para LIMIT_MAKER)
        
        Devuelve order_id, status, executed_price (precio real), error.
        """
        self.set_isolated_margin(signal_dict.get("symbol"))

        side = signal_dict.get('side', 'BUY').upper()
        symbol = signal_dict.get('symbol')
        quantity = signal_dict.get('quantity')

        if not all([symbol, quantity]):
            return {'error': 'Missing required fields: symbol, quantity'}
        
        # Validar price para órdenes LIMIT_MAKER
        if order_type == 'LIMIT_MAKER' and price is None:
            return {'error': 'Price required for LIMIT_MAKER orders'}

        # Configurar parámetros según tipo de orden
        order_params = {
            'symbol': symbol,
            'side': side,
            'quantity': str(quantity),
            'reduceOnly': 'true' if reduce_only else 'false',
        }
        
        if order_type == 'LIMIT_MAKER':
            # Post-Only: GTX = Good Till Crossing (maker only)
            order_params['type'] = 'LIMIT_MAKER'
            order_params['timeInForce'] = 'GTX'
            order_params['price'] = str(price)
        else:
            # Market order (taker)
            order_params['type'] = 'MARKET'
            order_params['newOrderRespType'] = 'FULL'

        # Firmar y enviar
        signed_params = self._sign_request(order_params)
        initial_resp = self._send_request('POST', '/fapi/v1/order', signed_params)

        if 'error' in initial_resp:
            return {'error': f"{order_type} order failed: {initial_resp.get('msg', initial_resp['error'])}"}

        order_id = initial_resp.get('orderId')
        
        # Para LIMIT_MAKER, verificar si se ejecutó inmediatamente
        if order_type == 'LIMIT_MAKER':
            # Si la orden se ejecuta inmediatamente como maker, está bien
            # Si intenta ser taker, Binance la rechaza automáticamente (por GTX)
            if initial_resp.get('status') == 'FILLED':
                price_exec = self._extract_price(initial_resp)
                return {
                    'order_id': order_id,
                    'status': 'FILLED',
                    'executed_price': round(price_exec, 4),
                    'error': None,
                    'order_type': 'LIMIT_MAKER'
                }
            elif initial_resp.get('status') == 'NEW':
                # Orden colocada pero no ejecutada aún (pendiente en el libro)
                return {
                    'order_id': order_id,
                    'status': 'NEW',
                    'executed_price': 0.0,
                    'error': None,
                    'order_type': 'LIMIT_MAKER',
                    'message': 'Order placed, waiting for fill'
                }
            elif initial_resp.get('status') == 'CANCELED':
                # GTX rechazada porque se ejecutaría como taker
                return {
                    'order_id': order_id,
                    'status': 'CANCELED',
                    'executed_price': 0.0,
                    'error': 'Post-only order would execute as taker',
                    'order_type': 'LIMIT_MAKER'
                }

        # Para MARKET orders, manejar como antes
        if initial_resp.get('status') == 'FILLED':
            price_exec = self._extract_price(initial_resp)
            return {
                'order_id': order_id,
                'status': 'FILLED',
                'executed_price': round(price_exec, 4),
                'error': None,
                'order_type': 'MARKET'
            }

        # Polling para órdenes que no se ejecutan inmediatamente
        final_resp = self._poll_order(symbol, order_id)
        if isinstance(final_resp, dict) and final_resp.get('status') == 'FILLED':
            price_exec = self._extract_price(final_resp)
            return {
                'order_id': order_id,
                'status': 'FILLED',
                'executed_price': round(price_exec, 4),
                'error': None,
                'order_type': order_type
            }
        else:
            return {
                'order_id': order_id,
                'status': final_resp.get('status', 'UNKNOWN') if final_resp else 'UNKNOWN',
                'executed_price': 0.0,
                'error': 'Order not filled within timeout',
                'order_type': order_type
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
            if isinstance(resp, dict) and resp.get("code"):
                print(f"[ExecutionManager] get_balance error: {resp.get('msg', resp)}")
            return 0.0
        except Exception as e:
            print(f"[ExecutionManager] Error al obtener balance: {e}")
            return 0.0