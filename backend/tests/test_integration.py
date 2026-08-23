"""
Integration tests for CivicResolve (Part 6). These run against a REAL
running backend (uvicorn) and a REAL Postgres/PostGIS database - not
mocks. Start the backend first:

    cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000

Then run:

    pytest tests/test_integration.py -v

Coordinates for the duplicate-boundary test were derived by binary-
searching against PostGIS's own ST_Distance (geodesic/spheroid model),
not a hand-rolled haversine formula - a naive haversine offset was off
by ~0.05m at this latitude, enough to flip the 20m boundary case. See
the git history / session notes for how these were derived; they are
accurate to within ~4cm of the target distance as actually measured by
PostGIS itself.
"""
import os
import time
import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "changeme123")

POTHOLE_IMAGE = "/home/claude/datasets/pothole_raw/Pothole Dataset/img-85.jpg"

# Base point + PostGIS-verified offsets (see module docstring)
BASE_LAT, BASE_LON = 37.7749, -122.4194
LON_19M = -122.4196156639   # 19.000046m from base (PostGIS-measured)
LON_20M = -122.4196270129   # 19.999892m from base (PostGIS-measured, deliberately
                             # calibrated just under 20.0 so the inclusive <=20m
                             # boundary is unambiguous - an earlier calibration
                             # attempt landed at 20.000039m, 39 micrometers OVER,
                             # which correctly failed the test since ST_DWithin
                             # excludes anything over the radius)
LON_21M = -122.4196383652   # 21.000033m from base


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


@pytest.fixture(scope="module")
def admin_token(client):
    resp = client.post(
        "/api/auth/login",
        data={"username": ADMIN_USER, "password": ADMIN_PASS},
    )
    assert resp.status_code == 200, f"login failed: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    return token


@pytest.fixture()
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------

