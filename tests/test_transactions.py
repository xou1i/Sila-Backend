import pytest

from tests.conftest import buy, make_listing, make_user

pytestmark = pytest.mark.anyio


async def test_history_views_and_party_access(client) -> None:
    seller = await make_user(client, "seller", kyc=True, name="صاغة شارع النهر")
    other_seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100", 22)
    other_listing = await make_listing(client, other_seller, "100", 24)
    investor = await make_user(client, kyc=True)
    stranger = await make_user(client, kyc=True)

    tx1 = (await buy(client, investor, listing["id"], "5")).json()["transaction"]
    await buy(client, investor, other_listing["id"], "2")
    await buy(client, stranger, other_listing["id"], "1")

    mine = (await client.get("/api/transactions", headers=investor.headers)).json()
    assert mine["total"] == 2
    assert all(t["buyer_ref"] is None for t in mine["items"])

    sales = (await client.get("/api/transactions", headers=seller.headers)).json()
    assert sales["total"] == 1
    sale = sales["items"][0]
    assert sale["id"] == tx1["id"] and sale["seller_name"] == "صاغة شارع النهر"
    assert sale["buyer_ref"].startswith("مستثمر #")
    assert investor.id not in str(sale)
    assert sale["principal_amount"] == tx1["principal_amount"]

    url = f"/api/transactions/{tx1['id']}"
    assert (await client.get(url, headers=investor.headers)).status_code == 200
    assert (await client.get(url, headers=seller.headers)).status_code == 200
    for outsider in (stranger, other_seller):
        r = await client.get(url, headers=outsider.headers)
        assert r.status_code == 404 and r.json()["error_code"] == "NOT_FOUND"


async def test_history_pagination(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "100")
    investor = await make_user(client, kyc=True)
    for _ in range(3):
        await buy(client, investor, listing["id"], "1")
    page = (
        await client.get("/api/transactions", params={"limit": 2}, headers=investor.headers)
    ).json()
    assert page["total"] == 3 and len(page["items"]) == 2


async def test_no_update_or_delete_routes(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller)
    investor = await make_user(client, kyc=True)
    tx = (await buy(client, investor, listing["id"], "1")).json()["transaction"]
    for method in ("PATCH", "PUT", "DELETE"):
        r = await client.request(method, f"/api/transactions/{tx['id']}", headers=investor.headers)
        assert r.status_code == 405
