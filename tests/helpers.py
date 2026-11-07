"""Shared test helpers: the app's own origin, a fixed clock, a tiny HTML form serialiser, file snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient

BASE = "http://127.0.0.1:8765"  # the app's own origin in fast tests (Host and Origin checks, DECISIONS #26)
FIXED_NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # Friday 30 October 2026, 12:00 New York
FAKE_CLAUDE = Path(__file__).resolve().parent / "fakes" / "fake_claude.py"  # stands in for Claude Code
RECORDINGS = FAKE_CLAUDE.parent / "recordings"


class _FormParser(HTMLParser):
    """Collects what a browser would submit for one ``<form>``: inputs, checked boxes, selects, textareas."""

    def __init__(self, form_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.inside = False
        self.depth = 0
        self.fields: list[tuple[str, str]] = []
        self._select: str | None = None
        self._select_value: str | None = None
        self._first_option: str | None = None
        self._option_value: str | None = None
        self._option_selected = False
        self._option_text = ""
        self._textarea: str | None = None
        self._text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            if a.get("id") == self.form_id:
                self.inside, self.depth = True, 1
            elif self.inside:
                self.depth += 1
            return
        if not self.inside:
            return
        name = a.get("name")
        if tag == "input" and name and "disabled" not in a:
            kind = a.get("type", "text")
            if kind in ("submit", "button", "reset", "image"):
                return
            if kind in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
                return
            self.fields.append((name, a.get("value", "")))
        elif tag == "select" and name:
            self._select, self._select_value, self._first_option = name, None, None
        elif tag == "option" and self._select is not None:
            self._option_value = a.get("value")
            self._option_selected = "selected" in a
            self._option_text = ""
        elif tag == "textarea" and name:
            self._textarea, self._text = name, ""

    def handle_data(self, data: str) -> None:
        if self._textarea is not None:
            self._text += data
        elif self._select is not None:
            self._option_text += data

    def handle_endtag(self, tag: str) -> None:
        if not self.inside:
            return
        if tag == "form":
            self.depth -= 1
            if self.depth == 0:
                self.inside = False
        elif tag == "option" and self._select is not None:
            value = self._option_value if self._option_value is not None else self._option_text.strip()
            if self._first_option is None:
                self._first_option = value
            if self._option_selected:
                self._select_value = value
            self._option_value = None
        elif tag == "select" and self._select is not None:
            value = self._select_value if self._select_value is not None else self._first_option
            self.fields.append((self._select, value or ""))
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            text = self._text[1:] if self._text.startswith("\n") else self._text  # HTML drops one leading LF
            self.fields.append((self._textarea, text))
            self._textarea = None


def form_fields(html: str, form_id: str) -> list[tuple[str, str]]:
    """The (name, value) pairs a browser would submit for ``<form id=form_id>`` (no submit buttons)."""
    parser = _FormParser(form_id)
    parser.feed(html)
    if not parser.fields and f'id="{form_id}"' not in html:
        raise AssertionError(f"no form with id {form_id!r} in the page")
    return parser.fields


def set_field(fields: list[tuple[str, str]], name: str, value: str) -> list[tuple[str, str]]:
    """Return a copy with ``name`` set to ``value`` (replacing every existing value, or appending)."""
    out = [(k, v) for k, v in fields if k != name]
    out.append((name, value))
    return out


def long_description(length: int = 3000) -> str:
    """An invented course description of exactly ``length`` characters for verbatim round trips (US-6).

    It has line breaks, a blank line, an indented first line, double and single quotes, a colon, a hash,
    trailing spaces and non-ASCII letters and punctuation, so any trimming or re-quoting would show.
    """
    head = (
        "  Indented first line: Behavioural Finance (FIN-330) # not a comment\n"
        'Week 1 — "Prospect theory" and Kahneman’s ‘framing’; '
        "café, naïve, über, 中文.  \n"
        "\n"
    )
    body = "Topics: heuristics, biases, limits to arbitrage; it's 50% cases and 50% data work.\n"
    text = head
    while len(text) < length:
        text += body
    return text[: length - 1] + "\n"


def tree_digest(folder: Path) -> dict[str, str]:
    """sha256 of every file under ``folder`` (relative path → digest), to prove nothing was written.

    It leaves out ``logs/``. AI failure paths use ``data_snapshot`` and ``assert_only_usage_line``
    instead, which include it (C-2b-05 made stricter, DECISIONS #60).
    """
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and "logs" not in p.relative_to(folder).parts
    }


USAGE_LOG = "logs/ai_usage.jsonl"  # the app's usage log, relative to data_dir (docs/01 §11)


DIR = b"<folder>"  # data_snapshot's value for a folder, so a new (even empty) folder shows as a change


def data_snapshot(folder: Path) -> dict[str, bytes]:
    """Every file and folder under ``folder``, ``logs/`` included (relative path → content; folders end
    in "/" and map to ``DIR``)."""
    return {
        (rel + "/" if p.is_dir() else rel): (DIR if p.is_dir() else p.read_bytes())
        for p in sorted(folder.rglob("*"))
        for rel in [p.relative_to(folder).as_posix()]
    }


HUB_LOG = "logs/hub.log"
# The store's save message as the app's logger writes it (app._configure_logging: "%(asctime)s %(levelname)s
# %(name)s: %(message)s"), e.g. "2026-11-06 21:26:12,779 INFO hub.store: saved professors/jansen.yaml".
_SAVE_LINE = r"\d{{4}}-\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}:\d{{2}},\d{{3}} INFO hub\.store: saved {file}"


USAGE_FIELDS = ["at", "skill", "backend", "action", "subject", "source", "duration_s", "total_cost_usd"]
USAGE_FIELDS += ["ok", "error"]


def assert_only_usage_line(
    folder: Path,
    before: dict[str, bytes],
    *,
    saved: tuple[str, ...] = (),
    run_log: Path | None = None,
    **expected: Any,
) -> dict[str, Any]:
    """C-2b-05 (stricter, DECISIONS #60). On a failure path the only changes in data_dir are exactly one
    appended line in ``logs/ai_usage.jsonl`` (with the ``expected`` fields) and, when the same request
    saved a data file first, that file and exactly one appended ``hub.log`` line naming it; nothing else.

    ``saved`` lists the data files the request saved before the failed call (e.g. Save and generate email).
    With none, ``hub.log`` must not change at all. With some, ``hub.log`` keeps its previous bytes and gains
    exactly one store save line per file, in order; any other line (one naming the skill, the call or the
    error, say) fails. The line must have exactly the documented fields, a subject of IDs only, and the
    ``expected`` values, which must include ``ok``, ``error``, ``action``, ``subject`` and ``source``.
    ``run_log``, if given, must not exist afterwards (an app failure writes no eval/live line).
    Returns the appended usage line.
    """
    from hub.ai.usage import validate_subject

    missing = {"ok", "error", "action", "subject", "source"} - set(expected)
    assert not missing, f"the check needs expected {sorted(missing)}"
    after = data_snapshot(folder)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    allowed = {USAGE_LOG, "logs/", *saved, *([HUB_LOG] if saved else [])}  # logs/ may be created for it
    unexpected = sorted(changed - allowed)
    assert not unexpected, f"files changed besides one usage line: {unexpected}"
    for file in saved:
        assert file in changed, f"{file} was not saved"
    if saved:
        old_log, new_log = before.get(HUB_LOG, b""), after.get(HUB_LOG, b"")
        assert new_log.startswith(old_log), "hub.log was rewritten, not appended to"
        appended = new_log[len(old_log) :].decode("utf-8")
        assert appended.endswith("\n"), f"hub.log gained a partial line: {appended!r}"
        lines = appended.splitlines()
        assert len(lines) == len(saved), f"expected {len(saved)} hub.log line(s), got {lines}"
        for line, file in zip(lines, saved, strict=True):
            assert re.fullmatch(_SAVE_LINE.format(file=re.escape(file)), line), (
                f"unexpected hub.log line {line!r}"
            )
            for word in ("rec-email", "claude", "skill", "error", "timeout", "invalid"):
                assert word not in line.lower(), f"hub.log line mentions {word!r}: {line!r}"
    assert USAGE_LOG in changed, "no usage line was written"
    old, new = before.get(USAGE_LOG, b""), after[USAGE_LOG]
    assert new.startswith(old), "the usage log was rewritten, not appended to"
    added = new[len(old) :].decode("utf-8")
    assert added.endswith("\n") and added.count("\n") == 1, f"expected exactly one new line, got {added!r}"
    line = json.loads(added)
    assert list(line) == USAGE_FIELDS, f"unexpected fields: {list(line)}"
    assert validate_subject(line["subject"]) == line["subject"], "the subject is not IDs only"
    for key, value in expected.items():
        assert line[key] == value, f"{key}: {line[key]!r} != {value!r}"
    if run_log is not None:
        assert not run_log.exists(), "an app failure wrote to the eval/live run log"
    return line


class FormClient(TestClient):
    """TestClient whose ``post(data=[(name, value), ...])`` sends repeated fields like a browser.

    httpx drops ``data`` given as a list of pairs, so it is URL-encoded here; dicts pass through.
    """

    def post(self, url: Any, *, data: Any = None, headers: Any = None, **kwargs: Any):  # type: ignore[override]
        if isinstance(data, list):
            headers = {**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"}
            return super().post(url, content=urlencode(data), headers=headers, **kwargs)
        return super().post(url, data=data, headers=headers, **kwargs)


class UsageLogOutsideTmp(AssertionError):
    """A non-live test resolved an AI usage log outside its tmp_path (it would write to the real log)."""


def check_usage_log(path: Path, root: Path) -> Path:
    """``path`` if it lies inside ``root``; otherwise raise ``UsageLogOutsideTmp``."""
    resolved, base = path.resolve(), root.resolve()
    if resolved != base and base not in resolved.parents:
        raise UsageLogOutsideTmp(f"AI usage log {resolved} is outside the test's tmp_path {base}")
    return path
