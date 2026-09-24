"""Market Data: price cache (latest `price_snapshots` row), karat engine, history (D-10)."""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.money import SUPPORTED_KARATS, gold_24k_iqd_per_gram, karat_price, round_money
from app.models import PriceSnapshot
from app.modules.market import provider
from app.modules.market.schemas import (
    HistoryRange,
    KaratPrice,
    MarketPricesOut,
    PriceHistoryOut,
    PricePoint,
)

logger = logging.getLogger("sila.market")

_HISTORY: dict[str, tuple[timedelta, timedelta]] = {
    # range: (lookback, bucket)
    "1D": (timedelta(days=1), timedelta(minutes=10)),
    "1W": (timedelta(days=7), timedelta(hours=1)),
    "1M": (timedelta(days=30), timedelta(hours=4)),
    "3M": (timedelta(days=90), timedelta(hours=12)),
    "1Y": (timedelta(days=365), timedelta(days=1)),
}
_BIN_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)


def make_snapshot(
    xau: Decimal, usd_iqd: Decimal, source: str, fetched_at: datetime | None = None
) -> PriceSnapshot:
    return PriceSnapshot(
        fetched_at=fetched_at or datetime.now(UTC),
        xau_usd_per_ounce=xau,
        usd_iqd=usd_iqd,
        gold_24k_iqd_per_gram=gold_24k_iqd_per_gram(xau, usd_iqd),
        source=source,
    )


def latest_snapshot(db: Session) -> PriceSnapshot | None:
    return db.scalar(select(PriceSnapshot).order_by(PriceSnapshot.fetched_at.desc()).limit(1))


def require_snapshot(db: Session) -> PriceSnapshot:
    snapshot = latest_snapshot(db)
    if snapshot is None:
        raise AppError(ErrorCode.PRICE_UNAVAILABLE)
    return snapshot


def refresh_prices(db: Session) -> PriceSnapshot | None:
    """Background job body. On provider failure keep serving the last snapshot."""
    try:
        xau, usd_iqd = provider.fetch_quotes()
    except provider.ProviderError as exc:
        logger.warning("price refresh failed, serving cached price: %s", exc)
        if latest_snapshot(db) is not None:
            return None
        settings = get_settings()
        snapshot = make_snapshot(
            settings.fallback_xau_usd_per_ounce, settings.fallback_usd_iqd, "fallback"
        )
    else:
        snapshot = make_snapshot(xau, usd_iqd, "live")
    db.add(snapshot)
    db.commit()
    return snapshot


def snapshot_at_or_before(db: Session, ts: datetime) -> PriceSnapshot | None:
    return db.scalar(
        select(PriceSnapshot)
        .where(PriceSnapshot.fetched_at <= ts)
        .order_by(PriceSnapshot.fetched_at.desc())
        .limit(1)
    )


def pct_change(old: Decimal, new: Decimal) -> Decimal | None:
    if old <= 0:
        return None
    return round_money((new - old) / old * 100)


def change_since(db: Session, current: PriceSnapshot, delta: timedelta) -> Decimal | None:
    previous = snapshot_at_or_before(db, current.fetched_at - delta)
    if previous is None:
        return None
    return pct_change(previous.gold_24k_iqd_per_gram, current.gold_24k_iqd_per_gram)


def is_stale(snapshot: PriceSnapshot, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return (now - snapshot.fetched_at).total_seconds() > get_settings().price_stale_seconds


def market_prices(db: Session) -> MarketPricesOut:
    snapshot = require_snapshot(db)
    karats = [
        KaratPrice(
            karat=k,
            price_per_gram_iqd=karat_price(snapshot.gold_24k_iqd_per_gram, k),
            price_per_gram_usd=round_money(
                karat_price(snapshot.gold_24k_iqd_per_gram, k) / snapshot.usd_iqd
            ),
        )
        for k in SUPPORTED_KARATS
    ]
    return MarketPricesOut(
        karats=karats,
        usd_iqd=snapshot.usd_iqd,
        xau_usd_per_ounce=snapshot.xau_usd_per_ounce,
        change_24h_pct=change_since(db, snapshot, timedelta(hours=24)),
        updated_at=snapshot.fetched_at,
        source=snapshot.source,
        is_stale=is_stale(snapshot),
    )


def price_history(db: Session, karat: int, range_: HistoryRange) -> PriceHistoryOut:
    lookback, bucket = _HISTORY[range_]
    since = datetime.now(UTC) - lookback
    ts = func.date_bin(bucket, PriceSnapshot.fetched_at, literal(_BIN_ORIGIN)).label("ts")
    rows = db.execute(
        select(ts, func.avg(PriceSnapshot.gold_24k_iqd_per_gram))
        .where(PriceSnapshot.fetched_at >= since)
        .group_by(ts)
        .order_by(ts)
    ).all()
    points = [PricePoint(ts=row[0], price_per_gram=karat_price(row[1], karat)) for row in rows]
    return PriceHistoryOut(karat=karat, range=range_, points=points)


def stats_since(
    db: Session, delta: timedelta
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """(average, min, max) of the 24K IQD/gram price over the last `delta`."""
    since = datetime.now(UTC) - delta
    col = PriceSnapshot.gold_24k_iqd_per_gram
    avg, low, high = db.execute(
        select(func.avg(col), func.min(col), func.max(col)).where(PriceSnapshot.fetched_at >= since)
    ).one()
    return (round_money(avg) if avg is not None else None, low, high)
