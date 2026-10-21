"""Configuration contract tests without files, credentials, or services."""

import importlib
import socket

import pytest
from pydantic import ValidationError

import hellas_dataops.config as config


def test_safe_defaults() -> None:
    settings = config.load_settings()

    assert settings.environment == "development"
    assert settings.log_level == "INFO"


def test_prefixed_environment_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELLAS_ENVIRONMENT", "test")
    monkeypatch.setenv("HELLAS_LOG_LEVEL", "DEBUG")

    settings = config.load_settings()

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"


def test_unprefixed_environment_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")

    assert config.load_settings().environment == "development"
    assert config.load_settings().log_level == "INFO"


def test_explicit_values_override_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HELLAS_ENVIRONMENT", "production")

    assert config.Settings(environment="test").environment == "test"


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("HELLAS_ENVIRONMENT", "staging", "environment"),
        ("HELLAS_ENVIRONMENT", "", "environment"),
        ("HELLAS_LOG_LEVEL", "verbose", "log_level"),
        ("HELLAS_LOG_LEVEL", "info", "log_level"),
    ],
)
def test_invalid_environment_values_fail(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError) as error:
        config.load_settings()

    assert error.value.errors(include_input=False)[0]["loc"] == (field,)


def test_unknown_constructor_setting_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        config.Settings(log_levle="DEBUG")


def test_text_validation_error_does_not_echo_input() -> None:
    with pytest.raises(ValidationError) as error:
        config.Settings(environment="synthetic-sensitive-value")

    assert "synthetic-sensitive-value" not in str(error.value)


def test_settings_are_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    first = config.load_settings()
    monkeypatch.setenv("HELLAS_LOG_LEVEL", "ERROR")
    second = config.load_settings()

    assert first.log_level == "INFO"
    assert second.log_level == "ERROR"
    assert first is not second


def test_module_import_does_not_validate_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HELLAS_ENVIRONMENT", "invalid-on-purpose")

    importlib.reload(config)

    with pytest.raises(ValidationError):
        config.load_settings()


def test_network_guard_rejects_connection() -> None:
    with pytest.raises(AssertionError, match="Network access is disabled"):
        socket.create_connection(("127.0.0.1", 1))
