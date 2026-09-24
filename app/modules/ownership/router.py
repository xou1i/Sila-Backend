from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_investor
from app.core.errors import error_responses
from app.models import User
from app.modules.ownership import service
from app.modules.ownership.service import OwnershipOut

router = APIRouter(prefix="/api/ownership", tags=["Ownership"])


@router.get(
    "/me",
    response_model=OwnershipOut,
    responses=error_responses(401, 403),
    summary="Verified gold balance (signature checked before returning)",
)
def my_ownership(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> OwnershipOut:
    return service.get_verified(db, user)
