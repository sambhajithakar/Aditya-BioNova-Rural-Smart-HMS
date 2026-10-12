"""Environment-based application configuration.

All secrets come from the environment (or a local git-ignored .env).
No secret is ever hard-coded for production use.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Aditya BioNova Rural Smart HMS API"
    environment: str = "development"

    # Runtime data access: the NON-OWNER application role. PostgreSQL row-level
    # security is enforced for this role (the table owner would bypass RLS).
    database_url: str = "postgresql+psycopg2://hms_app:change_me@127.0.0.1:5433/hms_dev"
    # DDL / migrations only: the table-owner role. Never used to serve requests.
    migration_database_url: str = "postgresql+psycopg2://hms:change_me@127.0.0.1:5433/hms_dev"

    jwt_secret: str = "insecure-dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "aditya-bionova-hms"
    access_token_ttl_seconds: int = Field(default=1800, ge=60)
    refresh_token_ttl_seconds: int = Field(default=60 * 60 * 24 * 14, ge=300)

    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    @field_validator("jwt_secret")
    @classmethod
    def _warn_dev_secret(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("JWT_SECRET must be at least 16 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
