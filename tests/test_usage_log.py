"""The AI usage log (docs/01 §11, docs/04 §8; DECISIONS #60): fields, privacy, location, import, totals.

Every ``run_skill`` call writes one line with its action, subject (IDs only) and source. The app's lines go
to ``<data_dir>/logs/ai_usage.jsonl``; eval and live lines go to the run log (``reports/`` in real use, a
temporary file here). A line never holds payload content, email text, essay text or biography text.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hub.ai import usage
from hub.ai.skills import SkillError, run_skill

from helpers import FIXED_NOW, RECORDINGS, assert_only_usage_line, data_snapshot
from paths import FIXTURES

USAGE_FIXTURES = FIXTURES / "usage"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body", "used_biography_ids", "warnings"],
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "used_biography_ids": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
PAYLOAD_MARKER = "PAYLOAD-MARKER-41d0"
BIOGRAPHY_MARKER = "BIOGRAPHY-MARKER-77e2"
EMAIL_MARKER = "EMAIL-BODY-MARKER-0b9c"
SUBJECT_MARKER = "EMAIL-SUBJECT-MARKER-5a1f"
TAG = {"action": "generate_email", "subject": {"professor_id": "jansen", "programme_ids": ["princeton-mfin"]}}
FIELDS = [
    "at",
    "skill",
    "backend",
    "action",
    "subject",
    "source",
    "duration_s",
    "total_cost_usd",
    "ok",
    "error",
]
SLUG = re.compile(r"^[a-z0-9-]{2,60}$")


@pytest.fixture
def marked_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake replies with an email whose subject and body carry markers that must not reach the log."""
    raw = json.loads((RECORDINGS / "rec-email.json").read_text(encoding="utf-8"))
    raw["structured_output"].update(subject=SUBJECT_MARKER, body=f"Dear Professor Jansen,\n\n{EMAIL_MARKER}")
    path = tmp_path / "marked.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(path))


def _payload() -> dict:
    return {"x": PAYLOAD_MARKER, "biography": [{"id": "b1", "detail": BIOGRAPHY_MARKER}]}


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _assert_ids_only(line: dict) -> None:
    """The privacy rule for every line: fixed fields, known action and source, subject values are IDs."""
    assert list(line) == FIELDS
    assert line["action"] in usage.ACTIONS and line["action"] != "unlabelled"
    assert line["source"] in ("app", "eval", "live")
    assert set(line["subject"]) <= set(usage.SUBJECT_KEYS)
    for key, value in line["subject"].items():
        values = value if key == "programme_ids" else [value]
        assert all(isinstance(v, str) and SLUG.match(v) for v in values), (key, value)
    assert line["error"] in (None, "not_installed", "timeout", "failed", "invalid_output")


# ---- fields and privacy ----------------------------------------------------------------------------


def test_a_line_has_exactly_the_documented_fields(config, marked_reply: None) -> None:
    run_skill("rec-email", _payload(), SCHEMA, config=config, source="app", **TAG)
    (line,) = _lines(usage.app_log_path(config))
    _assert_ids_only(line)
    assert line["skill"] == "rec-email" and line["backend"] == "cli" and line["ok"] is True
    assert line["action"] == "generate_email" and line["source"] == "app"
    assert line["subject"] == {"professor_id": "jansen", "programme_ids": ["princeton-mfin"]}
    assert datetime.fromisoformat(line["at"]).utcoffset() is not None


@pytest.mark.parametrize("mode", [None, "invalid_json", "exit", "schema_mismatch"])
@pytest.mark.parametrize("source", ["app", "eval", "live"])
def test_no_payload_email_or_biography_text_in_any_line(
    config, marked_reply: None, monkeypatch: pytest.MonkeyPatch, mode: str | None, source: str
) -> None:
    if mode:
        monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    try:
        run_skill("rec-email", _payload(), SCHEMA, config=config, source=source, **TAG)
    except SkillError:
        assert mode is not None
    path = usage.app_log_path(config) if source == "app" else usage.run_log_path(config)
    text = path.read_text(encoding="utf-8")
    for marker in (PAYLOAD_MARKER, BIOGRAPHY_MARKER, EMAIL_MARKER, SUBJECT_MARKER, "Dear Professor"):
        assert marker not in text, marker
    (line,) = _lines(path)
    _assert_ids_only(line)
    assert line["source"] == source


