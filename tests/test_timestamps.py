"""Stored timestamps carry their UTC offset (DECISIONS #18); deadlines stay local time + zone (rule 10)."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from hub.models import CalendarMap, CvMeta, EssayMeta, Evaluation, GeneratedEmail
from hub.store import DataError, Store

from paths import INVALID

EDT = timezone(timedelta(hours=-4))
YAML_SAFE = YAML(typ="safe")
AWARE = datetime(2026, 11, 4, 14, 5, tzinfo=timezone(timedelta(hours=-5)))


def test_naive_generated_at_in_a_file_names_the_field(tmp_path: Path, config) -> None:
    root = tmp_path / "one"
    (root / "professors").mkdir(parents=True)
    shutil.copy(INVALID / "professors" / "naive-generated-at.yaml", root / "professors")
    with pytest.raises(DataError) as info:
        Store(root, config).load_professor(root / "professors" / "naive-generated-at.yaml")
    assert info.value.path == "professors/naive-generated-at.yaml"
    assert info.value.field == "emails[0].generated_at"


EVAL = {
    "criteria": [
        {"name": n, "rating": "adequate", "reason": "r", "comments": []}
        for n in ("structure", "content_fit", "programme_fit", "accuracy")
    ],
    "summary": "s",
    "exact_checks": {"word_count": 10},
}


@pytest.mark.parametrize(
    ("model", "data", "loc"),
    [
        (GeneratedEmail, {"generated_at": "2026-10-31T10:12:00", "programmes": [], "subject": "s",
                          "body": "b"},
         ("generated_at",)),
        (EssayMeta, {"programme_id": "booth-mif", "essay_id": "essay-1", "title": "t", "current_version": 1,
                     "versions": [{"n": 1, "saved_at": "2026-11-04T14:05:00", "words": 3}]},
         ("versions", 0, "saved_at")),
        (CvMeta, {"kind": "base", "current_version": 1,
                  "versions": [{"n": 1, "saved_at": "2026-11-04T14:05:00", "words": 3}]},
         ("versions", 0, "saved_at")),
        (CalendarMap, {"r1": {"event_id": "e", "hash": "h", "synced_at": "2026-11-04T14:05:00"}},
         ("r1", "synced_at")),
        (Evaluation, {**EVAL, "evaluated_at": "2026-11-04T14:05:00"}, ("evaluated_at",)),
    ],
    ids=["generated_at", "essay-saved_at", "cv-saved_at", "synced_at", "evaluated_at"],
)  # fmt: skip
def test_naive_timestamps_are_rejected(model, data, loc) -> None:
    with pytest.raises(ValidationError) as info:
        model.model_validate(data)
    assert tuple(info.value.errors()[0]["loc"]) == loc


def test_aware_generated_at_round_trips_and_is_written_as_iso_8601(data_dir: Path, config) -> None:
    path = data_dir / "professors" / "whitfield.yaml"
    store = Store.from_config(config)
    professor = store.load_professor(path)
    assert professor.emails[0].generated_at == datetime(2026, 10, 31, 10, 12, tzinfo=EDT)
    assert professor.emails[0].generated_at.utcoffset() == timedelta(hours=-4)
    professor.emails.append(
        professor.emails[0].model_copy(update={"generated_at": AWARE, "subject": "Second"})
    )
    store.save_professor(professor)
    text = path.read_text(encoding="utf-8")
    assert "  - generated_at: 2026-10-31T10:12:00-04:00\n" in text  # untouched entry, exactly as written
    assert "  - generated_at: 2026-11-04T14:05:00-05:00\n" in text  # new entry: unquoted, T, offset
    reloaded = store.load_professor(path).emails
    assert [e.generated_at for e in reloaded] == [datetime(2026, 10, 31, 10, 12, tzinfo=EDT), AWARE]
    assert reloaded[1].generated_at.utcoffset() == timedelta(hours=-5)


def test_aware_timestamps_round_trip_through_json() -> None:
    entry = CalendarMap.model_validate({"r1": {"event_id": "e", "hash": "h", "synced_at": AWARE.isoformat()}})
    again = CalendarMap.model_validate_json(entry.model_dump_json())
    assert again.root["r1"].synced_at == AWARE and again.root["r1"].synced_at.utcoffset() == timedelta(
        hours=-5
    )
    evaluation = Evaluation.model_validate({**EVAL, "evaluated_at": AWARE})
    assert '"evaluated_at":"2026-11-04T14:05:00-05:00"' in evaluation.model_dump_json()


def test_due_local_stays_a_local_time_and_is_never_converted(data_dir: Path, config) -> None:
    path = data_dir / "programmes" / "princeton-mfin.yaml"
    store = Store.from_config(config)
    programme = store.load_programme(path)
    due = programme.deadlines[0].due_local
    assert due == datetime(2026, 12, 15, 23, 59) and due.tzinfo is None  # paired with tz, not an instant
    assert programme.deadlines[0].tz == "America/New_York"
    programme.status = "in_progress"
    store.save_programme(programme)
    text = path.read_text(encoding="utf-8")
    assert "    due_local: 2026-12-15T23:59\n" in text
    assert "    start_local: 2026-12-19T10:00\n" in text
    raw = YAML_SAFE.load(text)
    assert raw["deadlines"][0]["due_local"] == "2026-12-15T23:59"  # a string in YAML, never a timestamp


# ---- regressions from the follow-up data-integrity review: loaded values must be plain datetimes ----


def _whitfield(data_dir: Path, config):
    path = data_dir / "professors" / "whitfield.yaml"
    store = Store.from_config(config)
    return path, store, store.load_professor(path)


def test_loaded_timestamp_is_a_plain_datetime(data_dir: Path, config) -> None:
    _, _, professor = _whitfield(data_dir, config)
    assert type(professor.emails[0].generated_at) is datetime


def test_deep_copy_keeps_the_offset_and_saves(data_dir: Path, config) -> None:
    path, store, professor = _whitfield(data_dir, config)
    copy = professor.model_copy(deep=True)
    assert copy.emails[0].generated_at.utcoffset() == timedelta(hours=-4)
    copy.notes = "Changed after a deep copy"
    store.save_professor(copy)
    assert store.load_professor(path).emails[0].generated_at == datetime(2026, 10, 31, 10, 12, tzinfo=EDT)


def test_shifted_timestamp_is_written_with_t(data_dir: Path, config) -> None:
    path, store, professor = _whitfield(data_dir, config)
    professor.emails[0].generated_at += timedelta(hours=1)
    store.save_professor(professor)
    assert "  - generated_at: 2026-10-31T11:12:00-04:00\n" in path.read_text(encoding="utf-8")


def test_timestamp_moved_to_another_zone_saves_with_its_new_offset(data_dir: Path, config) -> None:
    from zoneinfo import ZoneInfo

    path, store, professor = _whitfield(data_dir, config)
    professor.emails[0].generated_at = professor.emails[0].generated_at.astimezone(
        ZoneInfo("Europe/Amsterdam")
    )
    assert store.save_professor(professor) is True  # same instant, new offset: still a change to write
    assert "  - generated_at: 2026-10-31T15:12:00+01:00\n" in path.read_text(encoding="utf-8")
    reloaded = store.load_professor(path).emails[0].generated_at
    assert reloaded == datetime(2026, 10, 31, 10, 12, tzinfo=EDT) and reloaded.utcoffset() == timedelta(
        hours=1
    )
