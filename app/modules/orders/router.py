import uuid

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_investor
from app.core.errors import error_responses
from app.core.schemas import Page
from app.models import User
from app.modules.orders import service
from app.modules.orders.schemas import ConfirmIn, ConfirmOut, PreviewIn, PreviewOut, TransactionOut

router = APIRouter(prefix="/api/transactions", tags=["Order & Transaction"])


@router.post(
    "/preview",
    response_model=PreviewOut,
    responses=error_responses(401, 403, 404, 409, 422, 503),
    summary="Price breakdown + signed quote + AI risk insight. Read-only (no reservation).",
)
def preview(
    body: PreviewIn, user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> PreviewOut:
    return service.preview(db, user, body)


@router.post(
    "/confirm",
    response_model=ConfirmOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"description": "Idempotent replay: the original transaction"},
        **error_responses(401, 403, 404, 409, 422),
    },
    summary="Execute the purchase atomically (KYC required; row-locked)",
)
def confirm(
    body: ConfirmIn,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=128,
        description="Recommended. A repeated key returns the original transaction.",
    ),
    user: User = Depends(require_investor),
    db: Session = Depends(get_db),
) -> ConfirmOut:
    result, created = service.confirm(db, user, body, idempotency_key)
    if not created:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "",
    response_model=Page[TransactionOut],
    responses=error_responses(401, 422),
    summary="My transactions (investor: purchases; seller: sales on my listings)",
)
def list_transactions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[TransactionOut]:
    return service.list_transactions(db, user, limit, offset)


@router.get(
    "/{transaction_id}",
    response_model=TransactionOut,
    responses=error_responses(401, 404, 422),
    summary="One transaction (parties only)",
)
def get_transaction(
    transaction_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TransactionOut:
    return service.get_transaction(db, user, transaction_id)
