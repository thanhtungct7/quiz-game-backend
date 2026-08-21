from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_SECRET = "development-only-change-this-secret"  # noqa: S105


class Settings(BaseSettings):
    app_name: str = "Quiz Game API"
    environment: Literal["development", "testing", "staging", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    secret_key: SecretStr = SecretStr(DEFAULT_DEVELOPMENT_SECRET)
    access_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    refresh_token_expire_days: int = Field(default=30, ge=1, le=365)
    jwt_algorithm: Literal["HS256"] = "HS256"

    database_url: str = "postgresql+asyncpg://quiz:quiz@localhost:5432/quiz"
    cors_origins: list[str] = ["http://localhost:3000"]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "10.0.2.2", "testserver"]

    google_web_client_id: str = Field(min_length=1)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
        if self.environment in {"staging", "production"} and self.debug:
            raise ValueError("DEBUG must be false in staging and production")
        if "*" in self.cors_origins:
            raise ValueError("Wildcard CORS origins are not allowed")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
