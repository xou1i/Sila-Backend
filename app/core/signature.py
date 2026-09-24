"""HMAC helpers: ownership signature (D-15), quote token (D-16), buyer pseudonym (D-19)."""

import base64
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.config import get_settings
from app.core.money import MILLIGRAM


def _hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def iso_utc(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def ownership_message(investor_id: uuid.UUID, total_grams: Decimal, ts: datetime) -> str:
    return f"{investor_id}|{total_grams.quantize(MILLIGRAM)}|{iso_utc(ts)}"


def sign_ownership(investor_id: uuid.UUID, total_grams: Decimal, ts: datetime) -> str:
    secret = get_settings().ownership_signing_secret
    return _hmac_hex(secret, ownership_message(investor_id, total_grams, ts))


def verify_ownership(
    investor_id: uuid.UUID, total_grams: Decimal, ts: datetime, signature: str
) -> bool:
    expected = sign_ownership(investor_id, total_grams, ts)
    return hmac.compare_digest(expected, signature or "")


def buyer_ref(investor_id: uuid.UUID) -> str:
    digest = _hmac_hex(get_settings().ownership_signing_secret, f"buyer|{investor_id}")
    return f"مستثمر #{digest[:6].upper()}"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_quote_token(payload: dict[str, Any]) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = _hmac_hex(get_settings().quote_signing_secret, body)
    return f"{body}.{sig}"


def read_quote_token(token: str) -> dict[str, Any] | None:
    """Return the payload if the signature is valid, else None. Expiry is checked by the caller."""
    try:
        body, sig = token.split(".", 1)
        expected = _hmac_hex(get_settings().quote_signing_secret, body)
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
