"""Application settings, loaded from environment variables / `.env`."""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://sila:sila@localhost:5433/sila"

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
