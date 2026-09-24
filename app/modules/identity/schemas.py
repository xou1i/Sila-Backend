import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.schemas import ORMModel
from app.models import RiskProfile, SubscriptionTier, UserRole


def _normalize_email(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


def _check_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    return value


class SignupIn(BaseModel):
    role: UserRole
    full_name: str = Field(min_length=2, max_length=255, examples=["زينب الموسوي"])
    email: EmailStr = Field(examples=["zainab@example.iq"])
    password: str = Field(min_length=8, max_length=72, examples=["Sila@2026"])
    risk_profile: RiskProfile | None = Field(
        default=None, description="Required for investors; ignored for sellers."
    )

    _email = field_validator("email", mode="before")(_normalize_email)
    _password = field_validator("password")(_check_password_bytes)

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("full_name is required")
        return value

    @model_validator(mode="after")
    def _risk_profile_by_role(self) -> "SignupIn":
        if self.role == UserRole.investor and self.risk_profile is None:
            raise ValueError("risk_profile is required for investors")
        if self.role == UserRole.seller:
            self.risk_profile = None
        return self


class LoginIn(BaseModel):
    email: EmailStr = Field(examples=["zainab@example.iq"])
    password: str = Field(min_length=1, max_length=256, examples=["Sila@2026"])

    _email = field_validator("email", mode="before")(_normalize_email)


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(ORMModel):
    id: uuid.UUID
    role: UserRole
    full_name: str
    email: str
    kyc_verified: bool
    risk_profile: RiskProfile | None
    subscription_tier: SubscriptionTier
    subscription_expiry_date: datetime | None
    is_premium_active: bool = Field(
        description="subscription_expiry_date > now(); use this, not subscription_tier, to gate UI"
    )
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth token type, not a secret)
    expires_in: int = Field(description="Access token lifetime in seconds")


class LoginOut(TokenOut):
    user: UserOut


class KycOut(BaseModel):
    kyc_verified: bool
    message: str
    user: UserOut
