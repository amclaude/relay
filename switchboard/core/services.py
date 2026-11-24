"""Shared services passed to the bot, the web app and every feature.

One `Services` object is built at start-up. The Telegram application and
the FastAPI app both keep a reference to it. This avoids global state and
makes the code easy to test with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from switchboard.core.config import Settings
from switchboard.core.crypto import TokenCipher
from switchboard.core.db import Database


@dataclass(frozen=True)
class Services:
    """Everything a handler needs to do its work."""

    settings: Settings
    db: Database
    cipher: TokenCipher


def build_services(settings: Settings) -> Services:
    """Create the database (and its tables) and the cipher from settings."""
    db = Database(settings.db_path)
    db.create_all()
    return Services(settings=settings, db=db, cipher=TokenCipher(settings.fernet_key))
