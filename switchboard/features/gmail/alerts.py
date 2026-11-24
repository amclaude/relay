"""Background polling for new mail.

A repeating job runs every few minutes. For each user who turned alerts on,
it asks Gmail for unread inbox mail that arrived after `last_checked`.
It sends one Telegram message for each new mail, then moves `last_checked`.

If Google refuses the token, alerts turn off for that user and the user
is told to run /connect again.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from telegram.ext import ContextTypes

from switchboard.core.crypto import CryptoError
from switchboard.core.db import GmailAccount, utcnow
from switchboard.core.telegram_app import get_services
from switchboard.features.gmail.client import GmailAuthError, GmailClient, GmailError, MessageSummary

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
MAX_ALERTS_PER_POLL = 5

RECONNECT_TEXT = (
    "Your Gmail connection expired or was revoked. Alerts are now off. Run /connect to link it again."
)


def format_alert(message: MessageSummary) -> str:
    """Format one new mail as a short Telegram message."""
    return f"New mail\nFrom: {message.sender}\nSubject: {message.subject}\n{message.snippet}"


def _fetch_new(client_id: str, client_secret: str, refresh_token: str, since: datetime, extra_query: str) -> list[MessageSummary]:
    """Blocking helper. Run it in a thread."""
    client = GmailClient(client_id, client_secret, refresh_token)
    return client.new_since(int(since.timestamp()), extra_query, MAX_ALERTS_PER_POLL)


async def poll_account(context: ContextTypes.DEFAULT_TYPE, account: GmailAccount) -> None:
    """Check one account and send alerts. Never raises."""
    services = get_services(context)
    settings = services.settings
    telegram_id = account.telegram_id
    started = utcnow()
    since = account.last_checked or started

    try:
        refresh_token = services.cipher.decrypt(account.encrypted_refresh_token)
        filters = services.db.get_filters(telegram_id)
        messages = await asyncio.to_thread(
            _fetch_new,
            settings.google_client_id,
            settings.google_client_secret,
            refresh_token,
            since,
            filters.get("query", ""),
        )
    except (GmailAuthError, CryptoError):
        services.db.set_alerts(telegram_id, False)
        await _safe_send(context, telegram_id, RECONNECT_TEXT)
        return
    except GmailError:
        log.warning("Gmail poll failed for user %s.", telegram_id, exc_info=True)
        return

    # Oldest first, so alerts arrive in the order the mail arrived.
    for message in reversed(messages):
        await _safe_send(context, telegram_id, format_alert(message))
    services.db.touch_last_checked(telegram_id, started)


async def _safe_send(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    try:
        await context.bot.send_message(chat_id=chat_id, text=text)
    except Exception:  # noqa: BLE001 - one failed send must not stop the poll loop
        log.warning("Could not send alert to %s.", chat_id, exc_info=True)


async def poll_all(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback. Poll every account that has alerts on."""
    services = get_services(context)
    accounts = await asyncio.to_thread(services.db.list_alert_accounts)
    for account in accounts:
        await poll_account(context, account)
