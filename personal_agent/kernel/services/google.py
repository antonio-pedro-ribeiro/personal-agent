"""Google OAuth and API clients. Outputs get clients only through ``ctx.google.client``, and
only for scopes they declared (``google_scopes``) and the user granted.

The refresh token is stored in the OS keyring (Windows Credential Manager) locally, or read
from ``PERSONAL_AGENT_GOOGLE_TOKEN_JSON`` (e.g. a GitHub Actions secret) in cloud mode.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCOPE_PREFIX = "https://www.googleapis.com/auth/"
FREEBUSY_SCOPE = "calendar.freebusy"
TOKEN_ENV = "PERSONAL_AGENT_GOOGLE_TOKEN_JSON"
CLIENT_SECRETS_ENV = "PERSONAL_AGENT_GOOGLE_CLIENT_SECRETS"
KEYRING_SERVICE = "personal-agent"
KEYRING_USER = "google-token"

log = logging.getLogger(__name__)


def full_scope(scope: str) -> str:
    return scope if scope.startswith("https://") else SCOPE_PREFIX + scope


def required_scopes(output_scopes: Iterable[str]) -> list[str]:
    """Scopes of enabled outputs plus free/busy for the Availability service."""
    return sorted({full_scope(s) for s in [*output_scopes, FREEBUSY_SCOPE]})


class GoogleService:
    def __init__(self, credentials: Any) -> None:
        self.credentials = credentials
        self.granted: set[str] = set(credentials.scopes or [])

    def missing(self, scopes: Iterable[str]) -> list[str]:
        return sorted(full_scope(s) for s in scopes if full_scope(s) not in self.granted)

    def client(self, api: str, version: str, scopes: Iterable[str]) -> Any:
        """A discovery client, refused if the token lacks any of ``scopes``."""
        missing = self.missing(scopes)
        if missing:
            raise PermissionError(
                f"Google token lacks {', '.join(missing)}: run `agent auth google` again"
            )
        from googleapiclient.discovery import build

        return build(api, version, credentials=self.credentials, cache_discovery=False)


# --- token storage ------------------------------------------------------------------------


def load_token() -> tuple[str | None, str]:
    """(token json, where it came from)."""
    if value := os.environ.get(TOKEN_ENV, "").strip():
        return value, "env"
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER), "keyring"
    except Exception:  # no keyring backend (e.g. headless Linux)
        log.info("keyring unavailable", exc_info=True)
        return None, "keyring"


def save_token(token_json: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token_json)


def from_saved() -> GoogleService | None:
    """The saved Google connection, refreshed if expired; None if never authorized."""
    token, origin = load_token()
    if not token:
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(json.loads(token))  # type: ignore[no-untyped-call]
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
        if origin == "keyring":
            save_token(creds.to_json())
    return GoogleService(creds)


def authorize(client_secrets: Path, scopes: list[str]) -> str:
    """Run the browser consent flow once; returns the token JSON (also saved to keyring)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=scopes)
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
    token: str = creds.to_json()
    save_token(token)
    return token
