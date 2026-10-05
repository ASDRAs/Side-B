from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    clap_inference_url: str = Field(default="", validation_alias="CLAP_INFERENCE_URL")
    clap_inference_audience: str = Field(
        default="", validation_alias="CLAP_INFERENCE_AUDIENCE"
    )
    clap_inference_use_iam: bool = Field(
        default=True, validation_alias="CLAP_INFERENCE_USE_IAM"
    )
    clap_inference_timeout_seconds: float = Field(
        default=90, gt=0, le=120, validation_alias="CLAP_INFERENCE_TIMEOUT_SECONDS"
    )

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
    backend_access_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SIDE_B_ACCESS_TOKEN",
            "YOUTUBE_EXPORT_TOKEN",
        ),
    )
    auth_mode: Literal["legacy", "dual", "firebase"] = Field(
        default="legacy",
        validation_alias="SIDE_B_AUTH_MODE",
    )
    firebase_project_id: str = Field(
        default="",
        validation_alias="FIREBASE_PROJECT_ID",
    )
    firebase_allowed_uids: str = Field(
        default="",
        validation_alias="FIREBASE_ALLOWED_UIDS",
    )
    firebase_allowed_emails: str = Field(
        default="",
        validation_alias="FIREBASE_ALLOWED_EMAILS",
    )
    firebase_verify_timeout_seconds: float = Field(
        default=8.0,
        ge=1.0,
        le=30.0,
        validation_alias="FIREBASE_VERIFY_TIMEOUT_SECONDS",
    )
    firebase_http_timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        validation_alias="FIREBASE_HTTP_TIMEOUT_SECONDS",
    )
    firebase_verify_concurrency: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="FIREBASE_VERIFY_CONCURRENCY",
    )
    # "env" keeps FIREBASE_ALLOWED_* (or the legacy token) as the approval
    # source. "firestore" stores approvals in access_users/{uid} and requires
    # Firebase-only identity plus at least one server administrator UID.
    access_store: Literal["env", "firestore"] = Field(
        default="env",
        validation_alias="SIDE_B_ACCESS_STORE",
    )
    admin_uids: str = Field(default="", validation_alias="SIDE_B_ADMIN_UIDS")
    firestore_project_id: str = Field(
        default="",
        validation_alias="SIDE_B_FIRESTORE_PROJECT_ID",
    )
    firestore_database: str = Field(
        default="(default)",
        min_length=1,
        max_length=63,
        validation_alias="SIDE_B_FIRESTORE_DATABASE",
    )
    access_store_timeout_seconds: float = Field(
        default=8.0,
        ge=1.0,
        le=30.0,
        validation_alias="SIDE_B_ACCESS_STORE_TIMEOUT_SECONDS",
    )
    access_store_concurrency: int = Field(
        default=8,
        ge=1,
        le=64,
        validation_alias="SIDE_B_ACCESS_STORE_CONCURRENCY",
    )
    access_request_daily_limit: int = Field(
        default=50,
        ge=1,
        le=10_000,
        validation_alias="ACCESS_REQUEST_DAILY_LIMIT",
    )
    access_status_requests_per_minute: int = Field(
        default=20, ge=1, le=600, validation_alias="ACCESS_STATUS_REQUESTS_PER_MINUTE"
    )
    access_status_aggregate_requests_per_minute: int = Field(
        default=300,
        ge=1,
        le=10_000,
        validation_alias="ACCESS_STATUS_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    access_lookup_requests_per_minute: int = Field(
        default=60, ge=1, le=600, validation_alias="ACCESS_LOOKUP_REQUESTS_PER_MINUTE"
    )
    access_lookup_aggregate_requests_per_minute: int = Field(
        default=600,
        ge=1,
        le=10_000,
        validation_alias="ACCESS_LOOKUP_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    access_request_requests_per_minute: int = Field(
        default=3, ge=1, le=60, validation_alias="ACCESS_REQUEST_REQUESTS_PER_MINUTE"
    )
    access_request_aggregate_requests_per_minute: int = Field(
        default=30,
        ge=1,
        le=1_000,
        validation_alias="ACCESS_REQUEST_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    admin_read_requests_per_minute: int = Field(
        default=30, ge=1, le=600, validation_alias="ADMIN_READ_REQUESTS_PER_MINUTE"
    )
    admin_read_aggregate_requests_per_minute: int = Field(
        default=120,
        ge=1,
        le=10_000,
        validation_alias="ADMIN_READ_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    admin_write_requests_per_minute: int = Field(
        default=30, ge=1, le=600, validation_alias="ADMIN_WRITE_REQUESTS_PER_MINUTE"
    )
    admin_write_aggregate_requests_per_minute: int = Field(
        default=120,
        ge=1,
        le=10_000,
        validation_alias="ADMIN_WRITE_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    preview_requests_per_minute: int = Field(
        default=30, ge=1, le=600, validation_alias="PREVIEW_REQUESTS_PER_MINUTE"
    )
    preview_aggregate_requests_per_minute: int = Field(
        default=300,
        ge=1,
        le=10_000,
        validation_alias="PREVIEW_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    recommend_requests_per_minute: int = Field(
        default=6,
        ge=1,
        le=60,
        validation_alias="RECOMMEND_REQUESTS_PER_MINUTE",
    )
    recommend_aggregate_requests_per_minute: int = Field(
        default=30,
        ge=1,
        le=1_000,
        validation_alias="RECOMMEND_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    genre_requests_per_minute: int = Field(
        default=6,
        ge=1,
        le=60,
        validation_alias="GENRE_REQUESTS_PER_MINUTE",
    )
    genre_aggregate_requests_per_minute: int = Field(
        default=30,
        ge=1,
        le=1_000,
        validation_alias="GENRE_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    allow_unauthenticated_recommend: bool = Field(
        default=False,
        validation_alias="ALLOW_UNAUTHENTICATED_RECOMMEND",
    )
    youtube_export_requests_per_minute: int = Field(
        default=6,
        ge=1,
        le=60,
        validation_alias="YOUTUBE_EXPORT_REQUESTS_PER_MINUTE",
    )
    youtube_export_aggregate_requests_per_minute: int = Field(
        default=30,
        ge=1,
        le=1_000,
        validation_alias="YOUTUBE_EXPORT_AGGREGATE_REQUESTS_PER_MINUTE",
    )
    authenticated_user_bucket_limit: int = Field(
        default=1_000,
        ge=1,
        le=100_000,
        validation_alias="AUTHENTICATED_USER_BUCKET_LIMIT",
    )
    authenticated_user_bucket_ttl_seconds: float = Field(
        default=600.0,
        ge=60.0,
        le=86_400.0,
        validation_alias="AUTHENTICATED_USER_BUCKET_TTL_SECONDS",
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
    cors_allowed_origins: str = Field(
        default="chrome-extension://hfcclomfoickmehgmdgjdjmiiekaciam",
        validation_alias="CORS_ALLOWED_ORIGINS",
    )

    @field_validator("cors_allowed_origins")
    @classmethod
    def reject_wildcard_cors_origin(cls, value: str) -> str:
        origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        if not origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must contain at least one origin")
        if "*" in origins:
            raise ValueError("CORS_ALLOWED_ORIGINS must not contain '*'")
        return ",".join(origins)

    @property
    def cors_origin_allowlist(self) -> list[str]:
        return self.cors_allowed_origins.split(",")

    @property
    def firebase_uid_allowlist(self) -> frozenset[str]:
        return frozenset(
            value.strip()
            for value in self.firebase_allowed_uids.split(",")
            if value.strip()
        )

    @property
    def firebase_email_allowlist(self) -> frozenset[str]:
        return frozenset(
            value.strip().casefold()
            for value in self.firebase_allowed_emails.split(",")
            if value.strip()
        )

    @property
    def admin_uid_set(self) -> frozenset[str]:
        return frozenset(
            value.strip() for value in self.admin_uids.split(",") if value.strip()
        )

    @property
    def effective_firestore_project_id(self) -> str:
        return self.firestore_project_id.strip() or self.firebase_project_id.strip()


class AccessConfigurationError(RuntimeError):
    """Startup must stop: the approval settings are unsafe or contradictory."""


def validate_access_configuration(settings: Settings) -> list[str]:
    """Fail startup for unsafe Firestore approval settings; return warnings.

    The Firestore store is never combined with the shared legacy token, dual
    mode or an environment allowlist, so none of them can bypass a stored
    rejection or block. ``env`` mode is left exactly as before.
    """
    if settings.access_store != "firestore":
        return []
    from app.services.access import InvalidAccessInputError, validate_uid

    problems = []
    if settings.auth_mode != "firebase":
        problems.append(
            "SIDE_B_ACCESS_STORE=firestore requires SIDE_B_AUTH_MODE=firebase"
        )
    if not settings.firebase_project_id.strip():
        problems.append("SIDE_B_ACCESS_STORE=firestore requires FIREBASE_PROJECT_ID")
    admins = settings.admin_uid_set
    if not admins:
        problems.append("SIDE_B_ACCESS_STORE=firestore requires SIDE_B_ADMIN_UIDS")
    for uid in sorted(admins):
        try:
            validate_uid(uid)
        except InvalidAccessInputError:
            problems.append("SIDE_B_ADMIN_UIDS contains an invalid UID")
            break
    if settings.allow_unauthenticated_recommend:
        problems.append(
            "ALLOW_UNAUTHENTICATED_RECOMMEND cannot be enabled with the Firestore store"
        )
    if problems:
        raise AccessConfigurationError("; ".join(problems))

    warnings = []
    if settings.firebase_uid_allowlist or settings.firebase_email_allowlist:
        warnings.append(
            "FIREBASE_ALLOWED_UIDS/EMAILS are ignored with the Firestore store; "
            "migrate them with backend/scripts/bootstrap_access.py and remove them."
        )
    if str(settings.backend_access_token or "").strip():
        warnings.append(
            "SIDE_B_ACCESS_TOKEN is ignored in Firebase-only mode; remove it."
        )
    return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
