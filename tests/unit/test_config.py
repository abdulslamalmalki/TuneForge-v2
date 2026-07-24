from app import config


def test_runtime_paths_derive_from_data_dir():
    # DATA_DIR may be relocated via KNOWLEDGE_BUILDER_DATA_DIR (tests use a
    # temporary directory); everything else must derive from it.
    assert config.UPLOADS_DIR.parent == config.DATA_DIR
    assert config.DATABASE_PATH.parent == config.DATA_DIR
    assert config.DATABASE_URL == f"sqlite:///{config.DATABASE_PATH}"
