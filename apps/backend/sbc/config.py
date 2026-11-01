"""Zentrale Konfiguration. Alle Werte kommen aus Umgebungsvariablen (Railway Variables).

Es werden keine Zugangsdaten im Code hinterlegt. Fehlende optionale Zugangsdaten
deaktivieren die jeweilige Integration sichtbar (Status "nicht konfiguriert"),
statt Ergebnisse zu simulieren.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_secret: str = Field(default="dev-insecure-secret-change-me-0123456789", min_length=32)
    log_level: str = "INFO"

    database_url: str = "sqlite:///./.data/dev.db"
    redis_url: str | None = None

    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    cors_origins: str = ""  # zusätzliche Origins, kommagetrennt

    # Bootstrap des ersten Admins (nur wenn noch kein Nutzer existiert)
    initial_admin_email: str | None = None
    initial_admin_password: str | None = None
    initial_org_name: str = "SBC symbio GmbH & Co. KG"

    # Objektspeicher (Railway Bucket oder anderer S3-kompatibler Speicher)
    storage_backend: Literal["s3", "local"] = "s3"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "auto"
    s3_force_path_style: bool = False
    local_storage_dir: str = "./.data/storage"
    file_retention_days: int = 180
    max_upload_mb: int = 100

    # KI
    anthropic_api_key: str | None = None
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    openai_api_key: str | None = None

    # Marktplätze
    keepa_api_key: str | None = None
    keepa_cache_ttl_hours: int = 12
    # Amazon SP-API (LWA-Refresh-Token-Flow). JSON mit client_id, client_secret,
    # refresh_token, seller_id; Region EU.
    amazon_sp_api_credentials: str | None = None
    ebay_client_id: str | None = None
    ebay_client_secret: str | None = None
    ebay_environment: Literal["production", "sandbox"] = "production"

    # Mailversand ist standardmäßig aus (keine automatischen E-Mails ohne Konfiguration)
    smtp_url: str | None = None
    report_recipients: str = ""

    access_token_minutes: int = 60 * 12

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Railway liefert postgresql://...; SQLAlchemy braucht den psycopg3-Treiber.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env == "production":
            if self.app_secret.startswith("dev-insecure"):
                raise ValueError("APP_SECRET muss in Produktion gesetzt sein")
            if self.storage_backend == "local":
                raise ValueError(
                    "STORAGE_BACKEND=local ist in Produktion nicht erlaubt "
                    "(Railway-Dateisystem ist flüchtig)"
                )
            if self.database_url.startswith("sqlite"):
                raise ValueError("DATABASE_URL muss in Produktion auf PostgreSQL zeigen")
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def allowed_origins(self) -> list[str]:
        extra = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return [self.frontend_url, *extra]


@lru_cache
def get_settings() -> Settings:
    return Settings()
