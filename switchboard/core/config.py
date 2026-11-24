"""Application configuration loaded from environment variables.

Values come from the process environment. A `.env` file in the working
directory is loaded first, so local development needs no exported variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_PUBLIC_BASE_URL = "https://switchboard.crawlshift.dev"
DEFAULT_PORT = 8742

# The web server only listens on the loopback interface.
# The Cloudflare tunnel is the only way in from the internet.
BIND_HOST = "127.0.0.1"

OAUTH_CALLBACK_PATH = "/oauth/google/callback"

# Gmail scope: read and archive mail. It does not allow sending.
# The email scope lets us show the connected address.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "switchboard.db"


class ConfigError(Exception):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """All runtime settings in one immutable object."""

    telegram_bot_token: str
    google_client_id: str
    google_client_secret: str
    fernet_key: str
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL
    port: int = DEFAULT_PORT
    db_path: Path = DEFAULT_DB_PATH
    host: str = BIND_HOST

    @property
    def redirect_uri(self) -> str:
        """The exact redirect URI registered in the Google Cloud console."""
        return self.public_base_url.rstrip("/") + OAUTH_CALLBACK_PATH


_REQUIRED = (
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "FERNET_KEY",
)


def load_settings(env: dict[str, str] | None = None, *, load_env_file: bool = True) -> Settings:
    """Build Settings from the environment.

    Pass `env` to read from a dict instead of os.environ (used in tests).
    Raise ConfigError and list every missing variable at once.
    """
    if env is None:
        if load_env_file:
            load_dotenv()
        env = dict(os.environ)

    missing = [name for name in _REQUIRED if not env.get(name)]
    if missing:
        raise ConfigError(
            "Missing required environment variables: " + ", ".join(missing)
            + ". Copy .env.example to .env and fill it in."
        )

    try:
        port = int(env.get("PORT") or DEFAULT_PORT)
    except ValueError as exc:
        raise ConfigError("PORT must be an integer.") from exc

    return Settings(
        telegram_bot_token=env["TELEGRAM_BOT_TOKEN"],
        google_client_id=env["GOOGLE_CLIENT_ID"],
        google_client_secret=env["GOOGLE_CLIENT_SECRET"],
        fernet_key=env["FERNET_KEY"],
        public_base_url=env.get("PUBLIC_BASE_URL") or DEFAULT_PUBLIC_BASE_URL,
        port=port,
    )
