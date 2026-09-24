from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.money import COMMISSION_TIERS, SUPPORTED_KARATS
from app.modules.market import service as market

router = APIRouter(prefix="/api", tags=["System"])


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    price_cache_age_seconds: int | None
    price_source: str | None
    price_is_stale: bool | None
    time: datetime


class CommissionTier(BaseModel):
    label: str
    label_ar: str
    min_grams: Decimal | None
    max_grams: Decimal | None
    rate: Decimal


class PublicConfigOut(BaseModel):
    currency: str
    supported_karats: list[int]
    commission_tiers: list[CommissionTier]
    promotion_fee_iqd: Decimal
    promotion_duration_days: int
    subscription_price_iqd: Decimal
    subscription_duration_days: int
    quote_ttl_seconds: int


@router.get("/health", response_model=HealthOut, summary="DB + price cache health")
def health(db: Session = Depends(get_db)) -> HealthOut:
    now = datetime.now(UTC)
    try:
        db.execute(text("SELECT 1"))
        snapshot = market.latest_snapshot(db)
        database = "ok"
    except SQLAlchemyError:
        snapshot, database = None, "error"
    age = int((now - snapshot.fetched_at).total_seconds()) if snapshot else None
    stale = market.is_stale(snapshot, now) if snapshot else None
    ok = database == "ok" and snapshot is not None and not stale
    return HealthOut(
        status="ok" if ok else "degraded",
        database=database,
        price_cache_age_seconds=age,
        price_source=snapshot.source if snapshot else None,
        price_is_stale=stale,
        time=now,
    )


@router.get(
    "/config",
    response_model=PublicConfigOut,
    summary="Server-side business values the UI displays (fees, tiers). Read-only.",
)
def public_config() -> PublicConfigOut:
    settings = get_settings()
    return PublicConfigOut(
        currency="IQD",
        supported_karats=list(SUPPORTED_KARATS),
        commission_tiers=[CommissionTier(**t) for t in COMMISSION_TIERS],
        promotion_fee_iqd=settings.promotion_fee_iqd,
        promotion_duration_days=settings.promotion_duration_days,
        subscription_price_iqd=settings.subscription_price_iqd,
        subscription_duration_days=settings.subscription_duration_days,
        quote_ttl_seconds=settings.quote_ttl_seconds,
    )
