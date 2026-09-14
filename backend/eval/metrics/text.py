"""
Shared text normalisation.

Every text comparison in the harness goes through here, so that a change in
normalisation changes every metric consistently and is visible in one place.
The normalisation is part of each metric's definition: the same outputs scored
with different normalisation give different numbers, and a report that does not
state it cannot be reproduced.
"""
from __future__ import annotations

import re
import unicodedata

# Function words that carry no task content. Used only for task matching, never
# for word error rate, where every word counts.
_STOPWORDS = frozenset(
    "a an the and or but of to for in on at by with from as is are was were be "
    "been being will shall would should can could may might must do does did "
    "this that these those it its their our your his her we they you he she i "
    "me us them to up about into over after before than then so not no".split()
)


def normalise(text: str) -> str:
    """
    Lowercase, strip accents and punctuation, collapse whitespace.

    Digits and decimal points inside numbers are preserved ("18.5%" becomes
    "18.5"), because figures are exactly the content that must not be lost.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    # Keep decimal points between digits; drop other punctuation.
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    text = re.sub(r"[^\w\s.]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_tokens(text: str) -> set[str]:
    """Normalised tokens with function words removed."""
    return {t for t in normalise(text).split() if t not in _STOPWORDS}


def token_jaccard(a: str, b: str) -> float:
    """Jaccard similarity over content tokens, in [0, 1]."""
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def contains_entity(haystack: str, entity: str) -> bool:
    """
    Whether a must-keep entity appears in the text, after normalisation and
    on token boundaries, so "18%" is not found inside "118%".
    """
    needle = normalise(entity)
    if not needle:
        return True
    hay = f" {normalise(haystack)} "
    return f" {needle} " in hay
