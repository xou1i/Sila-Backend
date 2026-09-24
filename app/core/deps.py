"""Auth dependencies: current user from the Bearer access token, and RBAC."""

import uuid
from collections.abc import Callable

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError, ErrorCode
from app.core.security import decode_token
from app.models import User, UserRole

bearer = HTTPBearer(auto_error=False, description="Access token from /api/auth/login")

_ROLE_MESSAGES = {
    UserRole.investor: "هذه العملية متاحة للمستثمرين فقط",
    UserRole.seller: "هذه العملية متاحة للبائعين فقط",
}


def _user_from_credentials(
    credentials: HTTPAuthorizationCredentials | None, db: Session
) -> User | None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        return None
    claims = decode_token(credentials.credentials, "access")
    if claims is None:
        return None
    try:
        user_id = uuid.UUID(claims["sub"])
    except ValueError:
        return None
    return db.get(User, user_id)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    user = _user_from_credentials(credentials, db)
    if user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"})
    return user


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    return _user_from_credentials(credentials, db)


def require_role(role: UserRole) -> Callable[..., User]:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role != role:
            raise AppError(ErrorCode.FORBIDDEN, _ROLE_MESSAGES[role])
        return user

    return dependency


require_investor = require_role(UserRole.investor)
require_seller = require_role(UserRole.seller)
