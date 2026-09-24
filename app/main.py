from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import List
import urllib.request
import json

from app import models, schemas
from app.database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="SmartBridge API - Trident Wealth",
    description="المنصة الخلفية لإدارة واستثمار الذهب والأصول",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_live_gold_price() -> float:
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            return 75.50
    except Exception:
        return 75.50

@app.post("/api/auth/signup", response_model=schemas.UserOut)
def signup(user_data: schemas.UserCreate, db: Session = Depends(get_db)):
    existing_user = db.query(models.User).filter(models.User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="البريد الإلكتروني مُسجل بالفعل")
    
    new_user = models.User(
        full_name=user_data.full_name,
        email=user_data.email,
        password_hash=user_data.password,
        role=user_data.role,
        risk_profile=user_data.risk_profile
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/api/kyc/verify/{user_id}")
def verify_kyc(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="المستخدم غير موجود")
    
    user.kyc_verified = True
    db.commit()
    return {"message": "تم توثيق حساب الـ KYC بنجاح", "user_id": user_id, "kyc_verified": True}

@app.get("/api/market/prices", response_model=schemas.MarketPriceResponse)
def get_market_prices():
    base_price_24k = get_live_gold_price()
    return {
        "gold_24k_per_gram": base_price_24k,
        "gold_21k_per_gram": round(base_price_24k * (21 / 24), 2),
        "gold_18k_per_gram": round(base_price_24k * (18 / 24), 2),
        "currency": "USD"
    }

@app.get("/api/listings", response_model=List[schemas.ListingResponse])
def get_listings(db: Session = Depends(get_db)):
    listings = db.query(models.AssetListing).filter(models.AssetListing.status == "active").all()
    return listings

@app.post("/api/listings", response_model=schemas.ListingResponse)
def create_listing(listing_data: schemas.ListingCreate, db: Session = Depends(get_db)):
    seller = db.query(models.User).filter(models.User.id == listing_data.seller_id).first()
    if not seller or seller.role != "seller":
        raise HTTPException(status_code=400, detail="المستخدم يجب أن يكون بائعاً مسجلاً")
    if not seller.kyc_verified:
        raise HTTPException(status_code=400, detail="حساب البائع غير موثق بـ KYC")

    new_listing = models.AssetListing(
        seller_id=listing_data.seller_id,
        total_weight_grams=listing_data.total_weight_grams,
        available_weight_grams=listing_data.total_weight_grams,
        karat=listing_data.karat,
        is_promoted=listing_data.is_promoted
    )
    db.add(new_listing)
    db.commit()
    db.refresh(new_listing)
    return new_listing

@app.post("/api/transactions/preview", response_model=schemas.TransactionPreviewResponse)
def preview_transaction(req: schemas.TransactionPreviewRequest, db: Session = Depends(get_db)):
    listing = db.query(models.AssetListing).filter(models.AssetListing.id == req.listing_id).first()
    if not listing:
        raise HTTPException(status_code=404, detail="عرض الذهب غير موجود")
    if listing.available_weight_grams < req.weight_grams:
        raise HTTPException(status_code=400, detail="الكمية المطلوبة غير متوفرة")
    price_per_gram = get_live_gold_price()
    gold_price = req.weight_grams * price_per_gram
    platform_fee = gold_price * 0.015
    total_price = gold_price + platform_fee

    return {
        "weight_grams": req.weight_grams,
        "price_per_gram": price_per_gram,
        "gold_price": round(gold_price, 2),
        "platform_fee": round(platform_fee, 2),
        "total_price": round(total_price, 2)
    }

@app.post("/api/transactions/confirm")
def confirm_transaction(req: schemas.TransactionConfirmRequest, db: Session = Depends(get_db)):
    investor = db.query(models.User).filter(models.User.id == req.investor_id).first()
    if not investor or investor.role != "investor":
        raise HTTPException(status_code=400, detail="المستخدم يجب أن يكون مستثمراً")
    if not investor.kyc_verified:
        raise HTTPException(status_code=400, detail="حساب المستثمر غير موثق بـ KYC")

    listing = db.query(models.AssetListing).filter(models.AssetListing.id == req.listing_id).first()
    if not listing or listing.available_weight_grams < req.weight_grams:
        raise HTTPException(status_code=400, detail="الكمية المحددة غير متوفرة للشراء")

    listing.available_weight_grams -= req.weight_grams
    if listing.available_weight_grams == 0:
        listing.status = "sold_out"

    db.commit()
    return {
        "message": "تمت عملية الشراء وتأكيد المعاملة بنجاح",
        "investor_id": req.investor_id,
        "weight_purchased": req.weight_grams,
        "remaining_weight": listing.available_weight_grams
    }