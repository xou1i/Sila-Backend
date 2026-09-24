"""External price providers (called only by the background job, never per request)."""

import json
import urllib.request
from decimal import Decimal
from typing import Any

from app.core.config import get_settings


class ProviderError(Exception):
    pass


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    if not url.startswith("https://"):
        raise ProviderError(f"refusing non-https provider url: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "sila-backend/1.0"})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            # parse_float=Decimal: provider numbers never pass through binary floats.
            return json.loads(response.read().decode("utf-8"), parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise ProviderError(str(exc)) from exc


def _positive_decimal(value: Any, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ProviderError(f"bad {name}: {value!r}") from exc
    if not number.is_finite() or number <= 0:
        raise ProviderError(f"bad {name}: {value!r}")
    return number


def fetch_quotes() -> tuple[Decimal, Decimal]:
    """Return (XAU in USD per troy ounce, USD→IQD rate)."""
    settings = get_settings()
    timeout = settings.price_fetch_timeout_seconds
    gold = _get_json(settings.gold_price_url, timeout)
    fx = _get_json(settings.fx_rates_url, timeout)
    xau = _positive_decimal(gold.get("price"), "XAU price")
    iqd = _positive_decimal((fx.get("rates") or {}).get("IQD"), "USD/IQD rate")
    return xau, iqd