VALID_SUBJECTS = [
    {},
    {"professor_id": "jansen", "programme_ids": ["princeton-mfin", "booth-mif"]},
    {"programme_id": "princeton-mfin"},
    {"programme_id": "princeton-mfin", "essay_id": "why-princeton"},
    {"cv_id": "base"},
]
INVALID_SUBJECTS = [
    {"professor_id": "Prof. Dr. Eva Jansen"},  # a name
    {"professor_id": "x" * 61},  # too long for an ID
    {"essay_id": "I have always wanted to study finance because"},  # essay text
    {"name": "jansen"},  # unknown key
    {"query": "berkeley-haas-mfe"},  # research query key is never logged
    {"programme_ids": ["princeton-mfin", "Princeton MFin"]},  # one item is not an ID
    {"programme_ids": "princeton-mfin"},  # a list key given a string
    {"professor_id": ["jansen"]},  # a single-ID key given a list
    {"professor_id": None},
    {"professor_id": "jansen\n"},  # a trailing newline passes "$" (data-integrity review W1)
]


@pytest.mark.parametrize("subject", VALID_SUBJECTS)
def test_subjects_made_of_ids_are_accepted(config, subject: dict) -> None:
    run_skill("rec-email", {}, SCHEMA, config=config, action="generate_email", subject=subject, source="app")
    (line,) = _lines(usage.app_log_path(config))
    assert line["subject"] == subject


@pytest.mark.parametrize("subject", INVALID_SUBJECTS)
def test_a_subject_that_is_not_ids_only_is_refused_before_any_call(
    config, data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, subject: dict
) -> None:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(calls))
    before = data_snapshot(data_dir)
    with pytest.raises(ValueError):
        run_skill(
            "rec-email", {}, SCHEMA, config=config, action="generate_email", subject=subject, source="app"
        )
    assert not calls.exists(), "Claude Code was called"
    assert data_snapshot(data_dir) == before
    assert not usage.run_log_path(config).exists()


@pytest.mark.parametrize(
    ("action", "source"),
    [("unlabelled", "app"), ("summarise", "app"), ("generate_email", "unknown"), ("generate_email", "dev")],
)
def test_an_unknown_action_or_source_is_refused(config, data_dir: Path, action: str, source: str) -> None:
    before = data_snapshot(data_dir)
    with pytest.raises(ValueError):
        run_skill("rec-email", {}, SCHEMA, config=config, action=action, subject={}, source=source)
    assert data_snapshot(data_dir) == before


# ---- where lines go --------------------------------------------------------------------------------


def test_app_lines_go_to_data_dir_logs_and_eval_and_live_lines_to_the_run_log(config, data_dir: Path) -> None:
    assert usage.app_log_path(config) == data_dir / "logs" / "ai_usage.jsonl"
    for source in ("app", "eval", "live", "app"):
        run_skill("rec-email", {}, SCHEMA, config=config, source=source, **TAG)
    assert [x["source"] for x in _lines(usage.app_log_path(config))] == ["app", "app"]
    assert [x["source"] for x in _lines(usage.run_log_path(config))] == ["eval", "live"]


def test_the_real_run_log_is_reports_in_the_repository(config) -> None:
    from hub.config import REPO_ROOT, load_config

    real = load_config().claude.run_usage_log
    assert real == "reports/ai_usage.jsonl" and load_config().claude.usage_log == "logs/ai_usage.jsonl"
    assert not Path(real).is_absolute()
    assert (REPO_ROOT / real).parent.name == "reports"


def test_without_a_data_dir_no_app_line_is_written_and_nothing_is_created(config, tmp_path: Path) -> None:
    missing = tmp_path / "no-such-data"
    cfg = config.model_copy(update={"storage": config.storage.model_copy(update={"data_dir": missing})})
    result = run_skill("rec-email", {}, SCHEMA, config=cfg, source="app", **TAG)
    assert result.data["subject"]
    assert not missing.exists()
    assert not usage.run_log_path(cfg).exists()


# ---- one-time import of the old reports/ai_usage.jsonl ---------------------------------------------

LEGACY = [
    {"at": "2026-11-06T20:56:57+00:00", "skill": "probe-skill", "duration_s": 3.34, "total_cost_usd": 0.06496,
     "ok": True, "error": None},
    {"at": "2026-11-06T21:06:57+00:00", "skill": "rec-email", "backend": "cli", "duration_s": 21.5,
     "total_cost_usd": 0.041, "ok": True, "error": None},
    {"at": "2026-11-06T21:10:00+00:00", "skill": "grade-claims", "backend": "cli", "duration_s": 9.0,
     "total_cost_usd": None, "ok": False, "error": "timeout"},
]  # fmt: skip


