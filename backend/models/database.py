from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from contextlib import contextmanager
import os

from dotenv import load_dotenv

# Load environment variables from backend/.env
load_dotenv()

# PostGIS-enabled PostgreSQL database
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://civicresolve:civicresolve_dev_pw@localhost:5432/civicresolve",
)

# Create database engine
engine = create_engine(
    DATABASE_URL,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    pool_pre_ping=True,
)

# Create database session
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Base class for SQLAlchemy models
Base = declarative_base()


def get_db() -> Session:
    """Provide a database session for FastAPI dependencies."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def get_db_context():
    """Provide a database session for non-FastAPI code."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all database tables."""
    from .report import (
        Report,
        CriticalInfrastructure,
        RoadSegment,
        TrainingFeedback,
    )
    from .user import AdminUser

    Base.metadata.create_all(bind=engine)