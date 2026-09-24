from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models import AssetListing, AuditLog
from tests.conftest import make_listing, make_user

pytestmark = pytest.mark.anyio


async def test_create_listing_prices_server_side(client) -> None:
    seller = await make_user(client, "seller", kyc=True, name="مجوهرات الكرّادة")
    r = await client.post(
        "/api/listings",
        json={
            "total_weight_grams": "84.250",
            "karat": 21,
            "base_price_per_gram": "1",
            "is_promoted": True,
        },
        headers=seller.headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["base_price_per_gram"] == "126875.00"  # 145000 × 21/24, client value ignored
    assert body["available_weight_grams"] == body["total_weight_grams"] == "84.250"
    assert body["status"] == "active"
    assert body["is_promoted"] is False
    assert body["seller_name"] == "مجوهرات الكرّادة"
    assert body["seller_kyc_verified"] is True


async def test_create_listing_requires_kyc_and_retry_is_idempotent(client, db) -> None:
    seller = await make_user(client, "seller")
    payload = {"total_weight_grams": "10", "karat": 24}
    headers = {**seller.headers, "Idempotency-Key": "publish-1"}
    r = await client.post("/api/listings", json=payload, headers=headers)
    assert r.status_code == 403 and r.json()["error_code"] == "KYC_NOT_VERIFIED"
    assert db.scalar(select(AssetListing)) is None

    await client.post("/api/kyc/seller", headers=seller.headers)
    first = await client.post("/api/listings", json=payload, headers=headers)
    again = await client.post("/api/listings", json=payload, headers=headers)
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]


@pytest.mark.parametrize(
    "payload",
    [
        {"total_weight_grams": "0", "karat": 24},
        {"total_weight_grams": "-5", "karat": 24},
        {"total_weight_grams": "10", "karat": 20},
        {"total_weight_grams": "1.2345", "karat": 24},
        {"karat": 24},
    ],
)
async def test_create_listing_validation(client, payload) -> None:
    seller = await make_user(client, "seller", kyc=True)
    r = await client.post("/api/listings", json=payload, headers=seller.headers)
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"


