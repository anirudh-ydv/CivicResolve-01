"""
CivicResolve Authentication Module
Handles admin authentication via JWT tokens with bcrypt password hashing.
"""

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from models.database import get_db
from models.user import AdminUser

# Configuration from environment
SECRET_KEY = os.getenv("CIVICRESOLVE_SECRET_KEY", "dev-secret-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for token extraction
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plain password."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def get_admin_user(db: Session, username: str) -> Optional[AdminUser]:
    """Retrieve an admin user by username."""
    return db.query(AdminUser).filter(AdminUser.username == username).first()


def authenticate_admin(db: Session, username: str, password: str) -> Optional[AdminUser]:
    """Authenticate an admin user with username and password."""
    user = get_admin_user(db, username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def get_current_admin(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    """
    FastAPI dependency to get the current authenticated admin user.
    Raises 401 if token is invalid or user not found.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    username: Optional[str] = payload.get("sub")
    if username is None:
        raise credentials_exception

    user = get_admin_user(db, username)
    if user is None:
        raise credentials_exception

    return user


def seed_default_admin(db: Session) -> None:
    """
    Seed a default admin user if none exists.
    Uses ADMIN_USERNAME and ADMIN_PASSWORD environment variables.
    """
    existing = db.query(AdminUser).first()
    if existing:
        return

    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "changeme123")

    hashed = get_password_hash(admin_password)
    admin = AdminUser(
        id=str(__import__("uuid").uuid4()),
        username=admin_username,
        hashed_password=hashed,
    )
    db.add(admin)
    db.commit()

    # Print credentials once for local dev discoverability
    print(f"\n{'='*60}")
    print(f"DEFAULT ADMIN ACCOUNT CREATED")
    print(f"Username: {admin_username}")
    print(f"Password: {admin_password}")
    print(f"{'='*60}\n")