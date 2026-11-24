"""Tests for OAuth state handling."""

from __future__ import annotations

from datetime import timedelta

import pytest

from relay.core.db import utcnow
from relay.core.sessions import (
    STATE_TTL,
    ExpiredStateError,
    UnknownStateError,
    consume_oauth_state,
    create_oauth_state,
    get_or_create_session,
)


def test_state_is_random_and_long(services):
    first = create_oauth_state(services.db, 1)
    second = create_oauth_state(services.db, 1)
    assert first != second
    assert len(first) >= 32


def test_state_returns_owner(services):
    state = create_oauth_state(services.db, 12345)
    assert consume_oauth_state(services.db, state) == 12345


def test_state_is_single_use(services):
    state = create_oauth_state(services.db, 1)
    consume_oauth_state(services.db, state)
    with pytest.raises(UnknownStateError):
        consume_oauth_state(services.db, state)


def test_unknown_state_is_rejected(services):
    with pytest.raises(UnknownStateError):
        consume_oauth_state(services.db, "never-issued")


def test_expired_state_is_rejected(services):
    now = utcnow()
    state = create_oauth_state(services.db, 1, now=now)
    with pytest.raises(ExpiredStateError):
        consume_oauth_state(services.db, state, now=now + STATE_TTL + timedelta(seconds=1))


def test_expired_state_is_deleted(services):
    now = utcnow()
    state = create_oauth_state(services.db, 1, now=now)
    later = now + STATE_TTL + timedelta(seconds=1)
    with pytest.raises(ExpiredStateError):
        consume_oauth_state(services.db, state, now=later)
    with pytest.raises(UnknownStateError):
        consume_oauth_state(services.db, state, now=now)


def test_state_valid_just_before_expiry(services):
    now = utcnow()
    state = create_oauth_state(services.db, 7, now=now)
    assert consume_oauth_state(services.db, state, now=now + STATE_TTL - timedelta(seconds=1)) == 7


def test_creating_a_state_purges_expired_ones(services):
    old = utcnow() - timedelta(hours=1)
    stale = create_oauth_state(services.db, 1, now=old)
    create_oauth_state(services.db, 1)
    with pytest.raises(UnknownStateError):
        consume_oauth_state(services.db, stale, now=old)


def test_each_state_belongs_to_its_user(services):
    first = create_oauth_state(services.db, 1)
    second = create_oauth_state(services.db, 2)
    assert consume_oauth_state(services.db, second) == 2
    assert consume_oauth_state(services.db, first) == 1


def test_get_or_create_session_is_idempotent(services):
    first = get_or_create_session(services.db, 99)
    second = get_or_create_session(services.db, 99)
    assert first.telegram_id == second.telegram_id == 99
    assert first.created_at == second.created_at
