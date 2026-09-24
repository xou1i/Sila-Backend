"""Identity & Security: signup, login, token refresh, mock KYC ("توقيعك")."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import AppError, ErrorCode
from app.core.security import (
    burn_password_check,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import SubscriptionTier, User, UserRole
from app.modules.identity.schemas import LoginOut, SignupIn, TokenOut, UserOut


def is_premium_active(user: User, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return user.subscription_expiry_date is not None and user.subscription_expiry_date > now


def user_out(user: User) -> UserOut:
    return UserOut.model_validate(
        {
            **{f: getattr(user, f) for f in UserOut.model_fields if f != "is_premium_active"},
            "is_premium_active": is_premium_active(user),
        }
    )


def signup(db: Session, data: SignupIn) -> User:
    if db.scalar(select(User.id).where(User.email == data.email)) is not None:
        raise AppError(ErrorCode.EMAIL_ALREADY_EXISTS)
    user = User(
        role=data.role,
        full_name=data.full_name,
        email=data.email,
        password_hash=hash_password(data.password),
        risk_profile=data.risk_profile,
        kyc_verified=False,
        subscription_tier=SubscriptionTier.free,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # concurrent signup with the same email
        db.rollback()
        raise AppError(ErrorCode.EMAIL_ALREADY_EXISTS) from exc
    db.refresh(user)
    return user


def _tokens(user: User) -> TokenOut:
    access, expires_in = create_token(user.id, user.role.value, "access")
    refresh, _ = create_token(user.id, user.role.value, "refresh")
    return TokenOut(access_token=access, refresh_token=refresh, expires_in=expires_in)


def login(db: Session, email: str, password: str) -> LoginOut:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check(password)
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    if not verify_password(password, user.password_hash):
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    return LoginOut(**_tokens(user).model_dump(), user=user_out(user))


def refresh(db: Session, refresh_token: str) -> TokenOut:
    claims = decode_token(refresh_token, "refresh")
    user = None
    if claims is not None:
        try:
            user = db.get(User, uuid.UUID(claims["sub"]))
        except ValueError:
            user = None
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "جلسة الدخول منتهية، سجّل الدخول من جديد")
    return _tokens(user)


def verify_kyc(db: Session, user: User, expected_role: UserRole) -> User:
    """Mock "توقيعك" verification: instant success, permanent (Workflow 02)."""
    if user.role != expected_role:  # defensive; routers already enforce the role
        raise AppError(ErrorCode.FORBIDDEN)
    if not user.kyc_verified:
        user.kyc_verified = True
        audit(
            db,
            "kyc_verified",
            actor_id=user.id,
            entity_type="user",
            entity_id=user.id,
            data={"role": user.role.value, "provider": "mock_tawqeeik"},
        )
        db.commit()
        db.refresh(user)
    return user
