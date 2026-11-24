"""Per-user sessions and OAuth state handling.

A "session" in Relay is the row for a Telegram user in the `users`
table. It is created the first time the user talks to the bot.

The OAuth `state` value links a Google consent screen back to the Telegram
user who asked for it. It protects the callback from forged requests:

    * it is random and hard to guess,
    * it expires after a short time,
    * it can be used once only.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from relay.core.db import Database, User, utcnow

STATE_TTL = timedelta(minutes=10)


class OAuthStateError(Exception):
    """Raised when an OAuth state value cannot be accepted."""


class UnknownStateError(OAuthStateError):
    """The state value is not known, or it was already used."""


class ExpiredStateError(OAuthStateError):
    """The state value is known, but it is too old."""


def get_or_create_session(db: Database, telegram_id: int) -> User:
    """Return the user row for a Telegram user. Create it on first contact."""
    return db.ensure_user(telegram_id)


def create_oauth_state(
    db: Database,
    telegram_id: int,
    *,
    ttl: timedelta = STATE_TTL,
    now: datetime | None = None,
) -> str:
    """Create a random single-use state value for a Telegram user."""
    now = now or utcnow()
    db.purge_expired_states(now)
    state = secrets.token_urlsafe(32)
    db.save_oauth_state(state, telegram_id, now + ttl)
    return state


def consume_oauth_state(
    db: Database, state: str, *, now: datetime | None = None
) -> int:
    """Check a state value and return the Telegram ID that owns it.

    The value is deleted in all cases, so a second call with the same
    value fails with UnknownStateError.

    Raises:
        UnknownStateError: the value is not stored.
        ExpiredStateError: the value is stored but past its expiry.
    """
    now = now or utcnow()
    found = db.pop_oauth_state(state)
    if found is None:
        raise UnknownStateError("Unknown or already used state value.")
    telegram_id, expires_at = found
    if expires_at is None or expires_at < now:
        raise ExpiredStateError("State value has expired.")
    return telegram_id
