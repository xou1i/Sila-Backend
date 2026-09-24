import hmac
import hashlib
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models import AssetListing, Transaction, User

SECRET_KEY = "super-secret-key-for-hackathon"

def calculate_commission_rate(weight: float) -> float:
    if weight < 50:
        return 0.015
    elif weight <= 200:
        return 0.010
    else:
        return 0.005

def preview_transaction(asset_id: int, weight: float, db: Session):
    asset = db.query(AssetListing).filter(AssetListing.id == asset_id).first()
    if not asset or asset.status != "active":
        raise HTTPException(status_code=400, detail="العرض غير متاح حالياً")
    
    global_24k_price = 60.0 
    price_per_gram = global_24k_price * (asset.karat / 24.0)
    
    principal_amount = weight * price_per_gram
    commission_rate = calculate_commission_rate(weight)
    commission_amount = principal_amount * commission_rate
    total_paid = principal_amount + commission_amount

    return {
        "execution_price_per_gram": price_per_gram,
        "principal_amount": principal_amount,
        "commission_rate": commission_rate,
        "commission_amount": commission_amount,
        "total_paid_by_investor": total_paid
    }

def confirm_transaction(investor_id: int, asset_id: int, weight: float, db: Session):
    user = db.query(User).filter(User.id == investor_id).first()
    if not user or not user.kyc_verified:
        raise HTTPException(status_code=403, detail="KYC_NOT_VERIFIED")

    asset = db.query(AssetListing).filter(AssetListing.id == asset_id).with_for_update().first()
    
    if not asset or asset.status != "active":
        raise HTTPException(status_code=400, detail="LISTING_NOT_ACTIVE")
        
    if weight > asset.available_weight_grams:
        raise HTTPException(status_code=400, detail="INSUFFICIENT_AVAILABLE_WEIGHT")

    preview_data = preview_transaction(asset_id, weight, db)

    asset.available_weight_grams -= weight
    if asset.available_weight_grams == 0:
        asset.status = "sold_out"

    new_tx = Transaction(
        investor_id=investor_id,
        asset_id=asset_id,
        purchased_weight_grams=weight,
        execution_price_per_gram=preview_data["execution_price_per_gram"],
        commission_amount=preview_data["commission_amount"],
        total_paid_by_investor=preview_data["total_paid_by_investor"]
    )
    db.add(new_tx)

    token_data = f"{investor_id}:{weight}:{datetime.utcnow().timestamp()}"
    signature = hmac.new(SECRET_KEY.encode(), token_data.encode(), hashlib.sha256).hexdigest()

    db.commit()
    db.refresh(new_tx)

    return {
        "status": "success",
        "transaction_id": new_tx.id,
        "digital_signature_token": signature
    }