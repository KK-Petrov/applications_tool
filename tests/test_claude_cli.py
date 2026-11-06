"""C-2b-05: the Claude Code wrapper (docs/04 §1.1) and its failure paths.

Every failure (Claude Code missing, timeout, non-zero exit, invalid or schema-breaking output) shows a clear
message and writes nothing to data_dir. The wrapper also locks headless calls down (DECISIONS, Phase 2b):
no shell, no file tools, no MCP, no project hooks, a turn cap, and only the tools a skill needs.
All calls go to tests/fakes/fake_claude.py; nothing here reaches the real CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hub.ai.claude_cli import LOCKED_OUT_TOOLS, build_argv
from hub.ai.skills import SKILL_TOOLS, SkillError, run_skill
from hub.app import create_app

from helpers import BASE, FAKE_CLAUDE, FIXED_NOW, FormClient, form_fields, set_field, tree_digest
from paths import REPO_ROOT

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["subject", "body", "used_biography_ids", "warnings"],
    "properties": {
        "subject": {"type": "string", "maxLength": 140},
        "body": {"type": "string"},
        "used_biography_ids": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}
GOOD = {"subject": "S", "body": "Dear Professor Jansen,\n\nBody.", "used_biography_ids": [], "warnings": []}
SECRET = "PAYLOAD-MARKER-7f3a"  # must never reach the usage log
HX = {"HX-Request": "true"}


def _with(config, **claude: object):
    return config.model_copy(update={"claude": config.claude.model_copy(update=claude)})


@pytest.fixture
def log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake's call log: one JSON line per call (argv, stdin, cwd)."""
    path = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))
    return path


@pytest.fixture
def good_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "good.json"
    path.write_text(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "11111111-2222-4333-8444-555555555555",
                "total_cost_usd": 0.042,
                "permission_denials": [],
                "structured_output": GOOD,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(path))
    return path


def _calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _flag(argv: list[str], name: str) -> str:
    assert name in argv, f"{name} missing from {argv}"
    return argv[argv.index(name) + 1]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out.split()
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---- argv and lockdown ---------------------------------------------------------------------------


def test_call_uses_an_argv_list_with_the_documented_flags(config, log: Path, good_recording: Path) -> None:
    run_skill("rec-email", {"x": SECRET}, SCHEMA, config=config)
    (call,) = _calls(log)
    argv = call["argv"]
    assert _flag(argv, "-p") == "The input is the JSON document on stdin."
    # The skill reaches the model as its SKILL.md (no settings source is loaded, so no project skills).
    skill_md = REPO_ROOT / ".claude" / "skills" / "rec-email" / "SKILL.md"
    assert Path(_flag(argv, "--append-system-prompt-file")).resolve() == skill_md.resolve()
    assert _flag(argv, "--output-format") == "json"
    assert json.loads(_flag(argv, "--json-schema")) == SCHEMA
    assert _flag(argv, "--permission-mode") == "dontAsk"
    assert "--bare" not in argv  # bare mode ignores the subscription login and skills (docs/04 §1.1)
    assert json.loads(call["stdin"]) == {"x": SECRET}
    assert Path(call["cwd"]).resolve() == REPO_ROOT.resolve()  # project skills load from the repo


