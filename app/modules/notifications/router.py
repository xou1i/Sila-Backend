from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.errors import error_responses
from app.models import User
from app.modules.notifications import service
from app.modules.notifications.service import MarkReadIn, NotificationsOut

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=NotificationsOut,
    responses=error_responses(401),
    summary="Latest in-app notifications (newest first) and the unread count",
)
def list_notifications(
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> NotificationsOut:
    return service.list_for(db, user, limit)


@router.post(
    "/read",
    response_model=NotificationsOut,
    responses=error_responses(401, 422),
    summary="Mark notifications as read (all of them when ids is empty)",
)
def mark_read(
    body: MarkReadIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> NotificationsOut:
    return service.mark_read(db, user, body.ids)
