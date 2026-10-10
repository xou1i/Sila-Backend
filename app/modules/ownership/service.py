"""Ownership: signed fractional-ownership record (Workflow 08-A, DECISIONS D-15) and investor
resale (Workflow 09, Domain Model v4).

Holdings by karat and reserved grams are derived, never stored: holdings come from the
permanent transaction log (bought minus sold through resale), reserved grams from the investor's
open resale listings. The signed total therefore keeps its meaning and its signature format.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError, ErrorCode
from app.core.money import MILLIGRAM, SUPPORTED_KARATS, karat_price
from app.core.schemas import Grams
from app.core.signature import sign_ownership, verify_ownership
from app.models import (
    AssetListing,
    FractionalOwnershipRecord,
    ListingStatus,
    ListingType,
    Transaction,
    User,
)
from app.modules.market import service as market

DISCLAIMER = (
    "التوقيع الرقمي إثبات تقني داخلي لسلامة رصيدك داخل منصة صِلة، "
    "وليس سند ملكية قانونياً معترفاً به رسمياً."
)
# Reserved by a resale listing: still owned (and in the signed total) until sold
_RESERVING = (ListingStatus.active, ListingStatus.suspended)


class KaratBalance(BaseModel):
    karat: int
    owned_grams: Decimal
    reserved_grams: Decimal = Field(description="Offered in open resale listings")
    available_to_resell_grams: Decimal


class OwnershipOut(BaseModel):
    investor_id: uuid.UUID
    total_accumulated_grams: Decimal
    verified: bool = Field(description="Signature checked on this request")
    digital_signature_token: str | None
    updated_at: datetime | None
    by_karat: list[KaratBalance] = Field(
        default_factory=list, description="Derived from the transaction log; sums to the total"
    )
    message: str | None = Field(default=None, description="Shown when the investor owns nothing")
    disclaimer: str = DISCLAIMER


def _integrity_failure(
    db: Session, investor_id: uuid.UUID, record: FractionalOwnershipRecord, context: str
) -> AppError:
    """Record a security incident in its own commit, then return the error to raise."""
    record_id = record.id
    db.rollback()
    audit(
        db,
        "integrity_check_failed",
        actor_id=investor_id,
        entity_type="fractional_ownership_record",
        entity_id=record_id,
        data={"context": context, "severity": "critical"},
    )
    db.commit()
    return AppError(ErrorCode.INTEGRITY_CHECK_FAILED)


def is_valid(record: FractionalOwnershipRecord) -> bool:
    return verify_ownership(
        record.investor_id,
        record.total_accumulated_grams,
        record.updated_at,
        record.digital_signature_token,
    )


# ---------------------------------------------------------------- holdings by karat


def holdings_by_karat(db: Session, investor_id: uuid.UUID) -> dict[int, Decimal]:
    """Grams bought minus grams sold through resale, per karat."""
    bought = db.execute(
        select(AssetListing.karat, func.sum(Transaction.purchased_weight_grams))
        .join(AssetListing, Transaction.asset_id == AssetListing.id)
        .where(Transaction.investor_id == investor_id)
        .group_by(AssetListing.karat)
    ).all()
    sold = db.execute(
        select(AssetListing.karat, func.sum(Transaction.purchased_weight_grams))
        .join(AssetListing, Transaction.asset_id == AssetListing.id)
        .where(
            AssetListing.seller_id == investor_id,
            AssetListing.listing_type == ListingType.investor_resale,
        )
        .group_by(AssetListing.karat)
    ).all()
    out = {karat: Decimal("0") for karat in SUPPORTED_KARATS}
    for karat, grams in bought:
        out[karat] += grams
    for karat, grams in sold:
        out[karat] -= grams
    return out


def reserved_by_karat(db: Session, investor_id: uuid.UUID) -> dict[int, Decimal]:
    rows = db.execute(
        select(AssetListing.karat, func.sum(AssetListing.available_weight_grams))
        .where(
            AssetListing.seller_id == investor_id,
            AssetListing.listing_type == ListingType.investor_resale,
            AssetListing.status.in_(_RESERVING),
        )
        .group_by(AssetListing.karat)
    ).all()
    out = {karat: Decimal("0") for karat in SUPPORTED_KARATS}
    for karat, grams in rows:
        out[karat] += grams
    return out


def balances(db: Session, investor_id: uuid.UUID) -> list[KaratBalance]:
    owned = holdings_by_karat(db, investor_id)
    reserved = reserved_by_karat(db, investor_id)
    return [
        KaratBalance(
            karat=k,
            owned_grams=owned[k].quantize(MILLIGRAM),
            reserved_grams=reserved[k].quantize(MILLIGRAM),
            available_to_resell_grams=max(owned[k] - reserved[k], Decimal("0")).quantize(MILLIGRAM),
        )
        for k in sorted(SUPPORTED_KARATS, reverse=True)
        if owned[k] > 0 or reserved[k] > 0
    ]


def get_verified(db: Session, investor: User) -> OwnershipOut:
    record = db.scalar(
        select(FractionalOwnershipRecord).where(
            FractionalOwnershipRecord.investor_id == investor.id
        )
    )
    if record is None:
        return OwnershipOut(
            investor_id=investor.id,
            total_accumulated_grams=Decimal("0.000"),
            verified=True,
            digital_signature_token=None,
            updated_at=None,
            message="ابدأ أول استثمار",
        )
    if not is_valid(record):
        raise _integrity_failure(db, investor.id, record, "GET /api/ownership/me")
    return OwnershipOut(
        investor_id=investor.id,
        total_accumulated_grams=record.total_accumulated_grams,
        verified=True,
        digital_signature_token=record.digital_signature_token,
        updated_at=record.updated_at,
        by_karat=balances(db, investor.id),
    )


# ---------------------------------------------------------------- signed balance changes


def ensure_record(db: Session, investor_id: uuid.UUID) -> bool:
    """Create an empty record if missing (inside the caller's transaction). True if created."""
    inserted = db.execute(
        insert(FractionalOwnershipRecord)
        .values(
            investor_id=investor_id,
            total_accumulated_grams=Decimal("0"),
            digital_signature_token="",  # signed by the caller before commit
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["investor_id"])
        .returning(FractionalOwnershipRecord.id)
    ).first()
    return inserted is not None


def _locked(db: Session, investor_id: uuid.UUID) -> FractionalOwnershipRecord | None:
    return db.scalar(
        select(FractionalOwnershipRecord)
        .where(FractionalOwnershipRecord.investor_id == investor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_records(db: Session, investor_ids: list[uuid.UUID]) -> None:
    """Lock several records in one fixed order (by id), so two crossing resale purchases
    (A buys from B while B buys from A) wait for each other instead of deadlocking."""
    for investor_id in sorted(set(investor_ids), key=str):
        _locked(db, investor_id)


def _resign(
    db: Session,
    record: FractionalOwnershipRecord,
    delta: Decimal,
    context: dict[str, object],
) -> FractionalOwnershipRecord:
    now = datetime.now(UTC)
    previous = record.total_accumulated_grams
    record.total_accumulated_grams = previous + delta
    record.updated_at = now
    record.digital_signature_token = sign_ownership(
        record.investor_id, record.total_accumulated_grams, now
    )
    audit(
        db,
        "ownership_updated",
        actor_id=record.investor_id,
        entity_type="fractional_ownership_record",
        entity_id=record.id,
        data={
            "previous_grams": previous,
            "delta_grams": delta,
            "total_accumulated_grams": record.total_accumulated_grams,
            **context,
        },
    )
    return record


def add_grams(
    db: Session, investor_id: uuid.UUID, grams: Decimal, context: dict[str, object]
) -> FractionalOwnershipRecord:
    """Inside the caller's transaction: lock/create the record, verify, add, re-sign."""
    created = ensure_record(db, investor_id)
    record = _locked(db, investor_id)
    assert record is not None
    # Never re-sign a balance that was tampered with: that would launder the tampering.
    if not created and not is_valid(record):
        raise _integrity_failure(db, investor_id, record, "purchase")
    return _resign(db, record, grams, context)


def remove_grams(
    db: Session, investor_id: uuid.UUID, grams: Decimal, context: dict[str, object]
) -> FractionalOwnershipRecord:
    """Inside the caller's transaction: the resale seller's side of a transfer."""
    record = _locked(db, investor_id)
    if record is None or not is_valid(record):
        if record is None:
            raise AppError(ErrorCode.INSUFFICIENT_HOLDINGS)
        raise _integrity_failure(db, investor_id, record, "resale transfer")
    if record.total_accumulated_grams < grams:
        raise AppError(ErrorCode.INSUFFICIENT_HOLDINGS)
    return _resign(db, record, -grams, context)


# ---------------------------------------------------------------- investor resale


class ResaleIn(BaseModel):
    karat: int = Field(examples=[21])
    weight_grams: Grams


class ResaleStatusIn(BaseModel):
    status: str = Field(
        pattern="^(active|suspended|withdrawn)$",
        description="suspended keeps the grams reserved; withdrawn is final and releases them",
    )


def _resale_by_key(db: Session, investor: User, key: str) -> AssetListing | None:
    return db.scalar(
        select(AssetListing).where(
            AssetListing.seller_id == investor.id,
            AssetListing.idempotency_key == key,
            AssetListing.listing_type == ListingType.investor_resale,
        )
    )


def create_resale(
    db: Session, investor: User, body: ResaleIn, idempotency_key: str | None
) -> tuple[AssetListing, bool]:
    if body.karat not in SUPPORTED_KARATS:
        raise AppError(ErrorCode.VALIDATION_ERROR, "العيار لازم يكون 18 أو 21 أو 22 أو 24")
    if not investor.kyc_verified:
        raise AppError(ErrorCode.KYC_NOT_VERIFIED, "يجب توثيق حسابك قبل عرض ذهبك للبيع")
    if idempotency_key and (existing := _resale_by_key(db, investor, idempotency_key)):
        return existing, False

    grams = body.weight_grams.quantize(MILLIGRAM)
    # The record lock serializes every resale of this investor: no double reservation
    record = _locked(db, investor.id)
    if record is None:
        raise AppError(ErrorCode.INSUFFICIENT_HOLDINGS)
    if not is_valid(record):
        raise _integrity_failure(db, investor.id, record, "resale listing")
    available = (
        holdings_by_karat(db, investor.id)[body.karat]
        - reserved_by_karat(db, investor.id)[body.karat]
    )
    if grams > available:
        left = max(available, Decimal("0")).quantize(MILLIGRAM)
        raise AppError(
            ErrorCode.INSUFFICIENT_HOLDINGS,
            f"المتاح للبيع من عيار {body.karat}: {left} غرام",
        )

    price = karat_price(market.require_snapshot(db).gold_24k_iqd_per_gram, body.karat)
    listing = AssetListing(
        seller_id=investor.id,
        total_weight_grams=grams,
        available_weight_grams=grams,
        karat=body.karat,
        base_price_per_gram=price,
        status=ListingStatus.active,
        is_promoted=False,
        listing_type=ListingType.investor_resale,
        idempotency_key=idempotency_key,
    )
    db.add(listing)
    try:
        db.flush()
        audit(
            db,
            "resale_listed",
            actor_id=investor.id,
            entity_type="asset_listing",
            entity_id=listing.id,
            data={"karat": body.karat, "grams": grams, "base_price_per_gram": price},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _resale_by_key(db, investor, idempotency_key) if idempotency_key else None
        if existing is None:
            raise
        return existing, False
    return listing, True


def my_resales(db: Session, investor: User) -> list[AssetListing]:
    return list(
        db.scalars(
            select(AssetListing)
            .where(
                AssetListing.seller_id == investor.id,
                AssetListing.listing_type == ListingType.investor_resale,
            )
            .order_by(
                case((AssetListing.status.in_(_RESERVING), 0), else_=1),
                AssetListing.created_at.desc(),
            )
        ).all()
    )


def update_resale_status(
    db: Session, investor: User, listing_id: uuid.UUID, new_status: str
) -> AssetListing:
    listing = db.scalar(
        select(AssetListing)
        .where(AssetListing.id == listing_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        listing is None
        or listing.seller_id != investor.id
        or listing.listing_type != ListingType.investor_resale
    ):
        raise AppError(ErrorCode.FORBIDDEN, "هذا العرض مو من عروض إعادة البيع مالتك")
    if listing.status in (ListingStatus.sold_out, ListingStatus.withdrawn):
        raise AppError(ErrorCode.INVALID_STATUS_TRANSITION, "العرض منتهي ولا يمكن تغيير حالته")
    target = ListingStatus(new_status)
    if listing.status != target:
        audit(
            db,
            "resale_status_changed",
            actor_id=investor.id,
            entity_type="asset_listing",
            entity_id=listing.id,
            data={
                "from": listing.status.value,
                "to": target.value,
                "released_grams": listing.available_weight_grams
                if target == ListingStatus.withdrawn
                else Decimal("0"),
            },
        )
        listing.status = target
    db.commit()
    return listing
