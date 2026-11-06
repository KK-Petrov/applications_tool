"""Word count (docs/02 §7): the one rule used for emails (2b), essays and CVs (4a).

A word is a maximal run of non-whitespace characters containing at least one letter or digit, so
``long-term``, ``U.S.``, ``2027``, ``well—known`` and a URL are one word each, while a free-standing ``—``
or a Markdown bullet (``#``, ``*``, ``-``) is none. Portals count differently, so the UI shows "≈".
"""

from __future__ import annotations


def count_words(text: str) -> int:
    """Number of words in ``text`` by the docs/02 §7 rule."""
    return sum(1 for token in text.split() if any(ch.isalnum() for ch in token))
