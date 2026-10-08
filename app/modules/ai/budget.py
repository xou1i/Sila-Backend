"""Reads a budget from the advisor question (product decision 2026-10-08).

- Digits ("2000000", "2,000,000", Arabic-Indic digits) are an explicit amount: used directly.
- Common words ("مليونين", "نص مليون", "3 ملايين", "500 ألف") are converted, but the advisor
  shows no suggestion until the user confirms the amount.
- Anything else is never guessed.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

# Below this a number is a karat, a weight or a percentage, not a budget.
MIN_BUDGET = Decimal("10000")
MAX_BUDGET = Decimal("99999999999999")  # fits NUMERIC(16,2)

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ALEF = str.maketrans("أإآ", "ااا")

_MILLION = Decimal("1000000")
_THOUSAND = Decimal("1000")
_UNIT = r"(مليون|ملايين|الف|الاف)"  # alef already normalized (آلاف -> الاف)

# Number words before "ملايين" / "الاف" (3 to 10), alef already normalized
_COUNT_WORDS = {
    "ثلاث": 3,
    "ثلاثه": 3,
    "ثلاثة": 3,
    "اربع": 4,
    "اربعه": 4,
    "اربعة": 4,
    "خمس": 5,
    "خمسه": 5,
    "خمسة": 5,
    "ست": 6,
    "سته": 6,
    "ستة": 6,
    "سبع": 7,
    "سبعه": 7,
    "سبعة": 7,
    "ثمان": 8,
    "ثمانيه": 8,
    "ثمانية": 8,
    "تسع": 9,
    "تسعه": 9,
    "تسعة": 9,
    "عشر": 10,
    "عشره": 10,
    "عشرة": 10,
}

# Fixed phrases, longest first so "مليون ونص" wins over "مليون"
_PHRASES: list[tuple[str, Decimal]] = [
    (r"مليون\s*و\s*(?:نص|نصف)", Decimal("1500000")),
    (r"ربع\s+مليون", Decimal("250000")),
    (r"(?:نص|نصف)\s+مليون", Decimal("500000")),
    (r"مليونين", Decimal("2000000")),
    (r"مليون", _MILLION),
]

# Phone numbers look like big amounts: drop them first (Iraqi 07xxxxxxxxx, +964 / 00964)
_PHONE = re.compile(r"(?:\+|00)\d[\d\s-]{7,}\d|\b0\d{9,}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class BudgetGuess:
    amount: Decimal
    source: Literal["question_digits", "question_words"]


def normalize(text: str) -> str:
    """Western digits, one alef, no tatweel."""
    return text.translate(_DIGITS).translate(_ALEF).replace("ـ", "")


def redact_personal(text: str) -> str:
    """Remove e-mail addresses and phone numbers before the question leaves the server."""
    return _PHONE.sub("[محذوف]", _EMAIL.sub("[محذوف]", text))


def to_decimal(token: str) -> Decimal | None:
    # "2,000,000" / "2٬000٬000" are thousands separators; "1.5" / "1٫5" a decimal point
    cleaned = token.replace("٬", ",").replace("٫", ".").rstrip(",")
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", cleaned):
        cleaned = cleaned.replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _in_range(amount: Decimal) -> bool:
    return MIN_BUDGET <= amount <= MAX_BUDGET


def parse_budget(question: str) -> BudgetGuess | None:
    text = normalize(redact_personal(question))
    # (position in the text, guess). Matched spans are blanked with spaces of the same length,
    # so later steps neither re-read them nor shift the positions of what is left.
    found: list[tuple[int, BudgetGuess]] = []

    def blank(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    # 1) A number followed by a unit word: "2 مليون", "1.5 مليون", "500 الف"
    def unit_amount(match: re.Match[str]) -> str:
        value = to_decimal(match.group(1))
        if value is not None:
            factor = _THOUSAND if match.group(2) in ("الف", "الاف") else _MILLION
            amount = value * factor
            if _in_range(amount):
                guess = BudgetGuess(amount.quantize(Decimal("1")), "question_words")
                found.append((match.start(), guess))
        return blank(match)

    text = re.sub(r"(\d+(?:[.,٫٬]\d+)*)\s*" + _UNIT, unit_amount, text)

    # 2) A number word (3 to 10) followed by a unit: "ثلاث ملايين", "خمس الاف"
    def word_amount(match: re.Match[str]) -> str:
        count = _COUNT_WORDS[match.group(1)]
        factor = _THOUSAND if match.group(2) in ("الف", "الاف") else _MILLION
        amount = Decimal(count) * factor
        if _in_range(amount):
            found.append((match.start(), BudgetGuess(amount, "question_words")))
        return blank(match)

    words = "|".join(sorted(_COUNT_WORDS, key=len, reverse=True))
    text = re.sub(rf"\b({words})\s+{_UNIT}", word_amount, text)

    # 3) Fixed phrases, longest first: "مليون ونص", "نص مليون", "مليونين", "مليون"...
    for pattern, amount in _PHRASES:
        for match in re.finditer(pattern, text):
            found.append((match.start(), BudgetGuess(amount, "question_words")))
        text = re.sub(pattern, blank, text)

    # 4) Bare digits: an explicit amount
    for match in re.finditer(r"\d[\d,٬]*(?:[.٫]\d+)?", text):
        value = to_decimal(match.group(0))
        if value is not None and _in_range(value):
            guess = BudgetGuess(value.quantize(Decimal("0.01")), "question_digits")
            found.append((match.start(), guess))

    if not found:
        return None
    found.sort(key=lambda item: item[0])
    first = found[0][1]
    if len({guess.amount for _, guess in found}) > 1:
        # Two different amounts in one question: never pick one silently, confirm the first
        return BudgetGuess(first.amount, "question_words")
    return first
