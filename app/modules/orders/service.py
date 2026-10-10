"""Order & Transaction: read-only preview, atomic confirm with row lock (Workflow 05)."""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.core.money import Breakdown, compute_breakdown, karat_price
from app.core.schemas import Page
from app.core.signature import buyer_ref, make_quote_token, read_quote_token
from app.models import (
    AssetListing,
    FractionalOwnershipRecord,
    ListingStatus,
    ListingType,
    Transaction,
    User,
)
from app.modules.ai import service as ai
from app.modules.listings import service as listings
from app.modules.market import service as market
from app.modules.notifications import service as notifications
from app.modules.orders.schemas import (
    ConfirmIn,
    ConfirmOut,
    OwnershipSummary,
    PreviewIn,
    PreviewOut,
    TransactionOut,
)
from app.modules.ownership import service as ownership

logger = logging.getLogger("sila.orders")

_AI_UNAVAILABLE_NOTE = "التحليل الذكي غير متاح حالياً"


def _check_purchasable(listing: AssetListing, grams: Decimal, buyer: User) -> None:
    # An investor cannot buy back their own resale listing (Workflow 09)
    if listing.seller_id == buyer.id:
        raise AppError(ErrorCode.FORBIDDEN, "هذا عرضك، ما تكدر تشتري منه")
    if listing.status != ListingStatus.active:
        raise AppError(ErrorCode.LISTING_NOT_ACTIVE)
    if grams > listing.available_weight_grams:
        raise AppError(ErrorCode.INSUFFICIENT_AVAILABLE_WEIGHT)


def preview(db: Session, investor: User, body: PreviewIn) -> PreviewOut:
    """Read-only: computes numbers and a signed quote. Reserves nothing, writes nothing."""
    listing = listings.get_listing(db, body.asset_id)
    _check_purchasable(listing, body.purchased_weight_grams, investor)
    snapshot = market.require_snapshot(db)
    breakdown = compute_breakdown(
        body.purchased_weight_grams, karat_price(snapshot.gold_24k_iqd_per_gram, listing.karat)
    )
    now = datetime.now(UTC)
    expires = now + timedelta(seconds=get_settings().quote_ttl_seconds)
    token = make_quote_token(
        {
            "investor_id": str(investor.id),
            "asset_id": str(listing.id),
            "grams": str(breakdown.purchased_weight_grams),
            "price": str(breakdown.execution_price_per_gram),
            "exp": int(expires.timestamp()),
        }
    )
    try:
        insight = ai.risk_insight_rules(
            db, investor, listing, body.purchased_weight_grams, snapshot
        )
        note = None
    except Exception:  # AI must never block checkout (Workflow 05 edge case)
        logger.exception("risk insight failed during preview")
        insight, note = None, _AI_UNAVAILABLE_NOTE
    return PreviewOut(
        asset_id=listing.id,
        karat=listing.karat,
        purchased_weight_grams=breakdown.purchased_weight_grams,
        execution_price_per_gram=breakdown.execution_price_per_gram,
        principal_amount=breakdown.principal_amount,
        commission_rate=breakdown.commission_rate,
        commission_amount=breakdown.commission_amount,
        total_paid_by_investor=breakdown.total_paid_by_investor,
        price_updated_at=snapshot.fetched_at,
        quoted_at=now,
        quote_expires_at=expires,
        quote_token=token,
        risk_insight=insight,
        risk_insight_note=note,
    )


def _quoted_price(investor: User, body: ConfirmIn) -> Decimal:
    payload = read_quote_token(body.quote_token)
    if payload is None:
        raise AppError(ErrorCode.PRICE_CHANGED, "عرض السعر غير صالح، حدّث المعاينة")
    try:
        matches = (
            payload.get("investor_id") == str(investor.id)
            and payload.get("asset_id") == str(body.asset_id)
            and Decimal(str(payload.get("grams"))) == body.purchased_weight_grams
        )
        price = Decimal(str(payload.get("price")))
        expires = int(payload.get("exp", 0))
    except (InvalidOperation, TypeError, ValueError):
        matches = False
        price, expires = Decimal("0"), 0
    if not matches or price <= 0:
        raise AppError(ErrorCode.PRICE_CHANGED, "بيانات الطلب لا تطابق المعاينة، حدّث المعاينة")
    if datetime.now(UTC).timestamp() > expires:
        raise AppError(ErrorCode.PRICE_CHANGED)
    return price


