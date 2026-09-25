import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


RWA_ASSETS = [
    {"symbol": "AAPLx", "name": "Apple Tokenized Stock", "provider": "XStocks"},
    {"symbol": "NVDAx", "name": "NVIDIA Tokenized Stock", "provider": "XStocks"},
    {"symbol": "TSLAx", "name": "Tesla Tokenized Stock", "provider": "XStocks"},
    {"symbol": "MSFTx", "name": "Microsoft Tokenized Stock", "provider": "XStocks"},
    {"symbol": "USDY", "name": "Ondo Tokenized Yield", "provider": "Ondo"},
    {"symbol": "OUSG", "name": "Ondo Short-Term US Treasuries", "provider": "Ondo"},
]


@dataclass
class BitgetRwaConfig:
    base_url: str
    api_key: str | None
    api_secret: str | None
    passphrase: str | None
    wallet_address: str | None
    dry_run: bool
    allow_live_trading: bool
    single_order_usd_limit: float
    daily_order_usd_limit: float


class BitgetWalletRwaService:
    def __init__(self) -> None:
        self.quote_path = os.getenv("BITGET_RWA_QUOTE_PATH", "/swapx/pro/quote")
        self.config_path = Path(
            os.getenv(
                "BITGET_RWA_CONFIG_FILE",
                "/root/.openclaw/workspace/baige-no.1/backend/.bitget_rwa_config.json",
            )
        )

    def get_config(self) -> BitgetRwaConfig:
        saved = self._read_saved_config()
        return BitgetRwaConfig(
            base_url=saved.get("base_url") or os.getenv("BITGET_WALLET_BASE_URL", "https://web3.bitget.com"),
            api_key=saved.get("api_key") or os.getenv("BITGET_WALLET_API_KEY"),
            api_secret=saved.get("api_secret") or os.getenv("BITGET_WALLET_API_SECRET"),
            passphrase=saved.get("passphrase") or os.getenv("BITGET_WALLET_PASSPHRASE"),
            wallet_address=saved.get("wallet_address") or os.getenv("BITGET_WALLET_ADDRESS"),
            dry_run=self._bool_value(saved.get("dry_run"), self._env_bool("RWA_DRY_RUN", True)),
            allow_live_trading=self._bool_value(
                saved.get("allow_live_trading"),
                self._env_bool("RWA_ALLOW_LIVE_TRADING", False),
            ),
            single_order_usd_limit=float(
                saved.get("single_order_usd_limit")
                or os.getenv("RWA_SINGLE_ORDER_USD_LIMIT", "10")
                or 10
            ),
            daily_order_usd_limit=float(
                saved.get("daily_order_usd_limit")
                or os.getenv("RWA_DAILY_ORDER_USD_LIMIT", "50")
                or 50
            ),
        )

    def status(self) -> dict[str, Any]:
        cfg = self.get_config()
        return {
            "configured": bool(cfg.api_key and cfg.api_secret and cfg.passphrase),
            "wallet_configured": bool(cfg.wallet_address),
            "dry_run": cfg.dry_run,
            "allow_live_trading": cfg.allow_live_trading,
            "single_order_usd_limit": cfg.single_order_usd_limit,
            "daily_order_usd_limit": cfg.daily_order_usd_limit,
            "base_url": cfg.base_url,
            "quote_path": self.quote_path,
            "api_key_hint": self._mask(cfg.api_key),
        }

    def save_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        current = self._read_saved_config()
        next_config = {
            **current,
            "base_url": payload.get("base_url") or current.get("base_url") or "https://web3.bitget.com",
            "wallet_address": payload.get("wallet_address") or current.get("wallet_address"),
            "dry_run": self._bool_value(payload.get("dry_run"), True),
            "allow_live_trading": self._bool_value(payload.get("allow_live_trading"), False),
            "single_order_usd_limit": float(payload.get("single_order_usd_limit") or 10),
            "daily_order_usd_limit": float(payload.get("daily_order_usd_limit") or 50),
        }
        for field in ("api_key", "api_secret", "passphrase"):
            value = payload.get(field)
            if value:
                next_config[field] = value

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(next_config, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.config_path)
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        return self.status()

    def assets(self) -> list[dict[str, str]]:
        return RWA_ASSETS

    def quote(self, params: dict[str, Any]) -> dict[str, Any]:
        cfg = self.get_config()
        if not cfg.api_key:
            return {
                "dry_run": True,
                "message": "BITGET_WALLET_API_KEY is not configured; returning request preview only.",
                "request": self._sanitize_params(params),
            }

        url = self._build_url(cfg.base_url, self.quote_path, params)
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-KEY": cfg.api_key,
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def _build_url(self, base_url: str, path: str, params: dict[str, Any]) -> str:
        clean_base = base_url.rstrip("/")
        clean_path = "/" + path.lstrip("/")
        query = urllib.parse.urlencode(
            {
                key: value
                for key, value in params.items()
                if value not in (None, "")
            }
        )
        return f"{clean_base}{clean_path}?{query}" if query else f"{clean_base}{clean_path}"

    def _sanitize_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            key: ("***" if "key" in key.lower() or "secret" in key.lower() else value)
            for key, value in params.items()
        }

    def _mask(self, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}...{value[-4:]}"

    def _read_saved_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _bool_value(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _env_bool(self, name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}


bitget_wallet_rwa_service = BitgetWalletRwaService()
