import json
import os
import zipfile
from types import SimpleNamespace

from app import config
from app.domain import release_readiness


def test_release_report_separates_product_and_deployment_gates(db_session):
    report = release_readiness.build_report(db_session)
    checks = {check.check_id: check for check in report.checks}

    assert report.report_version == "1.0"
    assert report.product_status == "hold"
    assert report.deployment_status == "hold"
    assert checks["provider-free-core"].passed is True
    assert checks["administrator"].passed is False
    assert checks["production-configuration"].passed is False
    assert checks["recovery-checkpoint"].passed is False


def test_release_report_can_reach_ready_without_a_model_provider(db_session, monkeypatch):
    monkeypatch.setattr(
        release_readiness.readiness,
        "check",
        lambda _session: SimpleNamespace(
            status="ready",
            authentication_initialized=True,
            checks={"database": True, "schema": True, "storage": True},
        ),
    )
    monkeypatch.setattr(
        release_readiness.am5_coverage,
        "build_report",
        lambda _session: SimpleNamespace(
            summary=SimpleNamespace(expansion_status="ready"),
            gates=(SimpleNamespace(passed=True),) * 5,
        ),
    )
    monkeypatch.setattr(
        release_readiness.am5_question_acceptance,
        "build_report",
        lambda _session: SimpleNamespace(
            summary=SimpleNamespace(
                acceptance_status="ready",
                passing_question_count=12,
                total_question_count=12,
            )
        ),
    )
    monkeypatch.setattr(release_readiness, "_failed_extraction_count", lambda _session: 0)
    monkeypatch.setattr(release_readiness, "_active_administrator_count", lambda _session: 1)
    monkeypatch.setattr(
        release_readiness.public_library, "approved_source_count", lambda _session: 1
    )
    monkeypatch.setattr(release_readiness.public_library, "canonical_count", lambda _session: 1)
    monkeypatch.setattr(
        release_readiness,
        "_recovery_checkpoint",
        lambda *_args: release_readiness.RecoveryCheckpoint(True, "Current checkpoint."),
    )
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "TRUSTED_HOSTS", ("tuneforge.example",))

    report = release_readiness.build_report(db_session)

    assert report.product_status == "ready"
    assert report.deployment_status == "ready"
    assert report.product_passed_count == report.product_required_count == 5
    assert report.deployment_passed_count == report.deployment_required_count == 9


def test_recovery_checkpoint_requires_a_current_tuneforge_manifest(tmp_path):
    data_dir = tmp_path / "data"
    backups = data_dir / "backups"
    backups.mkdir(parents=True)
    database = data_dir / "knowledge_builder.sqlite3"
    database.write_bytes(b"database")
    archive = backups / "tuneforge-test.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("database/knowledge_builder.sqlite3", b"database")
        output.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": "tuneforge-data-backup",
                    "format_version": 1,
                    "database_revision": config.EXPECTED_DATABASE_REVISION,
                    "sessions_included": False,
                    "created_at": "2026-07-19T00:00:00+00:00",
                    "app_version": config.APP_VERSION,
                    "files": [
                        {
                            "path": "database/knowledge_builder.sqlite3",
                            "size_bytes": 8,
                            "sha256": "a" * 64,
                        }
                    ],
                }
            ),
        )

    result = release_readiness._recovery_checkpoint(backups, database)

    assert result.passed is True
    assert result.archive_name == archive.name
    archive_mtime = archive.stat().st_mtime
    os.utime(database, (archive_mtime + 5, archive_mtime + 5))
    stale = release_readiness._recovery_checkpoint(backups, database)
    assert stale.passed is False
    assert "predates" in stale.detail
