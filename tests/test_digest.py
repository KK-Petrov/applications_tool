"""The daily digest (docs/05 §4; C-3-03).

Composed from the reminders fixture on Saturday 14 November 2026, 12:00 New York, it matches the hand-written
golden file tests/fixtures/digest/golden.txt; the HTML part carries the same lines. It can only go to the
owner's own address: the send function takes no recipient, To and From are ``settings.owner_email``, an empty
owner address is refused; at most one per day (``sync/digest_state.json``), skipped when there is nothing to
report (``digest.send_when_empty``); ``--dry-run`` prints and sends nothing.
"""

from __future__ import annotations

import base64
import email
import inspect
import shutil
from datetime import UTC, datetime
from email import policy
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from fakes.fake_google import FakeGoogle
from hub.config import load_config
from hub.google import auth, digest
from hub.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "reminders"
GOLDEN = Path(__file__).parent / "fixtures" / "digest" / "golden.txt"
NOW = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)  # Saturday 14 November 2026, 12:00 New York
OWNER = "alex.owner@example.com"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "data"
    shutil.copytree(FIXTURE, target)
    monkeypatch.setenv("HUB_DATA_DIR", str(target))
    return target


def _store() -> Store:
    return Store.from_config(load_config())


def _compose(now: datetime = NOW) -> digest.Digest:
    return digest.compose(_store(), load_config(), now)


def _send(fake: FakeGoogle, now: datetime = NOW, dry_run: bool = False) -> digest.SendResult:
    return digest.send(_store(), fake.gmail(), load_config(), now, dry_run=dry_run)


def _message(fake: FakeGoogle, n: int = 0) -> email.message.EmailMessage:
    raw = base64.urlsafe_b64decode(fake.sent[n]["raw"])
    return email.message_from_bytes(raw, policy=policy.default)  # type: ignore[return-value]


def _settings(data: Path, **fields: object) -> None:
    yaml = YAML()
    path = data / "settings.yaml"
    doc = yaml.load(path.read_text(encoding="utf-8"))
    doc.update(fields)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.dump(doc, fh)


# ---- C-3-03: the golden file -------------------------------------------------------------------------


def test_the_digest_matches_its_golden_file(data: Path) -> None:
    found = _compose()
    assert found.text == GOLDEN.read_text(encoding="utf-8")
    assert found.subject == "Application Hub · Sat 14 Nov: 2 items in the next 14 days"


def test_the_html_part_has_the_same_lines_escaped(data: Path) -> None:
    html = _compose().html
    assert html.startswith("<!doctype html>") and "<h2>Overdue</h2>" in html
    assert "Princeton MFin: follow up with Prof. A. Example" in html
    assert "Last cycle&#x27;s date" in html or "Last cycle&#39;s date" in html  # escaped
    assert '<a href="http://127.0.0.1:8765/">' in html


def test_the_subject_counts_one_item_in_the_singular(data: Path) -> None:
    # 4 Nov: only the 5 Nov follow-up is in the window (the 3 Nov letter request is overdue by then)
    subject = _compose(datetime(2026, 11, 4, 17, 0, tzinfo=UTC)).subject
    assert subject.endswith(": 1 item in the next 14 days")


# ---- C-3-03: only ever to the owner ----------------------------------------------------------------------


def test_the_send_function_takes_no_recipient() -> None:
    params = set(inspect.signature(digest.send).parameters)
    assert params == {"store", "service", "config", "now_utc", "dry_run"}


def test_it_is_sent_to_and_from_the_owner_only(data: Path) -> None:
    fake = FakeGoogle()
    result = _send(fake)
    assert result.sent and len(fake.sent) == 1 and fake.sent[0]["userId"] == "me"
    message = _message(fake)
    assert message["To"] == OWNER and message["From"] == OWNER
    assert message["Cc"] is None and message["Bcc"] is None and message["Reply-To"] is None
    assert message["Subject"] == "Application Hub · Sat 14 Nov: 2 items in the next 14 days"
    assert message.get_body(("plain",)).get_content() == GOLDEN.read_text(encoding="utf-8")
    assert message.get_body(("html",)) is not None


def test_a_changed_owner_address_is_the_only_recipient(data: Path) -> None:
    _settings(data, owner_email="new.owner@example.org")
    fake = FakeGoogle()
    _send(fake)
    assert _message(fake)["To"] == "new.owner@example.org"


def test_without_an_owner_address_nothing_is_sent(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGoogle()
    store = _store()
    settings = store.load_settings()
    monkeypatch.setattr(Store, "load_settings", lambda _self: settings.model_copy(update={"owner_email": ""}))
    result = digest.send(store, fake.gmail(), load_config(), NOW, dry_run=False)
    assert not result.sent and "owner" in (result.reason or "").lower()
    assert fake.sent == [] and fake.calls == []


# ---- once a day, empty days, dry run ------------------------------------------------------------------


def test_at_most_one_digest_a_day(data: Path) -> None:
    fake = FakeGoogle()
    assert _send(fake).sent
    again = _send(fake, datetime(2026, 11, 14, 23, 0, tzinfo=UTC))  # 18:00 New York, the same day
    assert not again.sent and "already" in (again.reason or "")
    assert len(fake.sent) == 1
    assert _send(fake, datetime(2026, 11, 15, 17, 0, tzinfo=UTC)).sent  # the next day
    assert (data / "sync" / "digest_state.json").read_text(encoding="utf-8").count("2026-11-15") == 1


def test_a_day_with_nothing_to_report_is_skipped(data: Path) -> None:
    for name in ("princeton-mfin", "booth-mif", "columbia-msfe"):
        path = data / "programmes" / f"{name}.yaml"
        yaml = YAML()
        doc = yaml.load(path.read_text(encoding="utf-8"))
        doc["status"] = "submitted"
        doc["interviews"] = []
        with path.open("w", encoding="utf-8", newline="\n") as fh:
            yaml.dump(doc, fh)
    (data / "professors" / "example-a.yaml").unlink()
    assert _compose().empty
    fake = FakeGoogle()
    result = _send(fake)
    assert not result.sent and "nothing to report" in (result.reason or "") and fake.sent == []


def test_dry_run_prints_and_sends_nothing(data: Path) -> None:
    fake = FakeGoogle()
    result = _send(fake, dry_run=True)
    assert not result.sent and fake.calls == []
    assert OWNER in result.preview and GOLDEN.read_text(encoding="utf-8") in result.preview
    assert not (data / "sync" / "digest_state.json").exists()


def test_hub_digest_dry_run_needs_no_google(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hub import cli

    monkeypatch.setattr(auth, "gmail_service", lambda _cfg: pytest.fail("dry run must not reach Google"))
    result = CliRunner().invoke(cli.app, ["digest", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Subject: Application Hub · " in result.output and f"To: {OWNER}" in result.output


def test_hub_digest_sends_through_gmail(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hub import cli

    fake = FakeGoogle()
    monkeypatch.setattr(auth, "gmail_service", lambda _cfg: fake.gmail())
    result = CliRunner().invoke(cli.app, ["digest"])
    assert result.exit_code == 0, result.output
    assert len(fake.sent) == 1 and "Sent the digest to the owner address" in result.output
