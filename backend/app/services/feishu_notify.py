import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx


class FeishuOpenClawNotifier:
    """Send trade notifications to the Feishu group used for OpenClaw."""

    def __init__(self) -> None:
        # OpenClaw 脱钩（2026-07-17）：不再默认指向 /root/.openclaw 下的配置文件。
        # 首选纯环境变量 FEISHU_OPENCLAW_WEBHOOK / FEISHU_OPENCLAW_SECRET；
        # 需要文件配置时显式设 FEISHU_OPENCLAW_CONFIG 指向 JSON 路径。
        config_path = os.getenv("FEISHU_OPENCLAW_CONFIG", "")
        self.config_path = Path(config_path) if config_path else None

    def _load_config(self) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if self.config_path and self.config_path.exists():
            try:
                config = json.loads(self.config_path.read_text(encoding="utf-8") or "{}")
            except Exception as exc:
                print(f"Feishu config read failed: {exc}")
        webhook = os.getenv("FEISHU_OPENCLAW_WEBHOOK") or config.get("webhook_url") or ""
        secret = os.getenv("FEISHU_OPENCLAW_SECRET") or config.get("secret") or ""
        enabled_raw = os.getenv("FEISHU_OPENCLAW_ENABLED", config.get("enabled", True))
        if isinstance(enabled_raw, str):
            enabled = enabled_raw.strip().lower() not in {"0", "false", "no", "off"}
        else:
            enabled = bool(enabled_raw)
        return {"enabled": enabled, "webhook": str(webhook).strip(), "secret": str(secret).strip()}

    @staticmethod
    def _sign(timestamp: str, secret: str) -> str:
        message = f"{timestamp}\n{secret}".encode("utf-8")
        digest = hmac.new(message, b"", digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def send_text(self, text: str) -> bool:
        config = self._load_config()
        if not config["enabled"] or not config["webhook"]:
            return False

        payload: dict[str, Any] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if config["secret"]:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self._sign(timestamp, config["secret"])

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(config["webhook"], json=payload)
                resp.raise_for_status()
                result = resp.json()
                # Webhooks may return HTTP 200 with a nonzero business error.
                code = result.get("code", result.get("StatusCode"))
                if isinstance(code, bool) or str(code) != "0":
                    print("Feishu notify rejected: business response not successful")
                    return False
            return True
        except Exception as exc:
            # HTTP exceptions can contain the secret webhook URL.
            print(f"Feishu notify failed: {type(exc).__name__}")
            return False

    async def notify_open_trade(
        self,
        *,
        strategy_name: str,
        strategy_marker: str,
        symbol: str,
        direction: str,
        signal: str,
        quantity: Any,
        price: Any,
        leverage: Any,
        order_id: Optional[Any] = None,
    ) -> bool:
        order_line = f"\nOrder: {order_id}" if order_id else ""
        text = (
            "Baige trend strategy opened a position\n"
            f"Strategy: {strategy_marker} {strategy_name}\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction} ({signal})\n"
            f"Quantity: {quantity}\n"
            f"Price: {price}\n"
            f"Leverage: {leverage}x"
            f"{order_line}"
        )
        return await self.send_text(text)


feishu_openclaw_notifier = FeishuOpenClawNotifier()
