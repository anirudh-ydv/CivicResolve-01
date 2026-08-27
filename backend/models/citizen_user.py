"""
CitizenUser model - real citizen accounts with email/password login and
real email verification (not a fake/instant-verified flag).

This is intentionally a SEPARATE table from AdminUser (models/user.py),
not a shared "users with a role column" table. Reasons:
  1. The task explicitly asked for signup to exist ONLY for citizens -
     admin stays a fixed seeded account with no signup path. Keeping them
     as separate models makes that constraint structural (there is no
     signup endpoint that could ever create an AdminUser row) rather than
     something enforced only by an if-check that could be bypassed by a
     future bug.
  2. Admin accounts need no email verification, no password self-service
     reset flow, etc. - conflating the two models would mean every future
     admin-specific field has to be nullable/irrelevant for citizens and
     vice versa.

Add this import to models/database.py's init_db() (or wherever your
Base.metadata.create_all() call lives) so this table actually gets
created - SQLAlchemy only creates tables for models it has seen imported.
"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID

from models.database import Base


class CitizenUser(Base):
    __tablename__ = "citizen_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)

    # Real email verification, not a fake instantly-true flag: a new
    # signup starts is_verified=False and CANNOT log in until they click
    # the emailed link, which the /api/auth/verify-email endpoint flips to
    # True. See app/email_service.py for the actual sending logic.
    is_verified = Column(Boolean, nullable=False, default=False)
    verification_token = Column(String, nullable=True, index=True)
    verification_token_expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": str(self.id),
            "email": self.email,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
