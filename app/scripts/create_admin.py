"""Create the platform admin account (admins can never sign up through the API).

python -m app.scripts.create_admin admin@sila.iq "إدارة صِلة"

Prints a random password once; store it safely. Refuses if the e-mail already exists.
"""

import secrets
import string
import sys

from sqlalchemy import select

from app.core.audit import audit
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import SubscriptionTier, User, UserRole

_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m app.scripts.create_admin <email> ["full name"]')
        return 2
    email = sys.argv[1].strip().lower()
    full_name = sys.argv[2] if len(sys.argv) > 2 else "إدارة صِلة"
    password = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            print(f"refused: {email} already exists")
            return 1
        admin = User(
            role=UserRole.admin,
            full_name=full_name,
            email=email,
            password_hash=hash_password(password),
            kyc_verified=True,
            subscription_tier=SubscriptionTier.free,
        )
        db.add(admin)
        db.flush()
        audit(db, "admin_created", entity_type="user", entity_id=admin.id, data={"email": email})
        db.commit()
    print(f"admin created: {email}")
    print(f"password (shown once): {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
