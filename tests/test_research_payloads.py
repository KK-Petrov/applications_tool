"""Research payloads carry programme data only (docs/04 §7, §7.1; plan approval, addition 2).

Research is the one skill with web tools, so fetched pages could steer the model; nothing about the owner
may be in reach of that call. New research sends ``{query, url, today}``; re-research sends
``{programme_id, current, today}`` where ``current`` is the stored programme file and nothing else. Checked
on the builders and on what the fake Claude Code actually receives on stdin from both routes.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from hub import research
from hub.store import Store

from helpers import FormClient, mirror_output, research_output, write_recording

TODAY = date(2026, 10, 30)


def _owner_strings(data_dir: Path) -> list[str]:
    """Every distinctive string about the owner in the fixture data: biography, professors, settings."""
    yaml = YAML(typ="safe")
    out: list[str] = []
    for entry in yaml.load((data_dir / "biography.yaml").read_text(encoding="utf-8"))["entries"]:
        out += [entry["title"], *([entry["detail"]] if entry.get("detail") else [])]
    for path in (data_dir / "professors").glob("*.yaml"):
        prof = yaml.load(path.read_text(encoding="utf-8"))
        out += [prof["name"], prof["email"], prof["course"], prof["course_description"].strip()]
        out += [v for v in (prof.get("interaction"), prof.get("grade")) if v]
    settings = yaml.load((data_dir / "settings.yaml").read_text(encoding="utf-8"))
    out += [settings["owner_name"], settings["owner_email"]]
    return [s for s in out if s]


def test_new_research_payload_has_exactly_query_url_today() -> None:
    payload = research.research_payload("Example MSQF", "https://grad.example.edu/msqf", TODAY)
    assert payload == {"query": "Example MSQF", "url": "https://grad.example.edu/msqf", "today": "2026-10-30"}
    assert research.research_payload("Example MSQF", None, TODAY)["url"] is None


def test_reresearch_payload_carries_only_researched_fields(config) -> None:
    """User decision (DECISIONS #66, superseding "current = the stored file"): ``current`` holds only what
    research compares; status, steps, documents, interviews, verification and free-text notes stay local."""
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == "princeton-mfin")
    payload = research.reresearch_payload(programme, TODAY)
    assert list(payload) == ["programme_id", "current", "today"]
    assert payload["programme_id"] == "princeton-mfin" and payload["today"] == "2026-10-30"
    current = payload["current"]
    assert set(current) == {
        "id", "name", "short_name", "school", "unit", "official_domains", "aliases", "portal_url",
        "deadlines", "letters_required", "requirements", "essays", "curriculum", "ai_policy", "fee",
        "sources",
    }  # fmt: skip
    assert set(current["deadlines"][0]) == {"id", "label", "due_local", "tz", "time_known", "source_url"}
    assert all(set(r) == {"id", "label"} for r in current["requirements"])
    assert set(current["fee"]) == {"amount", "currency"}
    assert current["deadlines"][0]["due_local"] == "2026-12-15T23:59"


def test_no_owner_note_reaches_the_reresearch_call(
    client: FormClient, config, data_dir: Path, tmp_path: Path, calls: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Markers planted in every free-text place of the programme file never reach the fake's stdin."""
    store = Store.from_config(config)
    p = next(x for x in store.load_programmes()[0] if x.id == "princeton-mfin")
    p.steps[0].note = "MARKER-STEP"
    p.documents[0].note = "MARKER-DOCUMENT"
    p.requirements[0].note = "MARKER-REQUIREMENT"
    p.letters_note = "MARKER-LETTERS"
    p.fee.note = "MARKER-FEE"
    p.interviews[0].notes = "MARKER-INTERVIEW"
    store.save_programme(p)
    rec = write_recording(tmp_path, mirror_output(p, p.sources[0].url), "mirror.json")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    assert client.post("/programmes/princeton-mfin/research", data={}).status_code == 200
    (stdin,) = _stdins(calls)
    assert "MARKER" not in stdin, [m for m in re.findall(r"MARKER-[A-Z]+", stdin)]
    sent = json.loads(stdin)["current"]
    assert "interviews" not in sent and "steps" not in sent and "documents" not in sent


def test_builders_can_only_take_programme_data() -> None:
    """The signatures leave no way to pass a snapshot, biography, professor or course."""
    assert list(inspect.signature(research.research_payload).parameters) == ["query", "url", "today"]
    assert list(inspect.signature(research.reresearch_payload).parameters) == ["programme", "today"]


def test_no_owner_data_reaches_either_payload(config, data_dir: Path) -> None:
    store = Store.from_config(config)
    texts = [json.dumps(research.research_payload("Princeton MFin", None, TODAY), ensure_ascii=False)]
    for programme in store.load_programmes()[0]:
        texts.append(
            json.dumps(research.reresearch_payload(programme, TODAY), ensure_ascii=False, default=str)
        )
    owner = _owner_strings(data_dir)
    assert owner, "the fixture has owner data to look for"
    for text in texts:
        for value in owner:
            assert value not in text, f"owner data {value!r} reached a research payload"
        # keys of owner files (a professor's letters are a mapping keyed by programme id)
        for key in ('"biography"', '"professor', '"owner', '"course_ids"', '"letters": {', '"emails"'):
            assert key not in text


@pytest.fixture
def calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "fake-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(path))
    return path


def _stdins(calls: Path) -> list[str]:
    return [json.loads(line)["stdin"] for line in calls.read_text(encoding="utf-8").splitlines()]


def test_routes_send_only_programme_data(
    client: FormClient, config, data_dir: Path, tmp_path: Path, calls: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(write_recording(tmp_path, research_output())))
    res = client.post("/programmes/research", data={"query": "Example MSQF", "url": ""})
    assert res.status_code == 200
    programme = next(p for p in Store.from_config(config).load_programmes()[0] if p.id == "princeton-mfin")
    rec = write_recording(tmp_path, mirror_output(programme, programme.sources[0].url), "mirror.json")
    monkeypatch.setenv("FAKE_CLAUDE_RECORDING", str(rec))
    res = client.post("/programmes/princeton-mfin/research", data={})
    assert res.status_code == 200
    new, again = (json.loads(s) for s in _stdins(calls))
    assert new == {"query": "Example MSQF", "url": None, "today": "2026-10-30"}
    assert list(again) == ["programme_id", "current", "today"] and again["programme_id"] == "princeton-mfin"
    owner = _owner_strings(data_dir)
    for stdin in _stdins(calls):
        for value in owner:
            assert value not in stdin, f"owner data {value!r} was sent to the research skill"
