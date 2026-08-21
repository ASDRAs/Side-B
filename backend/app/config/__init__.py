from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    lastfm_api_key: str | None = Field(default=None, validation_alias="LASTFM_API_KEY")
    lastfm_api_secret: str | None = Field(
        default=None, validation_alias="LASTFM_API_SECRET"
    )
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-3-flash-preview", validation_alias="GEMINI_MODEL"
    )
    youtube_api_key: str | None = Field(
        default=None, validation_alias="YOUTUBE_API_KEY"
    )
    youtube_export_token: str | None = Field(
        default=None, validation_alias="YOUTUBE_EXPORT_TOKEN"
    )
    youtube_export_requests_per_minute: int = Field(
        default=6,
        ge=1,
        le=60,
        validation_alias="YOUTUBE_EXPORT_REQUESTS_PER_MINUTE",
    )
    youtube_search_daily_budget: int = Field(
        default=80,
        ge=1,
        le=10_000,
        validation_alias="YOUTUBE_SEARCH_DAILY_BUDGET",
    )
    youtube_match_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        validation_alias="YOUTUBE_MATCH_THRESHOLD",
    )
    youtube_search_max_results: int = Field(
        default=5,
        ge=1,
        le=5,
        validation_alias="YOUTUBE_SEARCH_MAX_RESULTS",
    )
    youtube_search_concurrency: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias="YOUTUBE_SEARCH_CONCURRENCY",
    )
    http_timeout_seconds: float = Field(
        default=6.0, validation_alias="HTTP_TIMEOUT_SECONDS"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
