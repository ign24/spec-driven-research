"""The artifact section names must resolve from one declaration.

Templates, `schema.required_sections`, and the gate engine's section lookups all
name the same headings. When each repeats the literal, a translation or rename
can change one and leave the others behind, which is how the artifact contract
drifts from the artifacts it validates.
"""

import re
from pathlib import Path

from sdr.schema import artifact_for, declared_sections, stage_order

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATES = PROJECT_ROOT / "src" / "sdr" / "gates.py"
TEMPLATES = PROJECT_ROOT / "src" / "sdr" / "templates"
HEADING = re.compile(r"^## (.+)$", re.MULTILINE)


def test_every_required_section_comes_from_the_declaration() -> None:
    declared = set(declared_sections())

    for mode in ("light", "full"):
        for stage in stage_order(mode):
            for section in artifact_for(stage).required_sections:
                assert section in declared, f"{stage}: {section!r} is not declared"


def test_gate_engine_names_no_section_literal() -> None:
    source = GATES.read_text(encoding="utf-8")

    repeated = sorted(section for section in declared_sections() if f'"{section}"' in source)

    assert repeated == [], (
        "gates.py repeats section literals instead of resolving them from the "
        f"declaration: {repeated}"
    )


def test_every_template_heading_is_declared() -> None:
    declared = set(declared_sections())

    undeclared: list[str] = []
    for template in sorted(TEMPLATES.glob("*.md")):
        for heading in HEADING.findall(template.read_text(encoding="utf-8")):
            if heading.strip() not in declared:
                undeclared.append(f"{template.name}: {heading.strip()}")

    assert undeclared == [], f"templates use undeclared section names: {undeclared}"


SPANISH = re.compile(r"[áéíóúñ¿¡]", re.IGNORECASE)


def test_declared_sections_are_english() -> None:
    spanish = sorted(name for name in declared_sections() if SPANISH.search(name))

    assert spanish == [], f"artifact section names must be English: {spanish}"


def test_packaged_templates_declare_english_sections() -> None:
    spanish: list[str] = []
    for template in sorted(TEMPLATES.glob("*.md")):
        for heading in HEADING.findall(template.read_text(encoding="utf-8")):
            if SPANISH.search(heading):
                spanish.append(f"{template.name}: {heading.strip()}")

    assert spanish == [], f"templates must declare English sections: {spanish}"
