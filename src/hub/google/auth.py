"""Google sign-in (docs/05 §1): ``hub connect-google`` and the credentials every Google call uses.

The installed-app flow (``InstalledAppFlow.run_local_server``) asks for the two scopes in ``config.google``
(``calendar.app.created``: only calendars the app created; ``gmail.send``) and saves the token at
``config.google.token_path`` (inside the gitignored ``.secrets/``). Credentials are refreshed on use and
saved again; a refused refresh asks the owner to reconnect. The files are read only here, at runtime, and
never printed (CLAUDE.md rule 16). API clients get ``config.google.timeout_s`` per call (docs/05 §2.5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httplib2
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from hub.config import Config
from hub.store import Store

CONNECT = "Run `uv run hub connect-google` first (docs/05 §1 in the README)."


class NotConnected(Exception):
    """Google can't be used yet, or the saved sign-in was refused; the message says what to do."""


def _path(config: Config, rel: str) -> Path:
    path = Path(rel)
    return path if path.is_absolute() else config.repo_root / path


def _load_token(path: Path, scopes: list[str]) -> Any:
    return Credentials.from_authorized_user_file(str(path), scopes)


def _save_token(path: Path, creds: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def connect(config: Config, store: Store) -> str:
    """Run the installed-app flow, save the token and set ``settings.google_connected``."""
    secret = _path(config, config.google.client_secret_path)
    if not secret.exists():
        raise NotConnected(
            f"No OAuth client file at {config.google.client_secret_path}. Create a Desktop-app OAuth client "
            "in Google Cloud Console and save its JSON as .secrets/client_secret.json (docs/05 §1, step 3)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=config.google.scopes)
    creds = flow.run_local_server(port=0)
    _save_token(_path(config, config.google.token_path), creds)
    settings = store.load_settings()
    if not settings.google_connected:
        settings.google_connected = True
        store.save_settings(settings)
    return "Connected to Google. Run `uv run hub sync` to create the Applications calendar."


def credentials(config: Config) -> Any:
    """Valid credentials, refreshed (and saved) when expired; ``NotConnected`` when there are none."""
    token = _path(config, config.google.token_path)
    if not token.exists():
        raise NotConnected(f"Google is not connected yet. {CONNECT}")
    creds = _load_token(token, config.google.scopes)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as err:
            raise NotConnected(
                "Google refused the saved sign-in (expired or revoked). Reconnect Google: run "
                "`uv run hub connect-google`."
            ) from err
        _save_token(token, creds)
        return creds
    raise NotConnected(f"The saved Google sign-in can't be used. Reconnect Google: {CONNECT}")


def _service(config: Config, name: str, version: str) -> Any:
    http = AuthorizedHttp(credentials(config), http=httplib2.Http(timeout=config.google.timeout_s))
    return build(name, version, http=http, cache_discovery=False)


def calendar_service(config: Config) -> Any:
    return _service(config, "calendar", "v3")


def gmail_service(config: Config) -> Any:
    return _service(config, "gmail", "v1")
