"""Application settings, loaded from environment variables / `.env`."""

import os
from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _url_from_pg_env() -> str | None:
    env = os.environ
    if not all(env.get(k) for k in ("PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE")):
        return None
    return (
        f"postgresql://{quote(env['PGUSER'], safe='')}:{quote(env['PGPASSWORD'], safe='')}"
        f"@{env['PGHOST']}:{env.get('PGPORT', '5432')}/{env['PGDATABASE']}"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://sila:sila@localhost:5433/sila"

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, url: str) -> str:
        url = url.strip().strip("'\"").strip()
        if not url or "${{" in url:
            # Empty or an unresolved Railway reference: build it from the PG* variables
            # that a Railway/Heroku PostgreSQL service exposes, if they are present.
            url = _url_from_pg_env() or ""
        if not url:
            raise ValueError(
                "DATABASE_URL is empty or an unresolved reference. On Railway, set it on the "
                "API service to ${{<postgres-service>.DATABASE_URL}}, not on the Postgres service."
            )
        # Hosts like Railway/Heroku give postgres:// or postgresql://; we ship psycopg 3.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        if "://" not in url:
            raise ValueError("DATABASE_URL must look like postgresql://user:password@host:5432/db")
        return url

    # Secrets: required, never defaulted.
    jwt_secret: str = Field(min_length=32)
    ownership_signing_secret: str = Field(min_length=32)
    quote_signing_secret: str = Field(min_length=32)

    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    price_refresh_seconds: int = 60
    price_stale_seconds: int = 300
    price_fetch_timeout_seconds: float = 5
    gold_price_url: str = "https://api.gold-api.com/price/XAU"
    fx_rates_url: str = "https://open.er-api.com/v6/latest/USD"
    fallback_xau_usd_per_ounce: Decimal = Decimal("4250.00")
    fallback_usd_iqd: Decimal = Decimal("1310.00")
    scheduler_enabled: bool = True

    promotion_fee_iqd: Decimal = Decimal("25000")
    promotion_duration_days: int = 7
    subscription_price_iqd: Decimal = Decimal("15000")
    subscription_duration_days: int = 30
    quote_ttl_seconds: int = 60
    mock_payment_fail: bool = False

    rate_limit_enabled: bool = True
    rate_limit_login: str = "5/minute"
    rate_limit_kyc: str = "5/minute"
    rate_limit_ai: str = "20/minute"

    ai_api_key: str = ""
    ai_model: str = "claude-opus-5"
    ai_timeout_seconds: float = 6
    ai_match_min_grams: Decimal = Decimal("1")

    # AI Advisor (POST /api/ai/advisor). groq and gemini use an OpenAI-compatible Chat
    # Completions endpoint; anthropic reuses the SDK of llm.py; none = rule-based answer only.
    # Empty base URL / model = the provider defaults in app/modules/ai/providers.py.
    advisor_provider: Literal["none", "groq", "gemini", "anthropic"] = "none"
    advisor_base_url: str = ""
    advisor_api_key: str = ""
    advisor_model: str = ""
    advisor_timeout_seconds: float = 15
    # Reasoning models (gpt-oss) count their thinking in this limit, so keep room for it
    advisor_max_output_tokens: int = Field(default=2000, ge=50, le=8000)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
