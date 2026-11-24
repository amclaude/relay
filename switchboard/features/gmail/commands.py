"""Telegram command handlers for the Gmail feature.

Every handler follows the same steps:

    1. Make sure the user has a session row.
    2. Load the user's Gmail account. If there is none, say so.
    3. Run the blocking Gmail call in a thread.
    4. Turn Gmail errors into short, clear messages.

The last listing shown to a user is kept in memory. /read and /archive use
it to turn a list number (1, 2, 3 ...) into a message ID.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from telegram import LinkPreviewOptions, Update
from telegram.ext import ContextTypes

from switchboard.core.crypto import CryptoError
from switchboard.core.sessions import create_oauth_state, get_or_create_session
from switchboard.core.telegram_app import get_services
from switchboard.features.gmail.client import (
    GmailAuthError,
    GmailClient,
    GmailError,
    MessageSummary,
    revoke_token,
)
from switchboard.features.gmail.oauth import build_consent_url

log = logging.getLogger(__name__)

T = TypeVar("T")

# Telegram allows 4096 characters in one message. Stay below it.
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3800

LISTING_KEY = "gmail_last_listing"

NOT_CONNECTED_TEXT = "No Gmail account is connected. Run /connect first."
RECONNECT_TEXT = "Your Gmail connection expired or was revoked. Run /connect to link it again."
GENERIC_ERROR_TEXT = "Gmail returned an error. Try again in a moment."


class NotConnected(Exception):
    """The user has no Gmail account stored."""


# -- helpers ---------------------------------------------------------------


def truncate(text: str, limit: int = SAFE_LIMIT) -> str:
    """Cut text to fit in one Telegram message. Add a marker if text was cut."""
    if len(text) <= limit:
        return text
    marker = "\n\n[truncated]"
    return text[: limit - len(marker)].rstrip() + marker


def format_listing(messages: list[MessageSummary]) -> str:
    """Format a numbered list of messages for Telegram."""
    blocks = []
    for number, message in enumerate(messages, start=1):
        snippet = message.snippet[:120]
        blocks.append(f"{number}. {message.subject}\n   From: {message.sender}\n   {snippet}")
    return truncate("\n\n".join(blocks))


def parse_index(args: list[str], size: int) -> int:
    """Turn the first command argument into a zero-based list index.

    Raises ValueError with a message that is safe to show to the user.
    """
    if not args:
        raise ValueError("Give a message number, for example: /read 1")
    try:
        number = int(args[0])
    except ValueError:
        raise ValueError("The message number must be a whole number.") from None
    if size == 0:
        raise ValueError("No list to use. Run /unread or /search first.")
    if not 1 <= number <= size:
        raise ValueError(f"Choose a number from 1 to {size}.")
    return number - 1


def _client_for(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> GmailClient:
    """Build a GmailClient from the user's stored (encrypted) refresh token."""
    services = get_services(context)
    account = services.db.get_gmail_account(telegram_id)
    if account is None:
        raise NotConnected
    refresh_token = services.cipher.decrypt(account.encrypted_refresh_token)
    settings = services.settings
    return GmailClient(settings.google_client_id, settings.google_client_secret, refresh_token)


async def _run_gmail(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    action: Callable[[GmailClient], T],
) -> T | None:
    """Run a blocking Gmail action for the current user.

    Return the result, or None after telling the user what went wrong.
    """
    user = update.effective_user
    message = update.effective_message
    services = get_services(context)
    await asyncio.to_thread(get_or_create_session, services.db, user.id)

    try:
        client = await asyncio.to_thread(_client_for, context, user.id)
        return await asyncio.to_thread(action, client)
    except NotConnected:
        await message.reply_text(NOT_CONNECTED_TEXT)
    except (GmailAuthError, CryptoError):
        await message.reply_text(RECONNECT_TEXT)
    except GmailError:
        log.warning("Gmail error for user %s.", user.id, exc_info=True)
        await message.reply_text(GENERIC_ERROR_TEXT)
    return None


