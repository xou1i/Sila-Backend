"""Password hashing (bcrypt) and JWT access/refresh tokens."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

import bcrypt
import jwt

from app.core.config import get_settings

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=get_settings().bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


@lru_cache
def _dummy_hash() -> str:
    return hash_password("timing-equalizer-password")


def burn_password_check(password: str) -> None:
    """Run a bcrypt check for unknown emails so login timing does not reveal accounts."""
    verify_password(password, _dummy_hash())


def create_token(
    user_id: uuid.UUID, role: str, token_type: TokenType, password_version: int = 0
) -> tuple[str, int]:
    settings = get_settings()
    ttl = (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if token_type == "access"  # noqa: S105 (token kind, not a secret)
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex,
        # Must equal the account's current password version (app.core.deps.token_still_valid)
        "pwv": password_version,
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, int(ttl.total_seconds())


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any] | None:
    """Return the claims, or None if the token is invalid, expired or of the wrong type."""
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "type", "exp", "iat"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("type") != expected_type:
        return None
    return claims
