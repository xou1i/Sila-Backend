import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import AmountIQD, Grams
from app.models import RiskProfile
from app.modules.listings.schemas import ListingOut
from app.modules.orders.schemas import RiskInsightOut

Engine = Literal["rules", "llm"]


class MatchIn(BaseModel):
    budget_iqd: AmountIQD


class MatchResult(BaseModel):
    rank: int
    listing: ListingOut
    suggested_weight_grams: Decimal
    execution_price_per_gram: Decimal
    estimated_total_iqd: Decimal = Field(description="Principal + commission for the suggestion")
    commission_rate: Decimal
    budget_usage_pct: Decimal
    score: Decimal
    reason: str


class MatchOut(BaseModel):
    budget_iqd: Decimal
    risk_profile: RiskProfile | None = Field(description="Read from the account, not the request")
    engine: Engine
    message: str
    results: list[MatchResult]


class RiskAnalysisIn(BaseModel):
    asset_id: uuid.UUID
    weight_grams: Grams = Field(description="Same meaning as purchased_weight_grams")


class RiskAnalysisOut(RiskInsightOut):
    asset_id: uuid.UUID
    purchased_weight_grams: Decimal


class MarketTrend(BaseModel):
    price_24k_per_gram: Decimal
    change_24h_pct: Decimal | None
    change_7d_pct: Decimal | None
    change_30d_pct: Decimal | None
    high_7d: Decimal | None
    low_7d: Decimal | None
    trend: Literal["up", "down", "flat"]
    summary: str


class KaratHolding(BaseModel):
    karat: int
    grams: Decimal
    current_value_iqd: Decimal


class PortfolioPerformance(BaseModel):
    total_grams: Decimal
    total_paid_iqd: Decimal = Field(description="Sum of total_paid_by_investor")
    current_value_iqd: Decimal = Field(description="Grams × today's price for each karat")
    unrealized_pnl_iqd: Decimal
    unrealized_pnl_pct: Decimal | None
    transactions_count: int
    by_karat: list[KaratHolding]


class InsightsOut(BaseModel):
    engine: Engine
    alerts: list[str]
    market: MarketTrend
    portfolio: PortfolioPerformance
