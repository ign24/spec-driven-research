"""Markdown artifact parser: YAML frontmatter plus sections by heading.

Turns a markdown file into an object the gates can query: the frontmatter as a
dict and every heading as a section with its content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from yaml.nodes import MappingNode, ScalarNode

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


class DuplicateFrontmatterKeyError(ValueError):
    """A protected top-level frontmatter key was declared more than once."""


def _normalize(text: str) -> str:
    """Collapse whitespace and casefold in order to compare headings."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class Artifact:
    """Parsed markdown artifact."""

    path: Path
    frontmatter: dict[str, Any]
    body: str
    sections: dict[str, str]

    def section(self, name: str) -> str | None:
        """Content of the section whose heading matches `name`.

        The match ignores case and whitespace, and accepts an annotation at the
        end of the heading (for example a heading "Sources (2)" matches "Sources").
        Returns None if the section does not exist.
        """
        target = _normalize(name)
        for heading, content in self.sections.items():
            norm = _normalize(heading)
            if norm == target or norm.startswith(target):
                return content
        return None

    def has_content(self, name: str) -> bool:
        """True if the section exists and has non-empty content."""
        content = self.section(name)
        return bool(content and content.strip())


def parse_artifact(path: str | Path) -> Artifact:
    """Parse the markdown file at `path` into an Artifact."""
    path = Path(path)
    _reject_duplicate_evidence_claim_ids(path)
    post = frontmatter.load(str(path))
    sections = _split_sections(post.content)
    return Artifact(
        path=path,
        frontmatter=dict(post.metadata),
        body=post.content,
        sections=sections,
    )


def _reject_duplicate_evidence_claim_ids(path: Path) -> None:
    """Reject only the lineage key whose last-value-wins parse is unsafe."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return
    node = yaml.compose("\n".join(lines[1:end]), Loader=yaml.SafeLoader)
    if not isinstance(node, MappingNode):
        return
    count = sum(
        1
        for key, _ in node.value
        if isinstance(key, ScalarNode) and key.value == "evidence_claim_ids"
    )
    if count > 1:
        raise DuplicateFrontmatterKeyError(
            "duplicate top-level frontmatter key: evidence_claim_ids"
        )


def _split_sections(body: str) -> dict[str, str]:
    """Split the body into sections {heading: content up to the next heading}."""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                sections[current] = "\n".join(buffer).strip()
            current = match.group(2)
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip()
    return sections