def _remember_listing(context: ContextTypes.DEFAULT_TYPE, messages: list[MessageSummary]) -> None:
    context.user_data[LISTING_KEY] = [m.id for m in messages]


def _message_id(context: ContextTypes.DEFAULT_TYPE, args: list[str]) -> str:
    ids: list[str] = context.user_data.get(LISTING_KEY, [])
    return ids[parse_index(args, len(ids))]


# -- handlers --------------------------------------------------------------


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/connect: send a Google consent link."""
    services = get_services(context)
    user = update.effective_user
    await asyncio.to_thread(get_or_create_session, services.db, user.id)
    state = await asyncio.to_thread(create_oauth_state, services.db, user.id)
    url = build_consent_url(services.settings, state)
    await update.effective_message.reply_text(
        "Open this link to connect your Gmail account. It works for 10 minutes and one use.\n\n"
        f"{url}\n\n"
        "Switchboard asks to read and archive mail. It cannot send mail.",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


async def disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/disconnect: revoke the token at Google and delete all stored data."""
    services = get_services(context)
    user = update.effective_user
    account = await asyncio.to_thread(services.db.get_gmail_account, user.id)
    if account is None:
        await update.effective_message.reply_text(NOT_CONNECTED_TEXT)
        return

    revoked = False
    try:
        token = services.cipher.decrypt(account.encrypted_refresh_token)
        revoked = await asyncio.to_thread(revoke_token, token)
    except CryptoError:
        log.warning("Could not decrypt token for user %s during disconnect.", user.id)

    await asyncio.to_thread(services.db.delete_user_data, user.id)
    context.user_data.pop(LISTING_KEY, None)

    note = (
        "Google access was revoked."
        if revoked
        else "Google did not confirm the revoke. Remove access at myaccount.google.com/permissions."
    )
    await update.effective_message.reply_text(f"Disconnected. All stored data was deleted. {note}")


async def unread(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unread: show the last 10 unread messages."""
    messages = await _run_gmail(update, context, lambda c: c.unread(10))
    if messages is None:
        return
    _remember_listing(context, messages)
    if not messages:
        await update.effective_message.reply_text("No unread mail.")
        return
    await update.effective_message.reply_text(format_listing(messages))


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/search <query>: run a Gmail search."""
    query = " ".join(context.args or []).strip()
    if not query:
        await update.effective_message.reply_text(
            "Give a search query, for example: /search from:alice has:attachment"
        )
        return
    messages = await _run_gmail(update, context, lambda c: c.list_messages(query, 10))
    if messages is None:
        return
    _remember_listing(context, messages)
    if not messages:
        await update.effective_message.reply_text("No messages found.")
        return
    await update.effective_message.reply_text(format_listing(messages))


async def read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/read <n>: show one message from the last listing as plain text."""
    try:
        message_id = _message_id(context, context.args or [])
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    body = await _run_gmail(update, context, lambda c: c.read(message_id))
    if body is None:
        return
    header = f"{body.summary.subject}\nFrom: {body.summary.sender}\nDate: {body.summary.date}\n\n"
    text = body.text or "(This message has no text body.)"
    await update.effective_message.reply_text(truncate(header + text))


async def archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/archive <n>: remove a message from the inbox."""
    try:
        message_id = _message_id(context, context.args or [])
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return
    done = await _run_gmail(update, context, lambda c: c.archive(message_id) or True)
    if done:
        await update.effective_message.reply_text("Archived.")


async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alerts on|off: turn new-mail alerts on or off."""
    services = get_services(context)
    user = update.effective_user
    choice = (context.args[0].lower() if context.args else "")
    if choice not in ("on", "off"):
        await update.effective_message.reply_text("Use /alerts on or /alerts off.")
        return
    changed = await asyncio.to_thread(services.db.set_alerts, user.id, choice == "on")
    if not changed:
        await update.effective_message.reply_text(NOT_CONNECTED_TEXT)
        return
    if choice == "on":
        await update.effective_message.reply_text(
            "Alerts are on. Switchboard checks for new mail every 5 minutes."
        )
    else:
        await update.effective_message.reply_text("Alerts are off.")