@pytest.mark.parametrize("skill", sorted(SKILL_TOOLS))
def test_every_skill_call_is_locked_down(config, skill: str) -> None:
    argv = build_argv(skill, SCHEMA, config, executable=["claude"])
    allowed = list(SKILL_TOOLS[skill])
    assert _flag(argv, "--tools") == ",".join(allowed)
    denied = set(_flag(argv, "--disallowedTools").split(","))
    for tool in ("Bash", "PowerShell", "Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "Glob", "Grep"):
        assert tool in denied, f"{tool} not denied for {skill}"
    assert denied == set(LOCKED_OUT_TOOLS) - set(allowed)
    assert not denied & set(allowed)
    assert "--strict-mcp-config" in argv
    assert _flag(argv, "--setting-sources") == ""  # no settings file applies, the project's included
    assert Path(_flag(argv, "--append-system-prompt-file")).parts[-2:] == (skill, "SKILL.md")
    assert json.loads(_flag(argv, "--settings")).get("disableAllHooks") is True
    assert _flag(argv, "--permission-mode") == "dontAsk"
    assert _flag(argv, "--permission-prompts") == "none"
    assert "--no-session-persistence" in argv
    assert int(_flag(argv, "--max-turns")) == config.claude.max_turns <= 3
    for unsafe in (
        "--bare",
        "--dangerously-skip-permissions",
        "bypassPermissions",
        "--allow-dangerously-skip-permissions",
    ):
        assert unsafe not in argv


def test_only_research_gets_web_tools_and_no_skill_gets_a_shell_or_file_tool() -> None:
    assert SKILL_TOOLS["rec-email"] == ()
    assert SKILL_TOOLS["grade-claims"] == ()
    assert set(SKILL_TOOLS["research-programme"]) == {"WebSearch", "WebFetch"}
    for skill, tools in SKILL_TOOLS.items():
        assert not set(tools) & {
            "Bash",
            "PowerShell",
            "Edit",
            "Write",
            "MultiEdit",
            "NotebookEdit",
            "Read",
        }, skill


def test_unknown_skill_and_extra_tools_are_refused(config) -> None:
    with pytest.raises(ValueError):
        run_skill("not-a-skill", {}, SCHEMA, config=config)
    with pytest.raises(ValueError):
        run_skill("rec-email", {}, SCHEMA, config=config, tools=["Bash"])


# ---- success -------------------------------------------------------------------------------------


def test_success_returns_the_structured_output(config, good_recording: Path) -> None:
    result = run_skill("rec-email", {"x": 1}, SCHEMA, config=config)
    assert result.data == GOOD
    assert result.cost_usd == pytest.approx(0.042)
    assert result.session_id == "11111111-2222-4333-8444-555555555555"
    assert result.duration_s > 0


def test_usage_log_has_one_line_per_call_and_never_the_payload(
    config, good_recording: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_skill("rec-email", {"x": SECRET}, SCHEMA, config=config)
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "invalid_json")
    with pytest.raises(SkillError):
        run_skill("rec-email", {"x": SECRET}, SCHEMA, config=config)
    text = Path(config.claude.usage_log).read_text(encoding="utf-8")
    assert SECRET not in text
    ok, bad = (json.loads(line) for line in text.splitlines())
    assert ok["skill"] == "rec-email" and ok["ok"] is True and ok["total_cost_usd"] == pytest.approx(0.042)
    assert ok["duration_s"] >= 0
    assert bad["ok"] is False and bad["error"] == "invalid_output"


# ---- failures ------------------------------------------------------------------------------------


def test_missing_binary_is_not_installed(config, tmp_path: Path) -> None:
    cfg = _with(config, path=str(tmp_path / "no-such-claude.exe"))
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {}, SCHEMA, config=cfg)
    assert info.value.kind == "not_installed"
    assert "Claude Code was not found" in info.value.message


def test_no_path_and_nothing_on_path_is_not_installed(config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_CLAUDE_PATH", raising=False)
    monkeypatch.setattr("hub.ai.claude_cli.shutil.which", lambda name: None)
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {}, SCHEMA, config=_with(config, path=None))
    assert info.value.kind == "not_installed"


def test_timeout_kills_the_whole_process_tree(
    config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pidfile = tmp_path / "pids.txt"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "timeout")
    monkeypatch.setenv("FAKE_CLAUDE_PIDFILE", str(pidfile))
    start = time.monotonic()
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {}, SCHEMA, config=config, timeout_s=2)
    assert time.monotonic() - start < 20
    assert info.value.kind == "timeout"
    assert info.value.message == "Claude Code did not respond within 2 s. Nothing was saved."
    pids = [int(x) for x in pidfile.read_text(encoding="utf-8").split()]
    assert len(pids) == 2
    deadline = time.monotonic() + 10
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(_alive(p) for p in pids), "the fake or its child survived the timeout"


def test_non_zero_exit_reports_the_first_stderr_line(config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "exit")
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {}, SCHEMA, config=config)
    assert info.value.kind == "failed"
    assert "fake failure: boom" in info.value.message
    assert "second line" not in info.value.message
    assert info.value.message.endswith("Nothing was saved.")


@pytest.mark.parametrize("mode", ["invalid_json", "is_error", "no_structured", "schema_mismatch"])
def test_unusable_output_is_invalid_output(config, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {}, SCHEMA, config=config)
    assert info.value.kind == "invalid_output"
    assert "Claude Code returned output the app could not use" in info.value.message
    assert info.value.message.endswith("Nothing was saved.")


# ---- the app: every failure shows its message and writes nothing --------------------------------

FAILURES = {
    "not_installed": ("Claude Code was not found", {}),
    "timeout": (
        "Claude Code did not respond within 1 s. Nothing was saved.",
        {"FAKE_CLAUDE_MODE": "timeout"},
    ),
    "exit": ("Claude Code stopped with an error: fake failure: boom", {"FAKE_CLAUDE_MODE": "exit"}),
    "invalid_json": (
        "Claude Code returned output the app could not use",
        {"FAKE_CLAUDE_MODE": "invalid_json"},
    ),
    "is_error": ("Claude Code returned output the app could not use", {"FAKE_CLAUDE_MODE": "is_error"}),
    "no_structured": (
        "Claude Code returned output the app could not use",
        {"FAKE_CLAUDE_MODE": "no_structured"},
    ),
    "schema_mismatch": (
        "Claude Code returned output the app could not use",
        {"FAKE_CLAUDE_MODE": "schema_mismatch"},
    ),
}


