"""Platform administration: overview, users, listing moderation, password requests, audit,
waitlist. Every write is audit-logged with the admin as actor."""

import secrets
import string
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.audit import audit
from app.core.errors import AppError, ErrorCode
from app.core.schemas import Page
from app.models import (
    AssetListing,
    AuditLog,
    InterestSignup,
    ListingStatus,
    ListingType,
    PasswordResetRequest,
    ResetRequestStatus,
    Transaction,
    User,
    UserRole,
)
from app.modules.identity.service import is_premium_active, set_password
from app.modules.listings import service as listings
from app.modules.listings.schemas import ListingOut
from app.modules.notifications import service as notifications

# Readable temporary passwords: no 0/O or 1/l/I to misread when said aloud
_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


class OverviewOut(BaseModel):
    investors: int
    sellers: int
    inactive_accounts: int
    premium_active: int
    active_listings: int
    active_resale_listings: int
    transactions: int
    volume_iqd: Decimal = Field(description="Sum of total_paid_by_investor")
    commission_iqd: Decimal
    pending_password_requests: int
    interest: dict[str, int] = Field(description="Waitlist signups per asset class")


class AdminUserOut(BaseModel):
    id: uuid.UUID
    role: UserRole
    full_name: str
    email: str
    kyc_verified: bool
    is_active: bool
    is_premium_active: bool
    must_change_password: bool
    created_at: datetime


class AdminUserPatch(BaseModel):
    is_active: bool | None = None
    kyc_verified: bool | None = None


class TempPasswordOut(BaseModel):
    user: AdminUserOut
    temporary_password: str = Field(
        description="Shown once. Give it to the user; they must choose a new one at sign-in."
    )


class AdminListingPatch(BaseModel):
    status: Literal["active", "suspended"]


class ResetRequestOut(BaseModel):
    id: uuid.UUID
    email: str
    user_id: uuid.UUID | None
    user_name: str | None
    status: ResetRequestStatus
    created_at: datetime
    resolved_at: datetime | None


class ResetRequestPatch(BaseModel):
    status: Literal["dismissed"]


class AuditOut(BaseModel):
    id: int
    event_type: str
    actor_id: uuid.UUID | None
    entity_type: str | None
    entity_id: str | None
    data: dict[str, Any]
    created_at: datetime


class InterestSignupOut(BaseModel):
    email: str
    asset_class: str
    created_at: datetime


def _count(db: Session, query) -> int:
    return db.scalar(select(func.count()).select_from(query.subquery())) or 0


def overview(db: Session) -> OverviewOut:
    now = datetime.now(UTC)
    by_role = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
    totals = db.execute(
        select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.total_paid_by_investor), 0),
            func.coalesce(func.sum(Transaction.commission_amount), 0),
        )
    ).one()
    interest = dict(
        db.execute(
            select(InterestSignup.asset_class, func.count()).group_by(InterestSignup.asset_class)
        ).all()
    )
    active = select(AssetListing.id).where(AssetListing.status == ListingStatus.active)
    return OverviewOut(
        investors=by_role.get(UserRole.investor, 0),
        sellers=by_role.get(UserRole.seller, 0),
        inactive_accounts=_count(db, select(User.id).where(User.is_active.is_(False))),
        premium_active=_count(db, select(User.id).where(User.subscription_expiry_date > now)),
        active_listings=_count(db, active),
        active_resale_listings=_count(
            db, active.where(AssetListing.listing_type == ListingType.investor_resale)
        ),
        transactions=totals[0],
        volume_iqd=totals[1],
        commission_iqd=totals[2],
        pending_password_requests=_count(
            db,
            select(PasswordResetRequest.id).where(
                PasswordResetRequest.status == ResetRequestStatus.pending
            ),
        ),
        interest={"real_estate": interest.get("real_estate", 0), "oil": interest.get("oil", 0)},
    )


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        role=user.role,
        full_name=user.full_name,
        email=user.email,
        kyc_verified=user.kyc_verified,
        is_active=user.is_active,
        is_premium_active=is_premium_active(user),
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


def list_users(
    db: Session, q: str | None, role: UserRole | None, limit: int, offset: int
) -> Page[AdminUserOut]:
    query = select(User)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.where(or_(func.lower(User.email).like(like), User.full_name.ilike(like)))
    if role is not None:
        query = query.where(User.role == role)
    total = _count(db, query)
    rows = db.scalars(
        query.order_by(User.created_at.desc(), User.id).limit(limit).offset(offset)
    ).all()
    return Page[AdminUserOut](
        items=[_user_out(u) for u in rows], total=total, limit=limit, offset=offset
    )


