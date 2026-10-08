import io
import json
import logging
import urllib.error
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.modules.ai import advisor, llm, providers
from tests.conftest import make_listing, make_user

pytestmark = pytest.mark.anyio

ADVISOR = "/api/ai/advisor"
KEY = "gsk-secret-test-key"
VALID = "أنسب خيار هو العرض 1 لأنه عيار 24 ويناسب ميزانيتك. سعر الذهب ممكن ينزل، فلا تستعجل."


def _choice(text: str | None, finish: str = "stop") -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}]
    }


def _use_groq(monkeypatch, *, reply=None, error=None, captured: list | None = None) -> None:
    """Point the advisor at a fake OpenAI-compatible provider (never a real call)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "advisor_provider", "groq")
    monkeypatch.setattr(settings, "advisor_api_key", KEY)

    def fake_post(url, payload, api_key, timeout):
        if captured is not None:
            captured.append({"url": url, "payload": payload, "api_key": api_key})
        if error is not None:
            raise error
        return reply

    monkeypatch.setattr(providers, "_post_json", fake_post)


async def _market(client) -> list[dict]:
    seller = await make_user(client, "seller", kyc=True, name="مجوهرات الاختبار")
    return [
        await make_listing(client, seller, "100", 24),
        await make_listing(client, seller, "100", 21),
    ]


async def _ask(client, investor, question: str, budget: str | None = None):
    body = {"question": question} | ({"budget_iqd": budget} if budget else {})
    return await client.post(ADVISOR, json=body, headers=investor.headers)


# ---------------------------------------------------------------- rules engine and budget


async def test_rules_answer_with_request_budget(client) -> None:
    await _market(client)
    investor = await make_user(client)
    r = await _ask(client, investor, "شنو أحسن شي أشتريه هسة؟", "1500000")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "rules"
    assert body["budget"] == {"amount_iqd": "1500000", "source": "request", "confirmed": True}
    assert len(body["suggestions"]) == 2 and body["suggestions"][0]["rank"] == 1
    assert "العرض 1" in body["answer"]
    assert body["disclaimer"] == advisor.DISCLAIMER
    assert body["market_snapshot"]["price_24k_per_gram"]


async def test_budget_from_digits_is_used_directly(client) -> None:
    await _market(client)
    investor = await make_user(client)
    body = (await _ask(client, investor, "عندي 1,500,000 دينار شنو أشتري؟")).json()
    assert body["budget"]["source"] == "question_digits" and body["budget"]["confirmed"] is True
    assert body["suggestions"]


async def test_budget_from_words_waits_for_confirmation(client) -> None:
    await _market(client)
    investor = await make_user(client)
    body = (await _ask(client, investor, "عندي مليونين شنو أحسن شي؟")).json()
    assert body["budget"] == {
        "amount_iqd": "2000000",
        "source": "question_words",
        "confirmed": False,
    }
    assert body["suggestions"] == []
    assert "2,000,000" in body["answer"]


async def test_no_budget_answers_about_the_market_only(client) -> None:
    await _market(client)
    investor = await make_user(client)
    body = (await _ask(client, investor, "هل هسة وقت مناسب للشراء؟")).json()
    assert body["budget"] is None and body["suggestions"] == []
    assert "ميزانيتك" in body["answer"] and body["disclaimer"]


async def test_request_budget_wins_over_the_question(client) -> None:
    await _market(client)
    investor = await make_user(client)
    body = (await _ask(client, investor, "عندي مليونين", "1500000")).json()
    assert body["budget"]["source"] == "request" and body["suggestions"]


# ---------------------------------------------------------------- the model and its check


async def test_model_answer_used_when_it_checks_out(client, monkeypatch) -> None:
    await _market(client)
    investor = await make_user(client)
    _use_groq(monkeypatch, reply=_choice(VALID))
    body = (await _ask(client, investor, "شنو أحسن عرض لميزانيتي؟", "1500000")).json()
    assert body["engine"] == "llm" and body["answer"] == VALID
    # Suggestions always come from the matcher, never from the model text
    assert len(body["suggestions"]) == 2


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://x", 429, "Too Many Requests", None, None),
        urllib.error.HTTPError("https://x", 503, "Unavailable", None, None),
        TimeoutError("timed out"),
        urllib.error.URLError("connection refused"),
    ],
    ids=["429", "5xx", "timeout", "network"],
)
async def test_provider_failure_falls_back_to_rules(client, monkeypatch, error) -> None:
    await _market(client)
    investor = await make_user(client)
    _use_groq(monkeypatch, error=error)
    r = await _ask(client, investor, "شنو أشتري؟", "1500000")
    assert r.status_code == 200 and r.json()["engine"] == "rules" and r.json()["suggestions"]


@pytest.mark.parametrize(
    "reply",
    [
        {"choices": []},
        {"unexpected": True},
        _choice(""),
        _choice(VALID, finish="length"),
        _choice(VALID, finish="content_filter"),
    ],
    ids=["no-choices", "malformed", "empty", "truncated", "filtered"],
)
async def test_unusable_reply_falls_back_to_rules(client, monkeypatch, reply) -> None:
    await _market(client)
    investor = await make_user(client)
    _use_groq(monkeypatch, reply=reply)
    assert (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()["engine"] == "rules"


@pytest.mark.parametrize(
    "text",
    [
        "اشتري هسة بسعر 123456 دينار للغرام.",  # invented price
        "أنصحك بالعرض 9 لأنه الأرخص.",  # offer that does not exist
        "اشتري العرض 1، الربح مضمون.",  # profit promise
        "اشتري العرض 1 لأن الذهب اكيد يرتفع.",
        "Buy offer 1 now, it is the best value for your budget today.",  # not Arabic
    ],
    ids=["invented-number", "unknown-offer", "guaranteed", "surely-rises", "english"],
)
async def test_unsafe_model_answer_is_rejected(client, monkeypatch, text) -> None:
    await _market(client)
    investor = await make_user(client)
    _use_groq(monkeypatch, reply=_choice(text))
    body = (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()
    assert body["engine"] == "rules" and body["answer"] != text


async def test_negated_promise_is_allowed(client, monkeypatch) -> None:
    await _market(client)
    investor = await make_user(client)
    text = "العرض 1 يناسب ميزانيتك، بس الربح مو مضمون والسعر ممكن ينزل."
    _use_groq(monkeypatch, reply=_choice(text))
    assert (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()["engine"] == "llm"


# ---------------------------------------------------------------- privacy


async def test_only_anonymous_data_reaches_the_provider(client, monkeypatch) -> None:
    listings = await _market(client)
    investor = await make_user(client, name="زينب الاختبار")
    captured: list[dict] = []
    _use_groq(monkeypatch, reply=_choice(VALID), captured=captured)
    question = "ايميلي zainab@example.com ورقمي 07701234567، شنو أشتري؟"
    body = (await _ask(client, investor, question, "1500000")).json()

    assert len(captured) == 1
    sent = json.dumps(captured[0]["payload"], ensure_ascii=False)
    for secret in (
        "زينب الاختبار",
        investor.user["email"],
        investor.user["id"],
        "مجوهرات الاختبار",
        "zainab@example.com",
        "07701234567",
        KEY,
        *(listing["id"] for listing in listings),
        *(listing["seller_id"] for listing in listings),
    ):
        assert secret not in sent
    assert captured[0]["api_key"] == KEY  # in the Authorization header only
    assert captured[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured[0]["payload"]["model"] == providers.DEFAULT_MODELS["groq"]
    assert body["budget"]["source"] == "request"  # the phone number was never read as a budget


async def test_api_key_is_never_logged(client, monkeypatch, caplog) -> None:
    await _market(client)
    investor = await make_user(client)
    _use_groq(monkeypatch, error=urllib.error.HTTPError("https://x", 401, "Bad key", None, None))
    with caplog.at_level(logging.DEBUG):
        await _ask(client, investor, "شنو أشتري؟", "1500000")
    assert "401" in caplog.text and KEY not in caplog.text


async def test_provider_error_text_is_logged(client, monkeypatch, caplog) -> None:
    await _market(client)
    investor = await make_user(client)
    body = io.BytesIO(b'{"error": {"message": "Invalid API Key"}}')
    _use_groq(
        monkeypatch, error=urllib.error.HTTPError("https://x", 401, "Unauthorized", None, body)
    )
    with caplog.at_level(logging.WARNING):
        await _ask(client, investor, "شنو أشتري؟", "1500000")
    assert "Invalid API Key" in caplog.text and KEY not in caplog.text


def test_request_sends_a_user_agent(monkeypatch) -> None:
    # Without it, Cloudflare in front of Groq answers 403 (error 1010)
    sent: list = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        sent.append(request)
        return Reply()

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    providers._post_json("https://example.test/v1/chat/completions", {}, KEY, 5)
    assert sent[0].get_header("User-agent") == providers.USER_AGENT
    assert sent[0].get_header("Authorization") == f"Bearer {KEY}"


async def test_provider_none_makes_no_call(client, monkeypatch) -> None:
    await _market(client)
    investor = await make_user(client)
    monkeypatch.setattr(get_settings(), "advisor_api_key", KEY)  # provider stays "none"

    def must_not_run(*args, **kwargs):
        raise AssertionError("no provider call when ADVISOR_PROVIDER=none")

    monkeypatch.setattr(providers, "_post_json", must_not_run)
    assert (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()["engine"] == "rules"


# ---------------------------------------------------------------- anthropic provider


def _fake_anthropic(monkeypatch, stop_reason: str, text: str) -> None:
    response = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
    )
    messages = SimpleNamespace(create=lambda **kwargs: response)
    client = SimpleNamespace(with_options=lambda **kwargs: SimpleNamespace(messages=messages))
    monkeypatch.setattr(llm, "_client", lambda api_key: client)
    monkeypatch.setattr(get_settings(), "advisor_provider", "anthropic")
    monkeypatch.setattr(get_settings(), "advisor_api_key", "sk-ant-test")


async def test_anthropic_provider(client, monkeypatch) -> None:
    await _market(client)
    investor = await make_user(client)
    _fake_anthropic(monkeypatch, "end_turn", VALID)
    assert (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()["engine"] == "llm"


async def test_anthropic_refusal_falls_back(client, monkeypatch) -> None:
    await _market(client)
    investor = await make_user(client)
    _fake_anthropic(monkeypatch, "refusal", VALID)
    assert (await _ask(client, investor, "شنو أشتري؟", "1500000")).json()["engine"] == "rules"


# ---------------------------------------------------------------- access and limits


async def test_seller_is_forbidden(client) -> None:
    seller = await make_user(client, "seller", kyc=True)
    r = await client.post(ADVISOR, json={"question": "شنو أشتري؟"}, headers=seller.headers)
    assert r.status_code == 403 and r.json()["error_code"] == "FORBIDDEN"


async def test_advisor_is_rate_limited(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_ai", "1/minute")
    investor = await make_user(client)
    assert (await _ask(client, investor, "شنو أشتري؟")).status_code == 200
    r = await _ask(client, investor, "شنو أشتري؟")
    assert r.status_code == 429 and r.json()["error_code"] == "RATE_LIMITED"
    assert r.headers["Retry-After"]


@pytest.mark.parametrize("question", ["", "   ", "س" * 501])
async def test_question_is_validated(client, question) -> None:
    investor = await make_user(client)
    r = await client.post(ADVISOR, json={"question": question}, headers=investor.headers)
    assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"
