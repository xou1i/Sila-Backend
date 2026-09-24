"""Ownership: signed fractional-ownership record (Workflow 08-A, DECISIONS D-15)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError, ErrorCode
from app.core.signature import sign_ownership, verify_ownership
from app.models import FractionalOwnershipRecord, User

DISCLAIMER = (
    "التوقيع الرقمي إثبات تقني داخلي لسلامة رصيدك داخل منصة صِلة، "
    "وليس سند ملكية قانونياً معترفاً به رسمياً."
)


class OwnershipOut(BaseModel):
    investor_id: uuid.UUID
    total_accumulated_grams: Decimal
    verified: bool = Field(description="Signature checked on this request")
    digital_signature_token: str | None
    updated_at: datetime | None
    message: str | None = Field(default=None, description="Shown when the investor owns nothing")
    disclaimer: str = DISCLAIMER


def _integrity_failure(
    db: Session, investor_id: uuid.UUID, record: FractionalOwnershipRecord, context: str
) -> AppError:
    """Record a security incident in its own commit, then return the error to raise."""
    record_id = record.id
    db.rollback()
    audit(
        db,
        "integrity_check_failed",
        actor_id=investor_id,
        entity_type="fractional_ownership_record",
        entity_id=record_id,
        data={"context": context, "severity": "critical"},
    )
    db.commit()
    return AppError(ErrorCode.INTEGRITY_CHECK_FAILED)


def is_valid(record: FractionalOwnershipRecord) -> bool:
    return verify_ownership(
        record.investor_id,
        record.total_accumulated_grams,
        record.updated_at,
        record.digital_signature_token,
    )


def get_verified(db: Session, investor: User) -> OwnershipOut:
    record = db.scalar(
        select(FractionalOwnershipRecord).where(
            FractionalOwnershipRecord.investor_id == investor.id
        )
    )
    if record is None:
        return OwnershipOut(
            investor_id=investor.id,
            total_accumulated_grams=Decimal("0.000"),
            verified=True,
            digital_signature_token=None,
            updated_at=None,
            message="ابدأ أول استثمار",
        )
    if not is_valid(record):
        raise _integrity_failure(db, investor.id, record, "GET /api/ownership/me")
    return OwnershipOut(
        investor_id=investor.id,
        total_accumulated_grams=record.total_accumulated_grams,
        verified=True,
        digital_signature_token=record.digital_signature_token,
        updated_at=record.updated_at,
    )


def add_grams(
    db: Session, investor_id: uuid.UUID, grams: Decimal, context: dict[str, object]
) -> FractionalOwnershipRecord:
    """Inside the caller's transaction: lock/create the record, verify, add, re-sign."""
    now = datetime.now(UTC)
    inserted = db.execute(
        insert(FractionalOwnershipRecord)
        .values(
            investor_id=investor_id,
            total_accumulated_grams=Decimal("0"),
            digital_signature_token="",  # replaced below, before commit
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["investor_id"])
        .returning(FractionalOwnershipRecord.id)
    ).first()
    record = db.scalar(
        select(FractionalOwnershipRecord)
        .where(FractionalOwnershipRecord.investor_id == investor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert record is not None
    # Never re-sign a balance that was tampered with: that would launder the tampering.
    if inserted is None and not is_valid(record):
        raise _integrity_failure(db, investor_id, record, "purchase")

    previous = record.total_accumulated_grams
    record.total_accumulated_grams = previous + grams
    record.updated_at = now
    record.digital_signature_token = sign_ownership(
        investor_id, record.total_accumulated_grams, now
    )
    audit(
        db,
        "ownership_updated",
        actor_id=investor_id,
        entity_type="fractional_ownership_record",
        entity_id=record.id,
        data={
            "previous_grams": previous,
            "added_grams": grams,
            "total_accumulated_grams": record.total_accumulated_grams,
            **context,
        },
    )
    return record
