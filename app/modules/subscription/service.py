"""Subscription: monthly Premium via internal mock payment; daily expiry job (Workflow 07)."""

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import SubscriptionTier, User, UserRole
from app.modules.listings.schemas import PaymentOut
from app.modules.payments import service as payments


class SubscriptionStatusOut(BaseModel):
    subscription_tier: SubscriptionTier
    subscription_expiry_date: datetime | None
    is_active: bool
    days_remaining: int
    price_iqd: Decimal
    duration_days: int


class SubscribeOut(SubscriptionStatusOut):
    payment: PaymentOut


def status_of(user: User) -> SubscriptionStatusOut:
    settings = get_settings()
    now = datetime.now(UTC)
    expiry = user.subscription_expiry_date
    active = expiry is not None and expiry > now
    days = math.ceil((expiry - now).total_seconds() / 86400) if active and expiry else 0
    return SubscriptionStatusOut(
        subscription_tier=user.subscription_tier,
        subscription_expiry_date=expiry,
        is_active=active,
        days_remaining=days,
        price_iqd=settings.subscription_price_iqd,
        duration_days=settings.subscription_duration_days,
    )


def subscribe(db: Session, user: User) -> SubscribeOut:
    settings = get_settings()
    # Lock the user row so two concurrent renewals both count.
    locked = db.scalar(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert locked is not None
    payment = payments.charge(
        db,
        locked,
        amount_iqd=settings.subscription_price_iqd,
        purpose="subscription",
        context={"duration_days": settings.subscription_duration_days},
    )
    now = datetime.now(UTC)
    current = locked.subscription_expiry_date
    start = current if current is not None and current > now else now
    locked.subscription_expiry_date = start + timedelta(days=settings.subscription_duration_days)
    locked.subscription_tier = SubscriptionTier.premium
    db.commit()
    db.refresh(locked)
    return SubscribeOut(
        **status_of(locked).model_dump(),
        payment=PaymentOut(
            status=payment.status,
            payment_ref=payment.payment_ref,
            amount_iqd=payment.amount_iqd,
            purpose=payment.purpose,
        ),
    )


def expire_subscriptions(db: Session) -> int:
    """Daily job: premium users whose expiry passed go back to free. Returns rows changed."""
    result = db.execute(
        update(User)
        .where(
            User.role == UserRole.investor,
            User.subscription_tier == SubscriptionTier.premium,
            or_(
                User.subscription_expiry_date.is_(None),
                User.subscription_expiry_date <= datetime.now(UTC),
            ),
        )
        .values(subscription_tier=SubscriptionTier.free)
    )
    db.commit()
    return result.rowcount or 0
