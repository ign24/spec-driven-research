"""Deterministic, offline validation that the product surface is written in English.

The product surface is everything a user reads from the installed tool: the string
literals of the packaged modules (command help, group description, and the messages
raised on success, refusal, or failure) and the artifact templates the tool writes.

The check is deliberately conservative. It keys on the Spanish-specific characters and
on a small closed list of Spanish function words that are not English words, so it
cannot misread English prose. It is not a language classifier and it will not catch
English-looking Spanish. Documentation translations are excluded by path, never by
heuristic.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

PRODUCT_SOURCE_ROOT = "src/sdr"
PRODUCT_TEMPLATE_ROOT = "src/sdr/templates"
# English documentation, the canonical skills, and the maintained `examples/` fixtures
# quote or materialize the product surface, so they belong to it: a Spanish heading or
# message reaching a reader through a guide, or through the artifacts the five-minute
# tour tells them to inspect, is the same defect as one printed by the tool.
DOCUMENTATION_ROOTS = ("docs", "examples", "skills")
# The tour reads fixture metadata as well as prose, so both are surface.
DOCUMENTATION_SUFFIXES = (".md", ".yaml")
# The reciprocal link into the Spanish pair is required by README parity, and its label
# names the language in that language. It is the translation affordance, not a finding.
TRANSLATION_LINK = re.compile(r"\[[^]]*]\([^)\s]*\.es\.md\)")

# Documentation translations are excluded by location. `README.es.md` and `docs/*.es.md`
# are the Spanish documentation pair; the archived OpenSpec history is not retranslated.
TRANSLATION_SUFFIX = ".es.md"
EXCLUDED_PREFIXES = (PurePosixPath("openspec/changes/archive"),)
# These two modules validate Spanish text rather than address a user in it:
# `readme_parity.py` carries the contract markers of the Spanish documentation
# translation, and this module carries the Spanish marker list itself.
EXCLUDED_FILES = frozenset(
    {
        "src/sdr/legacy_sections.py",
        "src/sdr/product_language.py",
        "src/sdr/readme_parity.py",
    }
)

SPANISH_CHARACTERS = "áéíóúñ¿¡ÁÉÍÓÚÑ"
SPANISH_WORDS = (
    "cada",
    "como",
    "cuando",
    "debe",
    "deben",
    "del",
    "desde",
    "donde",
    "el",
    "entre",
    "esa",
    "esas",
    "ese",
    "esos",
    "esta",
    "estas",
    "este",
    "estos",
    "hasta",
    "hay",
    "la",
    "las",
    "los",
    "para",
    "pero",
    "por",
    "porque",
    "pueden",
    "puede",
    "que",
    "sobre",
    "todas",
    "todos",
    "una",
    "unas",
    "unos",
)
CHARACTER_CODE = "spanish-character"
WORD_CODE = "spanish-word"

_CHARACTER_RE = re.compile(f"[{SPANISH_CHARACTERS}]")
_WORD_RE = re.compile(rf"(?<!\w)(?:{'|'.join(SPANISH_WORDS)})(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class LanguageFinding:
    code: str
    path: str
    line: int
    message: str


def is_excluded(relative_path: str) -> bool:
    """Report whether a repository-relative path is outside the validated surface."""
    path = PurePosixPath(relative_path)
    if relative_path in EXCLUDED_FILES:
        return True
    if path.name.endswith(TRANSLATION_SUFFIX):
        return True
    return any(prefix == path or prefix in path.parents for prefix in EXCLUDED_PREFIXES)


def product_surface_files(root: Path) -> list[Path]:
    """List the files that make up the product surface, in a stable order."""
    modules = sorted((root / PRODUCT_SOURCE_ROOT).rglob("*.py"))
    templates = sorted(path for path in (root / PRODUCT_TEMPLATE_ROOT).rglob("*") if path.is_file())
    documentation = [
        path
        for name in DOCUMENTATION_ROOTS
        for suffix in DOCUMENTATION_SUFFIXES
        for path in sorted((root / name).rglob(f"*{suffix}"))
        if path.is_file()
    ]
    surface = []
    for path in [*modules, *templates, *documentation]:
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in path.parts or is_excluded(relative):
            continue
        surface.append(path)
    return surface


def validate_product_language(root: Path) -> list[LanguageFinding]:
    """Report every Spanish marker found in the product surface, with file and line."""
    markers: dict[tuple[str, int, str], set[str]] = {}
    for path in product_surface_files(root):
        relative = path.relative_to(root).as_posix()
        text = _read_text(path)
        if text is None:
            continue
        if path.suffix == ".py":
            segments = _string_literal_segments(text)
        else:
            segments = list(enumerate(text.splitlines(), start=1))
        for line, segment in segments:
            segment = TRANSLATION_LINK.sub("", segment)
            for code, found in _markers(segment).items():
                markers.setdefault((relative, line, code), set()).update(found)

    findings = [
        LanguageFinding(code, relative, line, _message(code, sorted(found)))
        for (relative, line, code), found in markers.items()
    ]
    return sorted(findings, key=lambda item: (item.path, item.line, item.code))


def render_findings(findings: Sequence[LanguageFinding]) -> str:
    """Render one line per finding, naming the file, the line, and the marker."""
    return "\n".join(
        f"{finding.path}:{finding.line} [{finding.code}] {finding.message}" for finding in findings
    )


def _markers(segment: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    characters = {match.group() for match in _CHARACTER_RE.finditer(segment)}
    if characters:
        found[CHARACTER_CODE] = characters
    words = {match.group().casefold() for match in _WORD_RE.finditer(segment)}
    if words:
        found[WORD_CODE] = words
    return found


def _message(code: str, found: Sequence[str]) -> str:
    subject = "characters" if code == CHARACTER_CODE else "words"
    return f"translate this product-surface text to English; Spanish {subject}: {', '.join(found)}"


def _string_literal_segments(source: str) -> list[tuple[int, str]]:
    """Return the source spans covered by string literals, keyed by their own line.

    Only the literal spans are inspected, so a Spanish word can never be reported from
    a comment or from a Python keyword sharing the line with a string.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    segments: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.end_lineno is None or node.end_col_offset is None:
            continue
        for number in range(node.lineno, node.end_lineno + 1):
            if number > len(lines):
                continue
            line = lines[number - 1]
            start = node.col_offset if number == node.lineno else 0
            end = node.end_col_offset if number == node.end_lineno else len(line)
            segments.append((number, line[start:end]))
    return segments


def _read_text(path: Path) -> str | None:
    content = path.read_bytes()
    if b"\0" in content:
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    findings = validate_product_language(args.root)
    if findings:
        print(render_findings(findings))
        return 1
    print("Product language: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
