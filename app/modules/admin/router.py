import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.core.errors import error_responses
from app.core.schemas import Page
from app.models import ListingStatus, ListingType, ResetRequestStatus, User, UserRole
from app.modules.admin import service
from app.modules.admin.service import (
    AdminListingPatch,
    AdminUserOut,
    AdminUserPatch,
    AuditOut,
    InterestOut,
    OverviewOut,
    ResetRequestOut,
    ResetRequestPatch,
    TempPasswordOut,
)
from app.modules.listings.schemas import ListingOut

# Every route needs an active admin account
router = APIRouter(
    prefix="/api/admin",
    tags=["Administration"],
    dependencies=[Depends(require_admin)],
    responses=error_responses(401, 403),
)


@router.get("/overview", response_model=OverviewOut, summary="Platform figures at a glance")
def overview(db: Session = Depends(get_db)) -> OverviewOut:
    return service.overview(db)


@router.get("/users", response_model=Page[AdminUserOut], summary="Search users")
def users(
    q: str | None = Query(None, max_length=100, description="Name or e-mail contains"),
    role: UserRole | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Page[AdminUserOut]:
    return service.list_users(db, q, role, limit, offset)


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserOut,
    responses=error_responses(404, 422),
    summary="Activate/deactivate an account (deactivating suspends its listings) or set KYC",
)
def update_user(
    user_id: uuid.UUID,
    body: AdminUserPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> AdminUserOut:
    return service.update_user(db, admin, user_id, body)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=TempPasswordOut,
    responses=error_responses(404),
    summary="Issue a temporary password (shown once; older sessions stop working)",
)
def reset_password(
    user_id: uuid.UUID, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> TempPasswordOut:
    return service.reset_password(db, admin, user_id)


@router.get("/listings", response_model=Page[ListingOut], summary="Every listing, any status")
def listings(
    status: ListingStatus | None = None,
    listing_type: ListingType | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Page[ListingOut]:
    return service.list_listings(db, status, listing_type, limit, offset)


@router.patch(
    "/listings/{listing_id}",
    response_model=ListingOut,
    responses=error_responses(404, 409, 422),
    summary="Suspend or re-activate any listing (moderation)",
)
def moderate_listing(
    listing_id: uuid.UUID,
    body: AdminListingPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ListingOut:
    return service.moderate_listing(db, admin, listing_id, body)


@router.get(
    "/password-requests",
    response_model=list[ResetRequestOut],
    summary='"Forgot password" requests (newest first)',
)
def password_requests(
    status: ResetRequestStatus | None = ResetRequestStatus.pending, db: Session = Depends(get_db)
) -> list[ResetRequestOut]:
    return service.list_reset_requests(db, status)


@router.patch(
    "/password-requests/{request_id}",
    response_model=ResetRequestOut,
    responses=error_responses(404, 422),
    summary="Dismiss a request (resolving happens through reset-password)",
)
def dismiss_password_request(
    request_id: uuid.UUID,
    body: ResetRequestPatch,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ResetRequestOut:
    return service.dismiss_reset_request(db, admin, request_id)


@router.get("/audit", response_model=Page[AuditOut], summary="Security and financial audit log")
def audit_log(
    event_type: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> Page[AuditOut]:
    return service.list_audit(db, event_type, limit, offset)


@router.get("/interest", response_model=list[InterestOut], summary="Waitlist signups")
def interest(db: Session = Depends(get_db)) -> list[InterestOut]:
    return service.list_interest(db)
