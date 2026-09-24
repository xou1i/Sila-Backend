"""Listing & Asset: create (KYC-gated, server-priced), browse, status, promotion."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.money import karat_price
from app.core.schemas import Page
from app.models import AssetListing, ListingStatus, User
from app.modules.listings.schemas import (
    ListingCreateIn,
    ListingOut,
    ListingSort,
    PaymentOut,
    PromoteOut,
)
from app.modules.market import service as market
from app.modules.payments import service as payments

_NOT_OWNER = "لا يمكنك تعديل عرض لا تملكه"


def effective_promoted(listing: AssetListing, now: datetime) -> bool:
    return bool(
        listing.is_promoted
        and listing.promotion_expiry_date is not None
        and listing.promotion_expiry_date > now
    )


def to_out(listing: AssetListing, price_24k: Decimal | None, now: datetime) -> ListingOut:
    return ListingOut(
        id=listing.id,
        seller_id=listing.seller_id,
        seller_name=listing.seller.full_name,
        seller_kyc_verified=listing.seller.kyc_verified,
        karat=listing.karat,
        total_weight_grams=listing.total_weight_grams,
        available_weight_grams=listing.available_weight_grams,
        base_price_per_gram=listing.base_price_per_gram,
        current_price_per_gram=karat_price(price_24k, listing.karat) if price_24k else None,
        status=listing.status,
        is_promoted=effective_promoted(listing, now),
        promotion_expiry_date=listing.promotion_expiry_date,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


def _price_24k(db: Session) -> Decimal | None:
    snapshot = market.latest_snapshot(db)
    return snapshot.gold_24k_iqd_per_gram if snapshot else None


def listing_out(db: Session, listing: AssetListing) -> ListingOut:
    return to_out(listing, _price_24k(db), datetime.now(UTC))


def get_listing(db: Session, listing_id: uuid.UUID) -> AssetListing:
    listing = db.scalar(
        select(AssetListing)
        .options(joinedload(AssetListing.seller))
        .where(AssetListing.id == listing_id)
    )
    if listing is None:
        raise AppError(ErrorCode.NOT_FOUND, "العرض غير موجود")
    return listing


def _lock_listing(db: Session, listing_id: uuid.UUID) -> AssetListing:
    listing = db.scalar(
        select(AssetListing)
        .where(AssetListing.id == listing_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if listing is None:
        raise AppError(ErrorCode.NOT_FOUND, "العرض غير موجود")
    return listing


def _by_idempotency_key(db: Session, seller: User, key: str) -> AssetListing | None:
    return db.scalar(
        select(AssetListing).where(
            AssetListing.seller_id == seller.id, AssetListing.idempotency_key == key
        )
    )


def create_listing(
    db: Session, seller: User, data: ListingCreateIn, idempotency_key: str | None
) -> tuple[AssetListing, bool]:
    """Returns (listing, created). created=False means an idempotent replay."""
    if not seller.kyc_verified:
        raise AppError(ErrorCode.KYC_NOT_VERIFIED, "يجب توثيق حسابك عبر توقيعك قبل نشر العرض")
    if idempotency_key and (existing := _by_idempotency_key(db, seller, idempotency_key)):
        return existing, False

    price = karat_price(market.require_snapshot(db).gold_24k_iqd_per_gram, data.karat)
    listing = AssetListing(
        seller_id=seller.id,
        total_weight_grams=data.total_weight_grams,
        available_weight_grams=data.total_weight_grams,
        karat=data.karat,
        base_price_per_gram=price,
        status=ListingStatus.active,
        is_promoted=False,
        idempotency_key=idempotency_key,
    )
    db.add(listing)
    try:
        db.flush()
        audit(
            db,
            "listing_created",
            actor_id=seller.id,
            entity_type="asset_listing",
            entity_id=listing.id,
            data={
                "karat": data.karat,
                "total_weight_grams": data.total_weight_grams,
                "base_price_per_gram": price,
            },
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _by_idempotency_key(db, seller, idempotency_key) if idempotency_key else None
        if existing is None:
            raise
        return existing, False
    return get_listing(db, listing.id), True


def browse(
    db: Session,
    *,
    viewer: User | None,
    seller_id: str | None,
    karat: int | None,
    min_price: Decimal | None,
    max_price: Decimal | None,
    status: ListingStatus | None,
    sort: ListingSort,
    limit: int,
    offset: int,
) -> Page[ListingOut]:
    now = datetime.now(UTC)
    query = select(AssetListing)

    if seller_id == "me":
        if viewer is None:
            raise AppError(ErrorCode.UNAUTHORIZED)
        if viewer.role.value != "seller":
            raise AppError(ErrorCode.FORBIDDEN, "هذه العملية متاحة للبائعين فقط")
        query = query.where(AssetListing.seller_id == viewer.id)
        if status is not None:
            query = query.where(AssetListing.status == status)
    else:
        if seller_id is not None:
            try:
                query = query.where(AssetListing.seller_id == uuid.UUID(seller_id))
            except ValueError as exc:
                raise AppError(ErrorCode.VALIDATION_ERROR, "seller_id غير صالح") from exc
        # Public browsing shows active listings only.
        query = query.where(AssetListing.status == ListingStatus.active)

    if karat is not None:
        query = query.where(AssetListing.karat == karat)
    if min_price is not None:
        query = query.where(AssetListing.base_price_per_gram >= min_price)
    if max_price is not None:
        query = query.where(AssetListing.base_price_per_gram <= max_price)

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0

    promoted_now = and_(
        AssetListing.is_promoted.is_(True), AssetListing.promotion_expiry_date > now
    )
    order = {
        "promoted_first": [
            case((promoted_now, 1), else_=0).desc(),
            AssetListing.created_at.desc(),
        ],
        "newest": [AssetListing.created_at.desc()],
        "price_asc": [AssetListing.base_price_per_gram.asc(), AssetListing.created_at.desc()],
        "price_desc": [AssetListing.base_price_per_gram.desc(), AssetListing.created_at.desc()],
    }[sort]
    rows = db.scalars(
        query.options(joinedload(AssetListing.seller))
        .order_by(*order, AssetListing.id)
        .limit(limit)
        .offset(offset)
    ).all()
    price_24k = _price_24k(db)
    return Page[ListingOut](
        items=[to_out(r, price_24k, now) for r in rows], total=total, limit=limit, offset=offset
    )


def update_status(
    db: Session, seller: User, listing_id: uuid.UUID, new_status: str
) -> AssetListing:
    listing = _lock_listing(db, listing_id)
    if listing.seller_id != seller.id:
        raise AppError(ErrorCode.FORBIDDEN, _NOT_OWNER)
    if listing.status == ListingStatus.sold_out:
        raise AppError(
            ErrorCode.INVALID_STATUS_TRANSITION, "العرض نفد بالكامل ولا يمكن تغيير حالته"
        )
    target = ListingStatus(new_status)
    if listing.status != target:
        audit(
            db,
            "listing_status_changed",
            actor_id=seller.id,
            entity_type="asset_listing",
            entity_id=listing.id,
            data={"from": listing.status.value, "to": target.value},
        )
        listing.status = target
    db.commit()
    return get_listing(db, listing.id)


def promote(db: Session, seller: User, listing_id: uuid.UUID) -> PromoteOut:
    settings = get_settings()
    listing = _lock_listing(db, listing_id)
    if listing.seller_id != seller.id:
        raise AppError(ErrorCode.FORBIDDEN, _NOT_OWNER)
    if not seller.kyc_verified:
        raise AppError(ErrorCode.KYC_NOT_VERIFIED, "يجب توثيق حسابك عبر توقيعك قبل الترويج")
    if listing.status != ListingStatus.active:
        raise AppError(ErrorCode.LISTING_NOT_ACTIVE, "يمكن ترويج العروض النشطة فقط")

    payment = payments.charge(
        db,
        seller,
        amount_iqd=settings.promotion_fee_iqd,
        purpose="promotion",
        context={"listing_id": listing.id, "duration_days": settings.promotion_duration_days},
    )
    now = datetime.now(UTC)
    start = listing.promotion_expiry_date if effective_promoted(listing, now) else now
    listing.is_promoted = True
    listing.promotion_expiry_date = start + timedelta(days=settings.promotion_duration_days)
    db.commit()
    return PromoteOut(
        listing=listing_out(db, get_listing(db, listing.id)),
        payment=PaymentOut(
            status=payment.status,
            payment_ref=payment.payment_ref,
            amount_iqd=payment.amount_iqd,
            purpose=payment.purpose,
        ),
    )
