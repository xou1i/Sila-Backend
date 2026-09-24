from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.money import compute_breakdown
from app.modules.ai import llm
from tests.conftest import make_listing, make_user

pytestmark = pytest.mark.anyio


async def test_match_ranks_active_listings_within_budget(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    l24 = await make_listing(client, seller, "100", 24)
    await make_listing(client, seller, "100", 18)
    suspended = await make_listing(client, seller, "100", 22)
    await client.patch(
        f"/api/listings/{suspended['id']}", json={"status": "suspended"}, headers=seller.headers
    )
    investor = await make_user(client, risk_profile="low")

    r = await client.post("/api/ai/match", json={"budget_iqd": "1500000"}, headers=investor.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["risk_profile"] == "low" and body["engine"] == "rules"
    ids = [x["listing"]["id"] for x in body["results"]]
    assert suspended["id"] not in ids and len(ids) == 2
    assert ids[0] == l24["id"]  # low risk prefers 24K
    for res in body["results"]:
        assert res["reason"]
        assert Decimal(res["estimated_total_iqd"]) <= Decimal("1500000")
        b = compute_breakdown(
            Decimal(res["suggested_weight_grams"]), Decimal(res["execution_price_per_gram"])
        )
        assert str(b.total_paid_by_investor) == res["estimated_total_iqd"]


async def test_risk_profile_changes_ranking(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    await make_listing(client, seller, "100", 24)
    l18 = await make_listing(client, seller, "100", 18)
    investor = await make_user(client, risk_profile="high")
    r = await client.post("/api/ai/match", json={"budget_iqd": "1500000"}, headers=investor.headers)
    assert r.json()["results"][0]["listing"]["id"] == l18["id"]


async def test_match_budget_too_small(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    await make_listing(client, seller, "100", 18)
    investor = await make_user(client)
    r = await client.post("/api/ai/match", json={"budget_iqd": "5000"}, headers=investor.headers)
    assert r.status_code == 200
    assert r.json()["results"] == [] and r.json()["message"]


async def test_match_validation_and_role(client) -> None:
    investor = await make_user(client)
    seller = await make_user(client, "seller")
    r = await client.post("/api/ai/match", json={"budget_iqd": "-1"}, headers=investor.headers)
    assert r.status_code == 422
    r = await client.post("/api/ai/match", json={"budget_iqd": "100"}, headers=seller.headers)
    assert r.status_code == 403


async def test_no_price_data_is_ai_unavailable(client, db) -> None:
    investor = await make_user(client)
    db.execute(text("TRUNCATE price_snapshots"))
    db.commit()
    r = await client.post("/api/ai/match", json={"budget_iqd": "100000"}, headers=investor.headers)
    assert r.status_code == 503 and r.json()["error_code"] == "AI_UNAVAILABLE"


async def test_risk_analysis(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    listing = await make_listing(client, seller, "500", 18)
    investor = await make_user(client, risk_profile="low")
    r = await client.post(
        "/api/ai/risk-analysis",
        json={"asset_id": listing["id"], "weight_grams": "250"},
        headers=investor.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "high"  # large trade + 18K for a low-risk profile
    assert body["insight"] and len(body["signals"]) >= 2
    assert body["purchased_weight_grams"] == "250.000"


async def test_llm_failure_falls_back_to_rules(client, monkeypatch) -> None:
    seller = await make_user(client, "seller", kyc=True)
    await make_listing(client, seller, "100", 24)
    investor = await make_user(client)
    monkeypatch.setattr(get_settings(), "ai_api_key", "sk-invalid-test-key")

    def boom(prompt: str) -> None:  # stands in for a timeout / provider error / refusal
        return None

    monkeypatch.setattr(llm, "_ask", boom)
    r = await client.post("/api/ai/match", json={"budget_iqd": "1500000"}, headers=investor.headers)
    assert r.status_code == 200 and r.json()["engine"] == "rules" and r.json()["results"]


async def test_llm_rewrite_used_when_available(client, monkeypatch) -> None:
    seller = await make_user(client, "seller", kyc=True)
    await make_listing(client, seller, "100", 24)
    investor = await make_user(client)
    monkeypatch.setattr(get_settings(), "ai_api_key", "sk-test")
    monkeypatch.setattr(llm, "_ask", lambda prompt: '["نص معاد صياغته"]')
    r = await client.post("/api/ai/match", json={"budget_iqd": "1500000"}, headers=investor.headers)
    body = r.json()
    assert body["engine"] == "llm" and body["results"][0]["reason"] == "نص معاد صياغته"
