import pytest

from tests.conftest import make_user

pytestmark = pytest.mark.anyio


async def test_investor_kyc_is_permanent_and_idempotent(client) -> None:
    investor = await make_user(client, "investor")
    assert investor.user["kyc_verified"] is False
    for _ in range(2):
        r = await client.post("/api/kyc/verify", headers=investor.headers)
        assert r.status_code == 200
        assert r.json()["kyc_verified"] is True
    me = await client.get("/api/users/me", headers=investor.headers)
    assert me.json()["kyc_verified"] is True


async def test_seller_kyc(client) -> None:
    seller = await make_user(client, "seller")
    r = await client.post("/api/kyc/seller", headers=seller.headers)
    assert r.status_code == 200 and r.json()["user"]["kyc_verified"] is True


async def test_kyc_endpoints_are_role_specific(client) -> None:
    investor = await make_user(client, "investor")
    seller = await make_user(client, "seller")
    r = await client.post("/api/kyc/seller", headers=investor.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"
    r = await client.post("/api/kyc/verify", headers=seller.headers)
    assert r.status_code == 403
    assert (await client.post("/api/kyc/verify")).status_code == 401
