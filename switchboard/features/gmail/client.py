"""Gmail API client for one user.

This module wraps google-api-python-client in a small, typed interface.
The calls are synchronous. Async callers run them with `asyncio.to_thread`.

Two error types matter to callers:

    GmailAuthError  The refresh token is expired or revoked. The user must run /connect.
    GmailError      Any other Gmail failure.
"""

from __future__ import annotations

import base64
import html
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

_METADATA_HEADERS = ["From", "Subject", "Date"]


class GmailError(Exception):
    """A Gmail request failed."""


class GmailAuthError(GmailError):
    """The stored refresh token no longer works. The user must reconnect."""


@dataclass(frozen=True)
class MessageSummary:
    """The short form of a message, used in listings."""

    id: str
    sender: str
    subject: str
    snippet: str
    date: str


@dataclass(frozen=True)
class MessageBody:
    """A full message as plain text."""

    summary: MessageSummary
    text: str


class GmailClient:
    """Read and archive mail for one Google account."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        service: Any | None = None,
    ) -> None:
        # `service` lets tests inject a fake Gmail service.
        if service is None:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=TOKEN_URI,
                client_id=client_id,
                client_secret=client_secret,
                scopes=[GMAIL_SCOPE],
            )
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._service = service

    # -- public API ------------------------------------------------------

    def list_messages(self, query: str, max_results: int = 10) -> list[MessageSummary]:
        """Return up to `max_results` messages that match a Gmail search query."""
        listing = self._call(
            self._service.users().messages().list(userId="me", q=query, maxResults=max_results)
        )
        return [self._summary(item["id"]) for item in listing.get("messages", [])]

    def unread(self, max_results: int = 10) -> list[MessageSummary]:
        """Return the newest unread messages in the inbox."""
        return self.list_messages("is:unread in:inbox", max_results)

    def new_since(self, epoch_seconds: int, extra_query: str = "", max_results: int = 10) -> list[MessageSummary]:
        """Return unread inbox mail received after a Unix time. Used by alerts."""
        query = f"is:unread in:inbox after:{epoch_seconds} {extra_query}".strip()
        return self.list_messages(query, max_results)

    def read(self, message_id: str) -> MessageBody:
        """Return one message with its body as plain text."""
        message = self._call(
            self._service.users().messages().get(userId="me", id=message_id, format="full")
        )
        return MessageBody(summary=self._to_summary(message), text=extract_text(message.get("payload", {})))

    def archive(self, message_id: str) -> None:
        """Remove a message from the inbox. The message is not deleted."""
        self._call(
            self._service.users()
            .messages()
            .modify(userId="me", id=message_id, body={"removeLabelIds": ["INBOX"]})
        )

    # -- helpers ---------------------------------------------------------

    def _summary(self, message_id: str) -> MessageSummary:
        message = self._call(
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="metadata", metadataHeaders=_METADATA_HEADERS)
        )
        return self._to_summary(message)

    @staticmethod
    def _to_summary(message: dict[str, Any]) -> MessageSummary:
        headers = {
            h["name"].lower(): h["value"]
            for h in message.get("payload", {}).get("headers", [])
        }
        return MessageSummary(
            id=message["id"],
            sender=headers.get("from", "(unknown sender)"),
            subject=headers.get("subject", "(no subject)"),
            snippet=html.unescape(message.get("snippet", "")),
            date=headers.get("date", ""),
        )

    @staticmethod
    def _call(request: Any) -> dict[str, Any]:
        """Run a Gmail request. Map library errors to our error types."""
        try:
            return request.execute()
        except RefreshError as exc:
            raise GmailAuthError("Google refused the refresh token.") from exc
        except HttpError as exc:
            status = getattr(exc.resp, "status", None)
            if status == 401:
                raise GmailAuthError("Google rejected the credentials.") from exc
            raise GmailError(f"Gmail API error (HTTP {status}).") from exc


def extract_text(payload: dict[str, Any]) -> str:
    """Find the best plain-text body in a Gmail message payload.

    Prefer a text/plain part. If there is none, strip tags from text/html.
    """
    plain = _find_part(payload, "text/plain")
    if plain is not None:
        return plain.strip()
    markup = _find_part(payload, "text/html")
    if markup is not None:
        return html_to_text(markup)
    return ""


def _find_part(payload: dict[str, Any], mime_type: str) -> str | None:
    """Depth-first search for a part with a given MIME type. Return decoded text."""
    if payload.get("mimeType") == mime_type:
        data = payload.get("body", {}).get("data")
        if data:
            return _decode(data)
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found is not None:
            return found
    return None


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def html_to_text(markup: str) -> str:
    """Convert HTML to readable plain text. Simple regex pass, not a full parser."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def revoke_token(token: str) -> bool:
    """Ask Google to revoke a token. Return True on success.

    A failure is not fatal. The caller still deletes local data.
    """
    try:
        response = httpx.post(
            REVOKE_URI,
            params={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except httpx.HTTPError:
        log.warning("Token revoke request failed.", exc_info=True)
        return False
    return response.status_code == 200