@pytest.fixture
def failing_app(config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """The app with a 1 s timeout and the fake set to fail as the test's ``failure`` parameter says."""
    message, env = FAILURES[request.param]
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    cfg = _with(config, timeout_s=1)
    if request.param == "not_installed":
        cfg = _with(cfg, path=str(tmp_path / "no-such-claude.exe"))
    client = FormClient(
        create_app(cfg, clock=lambda: FIXED_NOW),
        base_url=BASE,
        headers={"Origin": BASE},
        follow_redirects=False,
    )
    return client, message


@pytest.mark.parametrize("failing_app", sorted(FAILURES), indirect=True)
@pytest.mark.parametrize("htmx", [True, False], ids=["htmx", "no-js"])
def test_failed_generation_shows_a_clear_message_and_writes_nothing(
    failing_app, data_dir: Path, htmx: bool
) -> None:
    client, message = failing_app
    before = tree_digest(data_dir)
    res = client.post(
        "/recommenders/jansen/email", data=[("programmes", "princeton-mfin")], headers=HX if htmx else None
    )
    assert message in res.text
    assert "Nothing was saved." in res.text
    if not htmx:
        assert res.status_code == 409
    assert "Traceback" not in res.text
    assert tree_digest(data_dir) == before


@pytest.mark.parametrize("failing_app", sorted(FAILURES), indirect=True)  # review: every kind, not 2
def test_save_and_generate_keeps_the_professor_but_writes_no_email(failing_app, data_dir: Path) -> None:
    client, message = failing_app
    before = tree_digest(data_dir)
    fields = form_fields(client.get("/recommenders/new").text, "professor-form")
    for name, value in {
        "name": "Prof. Rosa Lindqvist",
        "email": "r.lindqvist@example.com",
        "university": "RSM",
        "course": "Behavioural Finance (FIN-330)",
        "course_description": "Invented description.",
    }.items():
        fields = set_field(fields, name, value)
    fields = [*fields, ("programmes", "princeton-mfin"), ("then", "generate")]
    res = client.post("/recommenders", data=fields)
    assert res.status_code == 303
    location = res.headers["location"]
    assert location.startswith("/recommenders?") and "selected=lindqvist" in location
    page = client.get(location).text
    # ui-reviewer: the old "Professor saved. … Nothing was saved." contradicted itself (disclosed change)
    assert "The professor was saved, but no email was generated:" in page
    assert message.removesuffix(" Nothing was saved.") in page
    assert "Nothing was saved" not in _alert(page)
    after = tree_digest(data_dir)
    assert set(after) - set(before) == {"professors/lindqvist.yaml"}
    assert {k: v for k, v in after.items() if k in before} == before
    stored = (data_dir / "professors" / "lindqvist.yaml").read_text(encoding="utf-8")
    assert "draft_ready" not in stored and "generated_at" not in stored


def _alert(page: str) -> str:
    """The text of the panel's email error (the message the owner reads)."""
    start = page.index('class="form-error" role="alert"')
    return page[start : page.index("</div>", start)]


def test_fake_is_the_binary_under_test() -> None:
    """Guard: the suite never points at a real Claude Code."""
    assert os.environ["HUB_CLAUDE_PATH"] == str(FAKE_CLAUDE)


@pytest.mark.parametrize("failing_app", sorted(FAILURES), indirect=True)
def test_edit_and_generate_failure_keeps_the_edit_but_writes_no_email(failing_app, data_dir: Path) -> None:
    """Save and generate on Edit: the edit is saved; the failed generation adds nothing (review)."""
    client, message = failing_app
    fields = form_fields(client.get("/recommenders/jansen/edit").text, "professor-form")
    fields = [*set_field(fields, "term", "Spring 2026"), ("then", "generate")]
    res = client.post("/recommenders/jansen", data=fields)
    assert res.status_code == 303
    page = client.get(res.headers["location"]).text
    assert "The professor was saved, but no email was generated:" in page
    assert message.removesuffix(" Nothing was saved.") in page and "Nothing was saved" not in _alert(page)
    stored = (data_dir / "professors" / "jansen.yaml").read_text(encoding="utf-8")
    assert "term: Spring 2026" in stored
    assert "generated_at" not in stored  # jansen had no emails; none was added
    assert "princeton-mfin:\n    status: draft_ready" in stored.replace(
        "\r\n", "\n"
    )  # unchanged from the fixture