def _target_user(db: Session, admin: User, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError(ErrorCode.NOT_FOUND, "المستخدم غير موجود")
    if user.role == UserRole.admin or user.id == admin.id:
        raise AppError(ErrorCode.FORBIDDEN, "ما تكدر تعدّل حساب مدير من هنا")
    return user


def update_user(db: Session, admin: User, user_id: uuid.UUID, body: AdminUserPatch) -> AdminUserOut:
    user = _target_user(db, admin, user_id)
    changes: dict[str, object] = {}
    if body.is_active is not None and body.is_active != user.is_active:
        user.is_active = body.is_active
        changes["is_active"] = body.is_active
        if not body.is_active:
            # A deactivated account sells nothing: its open listings are suspended
            suspended = db.execute(
                update(AssetListing)
                .where(
                    AssetListing.seller_id == user.id,
                    AssetListing.status == ListingStatus.active,
                )
                .values(status=ListingStatus.suspended)
            )
            changes["listings_suspended"] = suspended.rowcount
    if body.kyc_verified is not None and body.kyc_verified != user.kyc_verified:
        user.kyc_verified = body.kyc_verified
        changes["kyc_verified"] = body.kyc_verified
    if changes:
        audit(
            db,
            "admin_user_updated",
            actor_id=admin.id,
            entity_type="user",
            entity_id=user.id,
            data=changes,
        )
    db.commit()
    db.refresh(user)
    return _user_out(user)


def reset_password(db: Session, admin: User, user_id: uuid.UUID) -> TempPasswordOut:
    user = _target_user(db, admin, user_id)
    temporary = "".join(secrets.choice(_ALPHABET) for _ in range(12))
    set_password(user, temporary, temporary=True)
    now = datetime.now(UTC)
    resolved = db.execute(
        update(PasswordResetRequest)
        .where(
            PasswordResetRequest.status == ResetRequestStatus.pending,
            or_(PasswordResetRequest.user_id == user.id, PasswordResetRequest.email == user.email),
        )
        .values(status=ResetRequestStatus.resolved, resolved_at=now, resolved_by=admin.id)
    )
    audit(
        db,
        "admin_password_reset",
        actor_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        data={"requests_resolved": resolved.rowcount},
    )
    notifications.notify(
        db,
        user.id,
        "password_reset",
        "كلمة مرور جديدة",
        "إدارة صِلة أصدرت إلك كلمة مرور مؤقتة. غيّرها أول ما تدخل.",
        "/app/settings",
    )
    db.commit()
    db.refresh(user)
    return TempPasswordOut(user=_user_out(user), temporary_password=temporary)


def list_listings(
    db: Session,
    status: ListingStatus | None,
    listing_type: ListingType | None,
    limit: int,
    offset: int,
) -> Page[ListingOut]:
    query = select(AssetListing)
    if status is not None:
        query = query.where(AssetListing.status == status)
    if listing_type is not None:
        query = query.where(AssetListing.listing_type == listing_type)
    total = _count(db, query)
    rows = db.scalars(
        query.options(joinedload(AssetListing.seller))
        .order_by(AssetListing.created_at.desc(), AssetListing.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page[ListingOut](
        items=[listings.listing_out(db, r) for r in rows], total=total, limit=limit, offset=offset
    )


def moderate_listing(
    db: Session, admin: User, listing_id: uuid.UUID, body: AdminListingPatch
) -> ListingOut:
    listing = listings._lock_listing(db, listing_id)
    if listing.status in (ListingStatus.sold_out, ListingStatus.withdrawn):
        raise AppError(ErrorCode.INVALID_STATUS_TRANSITION, "العرض منتهي ولا يمكن تغيير حالته")
    target = ListingStatus(body.status)
    if listing.status != target:
        audit(
            db,
            "admin_listing_moderated",
            actor_id=admin.id,
            entity_type="asset_listing",
            entity_id=listing.id,
            data={"from": listing.status.value, "to": target.value},
        )
        listing.status = target
        if target == ListingStatus.suspended:
            notifications.notify(
                db,
                listing.seller_id,
                "listing_suspended",
                "تم إيقاف عرض",
                f"إدارة صِلة أوقفت عرضك من عيار {listing.karat} مؤقتاً.",
                "/app/portfolio"
                if listing.listing_type == ListingType.investor_resale
                else "/app/listings",
            )
    db.commit()
    return listings.listing_out(db, listings.get_listing(db, listing.id))


def list_reset_requests(db: Session, status: ResetRequestStatus | None) -> list[ResetRequestOut]:
    query = select(PasswordResetRequest, User.full_name).outerjoin(
        User, PasswordResetRequest.user_id == User.id
    )
    if status is not None:
        query = query.where(PasswordResetRequest.status == status)
    rows = db.execute(query.order_by(PasswordResetRequest.created_at.desc()).limit(100)).all()
    return [
        ResetRequestOut(
            id=r.id,
            email=r.email,
            user_id=r.user_id,
            user_name=name,
            status=r.status,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
        )
        for r, name in rows
    ]


def dismiss_reset_request(db: Session, admin: User, request_id: uuid.UUID) -> ResetRequestOut:
    request = db.get(PasswordResetRequest, request_id)
    if request is None:
        raise AppError(ErrorCode.NOT_FOUND, "الطلب غير موجود")
    if request.status == ResetRequestStatus.pending:
        request.status = ResetRequestStatus.dismissed
        request.resolved_at = datetime.now(UTC)
        request.resolved_by = admin.id
        audit(
            db,
            "admin_reset_request_dismissed",
            actor_id=admin.id,
            entity_type="password_reset_request",
            entity_id=request.id,
        )
        db.commit()
    user_name = db.scalar(select(User.full_name).where(User.id == request.user_id))
    return ResetRequestOut(
        id=request.id,
        email=request.email,
        user_id=request.user_id,
        user_name=user_name,
        status=request.status,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
    )


def list_audit(db: Session, event_type: str | None, limit: int, offset: int) -> Page[AuditOut]:
    query = select(AuditLog)
    if event_type:
        query = query.where(AuditLog.event_type == event_type)
    total = _count(db, query)
    rows = db.scalars(
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit).offset(offset)
    ).all()
    return Page[AuditOut](
        items=[AuditOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def list_interest(db: Session) -> list[InterestSignupOut]:
    rows = db.scalars(select(InterestSignup).order_by(InterestSignup.created_at.desc())).all()
    return [InterestSignupOut.model_validate(r, from_attributes=True) for r in rows]
