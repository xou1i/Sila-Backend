from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.jobs.scheduler import expire_subscriptions_job
from app.models import AuditLog, User
from tests.conftest import buy, make_listing, make_user

pytestmark = pytest.mark.anyio


def _set_sub(db, user_id, tier, expiry) -> None:
    db.execute(
        update(User)
        .where(User.id == user_id)
        .values(subscription_tier=tier, subscription_expiry_date=expiry)
    )
    db.commit()


async def test_subscribe_from_now_without_kyc(client, db) -> None:
    investor = await make_user(client)  # no KYC needed
    before = datetime.now(UTC)
    r = await client.post("/api/subscription/subscribe", headers=investor.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    expiry = datetime.fromisoformat(body["subscription_expiry_date"])
    assert body["subscription_tier"] == "premium" and body["is_active"] is True
    assert timedelta(days=30) <= expiry - before < timedelta(days=30, seconds=30)
    assert body["payment"]["status"] == "success" and body["payment"]["payment_ref"]
    log = db.scalar(select(AuditLog).where(AuditLog.event_type == "mock_payment"))
    assert log.data["purpose"] == "subscription"
    assert log.data["payment_ref"] == body["payment"]["payment_ref"]


async def test_renewal_extends_from_current_expiry(client, db) -> None:
    investor = await make_user(client)
    current = datetime.now(UTC) + timedelta(days=10)
    _set_sub(db, investor.id, "premium", current)
    r = await client.post("/api/subscription/subscribe", headers=investor.headers)
    expiry = datetime.fromisoformat(r.json()["subscription_expiry_date"])
    assert expiry == current + timedelta(days=30)


async def test_expired_subscription_restarts_from_now(client, db) -> None:
    investor = await make_user(client)
    _set_sub(db, investor.id, "premium", datetime.now(UTC) - timedelta(days=3))
    before = datetime.now(UTC)
    r = await client.post("/api/subscription/subscribe", headers=investor.headers)
    expiry = datetime.fromisoformat(r.json()["subscription_expiry_date"])
    assert timedelta(days=30) <= expiry - before < timedelta(days=30, seconds=30)


async def test_status_endpoint(client) -> None:
    investor = await make_user(client)
    r = await client.get("/api/subscription/status", headers=investor.headers)
    body = r.json()
    assert body["subscription_tier"] == "free" and body["subscription_expiry_date"] is None
    assert body["is_active"] is False and body["days_remaining"] == 0
    seller = await make_user(client, "seller")
    assert (await client.get("/api/subscription/status", headers=seller.headers)).status_code == 403


async def test_insights_gate_is_the_date_not_the_tier(client, db) -> None:
    investor = await make_user(client, kyc=True)
    r = await client.get("/api/ai/insights", headers=investor.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "SUBSCRIPTION_REQUIRED"

    # Stale tier 'premium' but expired date → still blocked.
    _set_sub(db, investor.id, "premium", datetime.now(UTC) - timedelta(seconds=1))
    r = await client.get("/api/ai/insights", headers=investor.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "SUBSCRIPTION_REQUIRED"

    await client.post("/api/subscription/subscribe", headers=investor.headers)
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 21)
    await buy(client, investor, listing["id"], "10")
    r = await client.get("/api/ai/insights", headers=investor.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "rules" and body["alerts"]
    assert body["market"]["trend"] in {"up", "down", "flat"}
    assert body["portfolio"]["total_grams"] == "10.000"
    assert body["portfolio"]["transactions_count"] == 1
    assert body["portfolio"]["by_karat"][0]["karat"] == 21


async def test_expiry_job(client, db) -> None:
    expired = await make_user(client)
    active = await make_user(client)
    _set_sub(db, expired.id, "premium", datetime.now(UTC) - timedelta(minutes=5))
    _set_sub(db, active.id, "premium", datetime.now(UTC) + timedelta(days=5))
    expire_subscriptions_job()
    db.expire_all()
    tiers = dict(db.execute(select(User.id, User.subscription_tier)).all())
    assert tiers[__import__("uuid").UUID(expired.id)].value == "free"
    assert tiers[__import__("uuid").UUID(active.id)].value == "premium"


async def test_payment_failure_leaves_tier_unchanged(client, monkeypatch) -> None:
    investor = await make_user(client)
    monkeypatch.setattr(get_settings(), "mock_payment_fail", True)
    r = await client.post("/api/subscription/subscribe", headers=investor.headers)
    assert r.status_code == 402 and r.json()["error_code"] == "PAYMENT_FAILED"
    me = (await client.get("/api/users/me", headers=investor.headers)).json()
    assert me["subscription_tier"] == "free" and me["subscription_expiry_date"] is None
