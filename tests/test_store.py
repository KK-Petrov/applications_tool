"""Store behaviour: comment-preserving round trips, deletions survive a save, atomic writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from hub.models import Programme
from hub.store import DataError, Store


def _store(config) -> Store:
    return Store.from_config(config)


def test_round_trip_without_changes_keeps_the_file_identical(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = _store(config)
    store.save_programme(store.load_programme(path))
    assert path.read_text(encoding="utf-8") == before


def test_change_keeps_comments_and_key_order(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    store = _store(config)
    programme = store.load_programme(path)
    programme.requirements[1].done = True
    store.save_programme(programme)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Seed data researched 30 Oct 2026.")
    keys = [ln.split(":")[0] for ln in text.splitlines() if ln and not ln.startswith((" ", "#", "-"))]
    assert keys[:4] == ["id", "name", "short_name", "school"]
    reloaded = store.load_programme(path)
    assert reloaded.requirements[1].done is True
    assert reloaded.requirements[0].done is False


def test_dropped_list_item_is_gone_after_save(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    store = _store(config)
    programme = store.load_programme(path)
    before_ids = [r.id for r in programme.requirements]
    programme.requirements = [r for r in programme.requirements if r.id != "gmat"]
    store.save_programme(programme)
    reloaded = store.load_programme(path)
    assert [r.id for r in reloaded.requirements] == [i for i in before_ids if i != "gmat"]
    text = path.read_text(encoding="utf-8")
    requirements_block = text[text.index("requirements:") : text.index("steps:")]
    assert "id: gmat" not in requirements_block  # the dropped requirement (block style, DECISIONS #19)
    assert text.startswith("# Seed data researched 30 Oct 2026.")


def test_shrunk_list_without_ids_is_truncated(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    store = _store(config)
    programme = store.load_programme(path)
    programme.steps = programme.steps[:2]
    store.save_programme(programme)
    assert [s.order for s in store.load_programme(path).steps] == [1, 2]


def test_removed_field_is_gone_after_save(data_dir: Path, config) -> None:
    path = data_dir / "professors" / "jansen.yaml"
    store = _store(config)
    professor = store.load_professor(path)
    data = professor.model_dump(exclude_unset=True)
    del data["interaction"]  # an optional field the user cleared out of the file
    del data["letters"]["booth-mif"]  # a letter that is no longer asked for (absent key = not asked)
    store.save_professor(type(professor).model_validate(data))
    text = path.read_text(encoding="utf-8")
    assert "interaction:" not in text
    assert "booth-mif" not in text
    assert text.startswith("# Test fixture: invented professor.")
    reloaded = store.load_professor(path)
    assert reloaded.interaction is None
    assert set(reloaded.letters) == {"princeton-mfin"}


def test_multiline_text_is_kept_verbatim(data_dir: Path, config) -> None:
    path = data_dir / "professors" / "jansen.yaml"
    store = _store(config)
    professor = store.load_professor(path)
    original = (
        "Line one.\n\n  Indented line two with trailing text.\nLine three: colons, # hashes, 'quotes'.\n"
    )
    professor.course_description = original
    store.save_professor(professor)
    assert store.load_professor(path).course_description == original


def test_null_stays_written_as_null(data_dir: Path, config) -> None:
    path = data_dir / "professors" / "jansen.yaml"
    store = _store(config)
    professor = store.load_professor(path)
    professor.grade = "9.0 / 10"
    store.save_professor(professor)
    text = path.read_text(encoding="utf-8")
    assert "notes: null\n" in text
    assert "requested_on: null" in text


@pytest.mark.filterwarnings("ignore::UserWarning")  # model_construct with an invalid enum value, on purpose
def test_failed_validation_never_overwrites(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    before = path.read_text(encoding="utf-8")
    store = _store(config)
    programme = store.load_programme(path)
    broken = Programme.model_construct(**{**programme.__dict__, "status": "started"})
    with pytest.raises(DataError) as info:
        store.save_programme(broken)
    assert info.value.field == "status"
    assert path.read_text(encoding="utf-8") == before
    assert not list(path.parent.glob("*.tmp"))


def test_new_file_is_written_atomically(data_dir: Path, config) -> None:
    store = _store(config)
    source = store.load_programme(data_dir / "programmes" / "booth-mif.yaml")
    copy = source.model_copy(update={"id": "booth-mif-copy"})
    store.save_programme(copy)
    target = data_dir / "programmes" / "booth-mif-copy.yaml"
    assert target.exists()
    assert not list(target.parent.glob("*.tmp"))
    assert store.load_programme(target).id == "booth-mif-copy"
    assert (
        store.load_programme(target).deadlines[0].due_local.isoformat(timespec="minutes")
        == "2027-01-07T23:59"
    )


def test_local_datetime_is_written_without_seconds_or_offset(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "mit-sloan-mfin.yaml"
    store = _store(config)
    programme = store.load_programme(path)
    programme.deadlines[0].label = "Application deadline (changed)"
    store.save_programme(programme)
    assert "due_local: 2027-01-05T23:59\n" in path.read_text(encoding="utf-8")


# ---- regressions found by the data-integrity review (2026-11-04) ---------------------------------

SPECIAL_TEXTS = {
    "line-separator": "first second",
    "nbsp-and-ls": "a b c\nd\n",
    "next-line": "first\u0085second\nthird\n",
    "paragraph-separator": "para para\n",
    "lone-cr": "a\rb\n",
    "crlf": "a\r\nb\r\n",
    "vertical-tab-form-feed": "a\x0bb\x0cc\nd\n",
    "tab-and-trailing-space": "a\tb  \n  c\n",
    "emoji-and-accents": "Café ☕ résumé 😀\nnext line\n",
    "looks-like-yaml": "null\nyes\n2026-12-15\n- item\nkey: value\n",
}


@pytest.mark.parametrize("name", sorted(SPECIAL_TEXTS))
def test_text_over_an_existing_literal_block_round_trips(name: str, data_dir: Path, config) -> None:
    """The fixture's course_description is a ``|`` block; any replacement text must reload exactly."""
    path = data_dir / "professors" / "jansen.yaml"
    store = _store(config)
    professor = store.load_professor(path)
    professor.course_description = SPECIAL_TEXTS[name]
    store.save_professor(professor)
    assert store.load_professor(path).course_description == SPECIAL_TEXTS[name]


