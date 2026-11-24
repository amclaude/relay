"""Shared test fixtures.

Tests use a file database in a temporary folder. The web app runs database
calls in worker threads, and a plain in-memory SQLite database is not
shared between threads.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from relay.core.config import Settings
from relay.core.services import Services, build_services


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="test-bot-token",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        fernet_key=Fernet.generate_key().decode(),
        db_path=tmp_path / "test.db",
    )


@pytest.fixture
def services(settings: Settings) -> Services:
    return build_services(settings)
