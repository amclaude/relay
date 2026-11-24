"""Tests for the FastAPI app: health route and Google OAuth callback.

Google and Telegram are never called. `exchange_code` is replaced with a
fake and `notify` records messages in a list.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from relay.core.config import OAUTH_CALLBACK_PATH
from relay.core.db import utcnow
from relay.core.sessions import create_oauth_state
from relay.features.gmail.oauth import GoogleConnection, OAuthExchangeError
from relay.web import app as web_app
from relay.web.app import create_app


@pytest.fixture
def sent() -> list[tuple[int, str]]:
    return []


@pytest.fixture
def client(services, sent) -> TestClient:
    async def notify(telegram_id: int, text: str) -> None:
        sent.append((telegram_id, text))

    return TestClient(create_app(services, notify))


@pytest.fixture
def fake_exchange(monkeypatch):
    """Replace the Google code exchange. Records the codes it receives."""
    calls: list[str] = []

    def fake(settings, code, *, service=None):
        calls.append(code)
        return GoogleConnection(email="user@example.com", refresh_token="refresh-secret")

    monkeypatch.setattr(web_app, "exchange_code", fake)
    return calls


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_callback_connects_account(client, services, sent, fake_exchange):
    state = create_oauth_state(services.db, 42)
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "good-code", "state": state})

    assert response.status_code == 200
    assert fake_exchange == ["good-code"]
    account = services.db.get_gmail_account(42)
    assert account.email == "user@example.com"
    assert services.cipher.decrypt(account.encrypted_refresh_token) == "refresh-secret"
    assert len(sent) == 1
    assert sent[0][0] == 42
    assert "user@example.com" in sent[0][1]


def test_refresh_token_is_not_stored_in_plain_text(client, services, fake_exchange):
    state = create_oauth_state(services.db, 42)
    client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    assert "refresh-secret" not in services.db.get_gmail_account(42).encrypted_refresh_token


def test_callback_rejects_missing_state(client, fake_exchange):
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "c"})
    assert response.status_code == 400
    assert fake_exchange == []


def test_callback_rejects_unknown_state(client, services, sent, fake_exchange):
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert fake_exchange == []
    assert sent == []
    assert services.db.get_gmail_account(42) is None


def test_callback_rejects_expired_state(client, services, sent, fake_exchange):
    state = create_oauth_state(
        services.db, 42, ttl=timedelta(minutes=10), now=utcnow() - timedelta(hours=1)
    )
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    assert response.status_code == 400
    assert fake_exchange == []
    assert sent == []


def test_callback_state_is_single_use(client, services, fake_exchange):
    state = create_oauth_state(services.db, 42)
    first = client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    second = client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    assert first.status_code == 200
    assert second.status_code == 400
    assert fake_exchange == ["c"]


def test_callback_user_denied_access(client, services, sent, fake_exchange):
    state = create_oauth_state(services.db, 42)
    response = client.get(OAUTH_CALLBACK_PATH, params={"error": "access_denied", "state": state})
    assert response.status_code == 400
    assert fake_exchange == []
    assert services.db.get_gmail_account(42) is None
    assert len(sent) == 1
    assert "/connect" in sent[0][1]


def test_callback_exchange_failure(client, services, sent, monkeypatch):
    def failing(settings, code, *, service=None):
        raise OAuthExchangeError("rejected")

    monkeypatch.setattr(web_app, "exchange_code", failing)
    state = create_oauth_state(services.db, 42)
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "bad", "state": state})
    assert response.status_code == 400
    assert services.db.get_gmail_account(42) is None
    assert len(sent) == 1


def test_callback_survives_telegram_failure(services, fake_exchange):
    async def broken_notify(telegram_id: int, text: str) -> None:
        raise RuntimeError("telegram is down")

    client = TestClient(create_app(services, broken_notify))
    state = create_oauth_state(services.db, 42)
    response = client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    assert response.status_code == 200
    assert services.db.get_gmail_account(42) is not None


def test_callback_reconnect_keeps_alert_settings(client, services, fake_exchange):
    services.db.upsert_gmail_account(42, "old@example.com", "old-cipher")
    services.db.set_alerts(42, True)
    state = create_oauth_state(services.db, 42)
    client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    account = services.db.get_gmail_account(42)
    assert account.email == "user@example.com"
    assert account.alerts_enabled is True


def test_state_of_one_user_cannot_connect_another(client, services, fake_exchange):
    state = create_oauth_state(services.db, 1)
    client.get(OAUTH_CALLBACK_PATH, params={"code": "c", "state": state})
    assert services.db.get_gmail_account(1) is not None
    assert services.db.get_gmail_account(2) is None
