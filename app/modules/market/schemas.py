from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

HistoryRange = Literal["1D", "1W", "1M", "3M", "1Y"]


class KaratPrice(BaseModel):
    karat: int
    price_per_gram_iqd: Decimal
    price_per_gram_usd: Decimal


class MarketPricesOut(BaseModel):
    base_currency: str = "IQD"
    karats: list[KaratPrice] = Field(description="24, 22, 21, 18 (price = 24K × karat / 24)")
    usd_iqd: Decimal = Field(description="1 USD in IQD")
    xau_usd_per_ounce: Decimal = Field(description="Global gold reference, USD per troy ounce")
    change_24h_pct: Decimal | None = Field(description="24K change vs ~24h ago, percent (2 dp)")
    updated_at: datetime = Field(description="When this price was fetched from the provider")
    source: str = Field(description="live | fallback | seed")
    is_stale: bool = Field(description="True when the last successful refresh is too old")


class PricePoint(BaseModel):
    ts: datetime
    price_per_gram: Decimal


class PriceHistoryOut(BaseModel):
    karat: int
    range: HistoryRange
    currency: str = "IQD"
    points: list[PricePoint]
