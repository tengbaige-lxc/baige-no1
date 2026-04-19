import asyncio
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Optional
import httpx

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import os

_KEY_SECRET = os.environ.get("EXCHANGE_KEY_SECRET", "baige-no1-secret-key-32bytes!!")
OKX_BASE_URL = "https://www.okx.com"
OKX_DEMO_URL = "https://www.okx.com"  # OKX demo trading uses same endpoint with different keys


def _get_key() -> bytes:
    return hashlib.sha256(_KEY_SECRET.encode()).digest()


def encrypt_text(plain: str) -> str:
    if not plain:
        return ""
    key = _get_key()
    cipher = AES.new(key, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(plain.encode(), AES.block_size))
    return base64.b64encode(cipher.iv + ct_bytes).decode()


def decrypt_text(encrypted: str) -> str:
    if not encrypted:
        return ""
    key = _get_key()
    data = base64.b64decode(encrypted.encode())
    iv = data[:16]
    ct = data[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return unpad(cipher.decrypt(ct), AES.block_size).decode()


def generate_signature(timestamp: str, method: str, request_path: str, body: str, secret: str) -> str:
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(secret.encode(), message.encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


class OkxClientManager:
    """OKX API client manager."""
    
    def __init__(self):
        self._public_client = httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30)
    
    def _get_auth_headers(self, api_key: str, api_secret: str, passphrase: str, method: str, path: str, body: str = "") -> dict:
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
        signature = generate_signature(timestamp, method, path, body, api_secret)
        return {
            "OK-ACCESS-KEY": api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": passphrase,
            "Content-Type": "application/json",
        }
    
    # === Public API (no auth needed) ===
    async def get_ticker(self, inst_id: str) -> dict:
        """Get ticker price."""
        resp = await self._public_client.get("/api/v5/market/ticker", params={"instId": inst_id})
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"][0]
    
    async def get_candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> list:
        """Get candlestick data."""
        resp = await self._public_client.get("/api/v5/market/candles", params={
            "instId": inst_id, "bar": bar, "limit": str(limit)
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"]
    
    async def get_orderbook(self, inst_id: str, sz: int = 5) -> dict:
        """Get order book."""
        resp = await self._public_client.get("/api/v5/market/books", params={"instId": inst_id, "sz": str(sz)})
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "0":
            raise Exception(data.get("msg"))
        return data["data"][0]
    
    # === Private API (auth needed) ===
    async def get_balance(self, api_key: str, api_secret: str, passphrase: str) -> list:
        """Get account balance."""
        path = "/api/v5/account/balance"
        headers = self._get_auth_headers(api_key, api_secret, passphrase, "GET", path)
        async with httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30) as client:
            resp = await client.get(path, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"][0].get("details", [])
    
    async def create_order(self, api_key: str, api_secret: str, passphrase: str,
                           inst_id: str, side: str, ord_type: str, sz: str,
                           px: str = None, td_mode: str = "cash", pos_side: str = None) -> dict:
        """Create a new order."""
        path = "/api/v5/trade/order"
        body_dict = {
            "instId": inst_id,
            "tdMode": td_mode,      # cash (spot) | cross (margin) | isolate (contract)
            "side": side.lower(),    # buy | sell
            "ordType": ord_type.lower(),  # market | limit
            "sz": sz,
        }
        if px and ord_type.upper() == "LIMIT":
            body_dict["px"] = px
        if pos_side:
            body_dict["posSide"] = pos_side.lower()
        
        body = json.dumps(body_dict)
        headers = self._get_auth_headers(api_key, api_secret, passphrase, "POST", path, body)
        
        async with httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30) as client:
            resp = await client.post(path, headers=headers, content=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"][0]
    
    async def cancel_order(self, api_key: str, api_secret: str, passphrase: str,
                           inst_id: str, ord_id: str) -> dict:
        """Cancel an order."""
        path = "/api/v5/trade/cancel-order"
        body_dict = {"instId": inst_id, "ordId": ord_id}
        body = json.dumps(body_dict)
        headers = self._get_auth_headers(api_key, api_secret, passphrase, "POST", path, body)
        
        async with httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30) as client:
            resp = await client.post(path, headers=headers, content=body)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"][0]
    
    async def get_orders(self, api_key: str, api_secret: str, passphrase: str,
                         inst_id: str = None, limit: int = 100) -> list:
        """Get order history."""
        path = "/api/v5/trade/orders-history"
        params = {"limit": str(limit)}
        if inst_id:
            params["instId"] = inst_id
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        full_path = path + "?" + query
        headers = self._get_auth_headers(api_key, api_secret, passphrase, "GET", full_path)
        
        async with httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=30) as client:
            resp = await client.get(path, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "0":
                raise Exception(data.get("msg"))
            return data["data"]


okx_manager = OkxClientManager()
