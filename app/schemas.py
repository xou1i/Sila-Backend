from pydantic import BaseModel, EmailStr
from typing import Optional, List

class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    password: str
    role: str = "investor"
    risk_profile: Optional[str] = "Medium"

class UserOut(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: str
    risk_profile: Optional[str] = None
    kyc_verified: bool
    subscription_tier: str

    class Config:
        from_attributes = True

class MarketPriceResponse(BaseModel):
    gold_24k_per_gram: float
    gold_21k_per_gram: float
    gold_18k_per_gram: float
    currency: str

class ListingCreate(BaseModel):
    seller_id: int
    total_weight_grams: float
    karat: Optional[int] = 24
    image_url: Optional[str] = None  # <-- رابط الصورة
    is_promoted: Optional[bool] = False

class ListingResponse(BaseModel):
    id: int
    seller_id: int
    total_weight_grams: float
    available_weight_grams: float
    karat: Optional[int] = 24
    image_url: Optional[str] = None  # <-- رابط الصورة بالاستجابة
    is_promoted: bool
    status: str

    class Config:
        from_attributes = True

class TransactionPreviewRequest(BaseModel):
    listing_id: int
    weight_grams: float

class TransactionPreviewResponse(BaseModel):
    weight_grams: float
    price_per_gram: float
    gold_price: float
    platform_fee: float
    total_price: float

class TransactionConfirmRequest(BaseModel):
    investor_id: int
    listing_id: int
    weight_grams: float