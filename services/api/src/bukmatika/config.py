from functools import lru_cache
from pathlib import Path

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
    acquisition_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    acquisition_max_bytes: int = Field(default=536_870_912, ge=1, le=2_147_483_648)
    acquisition_redirect_limit: int = Field(default=5, ge=0, le=10)
    acquisition_chunk_size: int = Field(default=262_144, ge=16_384, le=4_194_304)
    acquisition_max_attempts: int = Field(default=3, ge=1, le=10)
    acquisition_retry_base_seconds: float = Field(default=2.0, gt=0, le=60)
    acquisition_retry_max_seconds: float = Field(default=60.0, gt=0, le=600)
    acquisition_worker_enabled: bool = True
    acquisition_worker_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    acquisition_job_lease_seconds: float = Field(default=180.0, ge=30, le=3600)
    acquisition_job_heartbeat_seconds: float = Field(default=30.0, ge=5, le=300)
    archive_max_members: int = Field(default=20_000, ge=1, le=100_000)
    archive_max_uncompressed_bytes: int = Field(
        default=2_147_483_648,
        ge=1,
        le=8_589_934_592,
    )
    archive_max_compression_ratio: float = Field(default=200.0, gt=1, le=10_000)
    processing_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    processing_max_bytes: int = Field(default=67_108_864, ge=1, le=536_870_912)
    document_search_default_limit: int = Field(default=20, ge=1, le=100)
    storage_root: Path = Path(".bukmatika/storage")
    user_agent: str = "Bukmatika/0.1 (+https://github.com/agustealo/bukmatika)"
    contact_email: str | None = None
    web_origin: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
