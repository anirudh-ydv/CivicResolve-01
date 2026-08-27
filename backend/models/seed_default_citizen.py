"""
Seeds a default, pre-verified citizen account for demos/competitions -
so a judge or teacher can log in as a citizen immediately without going
through real email verification (which needs real SMTP to be configured
first). This is separate from the admin account, which was already seeded
elsewhere from ADMIN_USERNAME/ADMIN_PASSWORD env vars - that part is
unchanged by this file.

Run this once after init_db() creates the tables, same pattern as the
existing seed_geo_data.py:

    python models/seed_geo_data.py
    python models/seed_default_citizen.py

Idempotent - safe to re-run; does nothing if the account already exists.
"""
import os
import uuid
from datetime import datetime

from passlib.context import CryptContext

from models.database import SessionLocal
from models.citizen_user import CitizenUser

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_CITIZEN_EMAIL = os.getenv("DEFAULT_CITIZEN_EMAIL", "citizen@example.com")
DEFAULT_CITIZEN_PASSWORD = os.getenv("DEFAULT_CITIZEN_PASSWORD", "citizen123")


def seed_default_citizen():
    db = SessionLocal()
    try:
        existing = db.query(CitizenUser).filter(CitizenUser.email == DEFAULT_CITIZEN_EMAIL).first()
        if existing:
            print(f"Default citizen account already exists ({DEFAULT_CITIZEN_EMAIL}) - skipping.")
            return

        user = CitizenUser(
            id=uuid.uuid4(),
            email=DEFAULT_CITIZEN_EMAIL,
            password_hash=pwd_context.hash(DEFAULT_CITIZEN_PASSWORD),
            is_verified=True,  # pre-verified on purpose - this is a demo account, real signups still require real verification
            verification_token=None,
            verification_token_expires_at=None,
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        print(f"Seeded default citizen account: {DEFAULT_CITIZEN_EMAIL} / {DEFAULT_CITIZEN_PASSWORD}")
        print("Change DEFAULT_CITIZEN_EMAIL / DEFAULT_CITIZEN_PASSWORD env vars before any real deployment.")
    finally:
        db.close()


if __name__ == "__main__":
    seed_default_citizen()
