"""Names (docs/02 §9). Phase 2b needs only the salutation form; Phase 2c adds title tokens, initials,
particles from ``config.names`` and matching.
"""

from __future__ import annotations


def salutation_surname(surname: str) -> str:
    """The surname as it stands alone after the title: "Dear Professor Van der Berg,".

    A Dutch surname keeps its particle in lower case after a first name or initials ("A. van der Berg");
    standing alone, the first particle is capitalised (docs/02 §9 rule 4). Capitalising the first letter
    does exactly that and leaves an ordinary surname unchanged.
    """
    return surname[:1].upper() + surname[1:]
