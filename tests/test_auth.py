import pytest
from sqlalchemy import select

from app.core.security import create_token
from app.models import User
from tests.conftest import PASSWORD, make_user

pytestmark = pytest.mark.anyio


async def test_signup_investor_defaults_and_hashing(client, db) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={
            "role": "investor",
            "full_name": "زينب الموسوي",
            "email": "  Zainab@Example.IQ ",
            "password": PASSWORD,
            "risk_profile": "low",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "zainab@example.iq"
    assert body["kyc_verified"] is False
    assert body["subscription_tier"] == "free"
    assert body["risk_profile"] == "low"
    assert "password" not in body and "password_hash" not in body
    stored = db.scalar(select(User).where(User.email == "zainab@example.iq"))
    assert stored.password_hash.startswith("$2") and PASSWORD not in stored.password_hash


async def test_investor_requires_risk_profile_and_seller_ignores_it(client) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"role": "investor", "full_name": "x y", "email": "a@a.iq", "password": PASSWORD},
    )
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"
    r = await client.post(
        "/api/auth/signup",
        json={
            "role": "seller",
            "full_name": "مجوهرات الكرّادة",
            "email": "s@s.iq",
            "password": PASSWORD,
            "risk_profile": "high",
        },
    )
    assert r.status_code == 201 and r.json()["risk_profile"] is None


async def test_duplicate_email_is_409(client) -> None:
    body = {"role": "seller", "full_name": "shop", "email": "dup@x.iq", "password": PASSWORD}
    assert (await client.post("/api/auth/signup", json=body)).status_code == 201
    body["email"] = "DUP@x.iq"
    r = await client.post("/api/auth/signup", json=body)
    assert r.status_code == 409
    assert r.json()["error_code"] == "EMAIL_ALREADY_EXISTS"


async def test_short_password_rejected(client) -> None:
    r = await client.post(
        "/api/auth/signup",
        json={"role": "seller", "full_name": "shop", "email": "p@x.iq", "password": "short"},
    )
    assert r.status_code == 422
    assert any(d["field"] == "password" for d in r.json()["details"])


async def test_login_generic_error_no_enumeration(client) -> None:
    await make_user(client, "seller")
    wrong_pw = await client.post(
        "/api/auth/login", json={"email": "seller0@test.iq", "password": "nope-nope"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "ghost@test.iq", "password": "nope-nope"}
    )
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()
    assert wrong_pw.json()["error_code"] == "INVALID_CREDENTIALS"


async def test_login_me_and_refresh(client) -> None:
    investor = await make_user(client, "investor", risk_profile="high")
    r = await client.get("/api/users/me", headers=investor.headers)
    assert r.status_code == 200
    me = r.json()
    for field in (
        "role",
        "full_name",
        "email",
        "kyc_verified",
        "risk_profile",
        "subscription_tier",
        "subscription_expiry_date",
        "is_premium_active",
    ):
        assert field in me

    login = await client.post("/api/auth/login", json={"email": me["email"], "password": PASSWORD})
    tokens = login.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 15 * 60

    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 200
    new_access = r.json()["access_token"]
    assert (
        await client.get("/api/users/me", headers={"Authorization": f"Bearer {new_access}"})
    ).status_code == 200


async def test_token_types_are_not_interchangeable(client) -> None:
    investor = await make_user(client)
    login = await client.post(
        "/api/auth/login", json={"email": investor.user["email"], "password": PASSWORD}
    )
    tokens = login.json()
    # Refresh token used as access token → 401
    r = await client.get(
        "/api/users/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )
    assert r.status_code == 401 and r.json()["error_code"] == "UNAUTHORIZED"
    # Access token used as refresh token → 401
    r = await client.post("/api/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert r.status_code == 401


async def test_invalid_and_expired_tokens(client, monkeypatch) -> None:
    investor = await make_user(client)
    assert (
        await client.get("/api/users/me", headers={"Authorization": "Bearer garbage"})
    ).status_code == 401
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "access_token_ttl_minutes", -1)
    import uuid

    expired, _ = create_token(uuid.UUID(investor.id), "investor", "access")
    r = await client.get("/api/users/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401
