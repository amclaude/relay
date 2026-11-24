"""Database layer: SQLite through SQLAlchemy.

Tables:
    users           one row for each Telegram user
    gmail_accounts  one connected Gmail account for each Telegram user
    oauth_states    single-use OAuth state values with an expiry

The functions in this module are small and synchronous. Async callers
run them with `asyncio.to_thread` so the event loop does not block.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    TypeDecorator,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def utcnow() -> datetime:
    """Current time in UTC, timezone aware."""
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite drops time zones. Put UTC back on values read from the database."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class UTCDateTime(TypeDecorator):
    """A DateTime column that always returns timezone-aware UTC values.

    SQLite stores no time zone. Without this, values read back are naive,
    and `.timestamp()` on a naive value would use the server's local zone.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return _aware(value)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # JSON text. Holds per-user settings that are not tied to one feature.
    settings: Mapped[str] = mapped_column(Text, default="{}")


class GmailAccount(Base):
    __tablename__ = "gmail_accounts"

    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    email: Mapped[str] = mapped_column(String(320))
    # Fernet ciphertext. The plain refresh token is never stored.
    encrypted_refresh_token: Mapped[str] = mapped_column(Text)
    last_checked: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # JSON text. Example: {"query": "is:unread category:primary"}
    filters: Mapped[str] = mapped_column(Text, default="{}")


class OAuthState(Base):
    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, path: Path | str) -> None:
        if str(path) == ":memory:":
            url = "sqlite+pysqlite:///:memory:"
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite+pysqlite:///{path}"
        self.engine: Engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _enable_foreign_keys(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        """Create the tables if they do not exist."""
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Open a session. Commit on success, roll back on error."""
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- users -----------------------------------------------------------

    def ensure_user(self, telegram_id: int) -> User:
        """Return the user row. Create it on first contact."""
        with self.session() as s:
            user = s.get(User, telegram_id)
            if user is None:
                user = User(telegram_id=telegram_id, created_at=utcnow(), settings="{}")
                s.add(user)
            return user

    def get_user_settings(self, telegram_id: int) -> dict[str, Any]:
        with self.session() as s:
            user = s.get(User, telegram_id)
            return json.loads(user.settings) if user else {}

    def set_user_settings(self, telegram_id: int, settings: dict[str, Any]) -> None:
        self.ensure_user(telegram_id)
        with self.session() as s:
            s.get(User, telegram_id).settings = json.dumps(settings)

    # -- gmail accounts --------------------------------------------------

    def get_gmail_account(self, telegram_id: int) -> GmailAccount | None:
        with self.session() as s:
            return s.get(GmailAccount, telegram_id)

    def upsert_gmail_account(
        self, telegram_id: int, email: str, encrypted_refresh_token: str
    ) -> GmailAccount:
        """Store or replace the Gmail account for a user.

        A new connection keeps existing alert settings and filters.
        """
        self.ensure_user(telegram_id)
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is None:
                account = GmailAccount(
                    telegram_id=telegram_id,
                    email=email,
                    encrypted_refresh_token=encrypted_refresh_token,
                    last_checked=utcnow(),
                    alerts_enabled=False,
                    filters="{}",
                )
                s.add(account)
            else:
                account.email = email
                account.encrypted_refresh_token = encrypted_refresh_token
            return account

    def delete_gmail_account(self, telegram_id: int) -> bool:
        """Delete the account row. Return True if a row existed."""
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is None:
                return False
            s.delete(account)
            return True

    def set_alerts(self, telegram_id: int, enabled: bool) -> bool:
        """Turn alerts on or off. Return False if no account is connected."""
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is None:
                return False
            account.alerts_enabled = enabled
            if enabled:
                # Only notify about mail that arrives after this moment.
                account.last_checked = utcnow()
            return True

    def touch_last_checked(self, telegram_id: int, when: datetime | None = None) -> None:
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is not None:
                account.last_checked = when or utcnow()

    def list_alert_accounts(self) -> list[GmailAccount]:
        """All accounts that have alerts turned on."""
        with self.session() as s:
            return list(s.scalars(select(GmailAccount).where(GmailAccount.alerts_enabled.is_(True))))

    def get_filters(self, telegram_id: int) -> dict[str, Any]:
        account = self.get_gmail_account(telegram_id)
        return json.loads(account.filters) if account else {}

    def set_filters(self, telegram_id: int, filters: dict[str, Any]) -> bool:
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is None:
                return False
            account.filters = json.dumps(filters)
            return True

    def delete_user_data(self, telegram_id: int) -> None:
        """Remove every stored row for a user: account, pending states, user row."""
        with self.session() as s:
            account = s.get(GmailAccount, telegram_id)
            if account is not None:
                s.delete(account)
            for row in s.scalars(select(OAuthState).where(OAuthState.telegram_id == telegram_id)):
                s.delete(row)
            user = s.get(User, telegram_id)
            if user is not None:
                s.delete(user)

    # -- oauth states ----------------------------------------------------

    def save_oauth_state(self, state: str, telegram_id: int, expires_at: datetime) -> None:
        with self.session() as s:
            s.add(OAuthState(state=state, telegram_id=telegram_id, expires_at=expires_at))

    def pop_oauth_state(self, state: str) -> tuple[int, datetime] | None:
        """Delete the state row and return (telegram_id, expires_at).

        The row is deleted in the same transaction as the read, so a state
        value can be used once only.
        """
        with self.session() as s:
            row = s.get(OAuthState, state)
            if row is None:
                return None
            result = (row.telegram_id, _aware(row.expires_at))
            s.delete(row)
            return result

    def purge_expired_states(self, now: datetime | None = None) -> int:
        """Delete expired state rows. Return how many were removed."""
        now = now or utcnow()
        with self.session() as s:
            rows = list(s.scalars(select(OAuthState).where(OAuthState.expires_at < now)))
            for row in rows:
                s.delete(row)
            return len(rows)
