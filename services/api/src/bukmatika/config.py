from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BUKMATIKA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Bukmatika API"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://bukmatika:bukmatika@localhost:5432/bukmatika"
    http_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    source_concurrency: int = Field(default=4, ge=1, le=16)
    discovery_session_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    discovery_provider_result_limit: int = Field(default=32, ge=1, le=100)
    discovery_max_records: int = Field(default=96, ge=1, le=300)
    catalog_search_default_limit: int = Field(default=20, ge=1, le=100)
    user_agent: str = "Bukmatika/0.1 (+https://github.com/agustealo/bukmatika)"
    contact_email: str | None = None
    web_origin: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
