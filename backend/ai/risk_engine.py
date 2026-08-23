"""
CivicResolve Composite Risk Engine

Calculates final priority score using:
Final Risk Score = (Visual Severity * 0.5) + (Road Hierarchy Weight * 0.3) + (Critical Proximity * 0.2)

Where:
- Visual Severity (1-10): Derived from computer vision model
- Road Hierarchy Weight: Arterial Highway=10, Secondary=7, Residential=4, Unknown=5
- Critical Proximity: Within 200m of hospital/school = 10, else 0

Road type and critical proximity are now backed by real PostGIS spatial
queries against the critical_infrastructure and road_segments tables
(see models/report.py, models/seed_geo_data.py) - not hardcoded Python
dicts or lat/lon box heuristics. This is the Part 3 migration; the old
mock implementation is preserved nowhere else in this file.
"""

from typing import Tuple, Dict, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func
from geoalchemy2.elements import WKTElement

from models.report import RoadType, CriticalInfrastructure, RoadSegment, Report, IssueCategory, ReportStatus

# Road type to weight mapping
ROAD_TYPE_WEIGHTS = {
    RoadType.ARTERIAL_HIGHWAY: 10.0,
    RoadType.SECONDARY: 7.0,
    RoadType.RESIDENTIAL: 4.0,
    RoadType.UNKNOWN: 5.0,
}

# Weight constants for composite score
VISUAL_WEIGHT = 0.5
ROAD_WEIGHT = 0.3
PROXIMITY_WEIGHT = 0.2

# Max distance (meters) to consider a report "on" a given road segment for
# hierarchy classification purposes.
ROAD_SEARCH_RADIUS_METERS = 100.0

# Two reports of the same category within this distance and both still
# open/in_progress are treated as likely duplicates (e.g. the same pothole
# reported by five different residents). 20m chosen to match the exact
# 19m/20m/21m boundary behavior verified in tests/test_integration.py.
DUPLICATE_SEARCH_RADIUS_METERS = 20.0


def _point(latitude: float, longitude: float) -> WKTElement:
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def classify_road_type(db: Session, latitude: float, longitude: float) -> Tuple[RoadType, float]:
    """
    Real PostGIS road type classification: finds the nearest road segment
    within ROAD_SEARCH_RADIUS_METERS via ST_Distance on the geography
    columns (real meters, accounts for Earth's curvature - not a naive
    planar distance on raw lat/lon degrees). Falls back to UNKNOWN if
    nothing is within range - a report far from any seeded segment should
    not be silently misclassified as residential (the old mock code's bug).
    """
    pt = _point(latitude, longitude)
    result = (
        db.query(
            RoadSegment.road_type,
            func.ST_Distance(RoadSegment.geom, pt).label("dist"),
        )
        .order_by("dist")
        .first()
    )

    if result is None or result.dist > ROAD_SEARCH_RADIUS_METERS:
        return RoadType.UNKNOWN, ROAD_TYPE_WEIGHTS[RoadType.UNKNOWN]

    return result.road_type, ROAD_TYPE_WEIGHTS[result.road_type]


def check_critical_proximity(
    db: Session, latitude: float, longitude: float, radius_meters: float = 200.0
) -> Tuple[bool, float, Optional[str]]:
    """
    Real PostGIS proximity check via ST_DWithin on the geography column
    (radius_meters is real meters, not degrees). Returns the nearest
    matching facility's name too, for admin-facing transparency about
    *why* a report was flagged as high priority.
    """
    pt = _point(latitude, longitude)

    match = (
        db.query(
            CriticalInfrastructure.name,
            func.ST_Distance(CriticalInfrastructure.location, pt).label("dist"),
        )
        .filter(func.ST_DWithin(CriticalInfrastructure.location, pt, radius_meters))
        .order_by("dist")
        .first()
    )

    if match is not None:
        return True, 10.0, match.name

    return False, 0.0, None


