"""Add standard steps and documents (DECISIONS #97; docs/03 §4.2): the proposal, in Python, no AI call.

Checked, fast and without I/O:
- documents come from the stored requirements through ``config.standard_items.documents`` (whole words in a
  requirement's id or label, any case): transcripts → official transcript, GMAT / GRE → their score report,
  an English test → its score report; one document per template, however many requirements name it;
- steps come from the fixed template ``config.standard_items.steps`` in its order, each linked to every
  requirement whose id or label has one of its words;
- an item whose words already stand in an existing document or step is "already there", never offered again;
- ``apply`` adds only the chosen keys that are still new, after the highest step order, with fresh document
  ids, and changes no existing item;
- ``Step.requirement_ids`` must name requirements of the programme; ``dropped_links`` names every link Edit
  programme dropped because its requirement was removed (user addition to Q2).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

from hub import standard_items
from hub.config import load_config
from hub.models.programme import Programme

SEED = Path(__file__).resolve().parents[1] / "seed" / "programmes"

PROBE = {
    "id": "probe-std",
    "name": "Probe Institute Master in Quantitative Finance",
    "short_name": "Probe MQF",
    "school": "Probe Institute",
    "deadlines": [
        {
            "id": "main",
            "label": "Application deadline",
            "due_local": "2027-02-01T17:00",
            "tz": "America/New_York",
            "source_url": "https://example.com/apply",
        }
    ],
    "requirements": [
        {"id": "online-application", "label": "Online application"},
        {"id": "transcripts", "label": "Official transcripts of all university studies"},
        {"id": "tests", "label": "GMAT or GRE score"},
        {"id": "english", "label": "TOEFL or IELTS for non-native speakers"},
        {"id": "letters", "label": "Two letters of recommendation"},
        {"id": "essays", "label": "Two essays"},
        {"id": "fee", "label": "Application fee"},
        {"id": "agreement", "label": "Signed honour agreement"},  # "gre" only inside a word
        {"id": "feedback", "label": "Feedback form"},  # "fee" only inside a word
    ],
}


@pytest.fixture(scope="module")
def cfg():
    return load_config().standard_items


def _probe(**update) -> Programme:  # noqa: ANN003
    return Programme.model_validate({**PROBE, **update})


def _seed(pid: str) -> Programme:
    return Programme.model_validate(YAML(typ="safe").load((SEED / f"{pid}.yaml").read_text(encoding="utf-8")))


def _by_key(proposals: list[standard_items.Proposal]) -> dict[str, standard_items.Proposal]:
    return {p.key: p for p in proposals}


# ---- config -------------------------------------------------------------------------------------------


def test_config_holds_the_template_in_order(cfg) -> None:
    assert [s.key for s in cfg.steps] == ["portal", "recommendations", "essays", "fee", "submit"]
    assert [d.key for d in cfg.documents] == ["transcript", "gmat", "gre", "english"]
    assert all(w == w.casefold() for item in [*cfg.steps, *cfg.documents] for w in item.requirement_words)


# ---- documents from requirements ----------------------------------------------------------------------


def test_documents_come_from_the_requirements(cfg) -> None:
    docs = _by_key([p for p in standard_items.propose(_probe(), cfg) if p.kind == "document"])
    assert list(docs) == ["document:transcript", "document:gmat", "document:gre", "document:english"]
    assert docs["document:transcript"].label == "Official transcript"
    assert docs["document:gmat"].label == "GMAT score report"
    assert docs["document:gre"].label == "GRE score report"
    assert docs["document:english"].label == "English test score report"
    assert [r.id for r in docs["document:gmat"].requirements] == ["tests"]
    assert [r.id for r in docs["document:english"].requirements] == ["english"]
    assert all(p.existing is None for p in docs.values())


def test_no_requirement_no_document(cfg) -> None:
    probe = _probe(requirements=[{"id": "cv", "label": "Resume"}, {"id": "agreement", "label": "Agreement"}])
    assert [p for p in standard_items.propose(probe, cfg) if p.kind == "document"] == []


def test_words_match_whole_words_only(cfg) -> None:
    probe = _probe(requirements=[{"id": "agreement", "label": "Signed honour agreement"}])
    proposals = _by_key(standard_items.propose(probe, cfg))
    assert "document:gre" not in proposals
    probe = _probe(requirements=[{"id": "feedback", "label": "Feedback form"}])
    assert _by_key(standard_items.propose(probe, cfg))["step:fee"].requirements == []


def test_one_document_however_many_requirements_name_it(cfg) -> None:
    probe = _probe(
        requirements=[
            {"id": "transcript-bsc", "label": "Bachelor transcript"},
            {"id": "transcript-msc", "label": "Master transcript"},
        ]
    )
    docs = [p for p in standard_items.propose(probe, cfg) if p.kind == "document"]
    assert [p.key for p in docs] == ["document:transcript"]
    assert [r.id for r in docs[0].requirements] == ["transcript-bsc", "transcript-msc"]


# ---- steps from the template --------------------------------------------------------------------------


def test_steps_follow_the_template_and_link_their_requirements(cfg) -> None:
    steps = [p for p in standard_items.propose(_probe(), cfg) if p.kind == "step"]
    assert [p.key for p in steps] == [
        "step:portal",
        "step:recommendations",
        "step:essays",
        "step:fee",
        "step:submit",
    ]
    links = {p.key: [r.id for r in p.requirements] for p in steps}
    assert links == {
        "step:portal": [],
        "step:recommendations": ["letters"],
        "step:essays": ["essays"],
        "step:fee": ["fee"],
        "step:submit": [],
    }


def test_a_step_links_every_matching_requirement(cfg) -> None:
    mit = _seed("mit-sloan-mfin")
    essays = _by_key(standard_items.propose(mit, cfg))["step:essays"]
    assert [r.id for r in essays.requirements] == ["essay", "short-essay"]


# ---- already there ------------------------------------------------------------------------------------


def test_princeton_already_has_everything(cfg) -> None:
    proposals = standard_items.propose(_seed("princeton-mfin"), cfg)
    assert proposals and all(p.existing is not None for p in proposals)
    docs = _by_key(proposals)
    assert docs["document:transcript"].existing == "Transcript, RSM"
    assert docs["document:gmat"].existing == "GMAT official score report"
    assert docs["step:recommendations"].existing == "Register recommenders early"


def test_booth_is_offered_only_the_fee_step(cfg) -> None:
    new = [p for p in standard_items.propose(_seed("booth-mif"), cfg) if p.existing is None]
    assert [p.key for p in new] == ["step:fee"]
    assert new[0].label == "Pay the application fee" and [r.id for r in new[0].requirements] == ["fee"]


# ---- apply --------------------------------------------------------------------------------------------


def test_apply_adds_only_the_chosen_new_items(cfg) -> None:
    probe = _probe(
        steps=[{"order": 3, "label": "Ask the admissions office about deferral"}],
        documents=[{"id": "transcript", "label": "Bachelor diploma"}],  # id taken, words don't match
    )
    before = probe.model_copy(deep=True)
    chosen = {"step:recommendations", "step:fee", "document:transcript", "document:gre"}
    after, steps, docs = standard_items.apply(probe, chosen, cfg)
    assert [(s.order, s.label, s.requirement_ids) for s in steps] == [
        (4, "Request the recommendation letters", ["letters"]),
        (5, "Pay the application fee", ["fee"]),
    ]
    assert [(d.id, d.label, d.method, d.status.value) for d in docs] == [
        ("transcript-2", "Official transcript", None, "not_requested"),
        ("gre", "GRE score report", None, "not_requested"),
    ]
    assert after.steps[0] == before.steps[0] and after.documents[0] == before.documents[0]
    assert len(after.steps) == 3 and len(after.documents) == 3
    assert probe == before  # the input is not changed in place


def test_apply_never_adds_an_item_already_there(cfg) -> None:
    princeton = _seed("princeton-mfin")
    every = {p.key for p in standard_items.propose(princeton, cfg)}
    after, steps, docs = standard_items.apply(princeton, every, cfg)
    assert steps == [] and docs == [] and after == princeton


def test_apply_refuses_an_unknown_key(cfg) -> None:
    with pytest.raises(standard_items.UnknownItem):
        standard_items.apply(_probe(), {"step:portal", "step:evil"}, cfg)


def test_adding_twice_adds_nothing_the_second_time(cfg) -> None:
    keys = {p.key for p in standard_items.propose(_probe(), cfg)}
    once, steps, docs = standard_items.apply(_probe(), keys, cfg)
    assert len(steps) == 5 and len(docs) == 4
    twice, steps2, docs2 = standard_items.apply(once, keys, cfg)
    assert steps2 == [] and docs2 == [] and twice == once


# ---- the link field -----------------------------------------------------------------------------------


def test_a_step_link_must_name_a_requirement_of_the_programme() -> None:
    _probe(steps=[{"order": 1, "label": "Pay", "requirement_ids": ["fee"]}])  # valid
    with pytest.raises(ValidationError, match="requirement"):
        _probe(steps=[{"order": 1, "label": "Pay", "requirement_ids": ["no-such-requirement"]}])


def test_dropped_links_are_named() -> None:
    old = _probe(
        steps=[
            {"order": 1, "label": "Pay the application fee", "requirement_ids": ["fee"]},
            {"order": 2, "label": "Finalise the essays", "requirement_ids": ["essays"]},
        ]
    )
    new = old.model_copy(deep=True)
    new.requirements = [r for r in new.requirements if r.id != "fee"]
    new.steps[0].requirement_ids = []
    assert standard_items.dropped_links(old, new) == [
        "Pay the application fee is no longer linked to Application fee"
    ]
    assert standard_items.dropped_links(old, old) == []
