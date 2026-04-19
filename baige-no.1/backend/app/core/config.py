from pydantic_settings import BaseSettings
from typing import List, Optional
import secrets


class Settings(BaseSettings):
    PROJECT_NAME: str = "白鸽一号"
    VERSION: str = "1.0.0"
    DESCRIPTION: str = "白鸽一号后台管理系统 API"
    
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "baige-no1-fixed-secret-key-do-not-change-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./baige_no1.db"
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://43.159.130.219:3022", "https://43.159.130.219:3022"]
    
    # Admin default
    FIRST_SUPERUSER: str = "admin"
    FIRST_SUPERUSER_PASSWORD: str = "admin123"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
