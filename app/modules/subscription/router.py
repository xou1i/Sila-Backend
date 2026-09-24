from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_investor
from app.core.errors import error_responses
from app.models import User
from app.modules.subscription import service
from app.modules.subscription.service import SubscribeOut, SubscriptionStatusOut

router = APIRouter(prefix="/api/subscription", tags=["Subscription & Mock Payment"])


@router.post(
    "/subscribe",
    response_model=SubscribeOut,
    responses=error_responses(401, 402, 403),
    summary="Buy / renew Premium for 30 days (internal mock payment; no KYC needed)",
)
def subscribe(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> SubscribeOut:
    return service.subscribe(db, user)


@router.get(
    "/status",
    response_model=SubscriptionStatusOut,
    responses=error_responses(401, 403),
    summary="Current subscription tier and expiry",
)
def subscription_status(user: User = Depends(require_investor)) -> SubscriptionStatusOut:
    return service.status_of(user)
