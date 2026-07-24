from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import ExtractionRun, Manual
from app.db.session import SessionLocal
from app.main import create_app


def test_startup_creates_missing_extraction_runs():
    # A manual persisted without a run, as M1 uploads were.
    with SessionLocal() as session:
        manual = Manual(
            original_filename="legacy.pdf",
            stored_filename="manual_legacy_startup.pdf",
            sha256="c" * 64,
            file_size_bytes=5,
            page_count=1,
            document_title="legacy.pdf",
            retrieved_at="2026-01-01T00:00:00+00:00",
            language="en",
            created_at="2026-01-01T00:00:00+00:00",
        )
        session.add(manual)
        session.commit()
        manual_id = manual.id

    # Entering the client context runs the app's startup lifespan.
    with TestClient(create_app()):
        pass

    with SessionLocal() as session:
        runs = list(
            session.scalars(select(ExtractionRun).where(ExtractionRun.manual_id == manual_id))
        )
    assert len(runs) == 1
    assert runs[0].status == "pending"
    assert runs[0].parser_version is None


def test_startup_fails_interrupted_running_runs():
    with SessionLocal() as session:
        manual = Manual(
            original_filename="interrupted.pdf",
            stored_filename="manual_interrupted_startup.pdf",
            sha256="e" * 64,
            file_size_bytes=5,
            page_count=1,
            document_title="interrupted.pdf",
            retrieved_at="2026-01-01T00:00:00+00:00",
            language="en",
            created_at="2026-01-01T00:00:00+00:00",
        )
        session.add(manual)
        session.flush()
        session.add(
            ExtractionRun(
                manual_id=manual.id,
                status="running",
                started_at="2026-01-01T00:00:01+00:00",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
        session.commit()
        manual_id = manual.id

    with TestClient(create_app()):
        pass  # startup lifespan runs the cleanup

    with SessionLocal() as session:
        run = session.scalars(
            select(ExtractionRun).where(ExtractionRun.manual_id == manual_id)
        ).one()
        assert run.status == "failed"
        assert "interrupted" in run.error_message
        assert run.finished_at is not None
