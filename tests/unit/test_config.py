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
