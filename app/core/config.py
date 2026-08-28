from functools import lru_cache
from typing import Literal

from pydantic import AnyUrl, EmailStr, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_SECRET = "development-only-change-this-secret"  # noqa: S105


class Settings(BaseSettings):
    app_name: str = "Quiz Game API"
    environment: Literal["development", "testing", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    password_reset_expire_minutes: int = Field(
        default=15,
        ge=5,
        le=60,
    )

    secret_key: SecretStr = SecretStr(DEFAULT_DEVELOPMENT_SECRET)
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=30, ge=1, le=365)
    jwt_algorithm: Literal["HS256"] = "HS256"

    database_url: str = "postgresql+asyncpg://quiz:quiz@localhost:5432/quiz"
    cors_origins: list[str] = ["http://localhost:3000"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "10.0.2.2", "testserver"]
    # AnyUrl, not AnyHttpUrl: the Android client is reached through its own URI scheme
    # (quizgame://reset-password), which AnyHttpUrl would reject.
    password_reset_url: AnyUrl = AnyUrl(  # noqa: S105 (URL, not a secret)
        "quizgame://reset-password"
    )

    smtp_host: str = "localhost"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: EmailStr = "claudetro03@gmail.com"
    smtp_use_ssl: bool = False
    smtp_start_tls: bool = False
    smtp_timeout_seconds: float = Field(default=10, gt=0, le=60)

    google_web_client_id: str = Field(min_length=1)

    # Avatar storage. Uploads go to a folder in the maintainer's own Drive, so these are
    # OAuth *user* credentials, not a service account: a service account has no My Drive
    # storage quota of its own and every upload would fail with storageQuotaExceeded.
    google_drive_client_id: str | None = None
    google_drive_client_secret: SecretStr | None = None
    google_drive_refresh_token: SecretStr | None = None
    google_drive_avatar_folder_id: str = "1FGB_U6l16Y-hNmMVTHBbfdgQkS5MeGiA"
    max_avatar_bytes: int = Field(default=2 * 1024 * 1024, ge=64 * 1024, le=16 * 1024 * 1024)

    first_admin_email: EmailStr | None = None
    first_admin_password: SecretStr | None = Field(default=None, min_length=8, max_length=128)
    first_admin_username: str | None = Field(default=None, min_length=1, max_length=50)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator(
        "first_admin_email",
        "first_admin_password",
        "first_admin_username",
        "google_drive_client_id",
        "google_drive_client_secret",
        "google_drive_refresh_token",
        mode="before",
    )
    @classmethod
    def _blank_env_value_means_unset(cls, value: object) -> object:
        # Empty-string env vars (e.g. `FIRST_ADMIN_EMAIL=` left blank in .env)
        # must mean "not configured", not "invalid email"/"password too short".
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @property
    def is_avatar_storage_configured(self) -> bool:
        return all(
            (
                self.google_drive_client_id,
                self.google_drive_client_secret,
                self.google_drive_refresh_token,
            )
        )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def validate_secure_production_settings(self) -> "Settings":
        secret = self.secret_key.get_secret_value()
        if self.environment in {"staging", "production"} and secret == DEFAULT_DEVELOPMENT_SECRET:
            raise ValueError("SECRET_KEY must be changed in staging and production")
        if len(secret) < 32:
            raise ValueError("SECRET_KEY must contain at least 32 characters")
        if self.environment in {"staging", "production"} and not self.is_avatar_storage_configured:
            raise ValueError(
                "GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET and "
                "GOOGLE_DRIVE_REFRESH_TOKEN must be set outside development"
            )
        if self.environment in {"staging", "production"} and self.debug:
            raise ValueError("DEBUG must be false in staging and production")
        if "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origins are not allowed")
        if self.smtp_use_ssl and self.smtp_start_tls:
            raise ValueError("SMTP_USE_SSL and SMTP_START_TLS cannot both be enabled")

        if (self.smtp_username is None) != (self.smtp_password is None):
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")

        if (self.first_admin_email is None) != (self.first_admin_password is None):
            raise ValueError(
                "FIRST_ADMIN_EMAIL and FIRST_ADMIN_PASSWORD must be configured together"
            )

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