MINIMAL_PROFESSOR = (
    "id: minimal\nname: Prof. Min Imal\nemail: m.imal@example.org\nuniversity: RSM\ncourse: C\n"
)


def test_in_place_change_to_a_defaulted_dict_is_saved(data_dir: Path, config) -> None:
    from hub.models import Letter

    path = data_dir / "professors" / "minimal.yaml"
    path.write_text(MINIMAL_PROFESSOR, encoding="utf-8")
    store = _store(config)
    professor = store.load_professor(path)
    professor.letters["booth-mif"] = Letter(status="requested")
    assert store.save_professor(professor) is True
    assert store.load_professor(path).letters["booth-mif"].status == "requested"


def test_in_place_changes_to_defaulted_list_and_model_are_saved(data_dir: Path, config) -> None:
    from hub.models import Requirement

    path = data_dir / "programmes" / "booth-mif.yaml"
    text = path.read_text(encoding="utf-8")
    start, end = text.index("requirements:"), text.index("steps:")
    text = text[:start] + text[end:]
    text = text[: text.index("curriculum:")] + text[text.index("ai_policy:") :]
    path.write_text(text, encoding="utf-8")
    store = _store(config)
    programme = store.load_programme(path)
    assert programme.requirements == [] and programme.curriculum.summary is None
    programme.requirements.append(Requirement(id="cv", label="Resume"))
    programme.curriculum.summary = "New summary"
    assert store.save_programme(programme) is True
    reloaded = store.load_programme(path)
    assert [r.id for r in reloaded.requirements] == ["cv"]
    assert reloaded.curriculum.summary == "New summary"


def test_first_biography_entry_in_an_empty_folder_is_saved(tmp_path: Path, config) -> None:
    from hub.models import BiographyEntry

    store = Store(tmp_path, config)
    biography = store.load_biography()
    biography.entries.append(BiographyEntry(id="gmat", category="test", title="Invented score"))
    store.save_biography(biography)
    assert [e.id for e in store.load_biography().entries] == ["gmat"]


def test_data_error_text_is_plain_ascii_for_any_console() -> None:
    err = DataError("programmes/x.yaml", "deadlines[0].tz", "Field required")
    assert str(err).isascii()
    assert "programmes/x.yaml" in str(err) and "deadlines[0].tz" in str(err)


def test_settings_save_rejects_a_display_zone_outside_the_config(data_dir: Path, config) -> None:
    store = _store(config)
    settings = store.load_settings()
    before = (data_dir / "settings.yaml").read_bytes()
    settings.display_tz = "Asia/Tokyo"
    with pytest.raises(DataError) as info:
        store.save_settings(settings)
    assert info.value.field == "display_tz"
    assert (data_dir / "settings.yaml").read_bytes() == before
