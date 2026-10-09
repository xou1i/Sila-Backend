import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_investor
from app.core.errors import error_responses
from app.models import User
from app.modules.listings import service as listings
from app.modules.listings.router import IdempotencyKey
from app.modules.listings.schemas import ListingOut
from app.modules.ownership import service
from app.modules.ownership.service import OwnershipOut, ResaleIn, ResaleStatusIn

router = APIRouter(prefix="/api/ownership", tags=["Ownership"])


@router.get(
    "/me",
    response_model=OwnershipOut,
    responses=error_responses(401, 403),
    summary="Verified gold balance (signature checked before returning), by karat",
)
def my_ownership(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> OwnershipOut:
    return service.get_verified(db, user)


@router.post(
    "/resale",
    response_model=ListingOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": ListingOut, "description": "Idempotent replay"},
        **error_responses(401, 403, 409, 422, 503),
    },
    summary="Offer part of my holdings on the market (Workflow 09; price set by the server)",
)
def create_resale(
    body: ResaleIn,
    response: Response,
    idempotency_key: str | None = IdempotencyKey,
    investor: User = Depends(require_investor),
    db: Session = Depends(get_db),
) -> ListingOut:
    listing, created = service.create_resale(db, investor, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return listings.listing_out(db, listings.get_listing(db, listing.id))


@router.get(
    "/resale",
    response_model=list[ListingOut],
    responses=error_responses(401, 403),
    summary="My resale listings (open first)",
)
def my_resales(
    investor: User = Depends(require_investor), db: Session = Depends(get_db)
) -> list[ListingOut]:
    return [
        listings.listing_out(db, listings.get_listing(db, listing.id))
        for listing in service.my_resales(db, investor)
    ]


@router.patch(
    "/resale/{listing_id}",
    response_model=ListingOut,
    responses=error_responses(401, 403, 409, 422),
    summary="Suspend, re-activate or withdraw (final, releases unsold grams) my resale listing",
)
def update_resale(
    listing_id: uuid.UUID,
    body: ResaleStatusIn,
    investor: User = Depends(require_investor),
    db: Session = Depends(get_db),
) -> ListingOut:
    listing = service.update_resale_status(db, investor, listing_id, body.status)
    return listings.listing_out(db, listings.get_listing(db, listing.id))
