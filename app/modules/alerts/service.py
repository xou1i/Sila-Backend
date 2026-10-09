"""Premium price alerts: the investor picks a karat and a target, and gets one in-app
notification when the live price crosses it. Checked after every price refresh."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError, ErrorCode
from app.core.money import SUPPORTED_KARATS, karat_price
from app.core.schemas import AmountIQD
from app.models import AlertDirection, AlertStatus, PriceAlert, PriceSnapshot, User
from app.modules.identity.service import is_premium_active
from app.modules.market import service as market
from app.modules.notifications import service as notifications

MAX_ACTIVE_ALERTS = 10


class AlertIn(BaseModel):
    karat: int = Field(examples=[21])
    direction: AlertDirection
    target_price_per_gram: AmountIQD


class AlertStatusIn(BaseModel):
    status: AlertStatus = Field(description="Only 'cancelled' can be set by the investor")


class AlertOut(BaseModel):
    id: uuid.UUID
    karat: int
    direction: AlertDirection
    target_price_per_gram: Decimal
    status: AlertStatus
    triggered_at: datetime | None
    created_at: datetime


def _out(alert: PriceAlert) -> AlertOut:
    return AlertOut.model_validate(alert, from_attributes=True)


def _require_premium(user: User) -> None:
    if not is_premium_active(user):
        raise AppError(ErrorCode.SUBSCRIPTION_REQUIRED, "تنبيهات الأسعار متاحة لمشتركي Premium")


def list_alerts(db: Session, user: User) -> list[AlertOut]:
    rows = db.scalars(
        select(PriceAlert)
        .where(PriceAlert.user_id == user.id)
        .order_by(PriceAlert.created_at.desc())
        .limit(50)
    ).all()
    return [_out(a) for a in rows]


def create_alert(db: Session, user: User, body: AlertIn) -> AlertOut:
    _require_premium(user)
    if body.karat not in SUPPORTED_KARATS:
        raise AppError(ErrorCode.VALIDATION_ERROR, "العيار لازم يكون 18 أو 21 أو 22 أو 24")
    active = db.scalars(
        select(PriceAlert.id).where(
            PriceAlert.user_id == user.id, PriceAlert.status == AlertStatus.active
        )
    ).all()
    if len(active) >= MAX_ACTIVE_ALERTS:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"الحد الأعلى {MAX_ACTIVE_ALERTS} تنبيهات فعّالة")
    # A target already reached would fire at once: ask for a meaningful one instead
    now_price = karat_price(market.require_snapshot(db).gold_24k_iqd_per_gram, body.karat)
    if body.direction == AlertDirection.above and body.target_price_per_gram <= now_price:
        raise AppError(ErrorCode.VALIDATION_ERROR, "السعر الحالي أعلى من هدفك، اختار سعر أعلى منه")
    if body.direction == AlertDirection.below and body.target_price_per_gram >= now_price:
        raise AppError(ErrorCode.VALIDATION_ERROR, "السعر الحالي أقل من هدفك، اختار سعر أقل منه")
    alert = PriceAlert(
        user_id=user.id,
        karat=body.karat,
        direction=body.direction,
        target_price_per_gram=body.target_price_per_gram,
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return _out(alert)


def cancel_alert(db: Session, user: User, alert_id: uuid.UUID, status: AlertStatus) -> AlertOut:
    if status != AlertStatus.cancelled:
        raise AppError(ErrorCode.VALIDATION_ERROR, "تكدر بس تلغي التنبيه")
    alert = db.get(PriceAlert, alert_id)
    if alert is None or alert.user_id != user.id:
        raise AppError(ErrorCode.NOT_FOUND, "التنبيه غير موجود")
    if alert.status == AlertStatus.active:
        alert.status = AlertStatus.cancelled
        db.commit()
    return _out(alert)


def check_alerts(db: Session, snapshot: PriceSnapshot) -> int:
    """Fire every active alert the new price crossed. Returns how many fired."""
    fired = 0
    alerts = db.scalars(
        select(PriceAlert).where(PriceAlert.status == AlertStatus.active).with_for_update()
    ).all()
    now = datetime.now(UTC)
    for alert in alerts:
        price = karat_price(snapshot.gold_24k_iqd_per_gram, alert.karat)
        crossed = (
            price >= alert.target_price_per_gram
            if alert.direction == AlertDirection.above
            else price <= alert.target_price_per_gram
        )
        if not crossed:
            continue
        alert.status = AlertStatus.triggered
        alert.triggered_at = now
        verb = "ارتفع إلى" if alert.direction == AlertDirection.above else "نزل إلى"
        notifications.notify(
            db,
            alert.user_id,
            "price_alert",
            f"تنبيه سعر عيار {alert.karat}",
            f"سعر غرام عيار {alert.karat} {verb} {price:,.0f} دينار "
            f"(هدفك {alert.target_price_per_gram:,.0f} دينار).",
            "/app/market",
        )
        audit(
            db,
            "price_alert_triggered",
            actor_id=alert.user_id,
            entity_type="price_alert",
            entity_id=alert.id,
            data={"karat": alert.karat, "price": price, "target": alert.target_price_per_gram},
        )
        fired += 1
    db.commit()
    return fired
