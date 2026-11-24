"""Google OAuth helpers: build the consent URL and exchange the code.

The flow has two halves, in two places:

    /connect command   -> build_consent_url()   (Telegram handler)
    /oauth/google/callback -> exchange_code()   (FastAPI route)

The two halves share only the `state` value. See `core/sessions.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from relay.core.config import GOOGLE_SCOPES, Settings

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class OAuthExchangeError(Exception):
    """The code exchange failed or Google returned no refresh token."""


@dataclass(frozen=True)
class GoogleConnection:
    """The result of a successful consent: who connected, and the long-lived token."""

    email: str
    refresh_token: str


def _make_flow(settings: Settings) -> Flow:
    config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.redirect_uri],
        }
    }
    # PKCE is off. The consent URL and the code exchange happen in different
    # requests, so a code verifier would have to be stored between them.
    # The client secret and the single-use `state` protect the flow instead.
    return Flow.from_client_config(
        config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.redirect_uri,
        autogenerate_code_verifier=False,
    )


def build_consent_url(settings: Settings, state: str) -> str:
    """Return the Google consent URL for a given state value.

    `access_type=offline` and `prompt=consent` make Google return a refresh
    token every time, even if the user connected before.
    """
    url, _ = _make_flow(settings).authorization_url(
        state=state,
        access_type="offline",
        prompt="consent",
    )
    return url


def exchange_code(settings: Settings, code: str, *, service: Any | None = None) -> GoogleConnection:
    """Trade an authorization code for a refresh token and the account email.

    Pass `service` to inject a fake Gmail service in tests.
    Blocking. Async callers run it with `asyncio.to_thread`.
    """
    flow = _make_flow(settings)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:  # noqa: BLE001 - oauthlib raises many error types
        raise OAuthExchangeError("Google rejected the authorization code.") from exc

    creds = flow.credentials
    if not creds.refresh_token:
        raise OAuthExchangeError("Google did not return a refresh token.")

    if service is None:
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    try:
        profile = service.users().getProfile(userId="me").execute()
    except Exception as exc:  # noqa: BLE001
        raise OAuthExchangeError("Could not read the Gmail address.") from exc

    return GoogleConnection(email=profile["emailAddress"], refresh_token=creds.refresh_token)
