"""Config loader: every section of config/project.yaml is typed; HUB_DATA_DIR overrides data_dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.config import load_config

from paths import REPO_ROOT


def test_all_sections_load() -> None:
    cfg = load_config()
    assert cfg.app.host == "127.0.0.1"
    assert cfg.app.port == 8765
    assert [z.tz for z in cfg.time.display_zones] == ["America/New_York", "Europe/Amsterdam"]
    assert cfg.time.assumed_deadline_time == "23:59"
    assert cfg.urgency.orange_days == 14
    assert cfg.letters.default_required == 2
    assert cfg.reminders.deadline_alert_days == [14, 7, 2, 1]
    assert cfg.email.min_words == 180
    assert cfg.claude.timeout_s == 120
    assert cfg.evaluation.criteria == ["structure", "content_fit", "programme_fit", "accuracy"]
    assert "Wharton" in cfg.wrong_school.extra_terms
    assert cfg.google.calendar_name == "Applications"
    assert cfg.digest.lookahead_days == 14
    assert cfg.schedule.sync_daily_time == "12:00"
    assert cfg.performance.sheet_professors_benchmark == 40
    assert cfg.storage.write_retries >= 1


def test_hub_data_dir_overrides_storage_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_DATA_DIR", str(tmp_path))
    assert load_config().storage.data_dir == tmp_path


def test_without_override_data_dir_comes_from_project_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_DATA_DIR", raising=False)
    cfg = load_config()
    assert cfg.storage.data_dir.is_absolute()


def test_repo_relative_paths_resolve_against_the_repository() -> None:
    cfg = load_config()
    assert cfg.repo_root == REPO_ROOT
    assert cfg.seed_dir == REPO_ROOT / "seed" / "programmes"


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    text = (REPO_ROOT / "config" / "project.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "project.yaml"
    bad.write_text(text.replace("  port: 8765", "  port: 8765\n  prot: 1"), encoding="utf-8")
    with pytest.raises(ValueError, match="prot"):
        load_config(bad)


def test_default_display_zone_must_be_a_display_zone(tmp_path: Path) -> None:
    text = (REPO_ROOT / "config" / "project.yaml").read_text(encoding="utf-8")
    bad = tmp_path / "project.yaml"
    bad.write_text(
        text.replace("default_display_tz: America/New_York", "default_display_tz: Asia/Tokyo"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="default_display_tz"):
        load_config(bad)
