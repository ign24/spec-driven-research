"""Parser de artefactos markdown: frontmatter YAML + secciones por heading.

Convierte un archivo markdown en un objeto consultable por los gates: el
frontmatter como dict y cada heading como una sección con su contenido.
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
    """Colapsa espacios y aplica casefold para comparar headings."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class Artifact:
    """Artefacto markdown parseado."""

    path: Path
    frontmatter: dict[str, Any]
    body: str
    sections: dict[str, str]

    def section(self, name: str) -> str | None:
        """Contenido de la sección cuyo heading coincide con `name`.

        La coincidencia es insensible a mayúsculas y espacios, y admite una
        anotación al final del heading (p. ej. "Criterios de evaluación (X)").
        Devuelve None si no existe la sección.
        """
        target = _normalize(name)
        for heading, content in self.sections.items():
            norm = _normalize(heading)
            if norm == target or norm.startswith(target):
                return content
        return None

    def has_content(self, name: str) -> bool:
        """True si la sección existe y tiene contenido no vacío."""
        content = self.section(name)
        return bool(content and content.strip())


def parse_artifact(path: str | Path) -> Artifact:
    """Parsea el archivo markdown en `path` a un Artifact."""
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
    """Divide el cuerpo en secciones {heading: contenido hasta el próximo heading}."""
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
