import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import Grams


class RiskInsightOut(BaseModel):
    level: Literal["low", "medium", "high"]
    insight: str
    signals: list[str]
    engine: Literal["rules", "llm"]


class PreviewIn(BaseModel):
    asset_id: uuid.UUID
    purchased_weight_grams: Grams


class ConfirmIn(BaseModel):
    asset_id: uuid.UUID
    purchased_weight_grams: Grams
    quote_token: str = Field(min_length=10, max_length=2048, description="From the preview")


class PreviewOut(BaseModel):
    asset_id: uuid.UUID
    karat: int
    purchased_weight_grams: Decimal
    execution_price_per_gram: Decimal
    principal_amount: Decimal = Field(description="What the seller receives in full")
    commission_rate: Decimal
    commission_amount: Decimal
    total_paid_by_investor: Decimal
    price_updated_at: datetime = Field(description="Timestamp of the market price used")
    quoted_at: datetime
    quote_expires_at: datetime
    quote_token: str = Field(description="Send back to /confirm before quote_expires_at")
    risk_insight: RiskInsightOut | None
    risk_insight_note: str | None = Field(
        default=None, description="Shown instead of the insight when AI analysis is unavailable"
    )


class TransactionOut(BaseModel):
    id: uuid.UUID
    asset_id: uuid.UUID
    karat: int
    seller_name: str
    buyer_ref: str | None = Field(description="Anonymized buyer (seller view only)")
    purchased_weight_grams: Decimal
    execution_price_per_gram: Decimal
    principal_amount: Decimal
    commission_rate: Decimal
    commission_amount: Decimal
    total_paid_by_investor: Decimal
    created_at: datetime


class OwnershipSummary(BaseModel):
    total_accumulated_grams: Decimal
    updated_at: datetime


class ConfirmOut(BaseModel):
    transaction: TransactionOut
    ownership: OwnershipSummary
    listing_status: str
    listing_available_weight_grams: Decimal
    idempotent_replay: bool = False
