"""\"سجّل اهتمامك\": a waitlist for the asset classes that are coming soon (real estate, oil)."""

from typing import Literal

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_optional_user
from app.core.errors import error_responses
from app.core.rate_limit import rate_limit
from app.models import InterestSignup, User

router = APIRouter(prefix="/api/interest", tags=["Coming soon"])

THANKS = {
    "real_estate": "تم تسجيل اهتمامك بالعقارات، راح نبلغك أول ما تتوفر على صِلة.",
    "oil": "تم تسجيل اهتمامك بالنفط، راح نبلغك أول ما يتوفر على صِلة.",
}


class InterestIn(BaseModel):
    email: EmailStr
    asset_class: Literal["real_estate", "oil"]

    @field_validator("email", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class InterestOut(BaseModel):
    message: str = Field(description="Same reply for a new or a repeated signup")


@router.post(
    "",
    response_model=InterestOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(422, 429),
    dependencies=[rate_limit("login")],
    summary="Join the waitlist for real estate or oil (public)",
)
def register_interest(
    body: InterestIn,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> InterestOut:
    # Idempotent: one row per (email, asset class), a repeat changes nothing
    db.execute(
        insert(InterestSignup)
        .values(email=body.email, asset_class=body.asset_class, user_id=user.id if user else None)
        .on_conflict_do_nothing(index_elements=["email", "asset_class"])
    )
    db.commit()
    return InterestOut(message=THANKS[body.asset_class])
