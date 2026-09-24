import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_optional_user, require_seller
from app.core.errors import error_responses
from app.core.schemas import KaratParam, Page
from app.models import ListingStatus, User
from app.modules.listings import service
from app.modules.listings.schemas import (
    ListingCreateIn,
    ListingOut,
    ListingSort,
    ListingStatusIn,
    PromoteOut,
)

router = APIRouter(prefix="/api/listings", tags=["Listing & Asset"])

IdempotencyKey = Header(
    default=None,
    alias="Idempotency-Key",
    max_length=128,
    description="Optional. Retrying with the same key returns the original result.",
)


@router.post(
    "",
    response_model=ListingOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Idempotent replay"}, **error_responses(401, 403, 422, 503)},
    summary="Create a listing (seller, KYC required; price computed server-side)",
)
def create_listing(
    body: ListingCreateIn,
    response: Response,
    idempotency_key: str | None = IdempotencyKey,
    seller: User = Depends(require_seller),
    db: Session = Depends(get_db),
) -> ListingOut:
    listing, created = service.create_listing(db, seller, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return service.listing_out(db, listing)


@router.get(
    "",
    response_model=Page[ListingOut],
    responses=error_responses(401, 403, 422),
    summary="Browse listings (public). seller_id=me lists the caller's own listings.",
)
def browse_listings(
    karat: KaratParam | None = None,
    min_price: Decimal | None = Query(None, ge=0, description="Min base_price_per_gram"),
    max_price: Decimal | None = Query(None, ge=0, description="Max base_price_per_gram"),
    sort: ListingSort = "promoted_first",
    seller_id: str | None = Query(None, description='"me" (seller auth) or a seller UUID'),
    status_filter: ListingStatus | None = Query(
        None, alias="status", description="Only with seller_id=me"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    viewer: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Page[ListingOut]:
    return service.browse(
        db,
        viewer=viewer,
        seller_id=seller_id,
        karat=karat,
        min_price=min_price,
        max_price=max_price,
        status=status_filter,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{listing_id}",
    response_model=ListingOut,
    responses=error_responses(404, 422),
    summary="Listing detail (public)",
)
def get_listing(listing_id: uuid.UUID, db: Session = Depends(get_db)) -> ListingOut:
    return service.listing_out(db, service.get_listing(db, listing_id))


@router.patch(
    "/{listing_id}",
    response_model=ListingOut,
    responses=error_responses(401, 403, 404, 409, 422),
    summary="Suspend / re-activate a listing (owner only). There is no delete.",
)
def update_listing(
    listing_id: uuid.UUID,
    body: ListingStatusIn,
    seller: User = Depends(require_seller),
    db: Session = Depends(get_db),
) -> ListingOut:
    return service.listing_out(db, service.update_status(db, seller, listing_id, body.status))


@router.post(
    "/{listing_id}/promote",
    response_model=PromoteOut,
    responses=error_responses(401, 402, 403, 404, 409),
    summary="Pay the fixed promotion fee (internal mock payment) and promote the listing",
)
def promote_listing(
    listing_id: uuid.UUID,
    seller: User = Depends(require_seller),
    db: Session = Depends(get_db),
) -> PromoteOut:
    return service.promote(db, seller, listing_id)
