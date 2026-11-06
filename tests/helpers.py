"""Shared test helpers: the app's own origin, a fixed clock, a tiny HTML form serialiser, file snapshots."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi.testclient import TestClient

BASE = "http://127.0.0.1:8765"  # the app's own origin in fast tests (Host and Origin checks, DECISIONS #26)
FIXED_NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)  # Friday 30 October 2026, 12:00 New York


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
    """sha256 of every file under ``folder`` (relative path → digest), to prove nothing was written."""
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and "logs" not in p.relative_to(folder).parts
    }


class FormClient(TestClient):
    """TestClient whose ``post(data=[(name, value), ...])`` sends repeated fields like a browser.

    httpx drops ``data`` given as a list of pairs, so it is URL-encoded here; dicts pass through.
    """

    def post(self, url: Any, *, data: Any = None, headers: Any = None, **kwargs: Any):  # type: ignore[override]
        if isinstance(data, list):
            headers = {**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"}
            return super().post(url, content=urlencode(data), headers=headers, **kwargs)
        return super().post(url, data=data, headers=headers, **kwargs)
