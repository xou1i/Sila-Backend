"""Demo data for Sila (صِلة).

    python -m app.scripts.seed          # refuses to run on a non-empty database
    python -m app.scripts.seed --reset  # wipes ALL data first (demo databases only)

Purchases go through the real purchase service, so every ownership signature is valid.
"""

import argparse
import random
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.money import karat_price
from app.core.security import hash_password
from app.models import (
    AssetListing,
    ListingStatus,
    PriceSnapshot,
    RiskProfile,
    SubscriptionTier,
    Transaction,
    User,
    UserRole,
)
from app.modules.market import provider
from app.modules.market import service as market
from app.modules.orders import service as orders

DEMO_PASSWORD = "Sila@2026"  # noqa: S105 (public demo password, printed in the README)
_TABLES = (
    "audit_logs, transactions, fractional_ownership_records, asset_listings, users, price_snapshots"
)


def _user(email: str, name: str, role: UserRole, **extra: object) -> User:
    return User(
        email=email,
        full_name=name,
        role=role,
        password_hash=hash_password(DEMO_PASSWORD),
        **extra,
    )


def _price_history(db: Session, now: datetime) -> PriceSnapshot:
    """One year of synthetic history ending at the live price (or the configured fallback)."""
    try:
        xau, iqd = provider.fetch_quotes()
        source = "live"
    except provider.ProviderError:
        from app.core.config import get_settings

        xau, iqd = get_settings().fallback_xau_usd_per_ounce, get_settings().fallback_usd_iqd
        source = "fallback"

    rng = random.Random(2026)
    points: list[datetime] = []
    points += [now - timedelta(days=d) for d in range(365, 90, -1)]  # daily, 1Y..3M
    points += [now - timedelta(hours=h) for h in range(90 * 24, 7 * 24, -4)]  # 4-hourly
    points += [now - timedelta(minutes=m) for m in range(7 * 24 * 60, 24 * 60, -60)]  # hourly
    points += [now - timedelta(minutes=m) for m in range(24 * 60, 0, -5)]  # 5-min, last day

    # Random walk backwards from today's price (~+17.5% trend over the year).
    walk, previous = xau, now
    snapshots = []
    for ts in reversed(points):  # newest → oldest
        hours = Decimal((previous - ts).total_seconds()) / 3600
        shock = Decimal(str(round(rng.gauss(0, 0.0006), 6)))
        walk = (walk * (1 - Decimal("0.00002") * hours + shock)).quantize(Decimal("0.0001"))
        snapshots.append(market.make_snapshot(walk, iqd, "seed", fetched_at=ts))
        previous = ts
    db.add_all(snapshots)
    latest = market.make_snapshot(xau, iqd, source, fetched_at=now)
    db.add(latest)
    db.flush()
    return latest


def seed(db: Session) -> None:
    now = datetime.now(UTC)
    latest = _price_history(db, now)
    price_24k = latest.gold_24k_iqd_per_gram

    karrada = _user("karrada@sila.iq", "مجوهرات الكرّادة", UserRole.seller, kyc_verified=True)
    nahr = _user("nahr@sila.iq", "صاغة شارع النهر", UserRole.seller, kyc_verified=True)
    mansour = _user("mansour@sila.iq", "ذهب المنصور", UserRole.seller, kyc_verified=False)
    zainab = _user(
        "zainab@sila.iq",
        "زينب الموسوي",
        UserRole.investor,
        kyc_verified=True,
        risk_profile=RiskProfile.medium,
        subscription_tier=SubscriptionTier.premium,
        subscription_expiry_date=now + timedelta(days=21),
    )
    haider = _user(
        "haider@sila.iq",
        "حيدر العبيدي",
        UserRole.investor,
        kyc_verified=True,
        risk_profile=RiskProfile.high,
    )
    ali = _user(
        "ali@sila.iq",
        "علي الجبوري",
        UserRole.investor,
        kyc_verified=False,
        risk_profile=RiskProfile.low,
    )
    sara = _user(  # stale 'premium' tier with an expired date: the daily job flips it to free
        "sara@sila.iq",
        "سارة الدليمي",
        UserRole.investor,
        kyc_verified=True,
        risk_profile=RiskProfile.low,
        subscription_tier=SubscriptionTier.premium,
        subscription_expiry_date=now - timedelta(days=2),
    )
    db.add_all([karrada, nahr, mansour, zainab, haider, ali, sara])
    db.flush()

    specs = [
        # seller, grams, karat, days ago, status, promoted days left
        (karrada, "84.250", 21, 12, ListingStatus.active, 5),
        (karrada, "250.000", 24, 9, ListingStatus.active, None),
        (karrada, "37.500", 18, 7, ListingStatus.active, -1),  # expired promotion
        (nahr, "120.750", 22, 6, ListingStatus.active, None),
        (nahr, "512.300", 24, 4, ListingStatus.active, None),
        (nahr, "15.125", 21, 3, ListingStatus.active, None),
        (nahr, "64.800", 18, 2, ListingStatus.suspended, None),
        (karrada, "199.990", 22, 1, ListingStatus.active, None),
    ]
    listings = []
    for seller, grams, karat, days, status, promo in specs:
        listing = AssetListing(
            seller_id=seller.id,
            total_weight_grams=Decimal(grams),
            available_weight_grams=Decimal(grams),
            karat=karat,
            base_price_per_gram=karat_price(price_24k, karat),
            status=status,
            is_promoted=promo is not None,
            promotion_expiry_date=now + timedelta(days=promo) if promo is not None else None,
        )
        db.add(listing)
        db.flush()
        db.execute(
            text("UPDATE asset_listings SET created_at = :ts, updated_at = :ts WHERE id = :id"),
            {"ts": now - timedelta(days=days), "id": listing.id},
        )
        listings.append(listing)
    db.commit()

    purchases = [
        (zainab, listings[0], "10.000", 10),
        (zainab, listings[3], "55.500", 5),
        (haider, listings[1], "205.000", 8),
        (haider, listings[5], "15.125", 2),  # buys it all → sold_out
        (sara, listings[4], "3.750", 1),
    ]
    for investor, listing, grams, days in purchases:
        price = karat_price(price_24k, listing.karat)
        tx, _, _, _ = orders.execute_purchase(db, investor, listing.id, Decimal(grams), price)
        db.execute(
            text("UPDATE transactions SET created_at = :ts WHERE id = :id"),
            {"ts": now - timedelta(days=days), "id": tx.id},
        )
        db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="wipe all data before seeding")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.reset:
            db.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
            db.commit()
        elif db.scalar(select(func.count()).select_from(User)):
            sys.exit("Database already has users. Re-run with --reset to wipe and re-seed.")
        seed(db)
        users = db.scalar(select(func.count()).select_from(User))
        txs = db.scalar(select(func.count()).select_from(Transaction))
    print(f"Seeded {users} users, 8 listings, {txs} transactions and 1 year of price history.")
    print(f"All demo accounts use the password: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
