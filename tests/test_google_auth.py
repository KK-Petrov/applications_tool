"""Google sign-in (docs/05 §1; Phase 3 step 4) and the ``hub sync`` / ``hub connect-google`` commands.

No test opens ``.secrets/`` or reaches Google: the installed-app flow, the credentials and the API client are
replaced, and every token path points into a temporary folder (CLAUDE.md rule 16).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fakes.fake_google import FakeGoogle
from hub.config import REPO_ROOT, load_config
from hub.google import auth
from hub.store import Store


@pytest.fixture
def cfg(config, tmp_path: Path):  # noqa: ANN001, ANN201
    """The fixture config with the Google files in a temporary folder."""
    google = config.google.model_copy(
        update={
            "client_secret_path": str(tmp_path / "secrets" / "client_secret.json"),
            "token_path": str(tmp_path / "secrets" / "token.json"),
        }
    )
    return config.model_copy(update={"google": google})


class _Creds:
    def __init__(self, valid: bool = True, expired: bool = False, refresh_token: str | None = "r") -> None:
        self.valid, self.expired, self.refresh_token = valid, expired, refresh_token
        self.refreshed = False

    def refresh(self, _request: object) -> None:
        self.refreshed, self.valid, self.expired = True, True, False

    def to_json(self) -> str:
        return json.dumps({"token": "invented", "refresh_token": self.refresh_token})


# ---- where the token lives (DECISIONS #106 addition 4) ----------------------------------------------


def test_the_token_and_client_files_live_in_the_gitignored_secrets_folder() -> None:
    google = load_config().google
    for rel in (google.token_path, google.client_secret_path):
        assert Path(rel).parts[0] == ".secrets", rel
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO_ROOT, check=False)
        assert ignored.returncode == 0, f"{rel} is not ignored by git"


def test_the_scopes_are_the_two_from_config() -> None:
    assert load_config().google.scopes == [
        "https://www.googleapis.com/auth/calendar.app.created",
        "https://www.googleapis.com/auth/gmail.send",
    ]


# ---- connect ----------------------------------------------------------------------------------------


def test_connect_runs_the_installed_app_flow_and_saves_the_token(
    cfg, monkeypatch: pytest.MonkeyPatch
) -> None:  # noqa: ANN001
    secret = Path(cfg.google.client_secret_path)
    secret.parent.mkdir(parents=True)
    secret.write_text("{}", encoding="utf-8")  # an invented placeholder; the flow is replaced
    seen: dict = {}

    class Flow:
        @classmethod
        def from_client_secrets_file(cls, path: str, scopes: list[str]):  # noqa: ANN206
            seen.update(path=path, scopes=scopes)
            return cls()

        def run_local_server(self, port: int):  # noqa: ANN201
            seen["port"] = port
            return _Creds()

    monkeypatch.setattr(auth, "InstalledAppFlow", Flow)
    message = auth.connect(cfg, Store.from_config(cfg))
    assert seen == {"path": str(secret), "scopes": cfg.google.scopes, "port": 0}
    assert json.loads(Path(cfg.google.token_path).read_text(encoding="utf-8"))["token"] == "invented"
    assert Store.from_config(cfg).load_settings().google_connected is True
    assert "Connected" in message


def test_connect_without_the_client_file_says_what_to_do(cfg) -> None:  # noqa: ANN001
    with pytest.raises(auth.NotConnected, match="client_secret.json"):
        auth.connect(cfg, Store.from_config(cfg))
    assert not Path(cfg.google.token_path).exists()
    assert Store.from_config(cfg).load_settings().google_connected is False


# ---- credentials on use -------------------------------------------------------------------------------


def test_no_token_means_not_connected(cfg) -> None:  # noqa: ANN001
    with pytest.raises(auth.NotConnected, match="hub connect-google"):
        auth.credentials(cfg)


def test_an_expired_token_is_refreshed_and_saved(cfg, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    token = Path(cfg.google.token_path)
    token.parent.mkdir(parents=True)
    token.write_text("{}", encoding="utf-8")
    creds = _Creds(valid=False, expired=True)
    monkeypatch.setattr(auth, "_load_token", lambda _path, _scopes: creds)
    assert auth.credentials(cfg) is creds and creds.refreshed
    assert json.loads(token.read_text(encoding="utf-8"))["refresh_token"] == "r"


def test_a_refused_refresh_asks_to_reconnect(cfg, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from google.auth.exceptions import RefreshError

    token = Path(cfg.google.token_path)
    token.parent.mkdir(parents=True)
    token.write_text("{}", encoding="utf-8")
    creds = _Creds(valid=False, expired=True)

    def refuse(_request: object) -> None:
        raise RefreshError("invalid_grant")

    creds.refresh = refuse  # type: ignore[method-assign]
    monkeypatch.setattr(auth, "_load_token", lambda _path, _scopes: creds)
    with pytest.raises(auth.NotConnected, match="Reconnect Google"):
        auth.credentials(cfg)


def test_the_api_client_has_the_configured_timeout(cfg, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    seen: dict = {}
    monkeypatch.setattr(auth, "credentials", lambda _cfg: _Creds())

    def build(name: str, version: str, http: object, cache_discovery: bool) -> str:  # noqa: FBT001
        seen.update(name=name, version=version, timeout=http.http.timeout, cache=cache_discovery)  # type: ignore[attr-defined]
        return "service"

    monkeypatch.setattr(auth, "build", build)
    assert auth.calendar_service(cfg) == "service"
    assert seen == {"name": "calendar", "version": "v3", "timeout": 30, "cache": False}


# ---- the commands -------------------------------------------------------------------------------------


def test_hub_sync_dry_run_needs_no_google(config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from hub import cli

    monkeypatch.setattr(auth, "calendar_service", lambda _cfg: pytest.fail("dry run must not reach Google"))
    result = CliRunner().invoke(cli.app, ["sync", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output and "created" in result.output


def test_hub_sync_reports_the_counts(config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from hub import cli

    fake = FakeGoogle()
    monkeypatch.setattr(auth, "calendar_service", lambda _cfg: fake.calendar())
    result = CliRunner().invoke(cli.app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "created" in result.output and fake.count("events.insert") >= 1


def test_hub_sync_without_a_connection_says_how_to_connect(config, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from hub import cli

    def not_connected(_cfg: object) -> None:
        raise auth.NotConnected("Google is not connected yet. Run `uv run hub connect-google` first.")

    monkeypatch.setattr(auth, "calendar_service", not_connected)
    result = CliRunner().invoke(cli.app, ["sync"])
    assert result.exit_code == 1 and "hub connect-google" in result.output
