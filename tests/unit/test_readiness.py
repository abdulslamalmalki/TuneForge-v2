from app.domain import auth, readiness


def test_readiness_reports_schema_storage_and_first_run_state(db_session):
    result = readiness.check(db_session)

    assert result.status == "ready"
    assert result.database_revision == "0024"
    assert result.authentication_initialized is False
    assert result.checks == {
        "database": True,
        "schema": True,
        "uploads_storage": True,
        "evidence_storage": True,
    }


def test_readiness_reports_initialized_authentication(db_session):
    auth.create_first_administrator(
        db_session,
        username="owner",
        display_name="TuneForge Owner",
        password="a long readiness test password",
    )

    result = readiness.check(db_session)

    assert result.status == "ready"
    assert result.authentication_initialized is True
    assert all(result.checks.values())
