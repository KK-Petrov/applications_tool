"""C-0-04: every seed file validates; an invalid file produces an error naming the file and field."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hub.models import Biography, Professor, Programme, Settings
from hub.store import DataError, Store

from paths import FIXTURE_DATA, INVALID, SEED_PROGRAMMES

SEED_FILES = sorted(SEED_PROGRAMMES.glob("*.yaml"))


def test_four_seed_programmes_exist() -> None:
    assert [p.stem for p in SEED_FILES] == ["booth-mif", "columbia-msfe", "mit-sloan-mfin", "princeton-mfin"]


@pytest.mark.parametrize("path", SEED_FILES, ids=lambda p: p.stem)
def test_seed_programme_validates(path: Path, tmp_path: Path, config) -> None:
    target = tmp_path / "seedcheck"
    (target / "programmes").mkdir(parents=True)
    shutil.copy(path, target / "programmes" / path.name)
    programme = Store(target, config).load_programme(target / "programmes" / path.name)
    assert isinstance(programme, Programme)
    assert programme.id == path.stem
    assert programme.verification == "unverified"  # rule 11: seed data stays unverified
    assert programme.verification_note  # every seed carries its badge reason
    for deadline in programme.deadlines:
        assert deadline.tz and deadline.source_url  # rule 10


def test_fixture_data_folder_loads_without_errors(config) -> None:
    snapshot = Store.from_config(config).load_all()
    assert snapshot.errors == []
    assert isinstance(snapshot.settings, Settings)
    assert len(snapshot.programmes) == 4
    assert {p.id for p in snapshot.professors} == {"jansen", "whitfield"}
    assert isinstance(snapshot.biography, Biography)
    assert all(isinstance(p, Professor) for p in snapshot.professors)


# file → (expected field, fragment expected in the message or None)
INVALID_PROGRAMMES = {
    "missing-tz": ("deadlines[0].tz", "required"),
    "missing-source-url": ("deadlines[0].source_url", "required"),
    "bad-tz": ("deadlines[0].tz", "Eastern"),
    "bad-status": ("status", None),
    "bad-due-local": ("deadlines[0].due_local", "YYYY-MM-DDTHH:MM"),
    "id-mismatch": ("id", "id-mismatch"),
    "verified-no-sources": ("sources", "source"),
    "verified-sources-absent": ("sources", "source"),
    "unknown-key": ("deadlines[0].timezone_name", None),
    "syntax-error": (None, "line"),
}


def _single_file_dir(tmp_path: Path, kind: str, src: Path) -> Path:
    root = tmp_path / "one"
    (root / kind).mkdir(parents=True)
    shutil.copy(src, root / kind / src.name)
    return root


@pytest.mark.parametrize("name", sorted(INVALID_PROGRAMMES))
def test_invalid_programme_names_file_and_field(name: str, tmp_path: Path, config) -> None:
    root = _single_file_dir(tmp_path, "programmes", INVALID / "programmes" / f"{name}.yaml")
    with pytest.raises(DataError) as info:
        Store(root, config).load_programme(root / "programmes" / f"{name}.yaml")
    err = info.value
    expected_field, fragment = INVALID_PROGRAMMES[name]
    assert err.path == f"programmes/{name}.yaml"
    assert err.field == expected_field
    if fragment:
        assert fragment.lower() in err.message.lower()
    shown = str(err)
    assert f"programmes/{name}.yaml" in shown
    if expected_field:
        assert expected_field in shown


def test_invalid_professor_names_file_and_field(tmp_path: Path, config) -> None:
    root = _single_file_dir(tmp_path, "professors", INVALID / "professors" / "bad-letter-status.yaml")
    with pytest.raises(DataError) as info:
        Store(root, config).load_professor(root / "professors" / "bad-letter-status.yaml")
    assert info.value.path == "professors/bad-letter-status.yaml"
    assert info.value.field == "letters.princeton-mfin.status"


def test_invalid_settings_names_file_and_field(tmp_path: Path, config) -> None:
    root = tmp_path / "s"
    root.mkdir()
    shutil.copy(INVALID / "settings" / "settings.yaml", root / "settings.yaml")
    with pytest.raises(DataError) as info:
        Store(root, config).load_settings()
    assert info.value.path == "settings.yaml"
    assert info.value.field == "owner_email"


def test_display_tz_must_be_a_configured_display_zone(tmp_path: Path, config) -> None:
    root = tmp_path / "s"
    root.mkdir()
    text = (FIXTURE_DATA / "settings.yaml").read_text(encoding="utf-8")
    (root / "settings.yaml").write_text(
        text.replace("display_tz: America/New_York", "display_tz: Asia/Tokyo")
    )
    with pytest.raises(DataError) as info:
        Store(root, config).load_settings()
    assert info.value.field == "display_tz"


def test_bulk_load_keeps_good_files_and_reports_bad_ones(data_dir: Path, config) -> None:
    for name in ("missing-tz", "syntax-error"):
        shutil.copy(INVALID / "programmes" / f"{name}.yaml", data_dir / "programmes" / f"{name}.yaml")
    programmes, errors = Store.from_config(config).load_programmes()
    assert len(programmes) == 4
    assert sorted(e.path for e in errors) == ["programmes/missing-tz.yaml", "programmes/syntax-error.yaml"]


def test_every_invalid_fixture_is_covered() -> None:
    on_disk = {p.stem for p in (INVALID / "programmes").glob("*.yaml")}
    assert on_disk == set(INVALID_PROGRAMMES)


@pytest.mark.parametrize("amount", [".inf", "1e400"])
def test_non_finite_fee_is_a_data_error(data_dir: Path, config, amount: str) -> None:
    from hub.store import DataError, Store

    path = data_dir / "programmes" / "booth-mif.yaml"
    text = path.read_text(encoding="utf-8")
    start = text.index("fee:")
    path.write_text(
        text[:start] + f"fee:\n  amount: {amount}\n  currency: USD\n  note: null\n", encoding="utf-8"
    )
    with pytest.raises(DataError) as err:
        Store.from_config(config).load_programme(path)
    assert err.value.field == "fee.amount"


# ---- DECISIONS #37: official_domains -------------------------------------------------------------------


def test_seeds_carry_official_domains(config) -> None:
    from hub.store import Store

    programmes, errors = Store.from_config(config).load_programmes()
    assert not errors
    assert {p.id: p.official_domains for p in programmes} == {
        "princeton-mfin": ["princeton.edu"],
        "mit-sloan-mfin": ["mit.edu"],
        "booth-mif": ["chicagobooth.edu", "uchicago.edu"],
        "columbia-msfe": ["columbia.edu"],
    }


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (["columbia.edu"], True),
        (["business.columbia.edu", "mba.com"], True),
        (["Columbia.EDU"], False),
        (["https://columbia.edu"], False),
        (["columbia.edu/ms"], False),
        (["edu"], False),
        (["columbia.edu", "columbia.edu"], False),
    ],
)
def test_official_domains_validation(data_dir: Path, config, value: list[str], ok: bool) -> None:
    from hub.store import DataError, Store

    path = data_dir / "programmes" / "mit-sloan-mfin.yaml"
    text = path.read_text(encoding="utf-8")
    block = "official_domains:\n" + "".join(f"  - {v}\n" for v in value)
    path.write_text(text.replace("official_domains:\n  - mit.edu\n", block), encoding="utf-8")
    store = Store.from_config(config)
    if ok:
        assert store.load_programme(path).official_domains == value
    else:
        with pytest.raises(DataError) as err:
            store.load_programme(path)
        assert str(err.value.field).startswith("official_domains")


def test_official_domains_is_optional(data_dir: Path, config) -> None:
    from hub.store import Store

    path = data_dir / "programmes" / "mit-sloan-mfin.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("official_domains:\n  - mit.edu\n", ""), encoding="utf-8"
    )
    assert Store.from_config(config).load_programme(path).official_domains == []


def test_config_has_shared_official_domains(config) -> None:
    assert config.research.official_shared_domains == ["mba.com"]