def _write_run_log(config, extra: list[str] | None = None) -> bytes:
    lines = [json.dumps(x) for x in LEGACY]
    lines.insert(1, json.dumps({**LEGACY[1], "action": "generate_email", "subject": {}, "source": "eval"}))
    lines += extra or []
    path = usage.run_log_path(config)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path.read_bytes()


def test_import_copies_old_lines_once_as_unlabelled_and_leaves_the_run_log_alone(config) -> None:
    run_bytes = _write_run_log(config, extra=["not json"])
    run_skill("rec-email", {}, SCHEMA, config=config, source="app", **TAG)  # an app line already there
    assert usage.read_all(config).legacy_pending == 3
    result = usage.import_legacy(config)
    assert (result.imported, result.already_done, result.unreadable) == (3, False, 1)
    lines = _lines(usage.app_log_path(config))
    assert len(lines) == 4 and lines[0]["action"] == "generate_email"
    for old, new in zip(LEGACY, lines[1:], strict=True):
        assert new["action"] == "unlabelled" and new["subject"] == {} and new["source"] == "unknown"
        assert new["imported"] is True
        for key in ("at", "skill", "duration_s", "total_cost_usd", "ok", "error"):
            assert new[key] == old[key], key
        assert new["backend"] == old.get("backend")
    assert usage.run_log_path(config).read_bytes() == run_bytes
    again = usage.import_legacy(config)
    assert (again.imported, again.already_done) == (0, True)
    assert len(_lines(usage.app_log_path(config))) == 4
    assert usage.read_all(config).legacy_pending == 0


def test_import_without_a_data_dir_writes_nothing(config, tmp_path: Path) -> None:
    _write_run_log(config)
    missing = tmp_path / "no-such-data"
    cfg = config.model_copy(update={"storage": config.storage.model_copy(update={"data_dir": missing})})
    with pytest.raises(usage.ImportRefused):
        usage.import_legacy(cfg)
    assert not missing.exists()


def test_import_usage_command(config, data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from hub import cli

    _write_run_log(config)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["import-usage"])
    assert first.exit_code == 0, first.output
    assert "Imported 3 earlier calls" in first.output
    second = runner.invoke(cli.app, ["import-usage"])
    assert second.exit_code == 0 and "Already imported" in second.output
    assert len(_lines(data_dir / "logs" / "ai_usage.jsonl")) == 3


# ---- reading and totals ------------------------------------------------------------------------------


@pytest.fixture
def fixture_logs(config) -> None:
    """The invented app and run logs from tests/fixtures/usage/ in place."""
    app = usage.app_log_path(config)
    app.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(USAGE_FIXTURES / "app.jsonl", app)
    shutil.copyfile(USAGE_FIXTURES / "run.jsonl", usage.run_log_path(config))


def test_reading_skips_old_run_log_lines_and_keeps_every_other_line(config, fixture_logs: None) -> None:
    log = usage.read_all(config)
    assert log.problems == []
    assert len(log.lines) == 8  # 5 app (one imported) + 3 eval/live; the old run-log line is skipped
    assert log.legacy_pending == 0  # the app log already holds imported lines
    assert sorted({x.source for x in log.lines}) == ["app", "eval", "live", "unknown"]


def _tally(t: usage.Tally) -> tuple[int, int, float, int]:
    return (t.calls, t.errors, round(t.cost_usd, 6), t.no_cost)


