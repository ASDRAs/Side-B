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
    lastfm_api_secret: str | None = Field(default=None, validation_alias="LASTFM_API_SECRET")
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field(default="gemini-3-flash-preview", validation_alias="GEMINI_MODEL")
    search_timeout_seconds: float = Field(default=15.0, validation_alias="SEARCH_TIMEOUT_SECONDS")
    http_timeout_seconds: float = Field(default=6.0, validation_alias="HTTP_TIMEOUT_SECONDS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
