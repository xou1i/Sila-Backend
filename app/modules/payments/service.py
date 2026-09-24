"""Mock payment gateway: an internal service call only, never a public route (07 §7).

Every payment is bound to a purpose (promotion / subscription) and audit-logged with its
`payment_ref`. Swap this function for a real gateway later without changing the API contract.
"""

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.config import get_settings
from app.core.errors import AppError, ErrorCode
from app.models import User

logger = logging.getLogger("sila.payments")

PaymentPurpose = Literal["promotion", "subscription"]


@dataclass(frozen=True)
class PaymentResult:
    status: str
    payment_ref: uuid.UUID
    amount_iqd: Decimal
    purpose: PaymentPurpose


def charge(
    db: Session,
    user: User,
    *,
    amount_iqd: Decimal,
    purpose: PaymentPurpose,
    context: dict[str, Any],
) -> PaymentResult:
    """Charge `amount_iqd`. The audit row joins the caller's DB transaction."""
    if get_settings().mock_payment_fail:
        logger.warning("mock payment declined (MOCK_PAYMENT_FAIL): %s %s", purpose, context)
        raise AppError(ErrorCode.PAYMENT_FAILED)
    result = PaymentResult(
        status="success", payment_ref=uuid.uuid4(), amount_iqd=amount_iqd, purpose=purpose
    )
    audit(
        db,
        "mock_payment",
        actor_id=user.id,
        entity_type=purpose,
        entity_id=result.payment_ref,
        data={
            "payment_ref": result.payment_ref,
            "amount_iqd": amount_iqd,
            "purpose": purpose,
            "status": result.status,
            **context,
        },
    )
    return result
