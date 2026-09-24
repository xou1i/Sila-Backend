from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.models import PriceSnapshot
from app.modules.market import provider
from app.modules.market import service as market
from tests.conftest import add_snapshot, hours_ago

pytestmark = pytest.mark.anyio


async def test_prices_all_karats_from_cache(client, monkeypatch) -> None:
    def must_not_call():
        raise AssertionError("provider called per request")

    monkeypatch.setattr(provider, "fetch_quotes", must_not_call)
    r = await client.get("/api/market/prices")
    assert r.status_code == 200
    body = r.json()
    prices = {k["karat"]: k["price_per_gram_iqd"] for k in body["karats"]}
    assert prices == {24: "145000.00", 22: "132916.67", 21: "126875.00", 18: "108750.00"}
    assert body["usd_iqd"] == "1310.0000" and body["updated_at"] and body["is_stale"] is False


async def test_change_24h(client) -> None:
    add_snapshot(Decimal("140000.00"), at=hours_ago(25))
    body = (await client.get("/api/market/prices")).json()
    assert body["change_24h_pct"] == "3.57"  # (145000-140000)/140000


async def test_provider_failure_serves_last_cached_value(client, db, monkeypatch) -> None:
    def down():
        raise provider.ProviderError("timeout")

    monkeypatch.setattr(provider, "fetch_quotes", down)
    before = (await client.get("/api/market/prices")).json()
    assert market.refresh_prices(db) is None
    after = (await client.get("/api/market/prices")).json()
    assert after["updated_at"] == before["updated_at"]
    assert after["karats"] == before["karats"]


async def test_refresh_stores_live_snapshot(db, monkeypatch) -> None:
    monkeypatch.setattr(provider, "fetch_quotes", lambda: (Decimal("3110.34768"), Decimal("1000")))
    snapshot = market.refresh_prices(db)
    assert snapshot.source == "live" and snapshot.gold_24k_iqd_per_gram == Decimal("100000.00")
    assert db.scalar(select(func.count()).select_from(PriceSnapshot)) == 2


async def test_first_boot_offline_uses_fallback(db, monkeypatch) -> None:
    db.execute(text("TRUNCATE price_snapshots"))
    db.commit()

    def down():
        raise provider.ProviderError("offline")

    monkeypatch.setattr(provider, "fetch_quotes", down)
    snapshot = market.refresh_prices(db)
    assert snapshot is not None and snapshot.source == "fallback"


async def test_stale_flag_and_health(client, db) -> None:
    db.execute(text("TRUNCATE price_snapshots"))
    db.commit()
    add_snapshot(Decimal("145000.00"), at=datetime.now(UTC) - timedelta(hours=2))
    assert (await client.get("/api/market/prices")).json()["is_stale"] is True
    health = (await client.get("/api/health")).json()
    assert health["database"] == "ok" and health["status"] == "degraded"
    assert health["price_cache_age_seconds"] >= 7200


async def test_health_ok(client) -> None:
    body = (await client.get("/api/health")).json()
    assert body["status"] == "ok" and body["price_source"] == "live"


async def test_history_downsampled(client) -> None:
    for minutes in range(0, 180, 2):
        add_snapshot(
            Decimal("140000.00") + minutes, at=datetime.now(UTC) - timedelta(minutes=minutes)
        )
    add_snapshot(Decimal("100000.00"), at=hours_ago(30))  # outside 1D

    r = await client.get("/api/market/prices/history", params={"karat": 21, "range": "1D"})
    assert r.status_code == 200
    points = r.json()["points"]
    assert 17 <= len(points) <= 20  # ~3h at 10-minute buckets
    assert [p["ts"] for p in points] == sorted(p["ts"] for p in points)
    assert all(Decimal(p["price_per_gram"]) > Decimal("120000") for p in points)

    r = await client.get("/api/market/prices/history", params={"range": "1W"})
    assert r.status_code == 200 and len(r.json()["points"]) >= 2
    bad = await client.get("/api/market/prices/history", params={"range": "5Y"})
    assert bad.status_code == 422


async def test_public_config(client) -> None:
    body = (await client.get("/api/config")).json()
    assert body["promotion_fee_iqd"] and body["subscription_duration_days"] == 30
    assert [t["rate"] for t in body["commission_tiers"]] == ["0.0150", "0.0100", "0.0050"]
