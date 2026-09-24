from app.database import SessionLocal, engine
from app import models

models.Base.metadata.create_all(bind=engine)

def seed_database():
    db = SessionLocal()

    # مسح البيانات القديمة لضمان تحديث الهيكلية والصور
    db.query(models.AssetListing).delete()
    db.query(models.User).delete()
    db.commit()

    # إنشاء تاجر معتمد
    seller = models.User(
        full_name="مجوهرات بغداد الأهلية",
        email="seller@baghdadgold.iq",
        password_hash="seller123",
        role="seller",
        kyc_verified=True,
        subscription_tier="Premium"
    )
    
    # إنشاء مستثمر معتمد
    investor = models.User(
        full_name="علي حسين المحمدي",
        email="investor@gmail.com",
        password_hash="investor123",
        role="investor",
        kyc_verified=True,
        risk_profile="Low"
    )

    db.add(seller)
    db.add(investor)
    db.commit()

    # إضافة عروض ذهب تحتوي على الوزن، العيار، ورابط الصورة
    listings = [
        models.AssetListing(
            seller_id=seller.id,
            total_weight_grams=10.0,
            available_weight_grams=10.0,
            karat=24,
            image_url="https://images.unsplash.com/photo-1610375461246-83df859d849d?w=500", # صورة سبيكة 10 غرام
            is_promoted=True
        ),
        models.AssetListing(
            seller_id=seller.id,
            total_weight_grams=50.0,
            available_weight_grams=50.0,
            karat=24,
            image_url="https://images.unsplash.com/photo-1610375461369-d613b563fbe6?w=500", # صورة سبيكة 50 غرام
            is_promoted=False
        ),
        models.AssetListing(
            seller_id=seller.id,
            total_weight_grams=100.0,
            available_weight_grams=100.0,
            karat=24,
            image_url="https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=500", # صورة سبيكة 100 غرام
            is_promoted=True
        )
    ]

    db.add_all(listings)
    db.commit()
    db.close()
    print("تم إعادة تغذية قاعدة البيانات بنجاح مع أوزان وعيارات وصور السبائك!")

if __name__ == "__main__":
    seed_database()