async def test_browse_filters_pagination_and_active_only(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    a = await make_listing(client, seller, "10", 24)
    await make_listing(client, seller, "20", 21)
    await make_listing(client, seller, "30", 18)
    suspended = await make_listing(client, seller, "40", 22)
    await client.patch(
        f"/api/listings/{suspended['id']}", json={"status": "suspended"}, headers=seller.headers
    )

    r = await client.get("/api/listings")
    assert r.status_code == 200 and r.json()["total"] == 3

    r = await client.get("/api/listings", params={"karat": 24})
    assert [i["id"] for i in r.json()["items"]] == [a["id"]]

    r = await client.get("/api/listings", params={"min_price": "110000", "max_price": "130000"})
    assert [i["karat"] for i in r.json()["items"]] == [21]

    r = await client.get("/api/listings", params={"limit": 2, "offset": 2})
    body = r.json()
    assert body["total"] == 3 and len(body["items"]) == 1 and body["limit"] == 2

    r = await client.get("/api/listings", params={"karat": 22})
    assert r.status_code == 200 and r.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_promoted_first_honors_expiry(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    old_promoted = await make_listing(client, seller, "10")
    expired_promo = await make_listing(client, seller, "11")
    newest = await make_listing(client, seller, "12")
    now = datetime.now(UTC)
    db.execute(
        update(AssetListing)
        .where(AssetListing.id == old_promoted["id"])
        .values(is_promoted=True, promotion_expiry_date=now + timedelta(days=2))
    )
    db.execute(
        update(AssetListing)
        .where(AssetListing.id == expired_promo["id"])
        .values(is_promoted=True, promotion_expiry_date=now - timedelta(minutes=1))
    )
    db.commit()

    for params in ({}, {"sort": "promoted_first"}):
        items = (await client.get("/api/listings", params=params)).json()["items"]
        assert [i["id"] for i in items] == [old_promoted["id"], newest["id"], expired_promo["id"]]
        assert [i["is_promoted"] for i in items] == [True, False, False]


async def test_detail_and_not_found(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    r = await client.get(f"/api/listings/{listing['id']}")
    assert r.status_code == 200 and r.json()["current_price_per_gram"] == "145000.00"
    r = await client.get("/api/listings/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404 and r.json()["error_code"] == "NOT_FOUND"


async def test_patch_status_owner_only_and_sold_out_final(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    other = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    url = f"/api/listings/{listing['id']}"

    r = await client.patch(url, json={"status": "suspended"}, headers=other.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"
    r = await client.patch(url, json={"status": "suspended"}, headers=seller.headers)
    assert r.status_code == 200 and r.json()["status"] == "suspended"
    r = await client.patch(url, json={"status": "active"}, headers=seller.headers)
    assert r.json()["status"] == "active"
    r = await client.patch(url, json={"status": "sold_out"}, headers=seller.headers)
    assert r.status_code == 422

    db.execute(
        update(AssetListing)
        .where(AssetListing.id == listing["id"])
        .values(status="sold_out", available_weight_grams=Decimal("0"))
    )
    db.commit()
    r = await client.patch(url, json={"status": "active"}, headers=seller.headers)
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATUS_TRANSITION"


async def test_no_delete_endpoint(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    r = await client.delete(f"/api/listings/{listing['id']}", headers=seller.headers)
    assert r.status_code == 405 and r.json()["error_code"] == "METHOD_NOT_ALLOWED"


async def test_seller_own_listings(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    other = await make_user(client, "seller", kyc=True)
    mine = await make_listing(client, seller)
    await make_listing(client, other)
    await client.patch(
        f"/api/listings/{mine['id']}", json={"status": "suspended"}, headers=seller.headers
    )
    r = await client.get("/api/listings", params={"seller_id": "me"}, headers=seller.headers)
    items = r.json()["items"]
    assert [i["id"] for i in items] == [mine["id"]] and items[0]["status"] == "suspended"
    assert (await client.get("/api/listings", params={"seller_id": "me"})).status_code == 401
    investor = await make_user(client)
    r = await client.get("/api/listings", params={"seller_id": "me"}, headers=investor.headers)
    assert r.status_code == 403


async def test_promote_charges_fee_and_extends(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    url = f"/api/listings/{listing['id']}/promote"
    r = await client.post(url, json={"fee": "1"}, headers=seller.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["listing"]["is_promoted"] is True
    assert body["payment"]["status"] == "success"
    assert Decimal(body["payment"]["amount_iqd"]) == get_settings().promotion_fee_iqd
    first_expiry = datetime.fromisoformat(body["listing"]["promotion_expiry_date"])

    r = await client.post(url, headers=seller.headers)
    second_expiry = datetime.fromisoformat(r.json()["listing"]["promotion_expiry_date"])
    assert second_expiry - first_expiry == timedelta(days=get_settings().promotion_duration_days)

    payments = db.scalars(select(AuditLog).where(AuditLog.event_type == "mock_payment")).all()
    assert len(payments) == 2
    assert all(p.data["purpose"] == "promotion" and p.data["payment_ref"] for p in payments)


async def test_promote_rules(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    other = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    url = f"/api/listings/{listing['id']}/promote"
    assert (await client.post(url, headers=other.headers)).status_code == 403
    await client.patch(
        f"/api/listings/{listing['id']}", json={"status": "suspended"}, headers=seller.headers
    )
    r = await client.post(url, headers=seller.headers)
    assert r.status_code == 409 and r.json()["error_code"] == "LISTING_NOT_ACTIVE"


async def test_promote_payment_failure_leaves_listing_unpromoted(client, monkeypatch) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    monkeypatch.setattr(get_settings(), "mock_payment_fail", True)
    r = await client.post(f"/api/listings/{listing['id']}/promote", headers=seller.headers)
    assert r.status_code == 402 and r.json()["error_code"] == "PAYMENT_FAILED"
    detail = (await client.get(f"/api/listings/{listing['id']}")).json()
    assert detail["is_promoted"] is False and detail["promotion_expiry_date"] is None


async def test_payments_route_is_not_public(client) -> None:
    investor = await make_user(client)
    r = await client.post("/api/payments/mock", headers=investor.headers)
    assert r.status_code == 404
