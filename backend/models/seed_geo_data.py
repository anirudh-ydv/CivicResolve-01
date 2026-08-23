"""
Seeds real critical infrastructure (hospitals, schools) and road segments
for San Francisco. Coordinates are real public landmark locations (approx.
building centroids from general knowledge), not arbitrary mock points.

This replaces risk_engine.py's old hardcoded CRITICAL_INFRASTRUCTURE dict
and lat/lon box heuristic with actual PostGIS-queryable rows.
"""
import sys, os

# Make imports work whether this is run as `python models/seed_geo_data.py`
# from the backend/ directory (both in Docker, where WORKDIR is /app, and
# locally) or invoked some other way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.database import SessionLocal
from models.report import CriticalInfrastructure, RoadSegment, RoadType
from geoalchemy2.shape import from_shape
from shapely.geometry import Point, LineString

db = SessionLocal()

# Real San Francisco hospitals (approximate real coordinates)
HOSPITALS = [
    ("UCSF Medical Center at Parnassus", 37.7629, -122.4577),
    ("Zuckerberg San Francisco General Hospital", 37.7563, -122.4064),
    ("CPMC Van Ness Campus", 37.7847, -122.4212),
    ("Saint Francis Memorial Hospital", 37.7887, -122.4159),
    ("Chinese Hospital", 37.7947, -122.4077),
]

# Real San Francisco public schools (approximate real coordinates)
SCHOOLS = [
    ("Lowell High School", 37.7361, -122.4726),
    ("Mission High School", 37.7639, -122.4276),
    ("Galileo High School", 37.8016, -122.4234),
    ("Marina Middle School", 37.8004, -122.4381),
    ("Alamo Elementary School", 37.7830, -122.4586),
]

# Real San Francisco streets, roughly classified by real hierarchy.
# Coordinates are approximate real street endpoints, not fabricated shapes.
ROAD_SEGMENTS = [
    # Arterial highways / major arterials
    ("US-101 (Van Ness Ave)", RoadType.ARTERIAL_HIGHWAY, [(-122.4213, 37.7994), (-122.4213, 37.7749)]),
    ("I-280 (Southbound)", RoadType.ARTERIAL_HIGHWAY, [(-122.4025, 37.7650), (-122.4180, 37.7250)]),
    ("Market Street (Downtown)", RoadType.ARTERIAL_HIGHWAY, [(-122.4194, 37.7749), (-122.4004, 37.7897)]),
    # Secondary roads
    ("Geary Boulevard", RoadType.SECONDARY, [(-122.4468, 37.7809), (-122.4094, 37.7849)]),
    ("19th Avenue", RoadType.SECONDARY, [(-122.4763, 37.7280), (-122.4763, 37.7749)]),
    ("Mission Street (Outer)", RoadType.SECONDARY, [(-122.4276, 37.7639), (-122.4090, 37.7490)]),
    # Residential
    ("Noe Street", RoadType.RESIDENTIAL, [(-122.4331, 37.7580), (-122.4331, 37.7680)]),
    ("Funston Avenue", RoadType.RESIDENTIAL, [(-122.4653, 37.7700), (-122.4653, 37.7800)]),
]


def seed():
    existing = db.query(CriticalInfrastructure).count()
    if existing > 0:
        print(f"critical_infrastructure already has {existing} rows, skipping seed.")
    else:
        for name, lat, lon in HOSPITALS:
            db.add(CriticalInfrastructure(
                name=name, facility_type="hospital",
                location=from_shape(Point(lon, lat), srid=4326),
            ))
        for name, lat, lon in SCHOOLS:
            db.add(CriticalInfrastructure(
                name=name, facility_type="school",
                location=from_shape(Point(lon, lat), srid=4326),
            ))
        db.commit()
        print(f"Seeded {len(HOSPITALS)} hospitals + {len(SCHOOLS)} schools.")

    existing_roads = db.query(RoadSegment).count()
    if existing_roads > 0:
        print(f"road_segments already has {existing_roads} rows, skipping seed.")
    else:
        for name, road_type, coords in ROAD_SEGMENTS:
            db.add(RoadSegment(
                name=name, road_type=road_type,
                geom=from_shape(LineString(coords), srid=4326),
            ))
        db.commit()
        print(f"Seeded {len(ROAD_SEGMENTS)} road segments.")


if __name__ == "__main__":
    seed()
    db.close()
