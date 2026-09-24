"""ORM models. Types, constraints, indexes and FK actions follow 06_erd_database_design.md."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class UserRole(StrEnum):
    investor = "investor"
    seller = "seller"


class RiskProfile(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class SubscriptionTier(StrEnum):
    free = "free"
    premium = "premium"


class ListingStatus(StrEnum):
    active = "active"
    sold_out = "sold_out"
    suspended = "suspended"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    role: Mapped[UserRole] = mapped_column(_pg_enum(UserRole, "user_role"), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    kyc_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    risk_profile: Mapped[RiskProfile | None] = mapped_column(
        _pg_enum(RiskProfile, "risk_profile"), nullable=True
    )
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        _pg_enum(SubscriptionTier, "subscription_tier"),
        nullable=False,
        default=SubscriptionTier.free,
        server_default=SubscriptionTier.free.value,
    )
    subscription_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    listings: Mapped[list["AssetListing"]] = relationship(back_populates="seller")

    __table_args__ = (
        Index("idx_users_email", "email", unique=True),
        Index("idx_users_role", "role"),
        Index("idx_users_sub_expiry", "subscription_expiry_date"),
    )


class AssetListing(Base):
    __tablename__ = "asset_listings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    seller_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    total_weight_grams: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    available_weight_grams: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    karat: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    base_price_per_gram: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[ListingStatus] = mapped_column(
        _pg_enum(ListingStatus, "listing_status"),
        nullable=False,
        default=ListingStatus.active,
        server_default=ListingStatus.active.value,
    )
    is_promoted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    promotion_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Additive (DECISIONS D-17): de-duplicates a retried "publish".
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    seller: Mapped[User] = relationship(back_populates="listings")

    __table_args__ = (
        CheckConstraint("total_weight_grams > 0", name="ck_listings_total_positive"),
        CheckConstraint("available_weight_grams >= 0", name="ck_listings_available_non_negative"),
        CheckConstraint(
            "available_weight_grams <= total_weight_grams", name="ck_listings_available_le_total"
        ),
        CheckConstraint("karat IN (18, 21, 22, 24)", name="ck_listings_karat"),
        Index("idx_listings_seller", "seller_id"),
        Index("idx_listings_status", "status"),
        Index("idx_listings_promoted", "is_promoted", "promotion_expiry_date"),
        Index("uq_listings_seller_idempotency", "seller_id", "idempotency_key", unique=True),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset_listings.id", ondelete="RESTRICT"), nullable=False
    )
    purchased_weight_grams: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    execution_price_per_gram: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    total_paid_by_investor: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    # Additive (DECISIONS D-17): Idempotency-Key header of the confirm request.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _created_at()

    asset: Mapped[AssetListing] = relationship()

    __table_args__ = (
        CheckConstraint("purchased_weight_grams > 0", name="ck_tx_purchased_positive"),
        Index("idx_tx_investor", "investor_id"),
        Index("idx_tx_asset", "asset_id"),
        Index("idx_tx_created", "created_at"),
        Index("uq_tx_investor_idempotency", "investor_id", "idempotency_key", unique=True),
    )


class FractionalOwnershipRecord(Base):
    __tablename__ = "fractional_ownership_records"

    id: Mapped[uuid.UUID] = _uuid_pk()
    investor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    total_accumulated_grams: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), nullable=False, default=Decimal("0"), server_default=text("0")
    )
    digital_signature_token: Mapped[str] = mapped_column(String(512), nullable=False)
    # Written explicitly by the service (no onupdate): it is part of the signed message.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("total_accumulated_grams >= 0", name="ck_ownership_non_negative"),
        Index("idx_ownership_investor", "investor_id", unique=True),
    )


class PriceSnapshot(Base):
    """Market-data cache + chart history (DECISIONS D-10)."""

    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    xau_usd_per_ounce: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    usd_iqd: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    gold_24k_iqd_per_gram: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("idx_price_snapshots_fetched_at", "fetched_at"),)


class AuditLog(Base):
    """Security/financial audit trail (DECISIONS D-14). Append-only."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        Index("idx_audit_event_type", "event_type"),
        Index("idx_audit_created", "created_at"),
    )
