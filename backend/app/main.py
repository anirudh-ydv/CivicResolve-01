"""
CivicResolve FastAPI Backend
REST API for citizen submissions and admin dashboard
"""

import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Query, BackgroundTasks, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from models.database import get_db, init_db
from models.report import Report, ReportStatus, IssueCategory, RoadType, TrainingFeedback
from models.user import AdminUser
from ai.model_pipeline import predict_image, CivicResolveInference
from ai.risk_engine import calculate_composite_risk_score
from app.auth import (
    authenticate_admin,
    create_access_token,
    get_current_admin,
    seed_default_admin,
)

# Configuration
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "./uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Pydantic Schemas
class ReportSubmitResponse(BaseModel):
    report_id: str
    category: str
    ai_confidence: float
    requires_manual_review: bool
    visual_severity_score: float
    road_type: str
    road_type_multiplier: float
    critical_proximity_flag: bool
    final_priority_score: float
    status: str
    created_at: str
    possible_duplicates: list = []
    message: str = "Report submitted successfully"


class ReportResponse(BaseModel):
    id: str
    user_id: str
    category: str
    ai_confidence: Optional[float] = None
    requires_manual_review: bool = False
    visual_severity_score: float
    road_type: str
    road_type_multiplier: float
    critical_proximity_flag: bool
    critical_proximity_score: float
    final_priority_score: float
    latitude: float
    longitude: float
    description: Optional[str]
    status: str
    image_path: Optional[str]
    created_at: str
    updated_at: str


class ReportListResponse(BaseModel):
    reports: List[ReportResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class StatsResponse(BaseModel):
    total_reports: int
    open_reports: int
    in_progress_reports: int
    resolved_reports: int
    by_category: dict
    avg_priority_score: float


class GeoJSONResponse(BaseModel):
    type: str = "FeatureCollection"
    features: List[dict]


# Auth Schemas
class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# FastAPI App
app = FastAPI(
    title="CivicResolve API",
    description="Automated Public Infrastructure Reporting System with Composite Risk Scoring",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://localhost:5174", 
        "http://127.0.0.1:5173", 
        "http://127.0.0.1:5174",
        "http://localhost:5500",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for uploaded images
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Startup
@app.on_event("startup")
async def startup_event():
    init_db()
    # Seed default admin user
    from models.database import SessionLocal
    db = SessionLocal()
    try:
        seed_default_admin(db)
    finally:
        db.close()
    # Warm up model
    try:
        _ = CivicResolveInference()
        print("AI Model loaded successfully")
    except Exception as e:
        print(f"Warning: AI Model not loaded: {e}")


# Helper Functions
def validate_image_file(file: UploadFile) -> None:
    """Validate uploaded file type and size."""
    # Check extension
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Check MIME type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid MIME type. Allowed: {', '.join(ALLOWED_MIME_TYPES)}"
        )

    # Check file size (read first chunk)
    file.file.seek(0, 2)  # Seek to end
    size = file.file.tell()
    file.file.seek(0)  # Reset
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB"
        )


def save_upload_file(file: UploadFile, report_id: str) -> str:
    """Save uploaded file to disk and return relative path."""
    ext = Path(file.filename).suffix.lower()
    filename = f"{report_id}{ext}"
    file_path = UPLOAD_DIR / filename

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return f"/uploads/{filename}"


def get_priority_color(score: float) -> str:
    """Map final priority score to color for map pins."""
    if score >= 8:
        return "red"
    elif score >= 4:
        return "yellow"
    return "green"


# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.post("/api/auth/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """
    OAuth2 password flow login for admin users.
    Returns JWT access token on success.
    """
    user = authenticate_admin(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return TokenResponse(access_token=access_token)


@app.post("/api/report/submit", response_model=ReportSubmitResponse, status_code=201)
async def submit_report(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    latitude: float = Form(..., ge=-90, le=90),
    longitude: float = Form(..., ge=-180, le=180),
    description: Optional[str] = Form(None, max_length=500),
    user_id: str = Form(...),
    db: Session = Depends(get_db),
):
    """
    Submit a new infrastructure issue report.
    Runs AI inference on the image to classify category and score visual severity.
    Calculates composite risk score using:
    Final Risk Score = (Visual Severity * 0.5) + (Road Hierarchy Weight * 0.3) + (Critical Proximity * 0.2)
    """
    # Validate inputs
    validate_image_file(image)

    if description and len(description.strip()) == 0:
        description = None

    # Generate report ID
    report_id = str(uuid.uuid4())

    # Read image bytes for AI inference
    image_bytes = await image.read()
    await image.seek(0)  # Reset for saving

    # Run AI inference (visual classification + severity)
    ai_result = predict_image(image_bytes)
    category = ai_result.get("category", "unclassified")
    visual_severity = ai_result.get("severity_score", 5)
    ai_confidence = ai_result.get("confidence", 0.0)
    requires_manual_review = ai_result.get("requires_manual_review", False)

    # Validate category (IssueCategory now includes UNCLASSIFIED - see
    # models/report.py - so a genuinely low-confidence prediction is
    # stored as such, not silently coerced to OTHER)
    try:
        category_enum = IssueCategory(category)
    except ValueError:
        category_enum = IssueCategory.UNCLASSIFIED
        requires_manual_review = True

    # Calculate composite risk score (also checks for likely duplicate
    # reports of the same category nearby, via PostGIS ST_DWithin)
    risk_result = calculate_composite_risk_score(db, visual_severity, latitude, longitude, category=category_enum.value)

    # Save image
    image_path = save_upload_file(image, report_id)

    # Create database record with all risk fields. Every new report
    # starts as PENDING_REVIEW (Part 2) - it is NOT an actionable dispatch
    # item until an admin confirms or corrects it via
    # PATCH /api/reports/{id}/review or /confirm.
    report = Report(
        id=report_id,
        user_id=user_id[:64],  # Truncate if too long
        category=category_enum,
        ai_confidence=ai_confidence,
        requires_manual_review=requires_manual_review,
        visual_severity_score=risk_result["visual_severity_score"],
        road_type=RoadType(risk_result["road_type"]),
        road_type_multiplier=risk_result["road_type_multiplier"],
        critical_proximity_flag=risk_result["critical_proximity_flag"],
        critical_proximity_score=risk_result["critical_proximity_score"],
        final_priority_score=risk_result["final_priority_score"],
        latitude=latitude,
        longitude=longitude,
        description=description,
        status=ReportStatus.PENDING_REVIEW,
        image_path=image_path,
    )

    db.add(report)
    db.commit()
    db.refresh(report)

    return ReportSubmitResponse(
        report_id=report.id,
        category=report.category.value,
        ai_confidence=report.ai_confidence,
        requires_manual_review=report.requires_manual_review,
        visual_severity_score=report.visual_severity_score,
        road_type=report.road_type.value,
        road_type_multiplier=report.road_type_multiplier,
        critical_proximity_flag=report.critical_proximity_flag,
        final_priority_score=report.final_priority_score,
        status=report.status.value,
        created_at=report.created_at.isoformat(),
        possible_duplicates=risk_result.get("possible_duplicates", []),
    )


@app.get("/api/reports", response_model=ReportListResponse)
async def list_reports(
    status: Optional[ReportStatus] = Query(None),
    category: Optional[IssueCategory] = Query(None),
    min_priority: Optional[float] = Query(None, ge=1, le=10),
    max_priority: Optional[float] = Query(None, ge=1, le=10),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get paginated list of reports with optional filters. Sorted by final_priority_score descending."""
    query = db.query(Report)

    if status:
        query = query.filter(Report.status == status)
    if category:
        query = query.filter(Report.category == category)
    if min_priority:
        query = query.filter(Report.final_priority_score >= min_priority)
    if max_priority:
        query = query.filter(Report.final_priority_score <= max_priority)

    # Sort by final_priority_score descending (highest priority first), then by creation date
    query = query.order_by(Report.final_priority_score.desc(), Report.created_at.desc())

    total = query.count()
    total_pages = (total + limit - 1) // limit

    reports = query.offset((page - 1) * limit).limit(limit).all()

    return ReportListResponse(
        reports=[ReportResponse(**r.to_dict()) for r in reports],
        total=total,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@app.get("/api/reports/geojson", response_model=GeoJSONResponse)
async def get_reports_geojson(
    status: Optional[ReportStatus] = Query(None),
    category: Optional[IssueCategory] = Query(None),
    min_priority: Optional[float] = Query(None, ge=1, le=10),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get reports as GeoJSON FeatureCollection for map rendering."""
    query = db.query(Report)

    if status:
        query = query.filter(Report.status == status)
    if category:
        query = query.filter(Report.category == category)
    if min_priority:
        query = query.filter(Report.final_priority_score >= min_priority)

    # Sort by final_priority_score for priority rendering
    query = query.order_by(Report.final_priority_score.desc()).limit(limit)

    reports = query.all()
    features = [r.to_geojson_feature() for r in reports]

    return GeoJSONResponse(features=features)


@app.get("/api/reports/stats", response_model=StatsResponse)
async def get_stats(
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get aggregate statistics for dashboard cards."""
    total = db.query(Report).count()
    open_count = db.query(Report).filter(Report.status == ReportStatus.OPEN).count()
    in_progress = db.query(Report).filter(Report.status == ReportStatus.IN_PROGRESS).count()
    resolved = db.query(Report).filter(Report.status == ReportStatus.RESOLVED).count()

    # By category
    from sqlalchemy import func
    cat_counts = db.query(Report.category, func.count(Report.id)).group_by(Report.category).all()
    by_category = {cat.value: count for cat, count in cat_counts}

    # Average final priority score
    avg_priority = db.query(func.avg(Report.final_priority_score)).scalar() or 0

    return StatsResponse(
        total_reports=total,
        open_reports=open_count,
        in_progress_reports=in_progress,
        resolved_reports=resolved,
        by_category=by_category,
        avg_priority_score=round(float(avg_priority), 1),
    )


@app.get("/api/reports/{report_id}", response_model=ReportResponse)
async def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Get a single report by ID."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportResponse(**report.to_dict())


@app.patch("/api/reports/{report_id}/status")
async def update_report_status(
    report_id: str,
    status: ReportStatus = Form(...),
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """Update report status (admin only in production)."""
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report.status = status
    report.updated_at = datetime.utcnow()
    db.commit()

    return {"message": "Status updated", "report_id": report_id, "new_status": status.value}


@app.patch("/api/reports/{report_id}/confirm")
async def confirm_report(
    report_id: str,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """
    Lightweight 'Confirm' action (Part 2.2): admin agrees with the AI's
    current category/severity as-is. Moves the report out of
    PENDING_REVIEW into OPEN (now an actionable dispatch item) and logs a
    non-correction row to training_feedback (still useful signal - it
    tells us the model got it right). For actual corrections, use
    PATCH /api/reports/{id}/review instead.
    """
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.category == IssueCategory.UNCLASSIFIED:
        raise HTTPException(
            status_code=422,
            detail="Cannot confirm an unclassified report - use /review to assign a real category first.",
        )
    if not report.image_path:
        raise HTTPException(status_code=422, detail="Report has no associated image to log feedback for")

    feedback = TrainingFeedback(
        report_id=report.id,
        image_path=report.image_path,
        ai_predicted_category=report.category.value,
        ai_predicted_severity=report.visual_severity_score,
        admin_confirmed_category=report.category,
        admin_confirmed_severity=int(round(report.visual_severity_score)),
        was_correction=False,
        admin_username=current_admin.username,
    )
    db.add(feedback)

    report.status = ReportStatus.OPEN
    report.requires_manual_review = False
    report.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)

    return {"message": "Confirmed", "report": report.to_dict()}


class ReviewRequest(BaseModel):
    category: IssueCategory
    severity: int = Field(..., ge=1, le=10)


@app.patch("/api/reports/{report_id}/review")
async def review_report(
    report_id: str,
    review: ReviewRequest,
    db: Session = Depends(get_db),
    current_admin: AdminUser = Depends(get_current_admin),
):
    """
    Admin confirms or corrects a report's AI-predicted category/severity
    (Part 4). Every review - whether it changes anything or just confirms
    the AI got it right - is logged to training_feedback for future
    retraining. This is the only place training_feedback rows get created.
    """
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    if not report.image_path:
        raise HTTPException(status_code=422, detail="Report has no associated image to log feedback for")

    ai_predicted_category = report.category.value
    ai_predicted_severity = report.visual_severity_score
    was_correction = (
        review.category != report.category
        or review.severity != round(report.visual_severity_score)
    )

    feedback = TrainingFeedback(
        report_id=report.id,
        image_path=report.image_path,
        ai_predicted_category=ai_predicted_category,
        ai_predicted_severity=ai_predicted_severity,
        admin_confirmed_category=review.category,
        admin_confirmed_severity=review.severity,
        was_correction=was_correction,
        admin_username=current_admin.username,
    )
    db.add(feedback)

    # Apply the admin's correction to the live report too, and recompute
    # the composite priority score since visual_severity_score changed.
    report.category = review.category
    report.visual_severity_score = float(review.severity)
    report.requires_manual_review = False
    if report.status == ReportStatus.PENDING_REVIEW:
        report.status = ReportStatus.OPEN
    risk_result = calculate_composite_risk_score(
        db, float(review.severity), report.latitude, report.longitude
    )
    report.road_type = RoadType(risk_result["road_type"])
    report.road_type_multiplier = risk_result["road_type_multiplier"]
    report.critical_proximity_flag = risk_result["critical_proximity_flag"]
    report.critical_proximity_score = risk_result["critical_proximity_score"]
    report.final_priority_score = risk_result["final_priority_score"]
    report.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(report)

    return {
        "message": "Correction confirmed" if not was_correction else "Correction applied",
        "was_correction": was_correction,
        "report": report.to_dict(),
    }


# Run with: uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)