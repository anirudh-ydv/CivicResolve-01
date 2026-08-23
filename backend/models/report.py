import enum
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Enum, Text, Index, Boolean
)
from geoalchemy2 import Geography
from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from .database import Base


class IssueCategory(str, enum.Enum):
    POTHOLE = "pothole"
    BROKEN_STREETLIGHT = "broken_streetlight"
    GRAFFITI = "graffiti"
    ILLEGAL_DUMPING = "illegal_dumping"
    CRACKED_SIDEWALK = "cracked_sidewalk"
    DAMAGED_SIGN = "damaged_sign"
    OTHER = "other"
    # Not a real infrastructure category - means "the model wasn't
    # confident enough to guess" (see ai/model_pipeline.py predict()).
    # Distinct from OTHER, which means "confidently not an issue".
    UNCLASSIFIED = "unclassified"


class ReportStatus(str, enum.Enum):
    # Every new report starts here - it does NOT appear as an actionable
    # dispatch item until an admin confirms or corrects the AI's
    # category/severity via PATCH /api/reports/{id}/review or /confirm
    # (see app/main.py). This is distinct from OPEN.
    PENDING_REVIEW = "pending_review"
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class RoadType(str, enum.Enum):
    ARTERIAL_HIGHWAY = "arterial_highway"
    SECONDARY = "secondary"
    RESIDENTIAL = "residential"
    UNKNOWN = "unknown"


class Report(Base):
    __tablename__ = "reports"

    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id = Column(String(64), nullable=False, index=True)
    category = Column(Enum(IssueCategory), nullable=False, index=True)

    # Raw AI confidence (0-100) for the category prediction, and whether
    # it fell below the gating threshold (see ai/model_pipeline.py). Both
    # persisted so the admin UI can badge low-confidence/unclassified
    # reports (Part 2.3) without re-running inference.
    ai_confidence = Column(Float, nullable=True)
    requires_manual_review = Column(Boolean, nullable=False, default=False, index=True)

    # Visual severity from CV model (1-10)
    visual_severity_score = Column(Float, nullable=False)
    
    # Road hierarchy weight (Arterial=10, Secondary=7, Residential=4)
    road_type = Column(Enum(RoadType), nullable=False, default=RoadType.UNKNOWN)
    road_type_multiplier = Column(Float, nullable=False)
    
    # Critical proximity flag (within 200m of hospital/school)
    critical_proximity_flag = Column(Boolean, nullable=False, default=False)
    critical_proximity_score = Column(Float, nullable=False, default=0)
    
    # Composite priority score: (Visual * 0.5) + (Road * 0.3) + (Proximity * 0.2)
    final_priority_score = Column(Float, nullable=False, index=True)
    
    # Real PostGIS geography column (SRID 4326 = WGS84 lat/lon). This is
    # the actual storage - there are no separate float lat/lng columns
    # (per the Part 3 spec: "Report location should use a real
    # geography(Point, 4326) column, not separate float lat/lng columns").
    # Geography (not Geometry) is used so distance/proximity queries in
    # risk_engine.py operate in real meters on a spherical model without
    # needing an explicit ::geography cast at every call site.
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)

    description = Column(Text, nullable=True)
    status = Column(Enum(ReportStatus), default=ReportStatus.PENDING_REVIEW, nullable=False, index=True)
    image_path = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_reports_status_priority", "status", "final_priority_score"),
        Index("ix_reports_created_at_desc", "created_at"),
        Index("ix_reports_location_gist", "location", postgresql_using="gist"),
    )

    def __init__(self, latitude: float = None, longitude: float = None, location=None, **kwargs):
        """Accepts latitude/longitude at construction time (matching the
        existing call site in app/main.py) and converts them into the real
        `location` geography column - there's no separate lat/lng storage
        to keep in sync."""
        super().__init__(**kwargs)
        if location is not None:
            self.location = location
        elif latitude is not None and longitude is not None:
            self.location = from_shape(Point(longitude, latitude), srid=4326)

    @property
    def latitude(self) -> Optional[float]:
        if self.location is None:
            return None
        return to_shape(self.location).y

    @property
    def longitude(self) -> Optional[float]:
        if self.location is None:
            return None
        return to_shape(self.location).x

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "category": self.category.value if self.category else None,
            "ai_confidence": self.ai_confidence,
            "requires_manual_review": self.requires_manual_review,
            "visual_severity_score": self.visual_severity_score,
            "road_type": self.road_type.value if self.road_type else None,
            "road_type_multiplier": self.road_type_multiplier,
            "critical_proximity_flag": self.critical_proximity_flag,
            "critical_proximity_score": self.critical_proximity_score,
            "final_priority_score": round(self.final_priority_score, 2),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "description": self.description,
            "status": self.status.value if self.status else None,
            "image_path": self.image_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def to_geojson_feature(self):
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [self.longitude, self.latitude],
            },
            "properties": {
                "id": self.id,
                "category": self.category.value if self.category else None,
                "visual_severity_score": self.visual_severity_score,
                "road_type": self.road_type.value if self.road_type else None,
                "road_type_multiplier": self.road_type_multiplier,
                "critical_proximity_flag": self.critical_proximity_flag,
                "final_priority_score": round(self.final_priority_score, 2),
                "status": self.status.value if self.status else None,
                "description": self.description,
                "created_at": self.created_at.isoformat() if self.created_at else None,
            },
        }




