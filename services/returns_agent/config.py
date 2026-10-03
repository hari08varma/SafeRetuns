from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["fake", "deepseek"] = "fake"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"  # DeepSeek-V4.1-Flash (per api-docs.deepseek.com)
    llm_timeout_s: float = 60.0

    database_url: str = "postgresql://returns:returns@localhost:5432/returns"


def get_settings() -> Settings:
    return Settings()
