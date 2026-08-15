"""Exact, deterministic and recoverable textual anchoring over local snapshots."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

NORMALIZATION_VERSION = "3"
MATCHER_VERSION = "2"

_PUNCTUATION_EQUIVALENTS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "…": "...",
    }
)


@dataclass(frozen=True)
class TextLocator:
    """Inclusive range of lines touched by a quote."""

    line_start: int
    line_end: int


@dataclass(frozen=True)
class TextMatch:
    """Recovered literal quote and its stable location in the snapshot."""

    quote: str
    locator: TextLocator


def normalize_text(text: str) -> str:
    """Normalize equivalences conservatively without erasing semantic content."""
    normalized, _ = _normalize_with_offsets(text)
    return normalized


def match_text(claim_text: str, snapshot_text: str) -> TextMatch | None:
    """Return the first exact normalized match and its literal quote."""
    claim = normalize_text(claim_text)
    if not claim or not snapshot_text.strip():
        return None

    snapshot, offsets = _normalize_with_offsets(snapshot_text)
    search_from = 0
    while (match_start := snapshot.find(claim, search_from)) >= 0:
        match_end = match_start + len(claim)
        original_start = offsets[match_start][0]
        original_end = offsets[match_end - 1][1]
        quote = snapshot_text[original_start:original_end]
        if (
            _has_lexical_boundaries(snapshot, claim, match_start, match_end)
            and not _is_immediately_negated(snapshot, match_start)
            and normalize_text(quote) == claim
        ):
            return TextMatch(
                quote=quote,
                locator=TextLocator(
                    line_start=_line_number(snapshot_text, original_start),
                    line_end=_line_number(snapshot_text, original_end - 1),
                ),
            )
        search_from = match_start + 1
    return None


def _normalize_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    characters: list[str] = []
    offsets: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        start = index
        index += 1
        cluster = text[start:index]
        while index < len(text) and _extends_canonical_segment(cluster, text[index]):
            cluster += text[index]
            index += 1

        if cluster.isspace():
            while index < len(text) and text[index].isspace():
                index += 1
            if characters and characters[-1] != " ":
                characters.append(" ")
                offsets.append((start, index))
            continue

        normalized = unicodedata.normalize("NFC", cluster)
        lowered = normalized.lower()
        if len(lowered) == len(normalized):
            normalized = lowered
        normalized = normalized.translate(_PUNCTUATION_EQUIVALENTS)
        characters.extend(normalized)
        offsets.extend((start, index) for _ in normalized)

    if characters and characters[-1] == " ":
        characters.pop()
        offsets.pop()
    return "".join(characters), offsets


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _extends_canonical_segment(segment: str, next_character: str) -> bool:
    if unicodedata.combining(next_character):
        return True
    together = unicodedata.normalize("NFC", segment + next_character)
    separate = unicodedata.normalize("NFC", segment) + unicodedata.normalize("NFC", next_character)
    return together != separate


def _has_lexical_boundaries(snapshot: str, claim: str, start: int, end: int) -> bool:
    starts_inside_word = claim[0].isalnum() and start > 0 and snapshot[start - 1].isalnum()
    ends_inside_word = claim[-1].isalnum() and end < len(snapshot) and snapshot[end].isalnum()
    return not starts_inside_word and not ends_inside_word


def _is_immediately_negated(snapshot: str, start: int) -> bool:
    prefix = snapshot[:start]
    if not prefix.endswith(" "):
        return False
    prefix = prefix.rstrip()
    token_start = len(prefix)
    while token_start > 0 and prefix[token_start - 1].isalnum():
        token_start -= 1
    return prefix[token_start:] == "no"