class CriticalInfrastructure(Base):
    """Real PostGIS-backed critical infrastructure (hospitals, schools)
    used by the risk engine's proximity check. Replaces the hardcoded
    Python dict of mock coordinates that used to live in risk_engine.py."""

    __tablename__ = "critical_infrastructure"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    facility_type = Column(String(32), nullable=False, index=True)  # 'hospital' | 'school'
    location = Column(Geography(geometry_type="POINT", srid=4326), nullable=False)

    __table_args__ = (
        Index("ix_critical_infra_location_gist", "location", postgresql_using="gist"),
    )


class RoadSegment(Base):
    """Real PostGIS-backed road hierarchy segments used by the risk
    engine's road-type classification. Replaces the hardcoded lat/lon
    box-heuristic that used to live in risk_engine.py. A report's road
    type is determined by the nearest segment within a search radius."""

    __tablename__ = "road_segments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    road_type = Column(Enum(RoadType), nullable=False, index=True)
    geom = Column(Geography(geometry_type="LINESTRING", srid=4326), nullable=False)

    __table_args__ = (
        Index("ix_road_segments_geom_gist", "geom", postgresql_using="gist"),
    )


class TrainingFeedback(Base):
    """
    Logs admin category/severity corrections so future retraining can pull
    from real usage instead of only the original bootstrap dataset (Part
    4). Created fresh - no such table existed before this migration.

    One row per admin review action (confirm-as-is counts too, since a
    confirmation is itself a useful signal that the AI got it right).
    """
    __tablename__ = "training_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(36), nullable=False, index=True)
    image_path = Column(String(512), nullable=False)

    ai_predicted_category = Column(String(32), nullable=False)
    ai_predicted_severity = Column(Float, nullable=False)

    admin_confirmed_category = Column(Enum(IssueCategory), nullable=False)
    admin_confirmed_severity = Column(Integer, nullable=False)

    # True if the admin's values exactly match what the AI predicted (a
    # confirmation), False if they changed something (a correction). Both
    # are useful training signal, but this makes it trivial to separate
    # "the model was right" from "the model was wrong" when auditing.
    was_correction = Column(Boolean, nullable=False, default=False)

    admin_username = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "report_id": self.report_id,
            "image_path": self.image_path,
            "ai_predicted_category": self.ai_predicted_category,
            "ai_predicted_severity": self.ai_predicted_severity,
            "admin_confirmed_category": self.admin_confirmed_category.value if self.admin_confirmed_category else None,
            "admin_confirmed_severity": self.admin_confirmed_severity,
            "was_correction": self.was_correction,
            "admin_username": self.admin_username,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
