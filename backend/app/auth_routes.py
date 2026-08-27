"""
Unified auth routes.
"""
import os
import secrets
import uuid
from datetime import datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from models.database import get_db
from models.citizen_user import CitizenUser
from app.email_service import send_verification_email, is_email_configured

router = APIRouter(prefix="/api/auth", tags=["auth"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SECRET_KEY = os.getenv("CIVICRESOLVE_SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")

VERIFICATION_TOKEN_EXPIRE_HOURS = 24


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    email: EmailStr
    password: str


class SignupResponse(BaseModel):
    message: str
    email_sent: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str  # "admin" | "citizen"


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def create_access_token(data: dict, expires_minutes: int = ACCESS_TOKEN_EXPIRE_MINUTES) -> str:
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(minutes=expires_minutes)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------------------------------------------------------------------
# Signup (citizens only)
# ---------------------------------------------------------------------------
@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(CitizenUser).filter(CitizenUser.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Could not create account with that email")

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    token = secrets.token_urlsafe(32)
    
    # --- MODIFIED: Auto-verify user and clear tokens ---
    user = CitizenUser(
        id=uuid.uuid4(),
        email=payload.email,
        password_hash=pwd_context.hash(payload.password),
        is_verified=True,  # Set to True so users can log in immediately
        verification_token=None,
        verification_token_expires_at=None,
    )
    db.add(user)
    db.commit()

    # --- MODIFIED: Email verification disabled for development ---
    # email_sent = False
    # email_error = None
    # try:
    #     send_verification_email(payload.email, token)
    #     email_sent = True
    # except Exception as e:
    #     email_error = str(e)
    #
    # if not email_sent:
    #     return SignupResponse(...)

    return SignupResponse(
        message="Account created instantly. You can now log in.",
        email_sent=False,
    )


# ---------------------------------------------------------------------------
# Email verification (Left intact in case you turn it back on later)
# ---------------------------------------------------------------------------
@router.get("/verify-email")
async def verify_email(token: str = Query(...), db: Session = Depends(get_db)):
    user = db.query(CitizenUser).filter(CitizenUser.verification_token == token).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or already-used verification link")

    if user.verification_token_expires_at and user.verification_token_expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=400,
            detail="Verification link expired. Please sign up again to receive a new one.",
        )

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires_at = None
    db.commit()

    return {"message": "Email verified. You can now log in."}


# ---------------------------------------------------------------------------
# Unified login - tries admin first, then citizen
# ---------------------------------------------------------------------------
@router.post("/login", response_model=TokenResponse)
async def login(
    username: str = Form(...),  # admin username OR citizen email
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    # 1. Admin: fixed seeded credentials, no DB lookup, no signup path.
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = create_access_token({"sub": username, "role": "admin"})
        return TokenResponse(access_token=token, role="admin")

    # 2. Citizen: real DB lookup + real bcrypt password check.
    user = db.query(CitizenUser).filter(CitizenUser.email == username).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email/username or password")

    if not user.is_verified:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before logging in. Check your inbox for the verification link.",
        )

    token = create_access_token({"sub": str(user.id), "email": user.email, "role": "citizen"})
    return TokenResponse(access_token=token, role="citizen")