"""Word count (docs/02 §7): a word is a maximal run of non-whitespace containing a letter or digit.

Phase 2b uses it for the 180–280-word email check (C-2b-03); Phase 4a adds the 15 fixture texts (C-4a-02).
"""

from __future__ import annotations

import pytest

from hub.rules.words import count_words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("long-term", 1),
        ("U.S.", 1),
        ("2027", 1),
        ("a — b", 2),  # a free-standing dash is not a word
        ("a – b", 2),
        ("well—known", 1),
        ("see https://example.org/a-b?c=d now", 3),
        ("# Heading", 1),
        ("* item", 1),
        ("- item", 1),
        ("Dear Professor Jansen,", 3),
        ("", 0),
        ("  \n\t ", 0),
        ("café naïve über 中文", 4),
        ("Kind regards,\nAlex Example", 4),
    ],
)
def test_word_count_rule(text: str, expected: int) -> None:
    assert count_words(text) == expected