def _by_idempotency_key(db: Session, investor: User, key: str) -> Transaction | None:
    return db.scalar(
        select(Transaction).where(
            Transaction.investor_id == investor.id, Transaction.idempotency_key == key
        )
    )


def execute_purchase(
    db: Session,
    investor: User,
    listing_id: uuid.UUID,
    grams: Decimal,
    execution_price_per_gram: Decimal,
    idempotency_key: str | None = None,
) -> tuple[Transaction, FractionalOwnershipRecord | None, AssetListing, bool]:
    """The atomic purchase. Commits on success, rolls back entirely on any failure.

    Returns (transaction, ownership record, listing, created).
    """
    # 1. Row-level lock on the listing: concurrent buyers of this listing queue here.
    listing = listings._lock_listing(db, listing_id)
    # 2. Idempotency re-check under the lock (a concurrent twin may have just committed).
    if idempotency_key and (existing := _by_idempotency_key(db, investor, idempotency_key)):
        db.rollback()
        return existing, None, listing, False
    # 3. Final checks at execution time.
    _check_purchasable(listing, grams, investor)
    resale = listing.listing_type == ListingType.investor_resale
    if resale:
        # Both ownership records, in one fixed order (no deadlock between crossing resales)
        ownership.lock_records(db, [investor.id, listing.seller_id])
    breakdown: Breakdown = compute_breakdown(grams, execution_price_per_gram)
    # 4. Decrement; auto sold_out at zero.
    listing.available_weight_grams -= grams
    if listing.available_weight_grams == 0:
        listing.status = ListingStatus.sold_out
    # 5. Insert the transaction with the quoted values.
    tx = Transaction(
        investor_id=investor.id,
        asset_id=listing.id,
        purchased_weight_grams=breakdown.purchased_weight_grams,
        execution_price_per_gram=breakdown.execution_price_per_gram,
        principal_amount=breakdown.principal_amount,
        commission_rate=breakdown.commission_rate,
        commission_amount=breakdown.commission_amount,
        total_paid_by_investor=breakdown.total_paid_by_investor,
        idempotency_key=idempotency_key,
    )
    db.add(tx)
    db.flush()
    audit(
        db,
        "transaction_executed",
        actor_id=investor.id,
        entity_type="transaction",
        entity_id=tx.id,
        data={
            "asset_id": listing.id,
            "seller_id": listing.seller_id,
            "grams": grams,
            "execution_price_per_gram": breakdown.execution_price_per_gram,
            "principal_amount": breakdown.principal_amount,
            "commission_rate": breakdown.commission_rate,
            "commission_amount": breakdown.commission_amount,
            "total_paid_by_investor": breakdown.total_paid_by_investor,
            "listing_available_after": listing.available_weight_grams,
            "listing_status_after": listing.status.value,
        },
    )
    # 6. Transfer from the resale seller (verified, re-signed), then upsert + re-sign the buyer.
    if resale:
        ownership.remove_grams(
            db, listing.seller_id, grams, {"transaction_id": tx.id, "resale_sold": True}
        )
        audit(
            db,
            "resale_payout",
            actor_id=listing.seller_id,
            entity_type="transaction",
            entity_id=tx.id,
            data={"amount_iqd": breakdown.principal_amount, "provider": "mock"},
        )
    record = ownership.add_grams(db, investor.id, grams, {"transaction_id": tx.id})
    _notify_parties(db, investor, listing, tx, resale)
    # 7. Commit everything at once.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _by_idempotency_key(db, investor, idempotency_key) if idempotency_key else None
        if existing is None:
            raise
        return existing, None, listings.get_listing(db, existing.asset_id), False
    return tx, record, listing, True


