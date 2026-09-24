"""Money and weight math. Decimal only (DECISIONS D-07, D-08).

Rounding rule: ROUND_HALF_UP to 2 decimals, in order: execution price, principal,
commission; total = principal + commission (exact).
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MILLIGRAM = Decimal("0.001")
TROY_OUNCE_GRAMS = Decimal("31.1034768")
SUPPORTED_KARATS = (24, 22, 21, 18)

TIER_SMALL_MAX = Decimal("50")  # grams < 50 → small
TIER_MEDIUM_MAX = Decimal("200")  # 50 ≤ grams ≤ 200 → medium; > 200 → large
RATE_SMALL = Decimal("0.0150")
RATE_MEDIUM = Decimal("0.0100")
RATE_LARGE = Decimal("0.0050")

COMMISSION_TIERS = (
    {"label": "< 50 g", "min_grams": None, "max_grams": "49.999", "rate": RATE_SMALL},
    {"label": "50 – 200 g", "min_grams": "50.000", "max_grams": "200.000", "rate": RATE_MEDIUM},
    {"label": "> 200 g", "min_grams": "200.001", "max_grams": None, "rate": RATE_LARGE},
)


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def floor_grams(value: Decimal) -> Decimal:
    return value.quantize(MILLIGRAM, rounding=ROUND_DOWN)


def gold_24k_iqd_per_gram(xau_usd_per_ounce: Decimal, usd_iqd: Decimal) -> Decimal:
    return round_money(xau_usd_per_ounce / TROY_OUNCE_GRAMS * usd_iqd)


def karat_price(price_24k_per_gram: Decimal, karat: int) -> Decimal:
    """PricePerGram(Karat) = GlobalPricePerGram24k × (Karat / 24)."""
    return round_money(price_24k_per_gram * Decimal(karat) / Decimal(24))


def commission_rate(grams: Decimal) -> Decimal:
    if grams < TIER_SMALL_MAX:
        return RATE_SMALL
    if grams <= TIER_MEDIUM_MAX:
        return RATE_MEDIUM
    return RATE_LARGE


@dataclass(frozen=True)
class Breakdown:
    purchased_weight_grams: Decimal
    execution_price_per_gram: Decimal
    principal_amount: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    total_paid_by_investor: Decimal


def compute_breakdown(grams: Decimal, execution_price_per_gram: Decimal) -> Breakdown:
    principal = round_money(grams * execution_price_per_gram)
    rate = commission_rate(grams)
    commission = round_money(principal * rate)
    return Breakdown(
        purchased_weight_grams=grams,
        execution_price_per_gram=execution_price_per_gram,
        principal_amount=principal,
        commission_rate=rate,
        commission_amount=commission,
        total_paid_by_investor=principal + commission,
    )


def max_affordable_grams(budget: Decimal, execution_price_per_gram: Decimal) -> Decimal:
    """Largest weight (3 dp) whose total incl. tiered commission fits the budget."""
    if budget <= 0 or execution_price_per_gram <= 0:
        return Decimal("0")

    def solve(rate: Decimal) -> Decimal:
        return floor_grams(budget / (execution_price_per_gram * (1 + rate)))

    candidates = [min(solve(RATE_SMALL), TIER_SMALL_MAX - MILLIGRAM)]
    medium = solve(RATE_MEDIUM)
    if medium >= TIER_SMALL_MAX:
        candidates.append(min(medium, TIER_MEDIUM_MAX))
    large = solve(RATE_LARGE)
    if large > TIER_MEDIUM_MAX:
        candidates.append(large)
    grams = max(candidates)
    # Guard against a one-cent overshoot from HALF_UP rounding.
    while (
        grams > 0
        and compute_breakdown(grams, execution_price_per_gram).total_paid_by_investor > budget
    ):
        grams -= MILLIGRAM
    return max(grams, Decimal("0"))
