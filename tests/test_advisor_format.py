"""Advisor reply format: JSON from the model, the figures flag, follow-up questions, clean text."""

import json

import pytest

from app.core.config import get_settings
from app.modules.ai import advisor, providers
from tests.conftest import make_listing, make_user

ADVISOR = "/api/ai/advisor"


def _json_reply(answer: str, show_figures=True, follow_ups=None) -> dict:
    content = json.dumps(
        {"answer": answer, "show_figures": show_figures, "follow_up_questions": follow_ups or []},
        ensure_ascii=False,
    )
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


def _use_model(monkeypatch, reply) -> None:
    monkeypatch.setattr(get_settings(), "advisor_provider", "groq")
    monkeypatch.setattr(get_settings(), "advisor_api_key", "gsk-test")
    monkeypatch.setattr(providers, "_post_json", lambda url, payload, key, timeout: reply)


async def _investor_and_market(client):
    seller = await make_user(client, "seller", kyc=True)
    await make_listing(client, seller, "100", 24)
    await make_listing(client, seller, "100", 21)
    return await make_user(client)


async def _ask(client, investor, question, budget=None):
    body = {"question": question} | ({"budget_iqd": budget} if budget else {})
    r = await client.post(ADVISOR, json=body, headers=investor.headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- rule-based answers


@pytest.mark.anyio
async def test_money_question_shows_figures_and_holdings(client) -> None:
    investor = await _investor_and_market(client)
    body = await _ask(client, investor, "هل هسة وقت مناسب للشراء؟")
    assert body["engine"] == "rules" and body["show_figures"] is True
    assert body["holdings_grams"] == "0.000"
    assert body["follow_up_questions"] == advisor._RULES_FOLLOW_UPS["missing"]
    # The live price sits in the figures panel, not in the sentence
    assert "دينار" not in body["answer"]


@pytest.mark.anyio
async def test_off_topic_question_hides_figures(client) -> None:
    investor = await _investor_and_market(client)
    body = await _ask(client, investor, "شلون أعالج الصداع؟")
    assert body["show_figures"] is False
    assert body["answer"] == advisor._OFF_TOPIC
    assert body["follow_up_questions"] == advisor._RULES_FOLLOW_UPS["off_topic"]


@pytest.mark.anyio
async def test_offers_state_follow_ups(client) -> None:
    investor = await _investor_and_market(client)
    body = await _ask(client, investor, "شنو أشتري؟", "1500000")
    assert body["suggestions"] and body["show_figures"] is True
    assert body["follow_up_questions"] == advisor._RULES_FOLLOW_UPS["offers"]


# ---------------------------------------------------------------- model JSON


@pytest.mark.anyio
async def test_model_json_answer_flag_and_follow_ups(client, monkeypatch) -> None:
    investor = await _investor_and_market(client)
    follow_ups = ["شنو الفرق بين عيار 21 وعيار 24؟", "شلون تنحسب العمولة؟", "أبدي بكمية صغيرة؟"]
    _use_model(monkeypatch, _json_reply("عذراً، هذا السؤال برا الذهب.", False, follow_ups))
    body = await _ask(client, investor, "شلون أعالج الصداع؟")
    assert body["engine"] == "llm" and body["show_figures"] is False
    assert body["follow_up_questions"] == follow_ups


@pytest.mark.anyio
async def test_a_budget_always_shows_figures(client, monkeypatch) -> None:
    investor = await _investor_and_market(client)
    _use_model(monkeypatch, _json_reply("العرض 1 يناسبك، والسعر ممكن ينزل.", False))
    body = await _ask(client, investor, "شنو أشتري؟", "1500000")
    assert body["engine"] == "llm" and body["show_figures"] is True


@pytest.mark.anyio
async def test_bad_follow_ups_are_dropped_one_by_one(client, monkeypatch) -> None:
    investor = await _investor_and_market(client)
    follow_ups = [
        "شلون تنحسب العمولة؟",
        "هل أشتري 7777 غرام؟",  # invented number
        "الربح مضمون؟",  # profit promise
        "شلون تنحسب العمولة؟",  # duplicate
        "Which karat is best for me today?",  # not Arabic
    ]
    _use_model(monkeypatch, _json_reply("سعر الذهب ممكن ينزل، فوزّع شراءك.", True, follow_ups))
    body = await _ask(client, investor, "هل هسة وقت مناسب للشراء؟")
    assert body["engine"] == "llm"
    assert body["follow_up_questions"] == ["شلون تنحسب العمولة؟"]


@pytest.mark.anyio
async def test_no_valid_follow_up_keeps_the_rule_based_ones(client, monkeypatch) -> None:
    investor = await _investor_and_market(client)
    _use_model(monkeypatch, _json_reply("سعر الذهب ممكن ينزل.", True, ["هل أشتري 7777 غرام؟"]))
    body = await _ask(client, investor, "هل هسة وقت مناسب للشراء؟")
    assert body["follow_up_questions"] == advisor._RULES_FOLLOW_UPS["missing"]


@pytest.mark.anyio
async def test_broken_json_falls_back_to_rules(client, monkeypatch) -> None:
    investor = await _investor_and_market(client)
    reply = {"choices": [{"message": {"content": '{"answer": "نص'}, "finish_reason": "stop"}]}
    _use_model(monkeypatch, reply)
    body = await _ask(client, investor, "هل هسة وقت مناسب للشراء؟")
    assert body["engine"] == "rules"


# ---------------------------------------------------------------- text cleanup


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("أنا مستشار منصة سِلة.", "أنا مستشار منصة صِلة."),
        ("أهلاً بيك بمنصة سلة للذهب.", "أهلاً بيك بمنصة صِلة للذهب."),
        ("خلال الـ 24‑ساعة الماضية", "خلال الـ 24 ساعة الماضية"),
        ("السعر — مستقر", "السعر مستقر"),
        ("تغيّر بنسبة -0.57% اليوم", "تغيّر بنسبة −0.57% اليوم"),
        ("**مهم**: الذهب", "مهم: الذهب"),
    ],
)
def test_clean_text(raw: str, clean: str) -> None:
    assert advisor.clean_text(raw) == clean
