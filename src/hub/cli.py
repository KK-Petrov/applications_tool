"""The ``hub`` command line (installed by uv): serve, init-data, email-payload, import-usage, and later sync,
digest, connect-google."""

from __future__ import annotations

import typer

from hub.config import Config, load_config
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


@app.command("email-payload")
def email_payload(
    professor_id: str = typer.Argument(..., help="The professor's file id, e.g. mulder."),
    programme: list[str] = typer.Option(  # noqa: B008
        None, "--programme", help="A programme id to include (repeatable). Default: To ask and Draft ready."
    ),
) -> None:
    """Print the /rec-email input for a professor as JSON, for "Refine in Claude Code" (writes nothing)."""
    import json
    from datetime import UTC, datetime

    from hub.ai.payloads import rec_email_payload
    from hub.rules.letters import ASKABLE
    from hub.store import Store

    cfg = load_config()
    snapshot = Store.from_config(cfg).load_all()
    professor = next((p for p in snapshot.professors if p.id == professor_id), None)
    if professor is None or snapshot.settings is None:
        what = f"No professor {professor_id!r}" if professor is None else "settings.yaml could not be loaded"
        typer.echo(f"{what}. Nothing to print.", err=True)
        raise typer.Exit(1)
    wanted = programme or [pid for pid, x in professor.letters.items() if x.status in ASKABLE]
    chosen = [p for p in snapshot.programmes if p.id in wanted]
    if not chosen:
        typer.echo("No programme to include: give --programme, or set a letter to To ask first.", err=True)
        raise typer.Exit(1)
    payload = rec_email_payload(
        professor,
        chosen,
        snapshot.biography,
        snapshot.settings.owner_name,
        datetime.now(UTC),
        cfg,
        courses=snapshot.courses,
    )
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


@app.command("import-usage")
def import_usage() -> None:
    """Copy the old reports/ai_usage.jsonl lines into the data folder's AI usage log, once (DECISIONS #60).

    They become "Unlabelled" calls from an unknown source: the old lines can't be told apart. The old file
    is left as it is; running this again does nothing.
    """
    from hub.ai.usage import ImportRefused, app_log_path, import_legacy

    cfg = load_config()
    try:
        result = import_legacy(cfg)
    except ImportRefused as err:
        typer.echo(f"Nothing was imported: {err}", err=True)
        raise typer.Exit(1) from None
    if result.already_done:
        typer.echo("Already imported; nothing to do.")
        return
    noun = "call" if result.imported == 1 else "calls"
    typer.echo(f"Imported {result.imported} earlier {noun} into {app_log_path(cfg)}.")
    if result.unreadable:
        typer.echo(f"{result.unreadable} unreadable line(s) in the old log were skipped.")


def _logging(cfg: Config) -> None:
    """The commands log to <data_dir>/logs/hub.log like the app (Task Scheduler runs them; docs/05 §5)."""
    from hub.app import configure_logging

    configure_logging(cfg)


@app.command("sync")
def sync(dry_run: bool = typer.Option(False, "--dry-run", help="Count the changes; call nothing.")) -> None:
    """Push reminders to the Google Calendar "Applications" (docs/05 §2)."""
    from datetime import UTC, datetime

    from hub.google import auth, calendar_sync
    from hub.store import Store

    cfg = load_config()
    _logging(cfg)
    try:
        service = None if dry_run else auth.calendar_service(cfg)
    except auth.NotConnected as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from None
    result = calendar_sync.sync(Store.from_config(cfg), service, cfg, datetime.now(UTC), dry_run=dry_run)
    counts = ", ".join(f"{k} {v}" for k, v in result.counts.items())
    if result.error:
        typer.echo(f"Sync stopped: {result.error}", err=True)
        raise typer.Exit(1)
    typer.echo(("Dry run, nothing sent: " if dry_run else "Synced: ") + counts + ".")
    if result.counts["errors"]:
        typer.echo("Some calls failed; the next sync tries them again (details in logs/hub.log).", err=True)
        raise typer.Exit(1)


@app.command("digest")
def digest(dry_run: bool = typer.Option(False, "--dry-run", help="Print the message; send nothing.")) -> None:
    """Send the daily digest to the owner's own address, at most once a day (docs/05 §4)."""
    from datetime import UTC, datetime

    from hub.google import auth
    from hub.google import digest as daily
    from hub.store import Store

    cfg = load_config()
    _logging(cfg)
    try:
        service = None if dry_run else auth.gmail_service(cfg)
    except auth.NotConnected as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from None
    result = daily.send(Store.from_config(cfg), service, cfg, datetime.now(UTC), dry_run=dry_run)
    if dry_run:
        typer.echo(result.preview)
    elif result.sent:
        typer.echo("Sent the digest to the owner address in settings.yaml.")
    else:
        typer.echo(f"No digest sent: {result.reason}")


@app.command("connect-google")
def connect_google() -> None:
    """Authorise Google Calendar and Gmail access in the browser (docs/05 §1)."""
    from hub.google import auth
    from hub.store import Store

    cfg = load_config()
    try:
        typer.echo(auth.connect(cfg, Store.from_config(cfg)))
    except auth.NotConnected as err:
        typer.echo(str(err), err=True)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
