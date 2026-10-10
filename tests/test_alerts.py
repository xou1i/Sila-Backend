"""Premium price alerts: gated by the subscription date, fire once when the price crosses."""

from decimal import Decimal

import pytest

from app.core.db import SessionLocal
from app.modules.alerts import service as alerts
from app.modules.market import service as market
from tests.conftest import PRICE_24K, add_snapshot, make_user

pytestmark = pytest.mark.anyio

ALERTS = "/api/alerts"
# 21K at the test price: 145,000 × 21/24 = 126,875.00
PRICE_21K = Decimal("126875.00")


async def _premium(client):
    investor = await make_user(client)
    r = await client.post("/api/subscription/subscribe", headers=investor.headers)
    assert r.status_code in (200, 201), r.text
    return investor


def _fire_with_24k(price_24k: Decimal) -> int:
    add_snapshot(price_24k)
    with SessionLocal() as db:
        return alerts.check_alerts(db, market.latest_snapshot(db))


async def test_alerts_need_premium(client) -> None:
    investor = await make_user(client)
    r = await client.post(
        ALERTS,
        json={"karat": 21, "direction": "above", "target_price_per_gram": "130000"},
        headers=investor.headers,
    )
    assert r.status_code == 403 and r.json()["error_code"] == "SUBSCRIPTION_REQUIRED"


async def test_target_must_be_ahead_of_the_price(client) -> None:
    investor = await _premium(client)
    r = await client.post(
        ALERTS,
        json={"karat": 21, "direction": "above", "target_price_per_gram": "120000"},
        headers=investor.headers,
    )
    assert r.status_code == 422
    r = await client.post(
        ALERTS,
        json={"karat": 21, "direction": "below", "target_price_per_gram": "130000"},
        headers=investor.headers,
    )
    assert r.status_code == 422


async def test_alert_fires_once_and_notifies(client) -> None:
    investor = await _premium(client)
    r = await client.post(
        ALERTS,
        json={"karat": 21, "direction": "above", "target_price_per_gram": "130000"},
        headers=investor.headers,
    )
    assert r.status_code == 201 and r.json()["status"] == "active"

    assert _fire_with_24k(PRICE_24K) == 0  # not crossed yet
    # 150,000 × 21/24 = 131,250 >= 130,000
    assert _fire_with_24k(Decimal("150000.00")) == 1
    assert _fire_with_24k(Decimal("151000.00")) == 0  # never twice

    mine = (await client.get(ALERTS, headers=investor.headers)).json()
    assert mine[0]["status"] == "triggered" and mine[0]["triggered_at"]
    bell = (await client.get("/api/notifications", headers=investor.headers)).json()
    assert bell["items"][0]["kind"] == "price_alert"
    assert "131,250" in bell["items"][0]["body"]


async def test_below_alert(client) -> None:
    investor = await _premium(client)
    await client.post(
        ALERTS,
        json={"karat": 21, "direction": "below", "target_price_per_gram": "120000"},
        headers=investor.headers,
    )
    # 136,000 × 21/24 = 119,000 <= 120,000
    assert _fire_with_24k(Decimal("136000.00")) == 1


async def test_cancel_alert(client) -> None:
    investor = await _premium(client)
    alert = (
        await client.post(
            ALERTS,
            json={"karat": 21, "direction": "above", "target_price_per_gram": "130000"},
            headers=investor.headers,
        )
    ).json()
    r = await client.patch(
        f"{ALERTS}/{alert['id']}", json={"status": "cancelled"}, headers=investor.headers
    )
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _fire_with_24k(Decimal("150000.00")) == 0
    other = await make_user(client)
    r = await client.patch(
        f"{ALERTS}/{alert['id']}", json={"status": "cancelled"}, headers=other.headers
    )
    assert r.status_code == 404
    assert PRICE_21K == Decimal("126875.00")
