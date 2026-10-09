"""Investor resale (Workflow 09): reservation, transfer between two signed records, privacy."""

from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import buy, make_listing, make_user

pytestmark = pytest.mark.anyio

RESALE = "/api/ownership/resale"


async def _holder(client, grams: str = "40", karat: int = 21):
    """An investor who bought `grams` of `karat` from a seller listing."""
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "200", karat)
    investor = await make_user(client, kyc=True, name="زينب المستثمرة")
    r = await buy(client, investor, listing["id"], grams)
    assert r.status_code == 201, r.text
    return investor


async def _list(client, investor, grams: str, karat: int = 21, key: str | None = None):
    headers = dict(investor.headers) | ({"Idempotency-Key": key} if key else {})
    return await client.post(RESALE, json={"karat": karat, "weight_grams": grams}, headers=headers)


async def _balance(client, investor) -> dict:
    r = await client.get("/api/ownership/me", headers=investor.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_holdings_by_karat_and_reservation(client) -> None:
    investor = await _holder(client, "40", 21)
    body = await _balance(client, investor)
    assert body["total_accumulated_grams"] == "40.000"
    assert body["by_karat"] == [
        {
            "karat": 21,
            "owned_grams": "40.000",
            "reserved_grams": "0.000",
            "available_to_resell_grams": "40.000",
        }
    ]

    r = await _list(client, investor, "15")
    assert r.status_code == 201, r.text
    listing = r.json()
    assert listing["listing_type"] == "investor_resale"
    assert listing["seller_name"] == "مستثمر على صِلة"  # never the investor's name
    assert listing["available_weight_grams"] == "15.000"

    k21 = (await _balance(client, investor))["by_karat"][0]
    assert k21["reserved_grams"] == "15.000" and k21["available_to_resell_grams"] == "25.000"
    # Still owned (and in the signed total) until sold
    assert (await _balance(client, investor))["total_accumulated_grams"] == "40.000"

    market = (await client.get("/api/listings")).json()["items"]
    assert any(item["id"] == listing["id"] for item in market)


async def test_cannot_offer_more_than_available(client) -> None:
    investor = await _holder(client, "10", 21)
    assert (await _list(client, investor, "8")).status_code == 201
    r = await _list(client, investor, "3")  # only 2 g left to offer
    assert r.status_code == 409 and r.json()["error_code"] == "INSUFFICIENT_HOLDINGS"
    r = await _list(client, investor, "1", karat=24)  # owns no 24K
    assert r.status_code == 409


async def test_investor_without_holdings_cannot_offer(client) -> None:
    investor = await make_user(client, kyc=True)
    r = await _list(client, investor, "1")
    assert r.status_code == 409 and r.json()["error_code"] == "INSUFFICIENT_HOLDINGS"


async def test_seller_cannot_use_resale(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    r = await _list(client, seller, "1")
    assert r.status_code == 403


async def test_idempotent_resale(client) -> None:
    investor = await _holder(client, "10", 21)
    first = await _list(client, investor, "4", key="resale-1")
    again = await _list(client, investor, "4", key="resale-1")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert (await _balance(client, investor))["by_karat"][0]["reserved_grams"] == "4.000"


async def test_purchase_transfers_between_both_signed_records(client) -> None:
    reseller = await _holder(client, "40", 21)
    listing = (await _list(client, reseller, "15")).json()
    buyer = await make_user(client, kyc=True)

    r = await buy(client, buyer, listing["id"], "5")
    assert r.status_code == 201, r.text
    # Both balances read back fine: both records were re-signed in the same transaction
    seller_side = await _balance(client, reseller)
    buyer_side = await _balance(client, buyer)
    assert seller_side["total_accumulated_grams"] == "35.000"
    assert seller_side["by_karat"][0] == {
        "karat": 21,
        "owned_grams": "35.000",
        "reserved_grams": "10.000",
        "available_to_resell_grams": "25.000",
    }
    assert buyer_side["total_accumulated_grams"] == "5.000"

    sold = (await client.get("/api/transactions", headers=reseller.headers)).json()["items"]
    sale = next(t for t in sold if t["side"] == "sell")
    assert sale["purchased_weight_grams"] == "5.000" and sale["buyer_ref"]
    bought = (await client.get("/api/transactions", headers=buyer.headers)).json()["items"]
    assert bought[0]["side"] == "buy" and bought[0]["seller_name"] == "مستثمر على صِلة"


async def test_buying_the_whole_resale_sells_it_out(client) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "10")).json()
    buyer = await make_user(client, kyc=True)
    assert (await buy(client, buyer, listing["id"], "10")).status_code == 201
    assert (await client.get(f"/api/listings/{listing['id']}")).json()["status"] == "sold_out"
    assert (await _balance(client, reseller))["total_accumulated_grams"] == "0.000"


async def test_cannot_buy_own_resale(client) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "5")).json()
    r = await buy(client, reseller, listing["id"], "1")
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"


