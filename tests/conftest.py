"""Offline protection for the default unit test suite."""

import os
import socket
from typing import NoReturn

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail fast if a unit test accidentally attempts network access."""

    def denied(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Network access is disabled in offline unit tests")

    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep configuration tests independent of the developer's shell settings."""
    for name in tuple(os.environ):
        if name.upper().startswith("HELLAS_"):
            monkeypatch.delenv(name)