def test_totals_this_month_and_all_time_by_action_and_by_source(config, fixture_logs: None) -> None:
    lines = usage.read_all(config).lines
    totals = usage.totals(lines, FIXED_NOW, "America/New_York")
    assert totals.month == "2026-10"
    src = totals.by_source
    assert _tally(src["app"].month) == (2, 1, 0.033, 1)  # 1 Oct 02:00 UTC is still 30 Sep in New York
    assert _tally(src["app"].all_time) == (4, 1, 0.126, 1)
    assert _tally(src["unknown"].month) == (0, 0, 0.0, 0)
    assert _tally(src["unknown"].all_time) == (1, 0, 0.02, 0)
    assert _tally(src["eval"].month) == (2, 0, 0.04, 0)
    assert _tally(src["live"].all_time) == (1, 0, 0.064, 0)
    act = totals.by_action
    assert _tally(act["generate_email"].all_time) == (5, 1, 0.152, 1)
    assert _tally(act["grade_claims"].all_time) == (1, 0, 0.014, 0)
    assert _tally(act["live_check"].month) == (1, 0, 0.064, 0)
    assert _tally(act["unlabelled"].all_time) == (1, 0, 0.02, 0)
    assert _tally(totals.overall.month) == (5, 1, 0.137, 1)
    assert _tally(totals.overall.all_time) == (8, 1, 0.25, 1)
    assert list(act) == [a for a in usage.ACTIONS if a in act]  # documented order


def test_the_month_follows_the_display_zone(config, fixture_logs: None) -> None:
    lines = usage.read_all(config).lines
    totals = usage.totals(lines, FIXED_NOW, "Europe/Amsterdam")
    assert _tally(totals.by_source["app"].month) == (3, 1, 0.075, 1)  # 1 Oct 04:00 in Rotterdam
    assert usage.month_key(datetime(2026, 10, 1, 2, 0, tzinfo=UTC), "America/New_York") == "2026-09"
    assert usage.month_key(datetime(2026, 10, 1, 2, 0, tzinfo=UTC), "Europe/Amsterdam") == "2026-10"


def _app_line(**changes: object) -> str:
    """A valid app-log line with ``changes`` applied."""
    line = {
        "at": "2026-10-02T10:00:00+00:00",
        "skill": "rec-email",
        "backend": "cli",
        "action": "generate_email",
        "subject": {},
        "source": "app",
        "duration_s": 1.0,
        "total_cost_usd": 0.5,
        "ok": True,
        "error": None,
    }
    return json.dumps({**line, **changes})


BAD_LINES = [
    "not json",
    "[1, 2]",
    _app_line(at="2026-10-02T10:00:00"),  # timestamp without offset
    _app_line(action="summarise"),  # unknown action
    _app_line(subject={"professor_id": "Prof. Dr. Eva Jansen"}),  # a name, not an ID
    _app_line(source="eval"),  # an eval line in the app log
    _app_line(ok="yes"),  # wrong type
    # data-integrity review W2 / ui review WARN 4: strict types and consistent lines
    _app_line(subject={"professor_id": "jansen\n"}),
    _app_line(skill="I have always wanted to study finance at Princeton because"),
    _app_line(backend="Dear Professor Jansen,"),
    _app_line(total_cost_usd=True),
    _app_line(total_cost_usd="0.5"),
    _app_line(total_cost_usd=float("inf")),
    _app_line(total_cost_usd=-1.0),
    _app_line(at=0),
    _app_line(duration_s="3"),
    _app_line(ok=True, error="timeout"),
    _app_line(ok=False, error=None),
    _app_line(source="unknown"),  # unknown source without being imported
    _app_line(imported=True),  # imported but labelled and from the app
]  # fmt: skip


@pytest.mark.parametrize("bad", BAD_LINES)
def test_an_unreadable_line_is_reported_with_its_number_and_left_out(
    config, fixture_logs: None, bad: str
) -> None:
    path = usage.app_log_path(config)
    path.write_text(path.read_text(encoding="utf-8") + bad + "\n", encoding="utf-8")
    log = usage.read_all(config)
    assert len(log.lines) == 8
    (problem,) = log.problems
    assert problem.line == 6 and problem.file == "logs/ai_usage.jsonl"
    assert _tally(usage.totals(log.lines, FIXED_NOW, "America/New_York").overall.all_time) == (8, 1, 0.25, 1)


def test_an_unreadable_file_is_a_problem_not_a_crash(config, fixture_logs: None) -> None:
    path = usage.app_log_path(config)
    path.unlink()
    path.mkdir()  # a folder where the file should be: reading it fails
    log = usage.read_all(config)
    assert [p.line for p in log.problems] == [None]
    assert len(log.lines) == 3  # the run log still loads


def test_no_logs_at_all_is_empty_without_problems(config) -> None:
    log = usage.read_all(config)
    assert (log.lines, log.problems, log.legacy_pending) == ([], [], 0)


# ---- the stricter C-2b-05 check itself (tests/helpers.py) ------------------------------------------


