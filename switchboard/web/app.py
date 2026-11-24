"""FastAPI app: the Google OAuth callback and a health check.

The app shares one `Services` object with the Telegram bot. It also gets a
`notify` function, so it can send a Telegram message without owning a bot.
In production `notify` calls the bot. In tests it is a fake.

Callback steps:

    1. Check that `state` is known, unused and not expired.
    2. Trade the `code` for a refresh token (blocking call, run in a thread).
    3. Encrypt the refresh token and store it for the Telegram user.
    4. Tell the user on Telegram that Gmail is connected.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from switchboard.core.config import OAUTH_CALLBACK_PATH
from switchboard.core.services import Services
from switchboard.core.sessions import OAuthStateError, consume_oauth_state
from switchboard.features.gmail.oauth import OAuthExchangeError, exchange_code

log = logging.getLogger(__name__)

# notify(telegram_id, text) sends one Telegram message.
Notifier = Callable[[int, str], Awaitable[None]]

STATE_ERROR_TEXT = "This link is not valid. It may have expired or been used already. Run /connect again in Telegram."
DENIED_TEXT = "Google access was not granted. Run /connect in Telegram to try again."
EXCHANGE_ERROR_TEXT = "Google did not accept the request. Run /connect in Telegram to try again."
SUCCESS_TEXT = "Gmail is connected. You can close this page and go back to Telegram."


def _page(title: str, message: str, status_code: int) -> HTMLResponse:
    """Return a small HTML page. The text is escaped."""
    body = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{html.escape(title)}</title></head>"
        "<body style=\"font-family: sans-serif; max-width: 32rem; margin: 4rem auto; padding: 0 1rem;\">"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p></body></html>"
    )
    return HTMLResponse(body, status_code=status_code)


async def _safe_notify(notify: Notifier, telegram_id: int, text: str) -> None:
    """Send a Telegram message. A failure is logged and never raised."""
    try:
        await notify(telegram_id, text)
    except Exception:  # noqa: BLE001 - the web page must still load if Telegram fails
        log.warning("Could not notify Telegram user %s.", telegram_id, exc_info=True)


def create_app(services: Services, notify: Notifier) -> FastAPI:
    """Build the FastAPI app."""
    app = FastAPI(title="Switchboard", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(OAUTH_CALLBACK_PATH, response_class=HTMLResponse)
    async def google_callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        if not state:
            return _page("Link not valid", STATE_ERROR_TEXT, 400)

        # The state is used up here, whatever happens next.
        try:
            telegram_id = await asyncio.to_thread(consume_oauth_state, services.db, state)
        except OAuthStateError:
            return _page("Link not valid", STATE_ERROR_TEXT, 400)

        if error or not code:
            await _safe_notify(notify, telegram_id, DENIED_TEXT)
            return _page("Not connected", DENIED_TEXT, 400)

        try:
            connection = await asyncio.to_thread(exchange_code, services.settings, code)
        except OAuthExchangeError:
            log.warning("Code exchange failed for user %s.", telegram_id, exc_info=True)
            await _safe_notify(notify, telegram_id, EXCHANGE_ERROR_TEXT)
            return _page("Not connected", EXCHANGE_ERROR_TEXT, 400)

        encrypted = services.cipher.encrypt(connection.refresh_token)
        await asyncio.to_thread(
            services.db.upsert_gmail_account, telegram_id, connection.email, encrypted
        )
        await _safe_notify(
            notify,
            telegram_id,
            f"Gmail is connected: {connection.email}. Try /unread.",
        )
        return _page("Connected", SUCCESS_TEXT, 200)

    return app
