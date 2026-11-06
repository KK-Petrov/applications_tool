"""The behaviour every AI backend must have (DECISIONS #50; docs/04 §1.2), checked offline.

``run_skill`` is the app's only AI entry point; whichever backend ``claude.backend`` names, a call must:
turn a payload and a schema into a schema-valid dict; fail only with the four ``SkillError`` kinds, each
with a message ending "Nothing was saved."; log one usage line without the payload; and write nothing to
data_dir on failure. Each backend gets a harness that can make it succeed or fail in each way; only "cli"
exists (it uses the fake Claude Code). A new backend adds a harness here and must pass the same tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from hub.ai.skills import SkillError, load_schema, run_skill

from helpers import tree_digest

SCHEMA = load_schema("rec_email")
GOOD = {"subject": "S", "body": "Dear Professor Example,\n\nBody.", "used_biography_ids": [], "warnings": []}
SECRET = "CONTRACT-PAYLOAD-91c2"
KINDS = ("not_installed", "timeout", "failed", "invalid_output")


@dataclass
class Harness:
    """How to make one backend succeed or fail, given the test's config and monkeypatch."""

    succeed: Callable[[pytest.MonkeyPatch, Path], None]
    fail: dict[str, Callable[[pytest.MonkeyPatch, Path], dict]]  # kind → set up; returns config overrides


def _cli_succeed(mp: pytest.MonkeyPatch, tmp: Path) -> None:
    path = tmp / "good.json"
    path.write_text(
        json.dumps({"type": "result", "is_error": False, "structured_output": GOOD}), encoding="utf-8"
    )
    mp.setenv("FAKE_CLAUDE_RECORDING", str(path))


CLI = Harness(
    succeed=_cli_succeed,
    fail={
        "not_installed": lambda mp, tmp: {"path": str(tmp / "missing-claude.exe")},
        "timeout": lambda mp, tmp: (mp.setenv("FAKE_CLAUDE_MODE", "timeout"), {"timeout_s": 1})[1],
        "failed": lambda mp, tmp: (mp.setenv("FAKE_CLAUDE_MODE", "exit"), {})[1],
        "invalid_output": lambda mp, tmp: (mp.setenv("FAKE_CLAUDE_MODE", "schema_mismatch"), {})[1],
    },
)
HARNESSES = {"cli": CLI}


@pytest.fixture(params=sorted(HARNESSES))
def backend(request: pytest.FixtureRequest, config):
    """(backend name, harness, config using that backend)."""
    name = request.param
    claude = config.claude.model_copy(update={"backend": name})
    return name, HARNESSES[name], config.model_copy(update={"claude": claude})


def _with(config, **claude: object):
    return config.model_copy(update={"claude": config.claude.model_copy(update=claude)})


def _log_lines(config) -> list[dict]:
    path = Path(config.claude.usage_log)
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_payload_and_schema_give_a_validated_dict(backend, monkeypatch, tmp_path: Path) -> None:
    name, harness, cfg = backend
    harness.succeed(monkeypatch, tmp_path)
    result = run_skill("rec-email", {"x": SECRET}, SCHEMA, config=cfg)
    assert result.data == GOOD
    assert result.duration_s > 0
    (line,) = _log_lines(cfg)
    assert line["ok"] is True and line["skill"] == "rec-email" and line["backend"] == name
    assert SECRET not in Path(cfg.claude.usage_log).read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", KINDS)
def test_every_failure_is_one_of_four_kinds_logged_and_writes_nothing(
    backend, monkeypatch, tmp_path: Path, data_dir: Path, kind: str
) -> None:
    _, harness, cfg = backend
    cfg = _with(cfg, **harness.fail[kind](monkeypatch, tmp_path))
    before = tree_digest(data_dir)
    with pytest.raises(SkillError) as info:
        run_skill("rec-email", {"x": SECRET}, SCHEMA, config=cfg)
    assert info.value.kind == kind
    assert info.value.message.endswith("Nothing was saved.")
    assert tree_digest(data_dir) == before
    (line,) = _log_lines(cfg)
    assert line["ok"] is False and line["error"] == kind
    assert SECRET not in Path(cfg.claude.usage_log).read_text(encoding="utf-8")


def test_an_unknown_backend_is_refused(config) -> None:
    from hub.ai.skills import backend as pick

    cfg = _with(config, backend="cli")
    assert pick(cfg).__class__.__name__ == "CliBackend"
    with pytest.raises(ValueError):
        pick(
            config.model_copy(
                update={"claude": config.claude.model_construct(**{**config.claude.__dict__, "backend": "x"})}
            )
        )
