"""hub init-data (docs/01 §9): copies the seed programmes, asks for owner details, never overwrites."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hub.cli import app
from hub.config import load_config
from hub.init_data import init_data
from hub.store import Store

SEEDS = {"booth-mif.yaml", "columbia-msfe.yaml", "mit-sloan-mfin.yaml", "princeton-mfin.yaml"}


@pytest.fixture
def empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "fresh"
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    return target


def test_creates_folder_seeds_and_settings(empty_dir: Path) -> None:
    cfg = load_config()
    report = init_data(cfg, owner_name="Alex Example", owner_email="alex.owner@example.com")
    assert {p.name for p in (empty_dir / "programmes").glob("*.yaml")} == SEEDS
    assert report.programmes_copied == sorted(SEEDS)
    settings = Store.from_config(cfg).load_settings()
    assert settings.owner_name == "Alex Example"
    assert settings.owner_email == "alex.owner@example.com"
    assert settings.display_tz == cfg.time.default_display_tz
    assert settings.reminder_tz == cfg.time.default_reminder_tz
    assert settings.calendar_id is None and settings.google_connected is False
    text = (empty_dir / "settings.yaml").read_text(encoding="utf-8")
    assert "calendar_id: null\n" in text and "google_connected: false\n" in text  # every docs/01 §2 field
    for sub in ("professors", "logs"):
        assert (empty_dir / sub).is_dir()
    # copied byte for byte, comments included
    seed = cfg.seed_dir / "princeton-mfin.yaml"
    assert (empty_dir / "programmes" / "princeton-mfin.yaml").read_bytes() == seed.read_bytes()


def test_second_run_overwrites_nothing(empty_dir: Path) -> None:
    cfg = load_config()
    init_data(cfg, owner_name="Alex Example", owner_email="alex.owner@example.com")
    princeton = empty_dir / "programmes" / "princeton-mfin.yaml"
    princeton.write_text(princeton.read_text(encoding="utf-8") + "# user edit\n", encoding="utf-8")
    settings_before = (empty_dir / "settings.yaml").read_bytes()
    report = init_data(cfg, owner_name="Someone Else", owner_email="other@example.com")
    assert report.programmes_copied == []
    assert report.settings_created is False
    assert princeton.read_text(encoding="utf-8").endswith("# user edit\n")
    assert (empty_dir / "settings.yaml").read_bytes() == settings_before


def test_skips_seeds_when_any_programme_exists(empty_dir: Path) -> None:
    (empty_dir / "programmes").mkdir(parents=True)
    (empty_dir / "programmes" / "own-programme.yaml").write_text("id: own-programme\n", encoding="utf-8")
    report = init_data(load_config(), owner_name="Alex Example", owner_email="alex.owner@example.com")
    assert report.programmes_copied == []
    assert {p.name for p in (empty_dir / "programmes").glob("*.yaml")} == {"own-programme.yaml"}


def test_rejects_an_invalid_owner_email(empty_dir: Path) -> None:
    with pytest.raises(ValueError, match="email"):
        init_data(load_config(), owner_name="Alex Example", owner_email="not-an-email")
    assert not (empty_dir / "settings.yaml").exists()


def test_cli_prompts_for_owner_details(empty_dir: Path) -> None:
    result = CliRunner().invoke(app, ["init-data"], input="Alex Example\nalex.owner@example.com\n")
    assert result.exit_code == 0, result.output
    assert "Owner name" in result.output and "Owner email" in result.output
    assert (empty_dir / "settings.yaml").is_file()
    assert "4 programme" in result.output


def test_cli_does_not_prompt_when_settings_exist(empty_dir: Path) -> None:
    init_data(load_config(), owner_name="Alex Example", owner_email="alex.owner@example.com")
    result = CliRunner().invoke(app, ["init-data"])
    assert result.exit_code == 0, result.output
    assert "Owner name" not in result.output
    assert "kept" in result.output.lower()


def test_an_invalid_seed_aborts_before_anything_is_copied(empty_dir: Path, tmp_path: Path) -> None:
    fake_repo = tmp_path / "repo"
    (fake_repo / "seed" / "programmes").mkdir(parents=True)
    (fake_repo / "seed" / "programmes" / "broken.yaml").write_text("id: broken\n", encoding="utf-8")
    cfg = load_config().model_copy(update={"repo_root": fake_repo})
    with pytest.raises(ValueError, match="broken.yaml"):
        init_data(cfg, owner_name="Alex Example", owner_email="alex.owner@example.com")
    assert not (empty_dir / "programmes").exists() or not list((empty_dir / "programmes").glob("*"))
    assert not (empty_dir / "settings.yaml").exists()
