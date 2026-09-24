"""Audit trail (DECISIONS D-14): a row in `audit_logs` plus a structured log line."""

import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

logger = logging.getLogger("sila.audit")


def audit(
    db: Session,
    event_type: str,
    *,
    actor_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the current DB transaction (committed together with the change)."""
    payload = json.loads(json.dumps(data or {}, default=str))
    db.add(
        AuditLog(
            event_type=event_type,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            data=payload,
        )
    )
    logger.info(
        "audit %s",
        json.dumps(
            {"event": event_type, "actor": str(actor_id), "entity": entity_type, **payload},
            ensure_ascii=False,
            default=str,
        ),
    )
