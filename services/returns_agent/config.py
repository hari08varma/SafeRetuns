from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["none", "deepseek"] = "none"  # none = structured input + templates
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"  # DeepSeek-V4.1-Flash (per api-docs.deepseek.com)
    llm_timeout_s: float = 60.0

    database_url: str = "postgresql://returns:returns@localhost:5432/returns"
    config_dir: str = ""  # holds policies/ and graphs/; found automatically in the repo
    graph_active_version: str = "returns-v1"

    # Security — must be set outside development (see .env.example).
    jwt_secret: str = ""
    access_token_ttl_min: int = 15
    refresh_token_ttl_days: int = 7
    pii_encryption_key: str = ""  # Fernet key
    pii_index_key: str = ""  # HMAC key for searchable blind indexes

    otp_ttl_s: int = 300
    otp_max_attempts: int = 5
    otp_max_requests_per_window: int = 3
    otp_window_s: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()


def config_dir() -> Path:
    configured = get_settings().config_dir
    if configured:
        return Path(configured)
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "policies").is_dir():
            return parent / "config"
    raise RuntimeError("config directory not found; set CONFIG_DIR")
