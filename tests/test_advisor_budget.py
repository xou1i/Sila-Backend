from decimal import Decimal

import pytest

from app.modules.ai.budget import parse_budget, redact_personal


@pytest.mark.parametrize(
    ("question", "amount", "source"),
    [
        ("عندي 2000000 دينار", "2000000", "question_digits"),
        ("عندي 2,000,000 شنو أشتري؟", "2000000", "question_digits"),
        ("عندي ٢٠٠٠٠٠٠ دينار", "2000000", "question_digits"),
        ("ميزانيتي 750000.", "750000", "question_digits"),
        ("عندي مليونين", "2000000", "question_words"),
        ("نص مليون", "500000", "question_words"),
        ("نصف مليون", "500000", "question_words"),
        ("ربع مليون", "250000", "question_words"),
        ("مليون ونص", "1500000", "question_words"),
        ("عندي مليون", "1000000", "question_words"),
        ("3 ملايين", "3000000", "question_words"),
        ("1.5 مليون", "1500000", "question_words"),
        ("500 ألف", "500000", "question_words"),
        ("ثلاث ملايين", "3000000", "question_words"),
        ("عشرة آلاف", "10000", "question_words"),
        # Two different amounts: never pick one silently
        ("عندي 2000000 او مليون", "2000000", "question_words"),
    ],
)
def test_parse_budget(question: str, amount: str, source: str) -> None:
    guess = parse_budget(question)
    assert guess is not None
    assert guess.amount == Decimal(amount) and guess.source == source


@pytest.mark.parametrize(
    "question",
    [
        "هل هسة وقت مناسب للشراء؟",
        "شنو أحسن عيار 21؟",
        "ابي 50 غرام",
        "رقمي 07701234567",
        "اتصل بي على +964 770 123 4567",
        "خمس آلاف",  # below the minimum budget
    ],
)
def test_parse_budget_finds_nothing(question: str) -> None:
    assert parse_budget(question) is None


def test_redact_personal() -> None:
    text = redact_personal("ايميلي zainab@example.com ورقمي 07701234567 عندي مليون")
    assert "zainab@example.com" not in text and "07701234567" not in text
    assert "عندي مليون" in text
