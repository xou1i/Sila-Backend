import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.models import AssetListing, AuditLog, FractionalOwnershipRecord, Transaction
from tests.conftest import add_snapshot, buy, make_listing, make_user

pytestmark = pytest.mark.anyio


def _counts(db) -> tuple:
    db.expire_all()
    return (
        db.scalar(select(func.count()).select_from(Transaction)),
        db.scalar(select(func.count()).select_from(FractionalOwnershipRecord)),
        db.scalar(select(func.count()).select_from(AuditLog)),
        db.scalar(select(func.sum(AssetListing.available_weight_grams))),
    )


async def _preview(client, investor, asset_id, grams):
    return await client.post(
        "/api/transactions/preview",
        json={"asset_id": asset_id, "purchased_weight_grams": grams},
        headers=investor.headers,
    )


async def test_preview_numbers_and_quote(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 21)
    investor = await make_user(client)  # KYC not needed for preview
    r = await _preview(client, investor, listing["id"], "84.250")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["execution_price_per_gram"] == "126875.00"
    assert body["principal_amount"] == "10689218.75"
    assert body["commission_rate"] == "0.0100"
    assert body["commission_amount"] == "106892.19"
    assert body["total_paid_by_investor"] == "10796110.94"
    assert body["quote_token"] and body["quote_expires_at"] > body["quoted_at"]
    assert body["risk_insight"]["level"] in {"low", "medium", "high"}


async def test_preview_has_no_side_effects(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investor = await make_user(client, kyc=True)
    before = _counts(db)
    for _ in range(3):
        assert (await _preview(client, investor, listing["id"], "60")).status_code == 200
    assert _counts(db) == before


async def test_confirm_happy_path(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 24)
    investor = await make_user(client, kyc=True)
    r = await buy(client, investor, listing["id"], "40")
    assert r.status_code == 201, r.text
    body = r.json()
    tx = body["transaction"]
    assert tx["purchased_weight_grams"] == "40.000"
    assert tx["principal_amount"] == "5800000.00"
    assert tx["commission_rate"] == "0.0150"
    assert tx["commission_amount"] == "87000.00"
    assert tx["total_paid_by_investor"] == "5887000.00"
    assert body["ownership"]["total_accumulated_grams"] == "40.000"
    assert body["listing_available_weight_grams"] == "60.000"
    assert body["listing_status"] == "active"

    events = set(db.scalars(select(AuditLog.event_type)).all())
    assert {"transaction_executed", "ownership_updated"} <= events


async def test_confirm_uses_quoted_price_even_if_market_moves(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 24)
    investor = await make_user(client, kyc=True)
    preview = (await _preview(client, investor, listing["id"], "10")).json()
    add_snapshot(Decimal("150000.00"))
    r = await client.post(
        "/api/transactions/confirm",
        json={
            "asset_id": listing["id"],
            "purchased_weight_grams": "10",
            "quote_token": preview["quote_token"],
        },
        headers=investor.headers,
    )
    assert r.status_code == 201
    assert r.json()["transaction"]["execution_price_per_gram"] == "145000.00"


async def test_kyc_gate_on_confirm(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client)
    before = _counts(db)
    r = await buy(client, investor, listing["id"], "5")
    assert r.status_code == 403 and r.json()["error_code"] == "KYC_NOT_VERIFIED"
    assert _counts(db) == before
    await client.post("/api/kyc/verify", headers=investor.headers)
    assert (await buy(client, investor, listing["id"], "5")).status_code == 201


async def test_listing_not_active(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client, kyc=True)
    preview = (await _preview(client, investor, listing["id"], "5")).json()
    await client.patch(
        f"/api/listings/{listing['id']}", json={"status": "suspended"}, headers=seller.headers
    )
    r = await client.post(
        "/api/transactions/confirm",
        json={
            "asset_id": listing["id"],
            "purchased_weight_grams": "5",
            "quote_token": preview["quote_token"],
        },
        headers=investor.headers,
    )
    assert r.status_code == 409 and r.json()["error_code"] == "LISTING_NOT_ACTIVE"


async def test_insufficient_weight_between_preview_and_confirm(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "10")
    alice = await make_user(client, kyc=True)
    bob = await make_user(client, kyc=True)
    bob_preview = (await _preview(client, bob, listing["id"], "8")).json()
    assert (await buy(client, alice, listing["id"], "5")).status_code == 201
    r = await client.post(
        "/api/transactions/confirm",
        json={
            "asset_id": listing["id"],
            "purchased_weight_grams": "8",
            "quote_token": bob_preview["quote_token"],
        },
        headers=bob.headers,
    )
    assert r.status_code == 409 and r.json()["error_code"] == "INSUFFICIENT_AVAILABLE_WEIGHT"
    r = await _preview(client, bob, listing["id"], "5.001")
    assert r.json()["error_code"] == "INSUFFICIENT_AVAILABLE_WEIGHT"


async def test_auto_sold_out(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "12.345")
    investor = await make_user(client, kyc=True)
    r = await buy(client, investor, listing["id"], "12.345")
    assert r.status_code == 201
    assert r.json()["listing_status"] == "sold_out"
    detail = (await client.get(f"/api/listings/{listing['id']}")).json()
    assert detail["status"] == "sold_out" and detail["available_weight_grams"] == "0.000"
    r = await buy(client, investor, listing["id"], "0.001")
    assert r.json()["error_code"] == "LISTING_NOT_ACTIVE"


@pytest.mark.parametrize("grams", ["0", "-3", "0.0001", "abc"])
async def test_invalid_weights_rejected(client, grams) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client, kyc=True)
    r = await _preview(client, investor, listing["id"], grams)
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"


