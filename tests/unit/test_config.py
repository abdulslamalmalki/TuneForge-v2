import pytest

from app import config


def test_runtime_paths_derive_from_data_dir():
    # DATA_DIR may be relocated via KNOWLEDGE_BUILDER_DATA_DIR (tests use a
    # temporary directory); everything else must derive from it.
    assert config.UPLOADS_DIR.parent == config.DATA_DIR
    assert config.DATABASE_PATH.parent == config.DATA_DIR
    assert config.DATABASE_URL == f"sqlite:///{config.DATABASE_PATH}"


@pytest.mark.parametrize(
    "value",
    (
        "http://search.example/",
        "https://user:secret@search.example/",
        "https://127.0.0.1/",
        "https://169.254.169.254/latest/meta-data/",
        "https://search.example:444/",
        "https://search.example/?target=internal",
    ),
)
def test_runtime_rejects_unsafe_search_service_endpoints(monkeypatch, value):
    monkeypatch.setattr(config, "PROVENANCE_SEARCH_ENDPOINT", value)
    with pytest.raises(RuntimeError, match="PROVENANCE_SEARCH_ENDPOINT"):
        config.validate_runtime_config()


@pytest.mark.parametrize("value", ("", "10", "0/60", "5/0", "many/60", "5/999999"))
def test_rate_limit_policy_parser_rejects_invalid_values(value):
    with pytest.raises(RuntimeError, match="RATE_LIMIT_TEST"):
        config.parse_rate_limit_policy(value, "TUNEFORGE_RATE_LIMIT_TEST")


def test_rate_limit_policy_parser_returns_limit_and_window():
    assert config.parse_rate_limit_policy("12/300", "test") == (12, 300)


def test_production_requires_rate_limiting(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", False)
    with pytest.raises(RuntimeError, match="RATE_LIMIT_ENABLED=1"):
        config.validate_runtime_config()


def test_production_requires_wal(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config, "SQLITE_JOURNAL_MODE", "delete")
    with pytest.raises(RuntimeError, match="SQLITE_JOURNAL_MODE=wal"):
        config.validate_runtime_config()
