"""Saved YAML keeps its layout (DECISIONS #19): block style everywhere, no wrapping, own line endings.

A one-field change must show up as only that field's line(s) in a diff, so the user's files stay
readable and OneDrive sync history stays meaningful.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from hub.models import Requirement
from hub.store import Store

from paths import FIXTURE_DATA, SEED_PROGRAMMES


def _flow_nodes(node, where: str = "") -> list[str]:
    """Paths of non-empty flow-style collections in a ruamel tree."""
    found = []
    if isinstance(node, CommentedMap | CommentedSeq):
        if len(node) and node.fa.flow_style():
            found.append(where or "<root>")
        children = node.items() if isinstance(node, CommentedMap) else enumerate(node)
        for key, child in children:
            found += _flow_nodes(child, f"{where}.{key}" if where else str(key))
    return found


def _changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    """(removed lines, added lines) between two texts."""
    removed, added = [], []
    for line in difflib.ndiff(before.splitlines(), after.splitlines()):
        if line.startswith("- "):
            removed.append(line[2:])
        elif line.startswith("+ "):
            added.append(line[2:])
    return removed, added


BLOCK_FILES = sorted(SEED_PROGRAMMES.glob("*.yaml")) + sorted(FIXTURE_DATA.rglob("*.yaml"))


@pytest.mark.parametrize("path", BLOCK_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_seed_and_fixture_files_are_block_style(path: Path) -> None:
    assert _flow_nodes(YAML().load(path.read_text(encoding="utf-8"))) == []


def test_one_field_inside_a_list_item_changes_one_line(data_dir: Path, config) -> None:  # (a)
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = Store.from_config(config)
    programme = store.load_programme(path)
    programme.interviews[0].notes = "Confirmed by email"
    store.save_programme(programme)
    removed, added = _changed_lines(before, path.read_text(encoding="utf-8"))
    assert removed == ['    notes: "Date and time estimated from last cycle\'s pattern"']
    assert added == ['    notes: "Confirmed by email"']  # the node keeps its existing (double-quoted) style


def test_one_top_level_scalar_changes_one_line(data_dir: Path, config) -> None:  # (b)
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = Store.from_config(config)
    programme = store.load_programme(path)
    programme.status = "in_progress"
    store.save_programme(programme)
    assert _changed_lines(before, path.read_text(encoding="utf-8")) == (
        ["status: not_started"],
        ["status: in_progress"],
    )


def test_one_top_level_scalar_changes_one_line_in_a_crlf_file(data_dir: Path, config) -> None:  # (b) CRLF
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    before = path.read_bytes()
    store = Store.from_config(config)
    programme = store.load_programme(path)
    programme.status = "in_progress"
    store.save_programme(programme)
    after = path.read_bytes()
    assert after.count(b"\n") == after.count(b"\r\n")  # every line still ends in CRLF
    assert _changed_lines(before.decode(), after.decode()) == (
        ["status: not_started"],
        ["status: in_progress"],
    )


def test_unchanged_save_writes_nothing(data_dir: Path, config) -> None:  # (c)
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    store = Store.from_config(config)
    assert store.save_programme(store.load_programme(path)) is False
    assert path.read_bytes() == before and path.stat().st_mtime_ns == stamp


def test_nested_mapping_field_changes_one_line(data_dir: Path, config) -> None:
    path = data_dir / "professors" / "jansen.yaml"
    before = path.read_text(encoding="utf-8")
    store = Store.from_config(config)
    professor = store.load_professor(path)
    professor.letters["booth-mif"].status = "confirmed"
    store.save_professor(professor)
    removed, added = _changed_lines(before, path.read_text(encoding="utf-8"))
    assert removed == ["    status: requested"] and added == ["    status: confirmed"]


def test_dropping_a_requirement_removes_only_its_lines(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = Store.from_config(config)
    programme = store.load_programme(path)
    programme.requirements = [r for r in programme.requirements if r.id != "gmat"]
    store.save_programme(programme)
    lines = before.splitlines()
    start = lines.index("  - id: gmat")
    assert lines[start : start + 4] == [
        "  - id: gmat",
        "    label: GMAT score report",
        '    note: "[verify whether optional this cycle]"',
        "    done: false",
    ]
    # exactly those four consecutive lines are gone; every other line is untouched
    assert path.read_text(encoding="utf-8").splitlines() == lines[:start] + lines[start + 4 :]


def test_new_list_item_is_written_in_block_style(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = Store.from_config(config)
    programme = store.load_programme(path)
    programme.requirements.append(Requirement(id="interview-prep", label="Interview preparation"))
    store.save_programme(programme)
    removed, added = _changed_lines(before, path.read_text(encoding="utf-8"))
    assert removed == []
    assert added == ["  - id: interview-prep", "    label: Interview preparation"]


@pytest.mark.parametrize("length", [250, 5000])
def test_long_text_is_never_wrapped(length: int, data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    store = Store.from_config(config)
    programme = store.load_programme(path)
    note = " ".join(["word"] * (length // 5)) + " end"
    assert len(note) > 200
    programme.requirements[0].note = note
    store.save_programme(programme)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert f"    note: {note}" in lines
    assert store.load_programme(path).requirements[0].note == note


FLOW_STYLE_FILE = """\
# A user file still in flow style (e.g. copied before DECISIONS #19)
id: flowy
name: Flow Example Programme
short_name: Flowy
school: Example University
aliases: [Flowy, Example]
sources: [{url: https://www.example.com/apply, title: Apply, checked: 2026-10-30}]
deadlines:
  - {id: main, label: Application deadline, due_local: 2027-01-05T23:59,
     tz: America/New_York, verification: unverified, source_url: https://www.example.com/apply}
requirements: [{id: cv, label: Resume, note: null, done: false}]
"""


def test_flow_style_user_file_becomes_block_style_on_first_save(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "flowy.yaml"
    path.write_text(FLOW_STYLE_FILE, encoding="utf-8")
    store = Store.from_config(config)
    programme = store.load_programme(path)
    expected = programme.model_dump()
    programme.status = "in_progress"
    store.save_programme(programme)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# A user file still in flow style")
    assert _flow_nodes(YAML().load(text)) == []
    reloaded = store.load_programme(path).model_dump()
    assert reloaded == {**expected, "status": "in_progress"}