async def test_concurrent_confirms_never_oversell(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investors = [await make_user(client, kyc=True) for _ in range(8)]
    previews = [(await _preview(client, inv, listing["id"], "30")).json() for inv in investors]

    async def confirm(inv, preview):
        return await client.post(
            "/api/transactions/confirm",
            json={
                "asset_id": listing["id"],
                "purchased_weight_grams": "30",
                "quote_token": preview["quote_token"],
            },
            headers=inv.headers,
        )

    results = await asyncio.gather(
        *(confirm(i, p) for i, p in zip(investors, previews, strict=True))
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 3, codes
    assert codes.count(409) == 5
    db.expire_all()
    remaining = db.scalar(
        select(AssetListing.available_weight_grams).where(AssetListing.id == listing["id"])
    )
    sold = db.scalar(select(func.sum(Transaction.purchased_weight_grams)))
    assert remaining == Decimal("10.000") and sold == Decimal("90.000")


async def test_concurrent_confirms_same_investor_first_purchase(client, db) -> None:
    """Two listings bought at once by a new investor: exactly one ownership record, both grams."""
    seller = await make_user(client, "seller", kyc=True)
    l1 = await make_listing(client, seller, "50")
    l2 = await make_listing(client, seller, "50")
    investor = await make_user(client, kyc=True)
    results = await asyncio.gather(
        buy(client, investor, l1["id"], "3"), buy(client, investor, l2["id"], "4")
    )
    assert [r.status_code for r in results] == [201, 201]
    r = await client.get("/api/ownership/me", headers=investor.headers)
    assert r.status_code == 200 and r.json()["total_accumulated_grams"] == "7.000"


async def test_quote_token_expired_or_tampered(client, monkeypatch) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client, kyc=True)
    other = await make_user(client, kyc=True)
    token = (await _preview(client, investor, listing["id"], "5")).json()["quote_token"]

    async def confirm(actor, grams, quote):
        return await client.post(
            "/api/transactions/confirm",
            json={"asset_id": listing["id"], "purchased_weight_grams": grams, "quote_token": quote},
            headers=actor.headers,
        )

    for actor, grams, quote in [
        (investor, "6", token),  # grams differ from the quote
        (other, "5", token),  # someone else's quote
        (investor, "5", token[:-2] + ("aa" if token[-2:] != "aa" else "bb")),  # bad signature
    ]:
        r = await confirm(actor, grams, quote)
        assert r.status_code == 409 and r.json()["error_code"] == "PRICE_CHANGED", r.text

    monkeypatch.setattr(get_settings(), "quote_ttl_seconds", -1)
    expired = (await _preview(client, investor, listing["id"], "5")).json()["quote_token"]
    r = await confirm(investor, "5", expired)
    assert r.status_code == 409 and r.json()["error_code"] == "PRICE_CHANGED"


async def test_idempotency_key_returns_original(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investor = await make_user(client, kyc=True)
    first = await buy(client, investor, listing["id"], "10", idempotency_key="k-1")
    again = await buy(client, investor, listing["id"], "10", idempotency_key="k-1")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["idempotent_replay"] is True
    assert first.json()["transaction"]["id"] == again.json()["transaction"]["id"]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Transaction)) == 1

    # Concurrent duplicates with the same key also buy once.
    results = await asyncio.gather(
        *(buy(client, investor, listing["id"], "10", idempotency_key="k-2") for _ in range(4))
    )
    ids = {r.json()["transaction"]["id"] for r in results}
    assert len(ids) == 1 and sorted(r.status_code for r in results).count(201) == 1
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Transaction)) == 2


async def test_transactions_are_append_only_in_db(client, db) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client, kyc=True)
    await buy(client, investor, listing["id"], "1")
    with pytest.raises(Exception, match=r"foreign key|violates"):
        db.execute(text("DELETE FROM asset_listings"))
    db.rollback()
