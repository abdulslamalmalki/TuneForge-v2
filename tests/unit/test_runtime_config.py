import pytest

from app import config


def test_production_config_requires_auth_secure_cookie_and_specific_hosts(monkeypatch):
    monkeypatch.setattr(config, "ENVIRONMENT", "production")
    monkeypatch.setattr(config, "AUTH_MODE", "disabled")
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        config.validate_runtime_config()

    monkeypatch.setattr(config, "AUTH_MODE", "required")
    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", False)
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        config.validate_runtime_config()

    monkeypatch.setattr(config, "AUTH_COOKIE_SECURE", True)
    monkeypatch.setattr(config, "TRUSTED_HOSTS", ("*",))
    with pytest.raises(RuntimeError, match="wildcard"):
        config.validate_runtime_config()

    monkeypatch.setattr(config, "TRUSTED_HOSTS", ("tuneforge.example",))
    config.validate_runtime_config()


def test_session_duration_is_bounded(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SESSION_HOURS", 0)
    with pytest.raises(RuntimeError, match="between 1 and 168"):
        config.validate_runtime_config()


def test_official_model_provider_requires_model_and_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_MODEL"):
        config.validate_runtime_config()

    monkeypatch.setattr(config, "LLM_MODEL", "gpt-test")
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        config.validate_runtime_config()


def test_compatible_model_provider_requires_safe_base_url(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr(config, "LLM_MODEL", "local-model")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "http://model.example/v1")
    with pytest.raises(RuntimeError, match="loopback"):
        config.validate_runtime_config()

    monkeypatch.setattr(config, "LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    config.validate_runtime_config()