def _notify_parties(
    db: Session, buyer: User, listing: AssetListing, tx: Transaction, resale: bool
) -> None:
    grams = f"{tx.purchased_weight_grams.normalize():f}"
    notifications.notify(
        db,
        buyer.id,
        "purchase_completed",
        "تمت عملية الشراء",
        f"اشتريت {grams} غرام عيار {listing.karat}، وانضافت لرصيدك الموثّق.",
        "/app/portfolio",
    )
    if resale:
        notifications.notify(
            db,
            listing.seller_id,
            "resale_sold",
            "انباع جزء من عرضك",
            f"انباع {grams} غرام عيار {listing.karat} من عرض إعادة البيع مالتك.",
            "/app/portfolio",
        )
    else:
        notifications.notify(
            db,
            listing.seller_id,
            "listing_sold",
            "عملية بيع جديدة",
            f"انباع {grams} غرام عيار {listing.karat} من عرضك.",
            "/app/sales",
        )


def confirm(
    db: Session, investor: User, body: ConfirmIn, idempotency_key: str | None
) -> tuple[ConfirmOut, bool]:
    # KYC first, before anything else (Workflow 05 §4, server-side gate).
    if not investor.kyc_verified:
        raise AppError(ErrorCode.KYC_NOT_VERIFIED, "يجب إكمال التوثيق قبل إتمام عملية الشراء")
    # A replay of a completed purchase returns it even if the quote has since expired.
    if idempotency_key and (existing := _by_idempotency_key(db, investor, idempotency_key)):
        return _confirm_out(db, investor, existing, replay=True), False
    price = _quoted_price(investor, body)
    tx, _, _, created = execute_purchase(
        db, investor, body.asset_id, body.purchased_weight_grams, price, idempotency_key
    )
    return _confirm_out(db, investor, tx, replay=not created), created


def _confirm_out(db: Session, investor: User, tx: Transaction, *, replay: bool) -> ConfirmOut:
    listing = listings.get_listing(db, tx.asset_id)
    record = db.scalar(
        select(FractionalOwnershipRecord).where(
            FractionalOwnershipRecord.investor_id == investor.id
        )
    )
    assert record is not None
    return ConfirmOut(
        transaction=_tx_out(tx, listing, seller_view=False),
        ownership=OwnershipSummary(
            total_accumulated_grams=record.total_accumulated_grams, updated_at=record.updated_at
        ),
        listing_status=listing.status.value,
        listing_available_weight_grams=listing.available_weight_grams,
        idempotent_replay=replay,
    )


def _tx_out(tx: Transaction, listing: AssetListing, *, seller_view: bool) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        asset_id=tx.asset_id,
        karat=listing.karat,
        side="sell" if seller_view else "buy",
        seller_name=listings.seller_label(listing),
        buyer_ref=buyer_ref(tx.investor_id) if seller_view else None,
        purchased_weight_grams=tx.purchased_weight_grams,
        execution_price_per_gram=tx.execution_price_per_gram,
        principal_amount=tx.principal_amount,
        commission_rate=tx.commission_rate,
        commission_amount=tx.commission_amount,
        total_paid_by_investor=tx.total_paid_by_investor,
        created_at=tx.created_at,
    )


def _visible_to(user: User):
    # Buyer of the transaction, or owner of the listing (a seller, or an investor's resale)
    return or_(Transaction.investor_id == user.id, AssetListing.seller_id == user.id)


def _is_sale(tx: Transaction, user: User) -> bool:
    return tx.asset.seller_id == user.id


def list_transactions(db: Session, user: User, limit: int, offset: int) -> Page[TransactionOut]:
    base = select(Transaction).join(AssetListing, Transaction.asset_id == AssetListing.id)
    base = base.where(_visible_to(user))
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(joinedload(Transaction.asset).joinedload(AssetListing.seller))
        .order_by(Transaction.created_at.desc(), Transaction.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[TransactionOut](
        items=[_tx_out(t, t.asset, seller_view=_is_sale(t, user)) for t in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_transaction(db: Session, user: User, tx_id: uuid.UUID) -> TransactionOut:
    tx = db.scalar(
        select(Transaction)
        .join(AssetListing, Transaction.asset_id == AssetListing.id)
        .options(joinedload(Transaction.asset).joinedload(AssetListing.seller))
        .where(Transaction.id == tx_id, _visible_to(user))
    )
    if tx is None:  # non-parties get 404, not 403 (DECISIONS D-20)
        raise AppError(ErrorCode.NOT_FOUND, "العملية غير موجودة")
    return _tx_out(tx, tx.asset, seller_view=_is_sale(tx, user))
