from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    delegation_worker_enabled: bool = True
    delegation_worker_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    delegation_job_lease_seconds: float = Field(default=120.0, gt=60, le=3600)
    delegation_job_heartbeat_seconds: float = Field(default=20.0, ge=5, le=300)
    delegation_job_max_attempts: int = Field(default=3, ge=1, le=10)
    delegation_retry_seconds: float = Field(default=1.0, gt=0, le=60)
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
    ocr_worker_poll_seconds: float = Field(default=0.5, gt=0, le=30)
    ocr_job_lease_seconds: float = Field(default=600.0, ge=60, le=7200)
    ocr_job_heartbeat_seconds: float = Field(default=30.0, ge=5, le=300)
    ocr_max_attempts: int = Field(default=2, ge=1, le=5)
    ocr_retry_base_seconds: float = Field(default=5.0, gt=0, le=120)
    ocr_retry_max_seconds: float = Field(default=60.0, gt=0, le=900)
    ocr_tesseract_executable: str = "tesseract"
    ocr_tessdata_prefix: Path | None = None
    ocr_language: str = "eng"
    ocr_page_segmentation_mode: int = Field(default=3, ge=0, le=13)
    ocr_page_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    ocr_render_dpi: int = Field(default=200, ge=72, le=400)
    ocr_max_pages: int = Field(default=1000, ge=1, le=10_000)
    ocr_max_render_pixels: int = Field(default=50_000_000, ge=1_000_000, le=200_000_000)
    ocr_stdout_max_bytes: int = Field(default=8_388_608, ge=1024, le=67_108_864)
    ocr_stderr_max_bytes: int = Field(default=131_072, ge=1024, le=1_048_576)
    ocr_total_text_max_bytes: int = Field(default=67_108_864, ge=1024, le=536_870_912)
    local_session_cookie_name: str = "bukmatika_session"
    local_session_ttl_days: int = Field(default=30, ge=1, le=365)
    local_session_secure_cookie: bool = False
    storage_root: Path = Path(".bukmatika/storage")
    user_agent: str = "Bukmatika/0.1 (+https://github.com/agustealo/bukmatika)"
    contact_email: str | None = None
    web_origin: str = "http://localhost:3000"
    model_provider: Literal["none", "ollama"] = "none"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str | None = None
    model_timeout_seconds: float = Field(default=45.0, gt=0, le=60)
    model_readiness_timeout_seconds: float = Field(default=2.5, gt=0, le=10)
    embedding_provider: Literal["none", "ollama"] = "none"
    ollama_embedding_model: str | None = None
    embedding_timeout_seconds: float = Field(default=30.0, gt=0, le=60)
    semantic_search_max_chunks: int = Field(default=256, ge=1, le=2_000)
    semantic_search_batch_size: int = Field(default=24, ge=1, le=32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
