import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

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


class AdvisorIn(BaseModel):
    question: str = Field(min_length=1, max_length=500, examples=["شنو أحسن شي أشتريه هسة؟"])
    budget_iqd: AmountIQD | None = Field(
        default=None, description="Wins over any amount written in the question"
    )

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class AdvisorBudget(BaseModel):
    amount_iqd: Decimal
    source: Literal["request", "question_digits", "question_words"]
    confirmed: bool = Field(
        description="False when read from words in the question: no suggestion until the user "
        "confirms by sending the amount as budget_iqd"
    )


class AdvisorMarket(BaseModel):
    price_24k_per_gram: Decimal
    change_24h_pct: Decimal | None
    updated_at: datetime
    is_stale: bool


class AdvisorOut(BaseModel):
    engine: Engine = Field(description="llm = worded by the model; rules = rule-based answer")
    answer: str = Field(description="Plain Arabic text, no markdown and no dashes")
    show_figures: bool = Field(
        description="The question is about prices, money, the budget or holdings: show the "
        "figures (market_snapshot, budget, holdings_grams) in their own panel under the answer"
    )
    budget: AdvisorBudget | None = Field(description="null when no budget was given or found")
    holdings_grams: Decimal = Field(description="The investor's own verified balance")
    suggestions: list[MatchResult] = Field(
        description="From the rule-based matcher (same as /api/ai/match), never from the model"
    )
    market_snapshot: AdvisorMarket
    follow_up_questions: list[str] = Field(
        description="Up to 3 questions to offer next, fitted to this question (checked like the "
        "answer; rule-based ones when the model is not used)"
    )
    disclaimer: str = Field(description="Always shown under the answer")
