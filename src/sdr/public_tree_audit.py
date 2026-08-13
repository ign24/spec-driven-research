"""Deterministic checks for content intended for public distribution."""

from __future__ import annotations

import argparse
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

EVALUATION_CATEGORY = "evaluation"
EVALUATION_CORPUS_DIRECTORIES = frozenset({"corpus", "reuse-corpus"})
PUBLIC_CATEGORIES = {
    ".github": "automation",
    "assets": "assets",
    "bench": EVALUATION_CATEGORY,
    "docs": "documentation",
    "examples": "examples",
    "integrations": "integrations",
    "openspec": "specifications",
    "skills": "skills",
    "src": "framework",
    "tests": "tests",
}
LIFECYCLE_METADATA_NAME = "sdr.yaml"
PROHIBITED_NAMES = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "knowledge",
    "research",
}
PROHIBITED_SUFFIXES = {".ipynb"}
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?<![\w])/(?:home|root|Users)/[^\s\"'<>]+"),
    re.compile(r"(?i)(?<![\w])[a-z]:\\Users\\[^\s\"'<>]+"),
)
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:gh[oprsu]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{82,255})"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk_live_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"),
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key>(?<![?&])\b(?:API_KEY|SECRET|TOKEN|PASSWORD)\b)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>[^\s\n,\"'\]}]+)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_RE = re.compile(
    r"(?P<prefix>[?&](?:api[_-]?key|access[_-]?token|token|password|passwd|secret|key)=)"
    r"(?P<value>[^&#\s]+)",
    re.IGNORECASE,
)
_REDACTION_MARKER_RE = re.compile(r"<redacted-value-[1-9][0-9]*>")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|token|password|passwd|secret|key)", re.IGNORECASE
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int | None = None


class RedactionContext:
    """Assign opaque labels while retaining equality only within one payload."""

    def __init__(self) -> None:
        self._labels: dict[str, str] = {}
        self._reserved: set[str] = set()
        self._next_label = 1

    def reserve(self, marker: str) -> None:
        self._reserved.add(marker)

    def marker(self, sensitive_value: str) -> str:
        marker = self._labels.get(sensitive_value)
        if marker is None:
            while True:
                marker = f"<redacted-value-{self._next_label}>"
                self._next_label += 1
                if marker not in self._reserved and marker not in self._labels.values():
                    break
            self._labels[sensitive_value] = marker
        return marker


def audit_tree(root: Path, excluded: Iterable[Path] = ()) -> list[Finding]:
    """Audit a directory without including matched sensitive values in findings."""
    root = root.resolve()
    exclusions = {PurePosixPath(_relative_posix(path)) for path in excluded}
    findings: list[Finding] = []

    for directory, names, files in os.walk(root):
        relative_directory = Path(directory).relative_to(root)
        names[:] = sorted(
            name for name in names if not _is_excluded(relative_directory / name, exclusions)
        )
        files.sort()

        for name in names:
            relative_path = relative_directory / name
            if _is_prohibited(relative_path):
                findings.append(Finding("prohibited-path", _relative_posix(relative_path)))
        names[:] = [name for name in names if not _is_prohibited(relative_directory / name)]

        for name in files:
            relative_path = relative_directory / name
            if _is_excluded(relative_path, exclusions):
                continue
            display_path = _relative_posix(relative_path)
            if _is_prohibited(relative_path):
                findings.append(Finding("prohibited-path", display_path))
            if _is_harness_residue(relative_path):
                findings.append(Finding("harness-residue", display_path))
            findings.extend(_audit_text(Path(directory) / name, display_path))

    return sorted(findings, key=lambda item: (item.path, item.line or 0, item.code))


def render_findings(findings: Sequence[Finding]) -> str:
    """Render only locations and categories, never matched content."""
    context = RedactionContext()
    lines = []
    for finding in findings:
        location = redact_sensitive(finding.path, context=context)
        if finding.line is not None:
            location = f"{location}:{finding.line}"
        lines.append(f"{location} [{finding.code}] sensitive content redacted")
    return "\n".join(lines)


def redact_sensitive(value: str, *, context: RedactionContext | None = None) -> str:
    """Redact secret-like and private-path values before writing logs."""
    context = context or RedactionContext()
    _reserve_markers(value, context)
    return _redact_unmarked(value, context)


