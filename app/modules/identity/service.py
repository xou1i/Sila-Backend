"""Identity & Security: signup, login, token refresh, passwords, mock KYC ("توقيعك")."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import password_version, token_still_valid
from app.core.errors import AppError, ErrorCode
from app.core.security import (
    burn_password_check,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import (
    PasswordResetRequest,
    ResetRequestStatus,
    SubscriptionTier,
    User,
    UserRole,
)
from app.modules.identity.schemas import LoginOut, SignupIn, TokenOut, UserOut
from app.modules.notifications import service as notifications

RESET_REQUEST_REPLY = (
    "إذا البريد الإلكتروني مسجل عدنا، طلبك وصل لإدارة صِلة، وراح يتواصلون وياك بكلمة مرور مؤقتة."
)


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
    version = password_version(user)
    access, expires_in = create_token(user.id, user.role.value, "access", version)
    refresh, _ = create_token(user.id, user.role.value, "refresh", version)
    return TokenOut(access_token=access, refresh_token=refresh, expires_in=expires_in)


def login(db: Session, email: str, password: str) -> LoginOut:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        burn_password_check(password)
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    if not verify_password(password, user.password_hash):
        raise AppError(ErrorCode.INVALID_CREDENTIALS)
    # Only after the password check, so a wrong password never reveals the account state
    if not user.is_active:
        raise AppError(ErrorCode.ACCOUNT_DISABLED)
    return LoginOut(**_tokens(user).model_dump(), user=user_out(user))


def refresh(db: Session, refresh_token: str) -> TokenOut:
    claims = decode_token(refresh_token, "refresh")
    user = None
    if claims is not None:
        try:
            user = db.get(User, uuid.UUID(claims["sub"]))
        except ValueError:
            user = None
    if user is None or not token_still_valid(user, claims or {}):
        raise AppError(ErrorCode.UNAUTHORIZED, "جلسة الدخول منتهية، سجّل الدخول من جديد")
    return _tokens(user)


def set_password(user: User, new_password: str, *, temporary: bool) -> None:
    """New hash; every session issued before now stops working."""
    user.password_hash = hash_password(new_password)
    user.must_change_password = temporary
    user.password_changed_at = datetime.now(UTC)


def change_password(db: Session, user: User, current: str, new: str) -> LoginOut:
    if not verify_password(current, user.password_hash):
        raise AppError(ErrorCode.INVALID_CREDENTIALS, "كلمة المرور الحالية غير صحيحة")
    if current == new:
        raise AppError(ErrorCode.VALIDATION_ERROR, "اختار كلمة مرور جديدة تختلف عن الحالية")
    set_password(user, new, temporary=False)
    audit(db, "password_changed", actor_id=user.id, entity_type="user", entity_id=user.id)
    db.commit()
    db.refresh(user)
    # Fresh tokens: the old ones were just invalidated, this session continues
    return LoginOut(**_tokens(user).model_dump(), user=user_out(user))


def forgot_password(db: Session, email: str) -> str:
    """Queue a request for the admins. Same reply whether the e-mail exists or not."""
    user = db.scalar(select(User).where(User.email == email))
    pending = db.scalar(
        select(PasswordResetRequest.id).where(
            PasswordResetRequest.email == email,
            PasswordResetRequest.status == ResetRequestStatus.pending,
        )
    )
    if pending is None and (user is None or user.role != UserRole.admin):
        db.add(PasswordResetRequest(email=email, user_id=user.id if user else None))
        if user is not None:
            notifications.notify_admins(
                db,
                "password_reset_request",
                "طلب استرجاع كلمة مرور",
                f"{user.full_name} ({email}) طلب كلمة مرور جديدة.",
                "/app/admin/password-requests",
            )
        db.commit()
    return RESET_REQUEST_REPLY


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
