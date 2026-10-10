import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_investor
from app.core.errors import error_responses
from app.models import User
from app.modules.alerts import service
from app.modules.alerts.service import AlertIn, AlertOut, AlertStatusIn

router = APIRouter(prefix="/api/alerts", tags=["Price alerts (Premium)"])


@router.get(
    "",
    response_model=list[AlertOut],
    responses=error_responses(401, 403),
    summary="My price alerts (newest first)",
)
def list_alerts(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> list[AlertOut]:
    return service.list_alerts(db, user)


@router.post(
    "",
    response_model=AlertOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(401, 403, 422, 503),
    summary="Create a price alert (Premium; fires once when the karat price crosses the target)",
)
def create_alert(
    body: AlertIn, user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> AlertOut:
    return service.create_alert(db, user, body)


@router.patch(
    "/{alert_id}",
    response_model=AlertOut,
    responses=error_responses(401, 403, 404, 422),
    summary="Cancel one of my alerts",
)
def cancel_alert(
    alert_id: uuid.UUID,
    body: AlertStatusIn,
    user: User = Depends(require_investor),
    db: Session = Depends(get_db),
) -> AlertOut:
    return service.cancel_alert(db, user, alert_id, body.status)