def find_possible_duplicates(
    db: Session,
    category: str,
    latitude: float,
    longitude: float,
    radius_meters: float = DUPLICATE_SEARCH_RADIUS_METERS,
) -> List[Dict]:
    """
    Real PostGIS duplicate detection: finds existing open/in-progress
    reports of the *same category* within radius_meters, via ST_DWithin.
    No prior implementation of this existed in the codebase to migrate
    from (searched; nothing found) - this is new functionality fulfilling
    the Part 3 spec's requirement, built PostGIS-native from the start
    rather than a Python-side haversine loop.

    Returns a list of {report_id, distance_meters, created_at} for any
    matches, nearest first, so the submit endpoint can flag likely
    duplicates for admin attention instead of silently creating a
    redundant entry.
    """
    pt = _point(latitude, longitude)

    try:
        category_enum = IssueCategory(category)
    except ValueError:
        return []

    matches = (
        db.query(
            Report.id,
            Report.created_at,
            func.ST_Distance(Report.location, pt).label("dist"),
        )
        .filter(
            Report.category == category_enum,
            Report.status.in_([ReportStatus.PENDING_REVIEW, ReportStatus.OPEN, ReportStatus.IN_PROGRESS]),
            func.ST_DWithin(Report.location, pt, radius_meters),
        )
        .order_by("dist")
        .limit(5)
        .all()
    )

    return [
        {
            "report_id": m.id,
            "distance_meters": round(m.dist, 1),
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in matches
    ]


def calculate_composite_risk_score(
    db: Session,
    visual_severity: float,
    latitude: float,
    longitude: float,
    category: Optional[str] = None,
) -> Dict:
    """
    Calculate the final composite priority score using real PostGIS
    spatial queries for road hierarchy and critical-infrastructure
    proximity, and (if a category is given) surface likely duplicate
    reports nearby.

    Formula: Final Risk Score = (Visual Severity * 0.5) + (Road Hierarchy Weight * 0.3) + (Critical Proximity * 0.2)

    Args:
        db: active SQLAlchemy session (needed for the spatial queries)
        visual_severity: Score from CV model (1-10)
        latitude: Report latitude
        longitude: Report longitude
        category: issue category string, used for duplicate detection

    Returns:
        Dictionary with all component scores, final priority score, and
        any likely duplicate reports found nearby.
    """
    road_type, road_weight = classify_road_type(db, latitude, longitude)
    proximity_flag, proximity_score, nearest_facility = check_critical_proximity(db, latitude, longitude)

    final_score = (
        visual_severity * VISUAL_WEIGHT +
        road_weight * ROAD_WEIGHT +
        proximity_score * PROXIMITY_WEIGHT
    )
    final_score = max(1.0, min(10.0, final_score))

    possible_duplicates = (
        find_possible_duplicates(db, category, latitude, longitude) if category else []
    )

    return {
        "visual_severity_score": round(visual_severity, 2),
        "road_type": road_type.value,
        "road_type_multiplier": round(road_weight, 2),
        "critical_proximity_flag": proximity_flag,
        "critical_proximity_score": round(proximity_score, 2),
        "nearest_critical_facility": nearest_facility,
        "final_priority_score": round(final_score, 2),
        "possible_duplicates": possible_duplicates,
    }


# For backwards compatibility with existing predict_image function
def predict_image_with_risk(db: Session, image_bytes: bytes, latitude: float, longitude: float) -> Dict:
    """
    Combined inference + risk scoring for FastAPI endpoint.
    """
    from ai.model_pipeline import predict_image as cv_predict_image

    cv_result = cv_predict_image(image_bytes)
    visual_severity = cv_result.get("severity_score", 5)
    category = cv_result.get("category", "other")
    confidence = cv_result.get("confidence", 0.0)

    risk_result = calculate_composite_risk_score(db, visual_severity, latitude, longitude, category=category)

    return {
        "category": category,
        "confidence": confidence,
        **risk_result,
    }


