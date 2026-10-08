"""AI Advisor: an Arabic answer to a free question, plus offers the investor can buy.

It extends the AI engine instead of running beside it: suggestions and every number come from
service.match() and the market service, exactly as in /api/ai/match. The model (providers.py)
only words an answer from that context, and the reply is checked before use: an unknown number,
an offer that does not exist, a profit promise or a non-Arabic reply all fall back to the
rule-based answer (engine "rules"). Only anonymous data leaves the server.
"""

import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import SUPPORTED_KARATS, karat_price
from app.models import FractionalOwnershipRecord, RiskProfile, User
from app.modules.ai import budget as budget_parser
from app.modules.ai import providers, service
from app.modules.ai.schemas import (
    AdvisorBudget,
    AdvisorIn,
    AdvisorMarket,
    AdvisorOut,
    MatchResult,
)
from app.modules.market import service as market

DISCLAIMER = "هذي المعلومات استرشادية وليست نصيحة مالية. قرار الشراء يرجعلك."
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "advisor_system.md").read_text(
    encoding="utf-8"
)
MAX_ANSWER_CHARS = 1200

_RISK_TIP = {
    RiskProfile.low: ("ملفك منخفض المخاطرة، فالأفضل تبدي بكمية صغيرة وتقسّم شراءك على أكثر من مرة."),
    RiskProfile.medium: (
        "ملفك متوسط المخاطرة، فوازن بين الكمية والعيار وما تحط كل ميزانيتك مرة وحدة."
    ),
    RiskProfile.high: "ملفك يقبل مخاطرة أعلى، بس حتى هيج لا تحط كل ميزانيتك بصفقة وحدة.",
}
_REMINDER = "تذكّر إن سعر الذهب ممكن ينزل مثل ما يصعد."