def _failure_folder(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    folder = tmp_path / "check"
    (folder / "logs").mkdir(parents=True)
    (folder / "professors").mkdir()
    old = "2026-11-06 09:00:00,000 INFO hub.store: saved x.yaml\n"
    (folder / "logs" / "hub.log").write_text(old, "utf-8")
    return folder, data_snapshot(folder)


def _fail_like_the_app(folder: Path, hub_lines: list[str], save: bool = True) -> None:
    line = json.loads(_app_line(ok=False, error="timeout", total_cost_usd=None))
    (folder / "logs" / "ai_usage.jsonl").write_text(json.dumps(line) + "\n", "utf-8")
    if save:
        (folder / "professors" / "lindqvist.yaml").write_text("name: x\n", "utf-8")
    with (folder / "logs" / "hub.log").open("a", encoding="utf-8") as fh:
        fh.writelines(x + "\n" for x in hub_lines)


SAVE = "2026-11-06 21:26:12,779 INFO hub.store: saved professors/lindqvist.yaml"
FAILED = {"ok": False, "error": "timeout", "action": "generate_email", "subject": {}, "source": "app"}


def test_the_failure_check_accepts_exactly_the_save_line(tmp_path: Path) -> None:
    folder, before = _failure_folder(tmp_path)
    _fail_like_the_app(folder, [SAVE])
    assert_only_usage_line(folder, before, saved=("professors/lindqvist.yaml",), **FAILED)


@pytest.mark.parametrize(
    "hub_lines",
    [
        [],  # the save line is missing
        [SAVE, SAVE],  # one line too many
        [SAVE, "2026-11-06 21:26:13,001 WARNING hub.ai.skills: rec-email failed: timeout"],
        ["2026-11-06 21:26:12,779 INFO hub.store: saved professors/jansen.yaml"],  # names another file
        [SAVE + " after rec-email timeout"],
    ],
)
def test_the_failure_check_refuses_any_other_hub_log_change(tmp_path: Path, hub_lines: list[str]) -> None:
    folder, before = _failure_folder(tmp_path)
    _fail_like_the_app(folder, hub_lines)
    with pytest.raises(AssertionError):
        assert_only_usage_line(folder, before, saved=("professors/lindqvist.yaml",), **FAILED)


def test_without_a_save_hub_log_must_not_change(tmp_path: Path) -> None:
    folder, before = _failure_folder(tmp_path)
    _fail_like_the_app(folder, [SAVE], save=False)
    with pytest.raises(AssertionError):
        assert_only_usage_line(folder, before, **FAILED)
    folder2, before2 = _failure_folder(tmp_path / "b")
    _fail_like_the_app(folder2, [], save=False)
    assert_only_usage_line(folder2, before2, **FAILED)


def test_a_rewritten_hub_log_is_refused(tmp_path: Path) -> None:
    folder, before = _failure_folder(tmp_path)
    _fail_like_the_app(folder, [])
    (folder / "logs" / "hub.log").write_text(SAVE + "\n", "utf-8")  # previous bytes gone
    with pytest.raises(AssertionError):
        assert_only_usage_line(folder, before, saved=("professors/lindqvist.yaml",), **FAILED)


def test_a_bad_line_is_reported_without_echoing_its_values(config) -> None:
    """data-integrity review W5: the banner names the field, never the text a hand-edited line holds."""
    path = usage.app_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _app_line(action="Dear Professor Jansen"),
        _app_line(source="My essay text"),
        _app_line(error="Biography detail"),
        _app_line(extra_payload="Some payload"),
        _app_line(skill="Prof. Dr. Eva Jansen"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = usage.read_all(config).problems
    assert len(problems) == len(lines)
    for problem in problems:
        for text in ("Dear Professor", "essay text", "Biography", "payload", "Some", "Jansen"):
            assert text not in problem.message, problem.message


def test_an_unexpected_exception_is_logged_as_failed_and_raised(
    config, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """data-integrity review note: a call that dies with something other than SkillError is still logged."""
    from hub.ai import skills

    class Broken:
        def call(self, *args: object) -> None:
            raise RuntimeError("broken pipe")

    monkeypatch.setattr(skills, "backend", lambda config: Broken())
    before = data_snapshot(data_dir)
    with pytest.raises(RuntimeError):
        run_skill("rec-email", _payload(), SCHEMA, config=config, source="app", **TAG)
    assert_only_usage_line(data_dir, before, ok=False, error="failed", source="app", **TAG)
