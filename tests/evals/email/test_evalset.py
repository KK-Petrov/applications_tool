"""The email eval set matches docs/04 §4: 10 cases and 3 calibration emails, invented data only.

Cases 1–7 tune the skill; 8–10 are held out and reported separately (phase 2b prompt, task 7).
"""

from __future__ import annotations

import json
import re

from evalset import CALIBRATION_DIR, CASES_DIR, build_payload, load_calibration, load_cases

from hub.rules.words import count_words


def test_ten_cases_with_cases_eight_to_ten_held_out() -> None:
    cases = load_cases()
    assert [c.number for c in cases] == list(range(1, 11))
    assert [c.number for c in cases if c.held_out] == [8, 9, 10]
    assert len(list(CASES_DIR.glob("*.yaml"))) == 10


def test_every_case_sets_a_title_and_uses_invented_addresses() -> None:
    for case in load_cases():
        raw = case.path.read_text(encoding="utf-8")
        assert re.search(r"^\s*title:\s*\S", raw, re.M), case.path.name
        for address in re.findall(r"[\w.+-]+@[\w.-]+", raw):
            assert address.endswith("@example.org"), address


def test_cases_cover_the_documented_situations() -> None:
    by = {c.number: c for c in load_cases()}
    p = {n: build_payload(c).payload for n, c in by.items()}
    one = p[1]["professor"]
    assert one["courses"][0]["grade"] and one["courses"][0]["term"] and one["interaction"]
    assert len(p[1]["programmes"]) == 1
    two = p[2]["professor"]
    assert two["courses"][0]["grade"] is None and two["interaction"] is None
    assert len(p[3]["programmes"]) == 4
    assert p[4]["professor"]["tone"] == "warm"
    assert len(p[5]["professor"]["courses"][0]["description"]) >= 3000
    assert any(e["state"] == "anticipated" and "cum laude" in e["title"].lower() for e in p[6]["biography"])
    assert by[6].anticipated == ["cum laude"]
    assert count_words(p[7]["professor"]["courses"][0]["description"]) <= 15
    eight = p[8]["professor"]
    assert (
        eight["title"] == "Dr."
        and "UVA" in eight["university"]
        and eight["courses"][0]["term"] == "Fall 2026"
    )
    nine = [x["deadline_text"] for x in p[9]["programmes"]]
    assert len(nine) == 2 and any(t.endswith(" ET") for t in nine) and any(t.endswith(" CT") for t in nine)
    ten = p[10]["professor"]
    assert (
        ten["name"] == "Prof. van der Berg"
        and ten["surname"] == "Van der Berg"
        and ten["title"] == "Professor"
    )


def test_payloads_are_built_by_the_app_and_name_no_unselected_programme() -> None:
    for case in load_cases():
        built = build_payload(case)
        text = json.dumps(built.payload)
        assert built.payload["programmes"], case.path.name
        for term in built.unselected:
            assert not re.search(rf"\b{re.escape(term)}\b", text), (case.path.name, term)


def test_three_calibration_emails_each_with_one_planted_claim() -> None:
    items = load_calibration()
    assert len(items) == 3 and len(list(CALIBRATION_DIR.glob("*.yaml"))) == 3
    for item in items:
        assert item.planted in item.body, item.path.name
        assert build_payload(item.case).payload  # the facts the grader checks against
