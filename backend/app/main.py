"""
CivicResolve FastAPI Backend
REST API for citizen submissions and admin dashboard
"""

import os
from dotenv import load_dotenv
load_dotenv() # <--- THESE ARE THE TWO NEW LINES

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
from app.auth_routes import router as auth_router

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
    ood_rejected: bool = False
    ood_reason: Optional[str] = None


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


# FastAPI App
app = FastAPI(
    title="CivicResolve API",
    description="Automated Public Infrastructure Reporting System with Composite Risk Scoring",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", 
        "http://localhost:5174",
        "http://127.0.0.1:5173", 
        "http://127.0.0.1:5174",
        "http://localhost:5500", 
        "http://127.0.0.1:5500",
        "http://localhost:3000",
        "http://localhost:8000",
        "https://civicresolve-wine.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for uploaded images
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Mount new unified auth routes
app.include_router(auth_router)

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
    
    try:
        from models.seed_geo_data import seed
        seed()
    except Exception as e:
        print(f"WARNING: geo data seeding failed (non-fatal): {e}")


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

    try:
        category_enum = IssueCategory(category)
    except ValueError:
        category_enum = IssueCategory.UNCLASSIFIED
        requires_manual_review = True

    # Calculate composite risk score
    risk_result = calculate_composite_risk_score(db, visual_severity, latitude, longitude, category=category_enum.value)

    # Save image
    image_path = save_upload_file(image, report_id)

    report = Report(
        id=report_id,
        user_id=user_id[:64],
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
        ood_rejected=ai_result.get("ood_rejected", False),
        ood_reason=ai_result.get("ood_reason"),
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
    """Get paginated list of reports with optional filters."""
    query = db.query(Report)

    if status:
        query = query.filter(Report.status == status)
    if category:
        query = query.filter(Report.category == category)
    if min_priority:
        query = query.filter(Report.final_priority_score >= min_priority)
    if max_priority:
        query = query.filter(Report.final_priority_score <= max_priority)

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

    from sqlalchemy import func
    cat_counts = db.query(Report.category, func.count(Report.id)).group_by(Report.category).all()
    by_category = {cat.value: count for cat, count in cat_counts}

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