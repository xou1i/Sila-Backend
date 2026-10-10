from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user, require_investor, require_seller
from app.core.errors import error_responses
from app.core.rate_limit import rate_limit
from app.models import User, UserRole
from app.modules.identity import service
from app.modules.identity.schemas import (
    ChangePasswordIn,
    ForgotPasswordIn,
    KycOut,
    LoginIn,
    LoginOut,
    MessageOut,
    RefreshIn,
    SignupIn,
    TokenOut,
    UserOut,
)

router = APIRouter(tags=["Identity & Security"])

_KYC_DONE = "تم التوثيق عبر توقيعك بنجاح"


@router.post(
    "/api/auth/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses(409, 422),
    summary="Create an investor or seller account",
)
def signup(body: SignupIn, db: Session = Depends(get_db)) -> UserOut:
    return service.user_out(service.signup(db, body))


@router.post(
    "/api/auth/forgot-password",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(422, 429),
    dependencies=[rate_limit("login")],
    summary="Ask the admins for a temporary password (same reply whether the e-mail exists)",
)
def forgot_password(body: ForgotPasswordIn, db: Session = Depends(get_db)) -> MessageOut:
    return MessageOut(message=service.forgot_password(db, body.email))


@router.post(
    "/api/users/me/password",
    response_model=LoginOut,
    responses=error_responses(401, 422, 429),
    dependencies=[rate_limit("login")],
    summary="Change my password; returns fresh tokens (older sessions stop working)",
)
def change_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> LoginOut:
    return service.change_password(db, user, body.current_password, body.new_password)


@router.post(
    "/api/auth/login",
    response_model=LoginOut,
    responses=error_responses(401, 422, 429),
    dependencies=[rate_limit("login")],
    summary="Log in; returns access + refresh tokens",
)
def login(body: LoginIn, db: Session = Depends(get_db)) -> LoginOut:
    return service.login(db, body.email, body.password)


@router.post(
    "/api/auth/refresh",
    response_model=TokenOut,
    responses=error_responses(401, 422),
    summary="Exchange a refresh token for a new token pair",
)
def refresh(body: RefreshIn, db: Session = Depends(get_db)) -> TokenOut:
    return service.refresh(db, body.refresh_token)


@router.get(
    "/api/users/me",
    response_model=UserOut,
    responses=error_responses(401),
    summary="Current user profile",
)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return service.user_out(user)


@router.post(
    "/api/kyc/verify",
    response_model=KycOut,
    responses=error_responses(401, 403, 429),
    dependencies=[rate_limit("kyc")],
    summary="Mock KYC (توقيعك) for investors",
)
def kyc_verify_investor(
    user: User = Depends(require_investor), db: Session = Depends(get_db)
) -> KycOut:
    user = service.verify_kyc(db, user, UserRole.investor)
    return KycOut(kyc_verified=True, message=_KYC_DONE, user=service.user_out(user))


@router.post(
    "/api/kyc/seller",
    response_model=KycOut,
    responses=error_responses(401, 403, 429),
    dependencies=[rate_limit("kyc")],
    summary="Mock KYC (توقيعك) for sellers",
)
def kyc_verify_seller(
    user: User = Depends(require_seller), db: Session = Depends(get_db)
) -> KycOut:
    user = service.verify_kyc(db, user, UserRole.seller)
    return KycOut(kyc_verified=True, message=_KYC_DONE, user=service.user_out(user))