async def test_withdraw_releases_and_is_final(client) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "6")).json()
    url = f"{RESALE}/{listing['id']}"

    r = await client.patch(url, json={"status": "suspended"}, headers=reseller.headers)
    assert r.status_code == 200
    # Suspended keeps the grams reserved
    assert (await _balance(client, reseller))["by_karat"][0]["reserved_grams"] == "6.000"

    r = await client.patch(url, json={"status": "withdrawn"}, headers=reseller.headers)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    k21 = (await _balance(client, reseller))["by_karat"][0]
    assert k21["reserved_grams"] == "0.000" and k21["available_to_resell_grams"] == "10.000"

    r = await client.patch(url, json={"status": "active"}, headers=reseller.headers)
    assert r.status_code == 409 and r.json()["error_code"] == "INVALID_STATUS_TRANSITION"


async def test_only_the_owner_changes_a_resale(client) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "5")).json()
    other = await make_user(client, kyc=True)
    r = await client.patch(
        f"{RESALE}/{listing['id']}", json={"status": "withdrawn"}, headers=other.headers
    )
    assert r.status_code == 403
    mine = (await client.get(RESALE, headers=reseller.headers)).json()
    assert [x["id"] for x in mine] == [listing["id"]]


async def test_match_never_suggests_my_own_resale(client) -> None:
    reseller = await _holder(client, "40", 21)
    listing = (await _list(client, reseller, "20")).json()
    r = await client.post("/api/ai/match", json={"budget_iqd": "5000000"}, headers=reseller.headers)
    assert listing["id"] not in [x["listing"]["id"] for x in r.json()["results"]]


async def test_parties_are_notified(client) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "5")).json()
    buyer = await make_user(client, kyc=True)
    assert (await buy(client, buyer, listing["id"], "2")).status_code == 201
    kinds = [
        n["kind"]
        for n in (await client.get("/api/notifications", headers=reseller.headers)).json()["items"]
    ]
    assert "resale_sold" in kinds
    body = (await client.get("/api/notifications", headers=buyer.headers)).json()
    assert body["unread_count"] >= 1 and body["items"][0]["kind"] == "purchase_completed"


async def test_tampered_reseller_balance_blocks_the_sale(client, db) -> None:
    reseller = await _holder(client, "10", 21)
    listing = (await _list(client, reseller, "5")).json()
    db.execute(
        text(
            "UPDATE fractional_ownership_records SET total_accumulated_grams = 999 "
            "WHERE investor_id = :id"
        ),
        {"id": reseller.id},
    )
    db.commit()
    buyer = await make_user(client, kyc=True)
    r = await buy(client, buyer, listing["id"], "1")
    assert r.status_code == 403 and r.json()["error_code"] == "INTEGRITY_CHECK_FAILED"
    # Nothing moved: the buyer owns nothing and the listing still has its grams
    assert (await _balance(client, buyer))["total_accumulated_grams"] == "0.000"
    after = (await client.get(f"/api/listings/{listing['id']}")).json()
    assert Decimal(after["available_weight_grams"]) == Decimal("5")


async def test_concurrent_offers_never_reserve_twice(client) -> None:
    import asyncio

    investor = await _holder(client, "10", 21)
    # Five parallel offers of 4 g each from 10 g: at most two can fit
    results = await asyncio.gather(*(_list(client, investor, "4") for _ in range(5)))
    created = [r for r in results if r.status_code == 201]
    refused = [r for r in results if r.status_code == 409]
    assert len(created) == 2 and len(refused) == 3
    assert (await _balance(client, investor))["by_karat"][0]["reserved_grams"] == "8.000"


async def test_crossing_resale_purchases_do_not_deadlock(client) -> None:
    import asyncio

    a = await _holder(client, "10", 21)
    b = await _holder(client, "10", 21)
    listing_a = (await _list(client, a, "5")).json()
    listing_b = (await _list(client, b, "5")).json()
    # A buys from B while B buys from A: both must finish
    ra, rb = await asyncio.gather(
        buy(client, a, listing_b["id"], "2"), buy(client, b, listing_a["id"], "2")
    )
    assert ra.status_code == 201 and rb.status_code == 201
    assert (await _balance(client, a))["total_accumulated_grams"] == "10.000"
    assert (await _balance(client, b))["total_accumulated_grams"] == "10.000"


async def test_premium_insights_count_only_what_is_still_held(client) -> None:
    reseller = await _holder(client, "40", 21)
    await client.post("/api/subscription/subscribe", headers=reseller.headers)
    paid_before = (await client.get("/api/ai/insights", headers=reseller.headers)).json()[
        "portfolio"
    ]["total_paid_iqd"]
    listing = (await _list(client, reseller, "10")).json()
    buyer = await make_user(client, kyc=True)
    assert (await buy(client, buyer, listing["id"], "10")).status_code == 201

    portfolio = (await client.get("/api/ai/insights", headers=reseller.headers)).json()["portfolio"]
    assert portfolio["total_grams"] == "30.000"
    assert portfolio["by_karat"][0]["grams"] == "30.000"
    # The cost of the 30 g still held: three quarters of what the 40 g cost
    assert Decimal(portfolio["total_paid_iqd"]) == (Decimal(paid_before) * 3 / 4).quantize(
        Decimal("0.01")
    )
