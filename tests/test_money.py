from decimal import Decimal

import pytest

from app.core.money import (
    compute_breakdown,
    gold_24k_iqd_per_gram,
    karat_price,
    max_affordable_grams,
    round_money,
)
from app.core.signature import sign_ownership, verify_ownership

D = Decimal


@pytest.mark.parametrize(
    ("karat", "expected"),
    [(24, "145000.00"), (22, "132916.67"), (21, "126875.00"), (18, "108750.00")],
)
def test_karat_conversion(karat: int, expected: str) -> None:
    assert karat_price(D("145000.00"), karat) == D(expected)


@pytest.mark.parametrize(
    ("grams", "rate"),
    [
        ("0.001", "0.0150"),
        ("49.999", "0.0150"),
        ("50", "0.0100"),
        ("50.000", "0.0100"),
        ("200", "0.0100"),
        ("200.000", "0.0100"),
        ("200.001", "0.0050"),
        ("5000", "0.0050"),
    ],
)
def test_commission_tier_boundaries(grams: str, rate: str) -> None:
    assert compute_breakdown(D(grams), D("100.00")).commission_rate == D(rate)


def test_breakdown_formula_and_rounding() -> None:
    b = compute_breakdown(D("84.250"), D("126875.00"))
    assert b.principal_amount == D("10689218.75")
    assert b.commission_rate == D("0.0100")
    assert b.commission_amount == D("106892.19")  # 106892.1875 → HALF_UP
    assert b.total_paid_by_investor == D("10796110.94")
    assert b.total_paid_by_investor == b.principal_amount + b.commission_amount


def test_rounding_is_half_up() -> None:
    assert round_money(D("0.005")) == D("0.01")
    assert round_money(D("0.015")) == D("0.02")
    assert round_money(D("2.675")) == D("2.68")


def test_global_price_conversion() -> None:
    # 1 troy ounce = 31.1034768 g
    assert gold_24k_iqd_per_gram(D("3110.34768"), D("1000")) == D("100000.00")


def test_max_affordable_grams_fits_budget_across_tiers() -> None:
    price = D("100000.00")
    for budget in ("1000000", "5075000", "5100000", "20200000", "20300000", "99999999"):
        grams = max_affordable_grams(D(budget), price)
        assert compute_breakdown(grams, price).total_paid_by_investor <= D(budget)
        more = grams + D("0.001")
        assert compute_breakdown(more, price).total_paid_by_investor > D(budget)


def test_ownership_signature_roundtrip_and_tamper() -> None:
    import uuid
    from datetime import UTC, datetime

    investor, ts = uuid.uuid4(), datetime.now(UTC)
    sig = sign_ownership(investor, D("12.5"), ts)
    assert verify_ownership(investor, D("12.500"), ts, sig)
    assert not verify_ownership(investor, D("12.501"), ts, sig)
    assert not verify_ownership(uuid.uuid4(), D("12.500"), ts, sig)
