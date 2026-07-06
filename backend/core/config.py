from pydantic_settings import BaseSettings
from functools import lru_cache
from pathlib import Path

# このファイルの場所から .env を探す（backend/.env）
ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    anthropic_api_key: str
    secret_key: str
    database_url: str = "sqlite:///./future_compass.db"
    encryption_key: str
    allowed_origins: str = "http://localhost:8080"
    access_token_expire_minutes: int = 60 * 24 * 7

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
