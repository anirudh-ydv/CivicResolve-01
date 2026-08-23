"""
Unit-level test for the Report model's default status.

This is deliberately NOT an HTTP integration test (see test_integration.py
for those) - it talks to the database directly through the ORM so it
actually exercises the Column(default=...) behavior, which only applies at
flush/INSERT time and would not be meaningfully tested by just constructing
a Python object and reading the attribute back before a commit.

Requires the same live Postgres/PostGIS instance as test_integration.py.
Run with:

    pytest tests/test_report_model.py -v
"""
import pytest
from models.database import SessionLocal
from models.report import Report, ReportStatus


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_report_created_without_explicit_status_defaults_to_pending_review(db):
    """
    Regression test for the bug found in review: the status column used to
    default to ReportStatus.OPEN, which silently bypassed the human-review
    requirement for any code path that didn't set status explicitly. The
    submit endpoint always sets it explicitly and was never affected, but
    the column-level default itself was wrong. This test creates a Report
    the same way a future script/seed/endpoint might - without passing
    status - and asserts the safe default actually wins.
    """
    report = Report(
        latitude=37.7749,
        longitude=-122.4194,
        description="unit test report - no status passed",
        category="pothole",
        user_id="unit-test-user",
        # Required (nullable=False) columns unrelated to what this test is
        # checking - filled with valid placeholder values so the flush
        # doesn't fail on an unrelated NOT NULL constraint and mask the
        # actual assertion below.
        visual_severity_score=1.0,
        road_type_multiplier=4.0,
        final_priority_score=1.0,
    )
    # status is intentionally NOT set here - this is the whole point of the test
    db.add(report)
    db.flush()  # triggers the Column(default=...) resolution without a full commit

    assert report.status == ReportStatus.PENDING_REVIEW, (
        f"expected a report created without an explicit status to default to "
        f"pending_review, got {report.status!r} instead - the column-level "
        f"default in models/report.py may have regressed"
    )
