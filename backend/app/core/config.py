from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings
from typing import List, Optional

# 旧硬编码 JWT 签名钥。它已随 git 历史公开（S1/S4），持有者可离线伪造任意用户的
# token——因此不只是"不再作默认值"，而是作为已泄露值被显式拒收。
_BURNED_SECRET_KEY = "baige-no1-fixed-secret-key-do-not-change-in-production"


class Settings(BaseSettings):
    PROJECT_NAME: str = "白鸽一号"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "白鸽一号后台管理系统 API"

    API_V1_STR: str = "/api/v1"
    # Phase 3e：production 时关闭 /docs、/redoc 与 openapi.json（默认 development 保留）
    ENVIRONMENT: str = "development"
    # Phase 3b（S1）：必填、无兜底默认——缺失即启动失败（fail-closed，与 Phase 2b
    # 同一原则：宁可起不来，不可带着人人可伪造的鉴权静默运行）。
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./baige_no1.db"

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://43.159.130.219:3022", "https://43.159.130.219:3022"]

    # Admin default
    FIRST_SUPERUSER: str = "admin"
    # Phase 3b（S2）：不再有 admin123 默认值。未设置时 init_db 首次建库随机生成
    # 并只在创建时回显一次；设置了则按它建号且绝不回显。
    FIRST_SUPERUSER_PASSWORD: Optional[str] = None

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_must_be_fresh(cls, v: str) -> str:
        if v == _BURNED_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY 等于旧硬编码值——该值已随 git 历史公开泄露，"
                "持有者可离线伪造任意用户 token，必须换新值"
            )
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY 过短（<32 字符）。生成：python -c "
                "\"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return v

    class Config:
        env_file = ".env"
        case_sensitive = True
        # Exchange-key loading is deliberately owned by okx_client, not Settings.
        extra = "ignore"


try:
    settings = Settings()
except ValidationError as exc:
    raise RuntimeError(
        "配置缺失或非法，拒绝启动（Phase 3b fail-closed）。\n"
        "必填环境变量（也可写入 backend/.env，模板见 backend/.env.example）：\n"
        "  SECRET_KEY           JWT 签名钥，≥32 字符，"
        "生成：python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
        "  EXCHANGE_KEY_SECRET  交易所密钥的 AES 加密钥"
        "（旧部署先设为原硬编码值保持可解密，再用 backend/rotate_exchange_key.py 轮换）\n"
        "可选：FIRST_SUPERUSER_PASSWORD（未设则首次建库随机生成并回显一次）。\n"
        f"原始错误：{exc}"
    ) from exc
