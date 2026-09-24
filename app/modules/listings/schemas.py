import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.core.schemas import Grams
from app.models import ListingStatus


class ListingCreateIn(BaseModel):
    """The price is computed server-side from the live 24K price; any price field is ignored."""

    total_weight_grams: Grams
    karat: Literal[18, 21, 22, 24] = Field(examples=[21])


class ListingStatusIn(BaseModel):
    status: Literal["active", "suspended"] = Field(
        description="Owner can suspend or re-activate. sold_out is set by the system only."
    )


class ListingOut(BaseModel):
    id: uuid.UUID
    seller_id: uuid.UUID
    seller_name: str
    seller_kyc_verified: bool
    karat: int
    total_weight_grams: Decimal
    available_weight_grams: Decimal
    base_price_per_gram: Decimal = Field(description="Reference price fixed at creation")
    current_price_per_gram: Decimal | None = Field(
        description="Live price for this karat now; the purchase uses this (see preview)"
    )
    status: ListingStatus
    is_promoted: bool = Field(description="Effective: is_promoted AND promotion_expiry_date > now")
    promotion_expiry_date: datetime | None
    created_at: datetime
    updated_at: datetime


class PaymentOut(BaseModel):
    status: str
    payment_ref: uuid.UUID
    amount_iqd: Decimal
    purpose: str


class PromoteOut(BaseModel):
    listing: ListingOut
    payment: PaymentOut


ListingSort = Literal["promoted_first", "newest", "price_asc", "price_desc"]
