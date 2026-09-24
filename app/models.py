from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from datetime import datetime
from app.database import Base
from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, default="investor")
    risk_profile = Column(String, default="Medium")
    kyc_verified = Column(Boolean, default=False)
    subscription_tier = Column(String, default="Basic")

    listings = relationship("AssetListing", back_populates="seller")

class AssetListing(Base):
    __tablename__ = "asset_listings"

    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"))
    total_weight_grams = Column(Float, nullable=False)
    available_weight_grams = Column(Float, nullable=False)
    karat = Column(Integer, default=24)
    image_url = Column(String, nullable=True)  # <-- إضافة رابط الصورة
    is_promoted = Column(Boolean, default=False)
    status = Column(String, default="active")

    seller = relationship("User", back_populates="listings")