import pytest
from sqlalchemy import select, text

from app.core.signature import verify_ownership
from app.models import AuditLog, FractionalOwnershipRecord
from tests.conftest import buy, make_listing, make_user

pytestmark = pytest.mark.anyio


async def test_new_investor_has_zero(client) -> None:
    investor = await make_user(client)
    r = await client.get("/api/ownership/me", headers=investor.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total_accumulated_grams"] == "0.000"
    assert body["verified"] is True and body["message"]
    assert body["disclaimer"]


async def test_signature_valid_after_purchases(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investor = await make_user(client, kyc=True)
    await buy(client, investor, listing["id"], "12.5")
    await buy(client, investor, listing["id"], "0.25")
    r = await client.get("/api/ownership/me", headers=investor.headers)
    assert r.status_code == 200
    assert r.json()["total_accumulated_grams"] == "12.750"
    record = db.scalar(select(FractionalOwnershipRecord))
    assert verify_ownership(
        record.investor_id,
        record.total_accumulated_grams,
        record.updated_at,
        record.digital_signature_token,
    )


async def test_tampering_detected(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investor = await make_user(client, kyc=True)
    await buy(client, investor, listing["id"], "10")
    db.execute(text("UPDATE fractional_ownership_records SET total_accumulated_grams = 999"))
    db.commit()

    r = await client.get("/api/ownership/me", headers=investor.headers)
    assert r.status_code == 403
    body = r.json()
    assert body["error_code"] == "INTEGRITY_CHECK_FAILED"
    assert "999" not in r.text and "total_accumulated_grams" not in body
    incident = db.scalar(select(AuditLog).where(AuditLog.event_type == "integrity_check_failed"))
    assert incident is not None and str(incident.actor_id) == investor.id

    # A purchase must not re-sign (launder) a tampered balance.
    r = await buy(client, investor, listing["id"], "1")
    assert r.status_code == 403 and r.json()["error_code"] == "INTEGRITY_CHECK_FAILED"
    db.expire_all()
    assert db.scalar(select(FractionalOwnershipRecord.total_accumulated_grams)) == 999


async def test_ownership_is_investor_only(client) -> None:
    seller = await make_user(client, "seller")
    assert (await client.get("/api/ownership/me", headers=seller.headers)).status_code == 403
