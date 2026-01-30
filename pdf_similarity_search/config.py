"""Configuration: env (PDF_SEARCH_*) with defaults. All limits configurable."""
from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with validation and env loading."""

    model_config = SettingsConfigDict(
        env_prefix="PDF_SEARCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Crawl limits
    max_pages: int = 200
    max_depth: int = 5
    request_timeout: float = 30.0
    rate_limit_delay: float = 1.0

    # PDF processing
    max_pdf_size_mb: float = 50.0
    concurrent_downloads: int = 5
    similarity_threshold: float = 90.0

    # Near-miss reporting (80-89% similarity)
    near_miss_min_similarity: float = 80.0
    near_miss_max_similarity: float = 89.99

    # Optional default reference PDF path (can override via API)
    default_reference_pdf_path: Optional[str] = None

    @property
    def max_pdf_size_bytes(self) -> int:
        """Max PDF size in bytes for validation."""
        return int(self.max_pdf_size_mb * 1024 * 1024)


# Module-level settings instance (lazy load for tests)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return application settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
