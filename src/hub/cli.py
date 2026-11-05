"""The ``hub`` command line (installed by uv): serve, init-data, and later sync, digest, connect-google."""

from __future__ import annotations

import typer

from hub.config import load_config
from hub.init_data import init_data, settings_exist

app = typer.Typer(help="Application Hub: track master's applications locally.", no_args_is_help=True)


@app.command("serve")
def serve(reload: bool = typer.Option(False, help="Restart on code changes (development).")) -> None:
    """Run the web app on the configured local address (http://127.0.0.1:8765)."""
    import uvicorn

    cfg = load_config()
    uvicorn.run("hub.app:create_app", factory=True, host=cfg.app.host, port=cfg.app.port, reload=reload)


@app.command("init-data")
def init_data_command(
    owner_name: str | None = typer.Option(None, help="Your name, used to sign emails."),
    owner_email: str | None = typer.Option(None, help="Your email; the only address the digest goes to."),
) -> None:
    """Prepare the data folder: copy the four seed programmes and create settings.yaml. Never overwrites."""
    cfg = load_config()
    typer.echo(f"Data folder: {cfg.storage.data_dir}")
    if not settings_exist(cfg):
        owner_name = owner_name or typer.prompt("Owner name")
        owner_email = owner_email or typer.prompt("Owner email")
    try:
        report = init_data(cfg, owner_name, owner_email)
    except ValueError as exc:
        typer.echo(f"Nothing was written: {exc}", err=True)
        raise typer.Exit(1) from None
    if report.programmes_copied:
        typer.echo(
            f"Copied {len(report.programmes_copied)} programme files: {', '.join(report.programmes_copied)}"
        )
    else:
        typer.echo(f"Programmes kept as they are ({report.programmes_skipped_reason or 'all present'}).")
    typer.echo("Created settings.yaml." if report.settings_created else "settings.yaml kept as it is.")


def _not_yet(phase: str) -> None:
    typer.echo(f"Not yet implemented (Phase {phase}).")
    raise typer.Exit(1)


@app.command("sync")
def sync(dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Push reminders to the Google Calendar "Applications" (Phase 3)."""
    _not_yet("3")


@app.command("digest")
def digest(dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Send the daily digest to the owner's own address (Phase 3)."""
    _not_yet("3")


@app.command("connect-google")
def connect_google() -> None:
    """Authorise Google Calendar and Gmail access (Phase 3)."""
    _not_yet("3")


if __name__ == "__main__":
    app()