# Promises the answer must never make; negated forms ("مو مضمون") stay allowed
_PROMISE = re.compile(
    r"(?<!مو )(?<!غير )(?<!ليس )(?<!مش )(?<!ما )مضمون"
    r"|اكيد\s+(?:راح\s+)?(?:يرتفع|يصعد|يزيد|تربح|ربح)"
    r"|(?:يرتفع|يصعد)\s+اكيد"
    r"|حتما"
    r"|بدون\s+(?:اي\s+)?(?:مخاطر|خساره|خسارة)"
    r"|guarantee"
)
_OFFER_REF = re.compile(r"العرض\s*(?:رقم\s*)?(\d+)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_ARABIC_LETTER = re.compile(r"[ء-ي]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")


def advise(db: Session, investor: User, body: AdvisorIn) -> AdvisorOut:
    snapshot = service._snapshot_or_unavailable(db)
    change_24h = market.change_since(db, snapshot, timedelta(hours=24))
    risk = investor.risk_profile or RiskProfile.medium
    budget = _understand_budget(body)

    suggestions: list[MatchResult] = []
    no_match_message: str | None = None
    if budget is not None and budget.confirmed:
        matched = service.match(db, investor, budget.amount_iqd)
        suggestions = matched.results
        if not suggestions:
            no_match_message = matched.message

    holdings = db.scalar(
        select(FractionalOwnershipRecord.total_accumulated_grams).where(
            FractionalOwnershipRecord.investor_id == investor.id
        )
    ) or Decimal("0")
    context = build_context(
        snapshot.gold_24k_iqd_per_gram, change_24h, risk, holdings, budget, suggestions
    )
    rules_answer = _rules_answer(
        snapshot.gold_24k_iqd_per_gram, change_24h, risk, budget, suggestions, no_match_message
    )

    answer, engine = rules_answer, "rules"
    question = budget_parser.redact_personal(body.question)
    reply = providers.complete(SYSTEM_PROMPT, _user_message(context, question))
    if reply is not None:
        checked = check_answer(reply, context, len(suggestions))
        if checked is not None:
            answer, engine = checked, "llm"

    return AdvisorOut(
        engine=engine,
        answer=answer,
        budget=budget,
        suggestions=suggestions,
        market_snapshot=AdvisorMarket(
            price_24k_per_gram=snapshot.gold_24k_iqd_per_gram,
            change_24h_pct=change_24h,
            updated_at=snapshot.fetched_at,
            is_stale=market.is_stale(snapshot),
        ),
        disclaimer=DISCLAIMER,
    )


def _understand_budget(body: AdvisorIn) -> AdvisorBudget | None:
    if body.budget_iqd is not None:
        return AdvisorBudget(amount_iqd=body.budget_iqd, source="request", confirmed=True)
    guess = budget_parser.parse_budget(body.question)
    if guess is None:
        return None
    # Digits are explicit; words need the user's confirmation (never guess silently)
    return AdvisorBudget(
        amount_iqd=guess.amount, source=guess.source, confirmed=guess.source == "question_digits"
    )


# ---------------------------------------------------------------- what the model sees


def build_context(
    price_24k: Decimal,
    change_24h: Decimal | None,
    risk: RiskProfile,
    holdings: Decimal,
    budget: AdvisorBudget | None,
    suggestions: list[MatchResult],
) -> dict[str, Any]:
    """Anonymous facts only: no name, e-mail, user id, listing id or seller name."""
    if budget is None:
        status = "missing"
    elif budget.confirmed:
        status = "confirmed"
    else:
        status = "needs_confirmation"
    return {
        "market": {
            "price_per_gram_iqd": {
                str(k): str(karat_price(price_24k, k)) for k in SUPPORTED_KARATS
            },
            "change_24h_pct": str(change_24h) if change_24h is not None else None,
        },
        "investor": {
            "risk_profile": risk.value,
            "holdings_grams": str(holdings),
        },
        "budget_iqd": str(budget.amount_iqd) if budget else None,
        "budget_status": status,
        "offers": [
            {
                "offer": s.rank,
                "karat": s.listing.karat,
                "available_grams": str(s.listing.available_weight_grams),
                "price_per_gram_iqd": str(s.execution_price_per_gram),
                "suggested_grams": str(s.suggested_weight_grams),
                "estimated_total_iqd": str(s.estimated_total_iqd),
                "commission_pct": str((s.commission_rate * 100).normalize()),
                "budget_usage_pct": str(s.budget_usage_pct),
            }
            for s in suggestions
        ],
        "coming_soon": ["real estate", "oil"],
    }


def _user_message(context: dict[str, Any], question: str) -> str:
    facts = json.dumps(context, ensure_ascii=False, indent=1)
    return f"<context>\n{facts}\n</context>\n\n<question>\n{question}\n</question>"


# ---------------------------------------------------------------- output check


def _allowed_numbers(context: dict[str, Any]) -> set[Decimal]:
    values: list[Decimal] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, (int, str)) and not isinstance(node, bool):
            number = budget_parser.to_decimal(str(node))
            if number is not None:
                values.append(abs(number))

    walk(context)
    allowed: set[Decimal] = {Decimal(24)}  # "خلال 24 ساعة"
    for value in values:
        allowed.add(value)
        allowed.add(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        allowed.add(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
        allowed.add(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    allowed.add(Decimal(len(context["offers"])))
    return allowed


def check_answer(reply: str, context: dict[str, Any], offer_count: int) -> str | None:
    """The cleaned answer, or None when it must not be shown."""
    text = re.sub(r"[*#`_]+", "", reply)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > MAX_ANSWER_CHARS:
        return None
    if len(_LATIN_LETTER.findall(text)) > len(_ARABIC_LETTER.findall(text)):
        return None
    normalized = budget_parser.normalize(text).replace("٬", ",").replace("٫", ".")
    if _PROMISE.search(normalized.lower()):
        return None
    for match in _OFFER_REF.finditer(normalized):
        if not 1 <= int(match.group(1)) <= offer_count:
            return None
    allowed = _allowed_numbers(context)
    for token in _NUMBER.findall(normalized):
        number = budget_parser.to_decimal(token)
        if number is None or number not in allowed:
            return None
    return text


# ---------------------------------------------------------------- rule-based answer


def _fmt_iqd(value: Decimal) -> str:
    return f"{value.quantize(Decimal('1'), rounding=ROUND_HALF_UP):,}"


def _fmt_grams(value: Decimal) -> str:
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _rules_answer(
    price_24k: Decimal,
    change_24h: Decimal | None,
    risk: RiskProfile,
    budget: AdvisorBudget | None,
    suggestions: list[MatchResult],
    no_match_message: str | None,
) -> str:
    market_line = f"سعر غرام الذهب عيار 24 هسة {_fmt_iqd(price_24k)} دينار"
    if change_24h is not None and change_24h > 0:
        market_line += f"، وارتفع {change_24h}% خلال 24 ساعة."
    elif change_24h is not None and change_24h < 0:
        market_line += f"، ونزل {abs(change_24h)}% خلال 24 ساعة."
    else:
        market_line += "."
    parts = [market_line]

    if budget is None:
        parts.append("حتى أقترح عليك عروض تناسبك، اختار ميزانيتك أو اكتبها.")
    elif not budget.confirmed:
        parts.append(
            f"فهمت إن ميزانيتك {_fmt_iqd(budget.amount_iqd)} دينار، "
            "أكّدها حتى أطلعلك العروض المناسبة."
        )
    elif suggestions:
        best = suggestions[0]
        parts.append(
            f"أنسب خيار لميزانيتك هو العرض 1: {_fmt_grams(best.suggested_weight_grams)} غرام "
            f"عيار {best.listing.karat} بإجمالي {_fmt_iqd(best.estimated_total_iqd)} دينار "
            "شامل العمولة."
        )
        parts.append(_RISK_TIP[risk])
    else:
        parts.append(f"{no_match_message}، جرّب ميزانية أكبر أو تصفح السوق بنفسك.")
    parts.append(_REMINDER)
    return " ".join(parts)