def _redact_unmarked(value: str, context: RedactionContext) -> str:
    spans: list[tuple[int, int, bool]] = []
    for pattern, group in (
        (_SECRET_ASSIGNMENT_RE, "value"),
        (_SENSITIVE_QUERY_RE, "value"),
        *((pattern, 0) for pattern in PRIVATE_PATH_PATTERNS),
        *((pattern, 0) for pattern in SECRET_PATTERNS),
    ):
        spans.extend((*match.span(group), False) for match in pattern.finditer(value))
    spans.extend((*match.span(), True) for match in _REDACTION_MARKER_RE.finditer(value))

    redacted_parts: list[str] = []
    position = 0
    for start, end, preserve in sorted(
        spans, key=lambda span: (span[0], -(span[1] - span[0]), not span[2])
    ):
        if start < position:
            continue
        redacted_parts.append(value[position:start])
        redacted_parts.append(value[start:end] if preserve else context.marker(value[start:end]))
        position = end
    redacted_parts.append(value[position:])
    redacted = "".join(redacted_parts)
    return "".join(
        character if character.isprintable() or character in "\r\n\t" else "?"
        for character in redacted
    )


def redact_sensitive_values(value: Any, *, context: RedactionContext | None = None) -> Any:
    """Recursively apply the canonical public-output redaction to JSON-like data."""
    context = context or RedactionContext()
    _reserve_markers(value, context)
    return _redact_sensitive_values(value, context)


def _redact_sensitive_values(value: Any, context: RedactionContext) -> Any:
    if isinstance(value, str):
        return _redact_unmarked(value, context)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted_key = _redact_unmarked(key, context) if isinstance(key, str) else key
            if isinstance(key, str) and _SENSITIVE_KEY_RE.fullmatch(key) and isinstance(item, str):
                structured = _redact_unmarked(item, context)
                redacted[redacted_key] = (
                    structured
                    if _has_redactable_span(item) or _REDACTION_MARKER_RE.fullmatch(item)
                    else context.marker(item)
                )
            else:
                redacted[redacted_key] = _redact_sensitive_values(item, context)
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_sensitive_values(item, context) for item in value]
    if isinstance(value, set):
        return [_redact_sensitive_values(item, context) for item in sorted(value, key=repr)]
    return value


def _reserve_markers(value: Any, context: RedactionContext) -> None:
    if isinstance(value, str):
        for match in _REDACTION_MARKER_RE.finditer(value):
            context.reserve(match.group())
    elif isinstance(value, dict):
        for key, item in value.items():
            _reserve_markers(key, context)
            _reserve_markers(item, context)
    elif isinstance(value, list | tuple | set):
        for item in value:
            _reserve_markers(item, context)


def _has_redactable_span(value: str) -> bool:
    return any(
        pattern.search(value)
        for pattern in (
            _SECRET_ASSIGNMENT_RE,
            _SENSITIVE_QUERY_RE,
            *PRIVATE_PATH_PATTERNS,
            *SECRET_PATTERNS,
        )
    )


def audit_bytes(content: bytes, display_path: str) -> list[Finding]:
    """Audit in-memory file content without including matched values in findings."""
    if b"\0" in content:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if any(pattern.search(line) for pattern in PRIVATE_PATH_PATTERNS):
            findings.append(Finding("private-absolute-path", display_path, line_number))
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            findings.append(Finding("secret", display_path, line_number))
    return findings


def _audit_text(path: Path, display_path: str) -> list[Finding]:
    return audit_bytes(path.read_bytes(), display_path)


def public_category(path: Path) -> str | None:
    """Return the documented public category of a repository-relative path."""
    parts = PurePosixPath(_relative_posix(path)).parts
    if not parts:
        return None
    return PUBLIC_CATEGORIES.get(parts[0])


def _is_harness_residue(path: Path) -> bool:
    """Report lifecycle metadata left inside the evaluation category by a harness run.

    Corpus directories hold reviewed fixture investigations that are committed on
    purpose, so lifecycle metadata under them is an input rather than run residue.
    """
    if public_category(path) != EVALUATION_CATEGORY or path.name != LIFECYCLE_METADATA_NAME:
        return False
    parts = PurePosixPath(_relative_posix(path)).parts
    return len(parts) < 2 or parts[1] not in EVALUATION_CORPUS_DIRECTORIES


def _is_prohibited(path: Path) -> bool:
    parts = path.parts
    return (
        any(
            part in PROHIBITED_NAMES or part == ".env" or part.startswith(".env.") for part in parts
        )
        or path.suffix.lower() in PROHIBITED_SUFFIXES
        or path.name.endswith(".env")
    )


def _is_excluded(path: Path, exclusions: set[PurePosixPath]) -> bool:
    relative = PurePosixPath(_relative_posix(path))
    return any(relative == excluded or excluded in relative.parents for excluded in exclusions)


def _relative_posix(path: Path) -> str:
    value = path.as_posix().removeprefix("./")
    return value or "."


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=Path,
        help="Relative path to exclude; repeat for multiple technical exclusions",
    )
    args = parser.parse_args(argv)
    findings = audit_tree(args.root, excluded=args.exclude)
    if findings:
        print(render_findings(findings))
        return 1
    print("audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