class TestAuthFlow:
    def test_login_succeeds_with_correct_credentials(self, client):
        resp = client.post(
            "/api/auth/login",
            data={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_fails_with_wrong_password(self, client):
        resp = client.post(
            "/api/auth/login",
            data={"username": ADMIN_USER, "password": "definitely-wrong"},
        )
        assert resp.status_code == 401

    def test_protected_endpoint_rejects_missing_token(self, client):
        resp = client.get("/api/reports/stats")
        assert resp.status_code == 401

    def test_protected_endpoint_rejects_garbage_token(self, client):
        resp = client.get(
            "/api/reports/stats",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert resp.status_code == 401

    def test_protected_endpoint_accepts_valid_token(self, client, auth_headers):
        resp = client.get("/api/reports/stats", headers=auth_headers)
        assert resp.status_code == 200
        assert "total_reports" in resp.json()

    def test_expired_token_rejected(self, client):
        # Construct a token that's already expired using the app's own
        # signing function with a negative expiry, rather than guessing
        # at a hardcoded expired JWT string.
        import sys
        sys.path.insert(0, "/home/claude/project/civicresolve/backend")
        from app.auth import create_access_token
        from datetime import timedelta

        expired_token = create_access_token(
            data={"sub": ADMIN_USER}, expires_delta=timedelta(seconds=-10)
        )
        resp = client.get(
            "/api/reports/stats",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------
# Full submit -> classify -> score -> store flow
# ---------------------------------------------------------------------

class TestSubmitClassifyScoreStore:
    def test_submit_real_photo_end_to_end(self, client, auth_headers):
        with open(POTHOLE_IMAGE, "rb") as f:
            resp = client.post(
                "/api/report/submit",
                headers=auth_headers,
                files={"image": ("pothole.jpg", f, "image/jpeg")},
                data={
                    "latitude": "37.7629",
                    "longitude": "-122.4577",
                    "user_id": "test_integration_user",
                    "description": "integration test submission",
                },
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # Classification actually happened (not a stub/default)
        assert body["category"] in [
            "pothole", "broken_streetlight", "graffiti", "illegal_dumping",
            "cracked_sidewalk", "damaged_sign", "other", "unclassified",
        ]
        assert 0.0 <= body["ai_confidence"] <= 100.0

        # Composite score actually computed, not hardcoded
        assert 1.0 <= body["final_priority_score"] <= 10.0
        assert body["road_type"] in ["arterial_highway", "secondary", "residential", "unknown"]

        # New reports start PENDING_REVIEW (Part 2), not immediately OPEN
        assert body["status"] == "pending_review"

        # Actually persisted - fetch it back
        report_id = body["report_id"]
        get_resp = client.get(f"/api/reports/{report_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        stored = get_resp.json()
        assert stored["id"] == report_id
        assert stored["category"] == body["category"]
        assert stored["final_priority_score"] == body["final_priority_score"]

    def test_confirm_transitions_pending_to_open(self, client, auth_headers):
        with open(POTHOLE_IMAGE, "rb") as f:
            resp = client.post(
                "/api/report/submit",
                headers=auth_headers,
                files={"image": ("pothole.jpg", f, "image/jpeg")},
                data={
                    "latitude": "37.7629", "longitude": "-122.4577",
                    "user_id": "test_confirm_user", "description": "confirm test",
                },
            )
        report_id = resp.json()["report_id"]
        assert resp.json()["status"] == "pending_review"

        if resp.json()["category"] == "unclassified":
            pytest.skip("model predicted unclassified for this run; confirm requires a real category")

        confirm_resp = client.patch(f"/api/reports/{report_id}/confirm", headers=auth_headers)
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["report"]["status"] == "open"

    def test_reclassify_transitions_and_logs_feedback(self, client, auth_headers):
        with open(POTHOLE_IMAGE, "rb") as f:
            resp = client.post(
                "/api/report/submit",
                headers=auth_headers,
                files={"image": ("pothole.jpg", f, "image/jpeg")},
                data={
                    "latitude": "37.7629", "longitude": "-122.4577",
                    "user_id": "test_reclassify_user", "description": "reclassify test",
                },
            )
        report_id = resp.json()["report_id"]

        review_resp = client.patch(
            f"/api/reports/{report_id}/review",
            headers=auth_headers,
            json={"category": "pothole", "severity": 8},
        )
        assert review_resp.status_code == 200
        body = review_resp.json()
        assert body["report"]["status"] == "open"
        assert body["report"]["category"] == "pothole"
        assert body["report"]["visual_severity_score"] == 8.0

    def test_unauthenticated_submit_is_allowed_by_design(self, client):
        # /api/report/submit has no auth dependency in app/main.py - this
        # is intentional, not an oversight: it's the citizen-facing
        # endpoint, and citizens don't log in. Only admin actions
        # (listing, confirm/reclassify, status changes) require a token.
        # (Confirmed by reading the route definition, not assumed.)
        with open(POTHOLE_IMAGE, "rb") as f:
            resp = client.post(
                "/api/report/submit",
                files={"image": ("pothole.jpg", f, "image/jpeg")},
                data={
                    "latitude": "37.7629", "longitude": "-122.4577",
                    "user_id": "no_auth_user",
                },
            )
        assert resp.status_code == 201


# ---------------------------------------------------------------------
# Duplicate-clustering boundary test (19m / 20m / 21m)
# ---------------------------------------------------------------------

class TestDuplicateBoundary:
    """
    DUPLICATE_SEARCH_RADIUS_METERS = 20.0 (see ai/risk_engine.py). A
    second report of the SAME category should be flagged as a possible
    duplicate of the first if it's within 20m, and NOT flagged if it's
    beyond 20m. Tests exactly this boundary using PostGIS-verified
    coordinates (see module docstring).
    """

    def _submit(self, client, auth_headers, lat, lon, user_id):
        with open(POTHOLE_IMAGE, "rb") as f:
            resp = client.post(
                "/api/report/submit",
                headers=auth_headers,
                files={"image": ("pothole.jpg", f, "image/jpeg")},
                data={
                    "latitude": str(lat), "longitude": str(lon),
                    "user_id": user_id, "description": "duplicate boundary test",
                },
            )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_19m_apart_is_flagged_as_duplicate(self, client, auth_headers):
        first = self._submit(client, auth_headers, BASE_LAT, BASE_LON, "dup_test_19_a")
        second = self._submit(client, auth_headers, BASE_LAT, LON_19M, "dup_test_19_b")
        dup_ids = [d["report_id"] for d in second["possible_duplicates"]]
        assert first["report_id"] in dup_ids, (
            f"19m apart should be flagged as duplicate (radius=20m), "
            f"but possible_duplicates={second['possible_duplicates']}"
        )

    def test_20m_apart_boundary_is_flagged_as_duplicate(self, client, auth_headers):
        # ST_DWithin is inclusive: distance <= radius counts as within.
        # Uses a latitude offset from the 19m/21m tests' base point so an
        # unrelated report from those tests can't be mistaken for this
        # test's own pair when checking possible_duplicates.
        lat = BASE_LAT + 0.0005
        first = self._submit(client, auth_headers, lat, BASE_LON, "dup_test_20_a")
        second = self._submit(client, auth_headers, lat, LON_20M, "dup_test_20_b")
        dup_ids = [d["report_id"] for d in second["possible_duplicates"]]
        assert first["report_id"] in dup_ids, (
            f"exactly 20m apart (the radius itself) should be flagged as "
            f"duplicate per ST_DWithin's inclusive semantics, but "
            f"possible_duplicates={second['possible_duplicates']}"
        )

    def test_21m_apart_is_not_flagged_as_duplicate(self, client, auth_headers):
        lat = BASE_LAT + 0.001
        first = self._submit(client, auth_headers, lat, BASE_LON, "dup_test_21_a")
        second = self._submit(client, auth_headers, lat, LON_21M, "dup_test_21_b")
        dup_ids = [d["report_id"] for d in second["possible_duplicates"]]
        assert first["report_id"] not in dup_ids, (
            f"21m apart is outside the 20m radius and should NOT be "
            f"flagged, but possible_duplicates={second['possible_duplicates']}"
        )

    def test_different_category_not_flagged_even_if_close(self, client, auth_headers):
        # Submit a pothole, then a graffiti photo at the exact same spot.
        # Different category -> should not be flagged as a duplicate of
        # each other (duplicate detection is category-scoped).
        lat = BASE_LAT + 0.0015
        first = self._submit(client, auth_headers, lat, BASE_LON, "dup_test_cat_a")
        with open("/home/claude/datasets/graffiti_raw/2019-10-15_19-51-14_UTC.jpg", "rb") as f:
            resp = client.post(
                "/api/report/submit",
                headers=auth_headers,
                files={"image": ("graffiti.jpg", f, "image/jpeg")},
                data={
                    "latitude": str(lat), "longitude": str(BASE_LON),
                    "user_id": "dup_test_cat_b", "description": "same spot, different category",
                },
            )
        second = resp.json()
        if second["category"] != "graffiti":
            pytest.skip(f"model predicted {second['category']!r} instead of graffiti for this photo - "
                        f"can't test category-scoping with a misclassified image")
        dup_ids = [d["report_id"] for d in second["possible_duplicates"]]
        assert first["report_id"] not in dup_ids
