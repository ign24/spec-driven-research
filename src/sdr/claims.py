"""Deterministic extraction of references and factual claims from Markdown."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sdr.verification_ledger import make_claim_id

_FACTUAL_MARKER_RE = re.compile(r"\[S(?P<number>\d+)\]")
_CONTEXTUAL_MARKER_RE = re.compile(
    r"\[\s*cf\s*\.\s*s(?P<number>\d+)\s*\]",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)[^\n]*?\1")
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t)")
_ATX_HEADING_RE = re.compile(r"^ {0,3}#{1,6}[ \t]+")
_LIST_ITEM_RE = re.compile(r"^ {0,3}(?:[-+*]|\d+[.)])\s+")
_LINK_DESTINATION_RE = re.compile(r"\]\((?:<[^>\n]*>|[^)\n]*)\)")
_AUTOLINK_RE = re.compile(r"<(?:https?://|mailto:)[^>\n]+>", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>)]+", re.IGNORECASE)
_DEFAULT_NOTE_PATH = "notes/unknown.md"


@dataclass(frozen=True)
class Reference:
    """Factual or contextual reference found in Markdown prose."""

    marker: str
    source_id: str
    contextual: bool
    start: int
    end: int
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Claim:
    """Factual claim cited against a source."""

    id: str
    text: str
    source_id: str
    claim_hash: str
    note_path: str
    line_start: int
    line_end: int


def extract_references(markdown: str) -> list[Reference]:
    """Classify references in the prose, excluding frontmatter and Markdown code."""
    prose = _mask_non_prose(markdown)
    matches = [
        (*match.span(), match.group(), False, match.group("number"))
        for match in _FACTUAL_MARKER_RE.finditer(prose)
    ]
    matches.extend(
        (*match.span(), match.group(), True, match.group("number"))
        for match in _CONTEXTUAL_MARKER_RE.finditer(prose)
    )
    return [
        Reference(
            marker=marker,
            source_id=f"S{number}",
            contextual=contextual,
            start=start,
            end=end,
            line_start=_line_number(markdown, start),
            line_end=_line_number(markdown, end - 1),
        )
        for start, end, marker, contextual, number in sorted(matches)
    ]


def extract_claims(markdown: str, note_path: str = _DEFAULT_NOTE_PATH) -> list[Claim]:
    """Extract one claim per sentence carrying a single factual `[S<n>]` marker.

    Consumers that persist identity must pass the complete Markdown and its
    `note_path`, so that absolute ranges and IDs match across stages.
    """
    prose = _mask_non_prose(markdown)
    references = extract_references(markdown)
    claims: list[Claim] = []
    for start, end in _sentence_ranges(prose, references, _atx_heading_breaks(markdown, prose)):
        sentence_references = [ref for ref in references if start <= ref.start < end]
        factual = [ref for ref in sentence_references if not ref.contextual]
        if not factual:
            continue
        line_start = _line_number(markdown, _first_content_offset(prose, start, end))
        line_end = _line_number(markdown, _last_content_offset(prose, start, end))
        if len(factual) > 1:
            markers = ", ".join(ref.marker for ref in factual)
            location = (
                f"line {line_start}" if line_start == line_end else f"lines {line_start}-{line_end}"
            )
            raise ValueError(
                f"{note_path}: {location}: the sentence contains multiple factual "
                f"references ({markers}); split the sentence to leave one factual reference "
                "per sentence"
            )
        text = _clean_claim_text(prose[start:end], sentence_references, start)
        source_id = factual[0].source_id
        digest = hashlib.sha256(text.encode()).hexdigest()
        claims.append(
            Claim(
                id=make_claim_id(note_path, line_start, line_end, source_id, digest),
                text=text,
                source_id=source_id,
                claim_hash=digest,
                note_path=note_path,
                line_start=line_start,
                line_end=line_end,
            )
        )
    return claims


def _mask_non_prose(markdown: str) -> str:
    chars = list(markdown)
    lines = markdown.splitlines(keepends=True)
    offset = 0
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    in_fence = False
    fence_char = ""
    fence_length = 0
    for index, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        fence = _FENCE_RE.match(stripped)
        mask_line = in_frontmatter or in_fence or bool(_INDENTED_CODE_RE.match(stripped))
        if in_frontmatter and index > 0 and stripped.strip() == "---":
            mask_line = True
            in_frontmatter = False
        elif not in_frontmatter and fence:
            token = fence.group(1)
            if not in_fence:
                in_fence = True
                fence_char = token[0]
                fence_length = len(token)
                mask_line = True
            elif token[0] == fence_char and len(token) >= fence_length:
                mask_line = True
                in_fence = False
        if mask_line:
            _mask_span(chars, offset, offset + len(line))
        else:
            heading = _ATX_HEADING_RE.match(stripped)
            if heading:
                _mask_span(chars, offset, offset + heading.end())
        offset += len(line)
    masked = "".join(chars)
    chars = list(masked)
    for match in _INLINE_CODE_RE.finditer(masked):
        _mask_span(chars, *match.span())
    masked = "".join(chars)
    chars = list(masked)
    for pattern in (_LINK_DESTINATION_RE, _AUTOLINK_RE, _URL_RE):
        current = "".join(chars)
        for match in pattern.finditer(current):
            start, end = match.span()
            if pattern is _LINK_DESTINATION_RE:
                start += 1  # Keep `]`, which belongs to the visible text.
            _mask_span(chars, start, end)
    return "".join(chars)


def _mask_span(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _sentence_ranges(
    prose: str,
    references: list[Reference],
    heading_breaks: set[int],
) -> list[tuple[int, int]]:
    marker_positions = {
        position for reference in references for position in range(reference.start, reference.end)
    }
    ranges: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(prose):
        if prose[index] in ".!?" and index not in marker_positions and _ends_sentence(prose, index):
            ranges.append((start, index + 1))
            start = index + 1
        elif prose[index] == "\n":
            if index in heading_breaks:
                ranges.append((start, index))
                start = index + 1
                index += 1
                continue
            paragraph_end = re.match(r"\n[ \t]*\n", prose[index:])
            if paragraph_end:
                end = index
                ranges.append((start, end))
                start = index + len(paragraph_end.group())
                index = start - 1
            elif _LIST_ITEM_RE.match(prose[index + 1 :]):
                ranges.append((start, index))
                start = index + 1
        index += 1
    if start < len(prose):
        ranges.append((start, len(prose)))
    return [(left, right) for left, right in ranges if prose[left:right].strip()]


def _atx_heading_breaks(markdown: str, prose: str) -> set[int]:
    breaks: set[int] = set()
    offset = 0
    for line in markdown.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        visible = prose[offset : offset + len(stripped)]
        if line.endswith("\n") and _ATX_HEADING_RE.match(stripped) and visible.strip():
            breaks.add(offset + len(line) - 1)
        offset += len(line)
    return breaks


def _ends_sentence(text: str, index: int) -> bool:
    if text[index] != ".":
        return True
    previous = text[index - 1] if index else ""
    following = text[index + 1] if index + 1 < len(text) else ""
    if previous.isalnum() and following.isalnum():
        return False
    line_prefix = text[text.rfind("\n", 0, index) + 1 : index]
    if re.fullmatch(r" {0,3}\d+", line_prefix) and following.isspace():
        return False
    prefix = text[:index]
    word_match = re.search(r"([A-Za-z]+)$", prefix)
    next_nonspace = text[index + 1 :].lstrip()[:1]
    return not (
        word_match and word_match.group(1).lower() in {"p", "pp"} and next_nonspace.isdigit()
    )


def _first_content_offset(text: str, start: int, end: int) -> int:
    match = re.search(r"\S", text[start:end])
    return start + (match.start() if match else 0)


def _last_content_offset(text: str, start: int, end: int) -> int:
    stripped_end = len(text[start:end].rstrip())
    return start + max(stripped_end - 1, 0)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _clean_claim_text(sentence: str, references: list[Reference], sentence_start: int) -> str:
    chars = list(sentence)
    for reference in references:
        _mask_span(chars, reference.start - sentence_start, reference.end - sentence_start)
    cleaned = "".join(chars)
    cleaned = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", cleaned)
    cleaned = re.sub(r"!?\[([^\]]*)\](?=\s|$)", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.!?])$", r"\1", cleaned)
    return cleaned
