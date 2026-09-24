import pytest

from app.core.config import get_settings
from tests.conftest import PASSWORD, make_user

pytestmark = pytest.mark.anyio

_KEYS = {"error_code", "message", "status"}


def _assert_standard(r, status: int, code: str) -> None:
    assert r.status_code == status, r.text
    body = r.json()
    assert _KEYS <= body.keys() and body["status"] == status and body["error_code"] == code
    assert body["message"]
    assert "Traceback" not in r.text and "detail" not in body


async def test_validation_error_format(client) -> None:
    r = await client.post("/api/auth/signup", json={"role": "investor"})
    _assert_standard(r, 422, "VALIDATION_ERROR")
    fields = {d["field"] for d in r.json()["details"]}
    assert {"full_name", "email", "password"} <= fields


async def test_malformed_json(client) -> None:
    r = await client.post(
        "/api/auth/login", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    _assert_standard(r, 422, "VALIDATION_ERROR")


async def test_401_404_405_409(client) -> None:
    _assert_standard(await client.get("/api/users/me"), 401, "UNAUTHORIZED")
    _assert_standard(await client.get("/api/nope"), 404, "NOT_FOUND")
    _assert_standard(await client.put("/api/market/prices"), 405, "METHOD_NOT_ALLOWED")
    body = {"role": "seller", "full_name": "shop", "email": "e@e.iq", "password": PASSWORD}
    await client.post("/api/auth/signup", json=body)
    _assert_standard(await client.post("/api/auth/signup", json=body), 409, "EMAIL_ALREADY_EXISTS")


async def test_429_rate_limited(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_login", "3/minute")
    creds = {"email": "x@x.iq", "password": "whatever1"}
    for _ in range(3):
        assert (await client.post("/api/auth/login", json=creds)).status_code == 401
    r = await client.post("/api/auth/login", json=creds)
    _assert_standard(r, 429, "RATE_LIMITED")
    assert int(r.headers["Retry-After"]) >= 1


async def test_rate_limit_kyc_and_ai(client, monkeypatch) -> None:
    investor = await make_user(client)
    monkeypatch.setattr(get_settings(), "rate_limit_kyc", "1/minute")
    monkeypatch.setattr(get_settings(), "rate_limit_ai", "1/minute")
    assert (await client.post("/api/kyc/verify", headers=investor.headers)).status_code == 200
    _assert_standard(
        await client.post("/api/kyc/verify", headers=investor.headers), 429, "RATE_LIMITED"
    )
    await client.post("/api/ai/match", json={"budget_iqd": "1000"}, headers=investor.headers)
    r = await client.post("/api/ai/match", json={"budget_iqd": "1000"}, headers=investor.headers)
    _assert_standard(r, 429, "RATE_LIMITED")


async def test_500_is_generic(client, monkeypatch) -> None:
    from app.modules.market import service

    def explode(_db):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(service, "market_prices", explode)
    r = await client.get("/api/market/prices")
    _assert_standard(r, 500, "INTERNAL_ERROR")
    assert "secret" not in r.text


async def test_cors_restricted(client) -> None:
    allowed = await client.options(
        "/api/market/prices",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = await client.options(
        "/api/market/prices",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in denied.headers
