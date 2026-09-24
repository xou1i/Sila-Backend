import pytest

from tests.conftest import make_listing, make_user

pytestmark = pytest.mark.anyio


async def test_investor_cannot_create_listings(client) -> None:
    investor = await make_user(client, kyc=True)
    r = await client.post(
        "/api/listings", json={"total_weight_grams": "5", "karat": 24}, headers=investor.headers
    )
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"


async def test_seller_cannot_buy_or_use_investor_features(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    body = {"asset_id": listing["id"], "purchased_weight_grams": "1"}
    for method, path, payload in [
        ("POST", "/api/transactions/preview", body),
        ("POST", "/api/transactions/confirm", {**body, "quote_token": "x" * 20}),
        ("POST", "/api/ai/match", {"budget_iqd": "1000"}),
        ("POST", "/api/ai/risk-analysis", {"asset_id": listing["id"], "weight_grams": "1"}),
        ("GET", "/api/ai/insights", None),
        ("POST", "/api/subscription/subscribe", None),
        ("GET", "/api/ownership/me", None),
    ]:
        r = await client.request(method, path, json=payload, headers=seller.headers)
        assert r.status_code == 403, (path, r.text)
        assert r.json()["error_code"] == "FORBIDDEN"


async def test_non_owner_cannot_patch_or_promote(client) -> None:
    owner = await make_user(client, "seller", kyc=True)
    intruder = await make_user(client, "seller", kyc=True)
    investor = await make_user(client, kyc=True)
    listing = await make_listing(client, owner)
    for actor in (intruder, investor):
        r = await client.patch(
            f"/api/listings/{listing['id']}", json={"status": "suspended"}, headers=actor.headers
        )
        assert r.status_code == 403
        r = await client.post(f"/api/listings/{listing['id']}/promote", headers=actor.headers)
        assert r.status_code == 403


async def test_protected_endpoints_require_auth(client) -> None:
    for method, path in [
        ("GET", "/api/users/me"),
        ("POST", "/api/listings"),
        ("POST", "/api/transactions/preview"),
        ("GET", "/api/transactions"),
        ("GET", "/api/ownership/me"),
        ("GET", "/api/subscription/status"),
        ("POST", "/api/ai/match"),
    ]:
        r = await client.request(method, path, json={})
        assert r.status_code == 401, path
