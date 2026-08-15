import socket
import subprocess
import sys
from pathlib import Path

import pytest

from sdr.product_language import product_surface_files, validate_product_language

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ENGLISH_MODULE = '''"""Create investigations, validate gates, and advance stages."""


def describe() -> str:
    """Return the group description shown by the console script."""
    return "Spec-Driven Research: a traceable applied research harness."
'''
SPANISH_MODULE = '''"""CLI `sdr`: crear investigaciones y validar gates.

La base de investigaciones se resuelve desde SDR_ROOT.
"""


def describe() -> str:
    """Ejecuta el gate de la etapa."""
    return "no hay investigaciones"
'''
ENGLISH_TEMPLATE = """# Brief

## Question

## Hypothesis

Evaluation criteria
"""
SPANISH_TEMPLATE = """# Brief

## Pregunta

## Hipótesis

Criterios de evaluación
"""


def _write_surface(root: Path, *, module: str, template: str) -> Path:
    package = root / "src" / "sdr"
    (package / "templates").mkdir(parents=True)
    (package / "cli.py").write_text(module, encoding="utf-8")
    (package / "templates" / "brief.md").write_text(template, encoding="utf-8")
    return root


def test_validation_reports_spanish_product_surface_with_file_and_line(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=SPANISH_MODULE, template=SPANISH_TEMPLATE)

    findings = validate_product_language(root)

    located = {(finding.path, finding.line) for finding in findings}
    assert ("src/sdr/cli.py", 3) in located, "the Spanish module docstring line must be reported"
    assert ("src/sdr/cli.py", 8) in located, "the Spanish command help must be reported"
    assert ("src/sdr/cli.py", 9) in located, "the Spanish user-facing message must be reported"
    assert ("src/sdr/templates/brief.md", 5) in located
    assert ("src/sdr/templates/brief.md", 7) in located
    for finding in findings:
        assert finding.line >= 1
        assert finding.code in {"spanish-character", "spanish-word"}
        assert finding.message


def test_validation_reports_no_finding_for_an_english_product_surface(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)

    assert validate_product_language(root) == []


@pytest.mark.parametrize(
    "prose",
    (
        "Retry the connection, then escalate to a human reviewer.",
        "Sources, hypotheses, and criteria are recorded per stage.",
        "The probe has no proxy; a sandbox runs each declared command.",
        "Stage gates run in order: structural, then evidential.",
    ),
)
def test_english_prose_is_never_reported(tmp_path: Path, prose: str) -> None:
    module = f'''"""Module."""


def describe() -> str:
    """Docstring."""
    return {prose!r}
'''
    root = _write_surface(tmp_path, module=module, template=ENGLISH_TEMPLATE)

    assert validate_product_language(root) == []


def test_documentation_translations_are_excluded_by_path(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)
    spanish = "# Título\n\nEsta es la traducción de la documentación.\n"
    (root / "README.es.md").write_text(spanish, encoding="utf-8")
    docs = root / "docs"
    docs.mkdir()
    (docs / "getting-started.es.md").write_text(spanish, encoding="utf-8")
    (docs / "README.es.md").write_text(spanish, encoding="utf-8")
    archive = root / "openspec" / "changes" / "archive" / "old-change"
    archive.mkdir(parents=True)
    (archive / "proposal.md").write_text(spanish, encoding="utf-8")

    findings = validate_product_language(root)

    assert findings == []
    reported = {finding.path for finding in findings}
    for excluded in (
        "README.es.md",
        "docs/getting-started.es.md",
        "docs/README.es.md",
        "openspec/changes/archive/old-change/proposal.md",
    ):
        assert excluded not in reported


def test_a_spanish_documentation_translation_never_becomes_a_finding(tmp_path: Path) -> None:
    """Exclusion is by path, so even a Spanish file inside the package is judged by location."""
    root = _write_surface(tmp_path, module=SPANISH_MODULE, template=SPANISH_TEMPLATE)
    (root / "README.es.md").write_text("# Guía\n\nla documentación\n", encoding="utf-8")

    findings = validate_product_language(root)

    assert findings
    assert all(finding.path != "README.es.md" for finding in findings)


def test_validation_is_deterministic_and_consults_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_surface(tmp_path, module=SPANISH_MODULE, template=SPANISH_TEMPLATE)

    def _forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("product-language validation must not use the network")

    monkeypatch.setattr(socket, "socket", _forbid_network)
    monkeypatch.setattr(socket, "create_connection", _forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", _forbid_network)

    first = validate_product_language(root)
    second = validate_product_language(root)

    assert first == second
    assert first == sorted(first, key=lambda item: (item.path, item.line, item.code))


def test_module_cli_reports_findings_and_is_deterministic_across_runs(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=SPANISH_MODULE, template=SPANISH_TEMPLATE)

    runs = [
        subprocess.run(
            [sys.executable, "-m", "sdr.product_language", str(root)],
            text=True,
            capture_output=True,
            check=False,
            cwd=PROJECT_ROOT,
        )
        for _ in range(2)
    ]

    assert runs[0].returncode == 1
    assert runs[1].stdout == runs[0].stdout
    assert "src/sdr/cli.py:9" in runs[0].stdout
    assert "src/sdr/templates/brief.md:5" in runs[0].stdout


def test_module_cli_reports_ok_on_an_english_product_surface(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)

    completed = subprocess.run(
        [sys.executable, "-m", "sdr.product_language", str(root)],
        text=True,
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
    )

    assert completed.returncode == 0
    assert completed.stdout == "Product language: OK\n"


def test_english_documentation_and_skills_are_part_of_the_surface(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "workflow.md").write_text(
        "# Workflow\n\nLa etapa transfer exige aprobación humana.\n", encoding="utf-8"
    )
    (root / "docs" / "workflow.es.md").write_text(
        "# Flujo\n\nLa etapa transfer exige aprobación humana.\n", encoding="utf-8"
    )
    (root / "skills" / "sdr-intake").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "sdr-intake" / "SKILL.md").write_text(
        "# Intake\n\nCompletá la sección Pregunta.\n", encoding="utf-8"
    )

    findings = validate_product_language(root)
    located = {(finding.path, finding.line) for finding in findings}

    assert ("docs/workflow.md", 3) in located, "English documentation must be validated"
    assert ("skills/sdr-intake/SKILL.md", 3) in located, "canonical skills must be validated"
    assert not any(finding.path.endswith(".es.md") for finding in findings), (
        "the Spanish translation must stay excluded by path"
    )


def test_maintained_examples_are_part_of_the_surface(tmp_path: Path) -> None:
    """The five-minute tour tells the reader to inspect these fixtures, so they are surface."""
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)
    fixture = root / "examples" / "light-complete"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "brief.md").write_text(
        "## Question\n\n¿Conviene evaluar un clasificador?\n", encoding="utf-8"
    )

    findings = validate_product_language(root)
    located = {(finding.path, finding.line) for finding in findings}

    assert ("examples/light-complete/brief.md", 3) in located, (
        "the maintained examples must be validated"
    )


def test_the_repository_examples_are_inside_the_validated_surface() -> None:
    surface = {
        path.relative_to(PROJECT_ROOT).as_posix() for path in product_surface_files(PROJECT_ROOT)
    }

    for fixture in (
        "examples/README.md",
        "examples/light-complete/README.md",
        "examples/light-complete/brief.md",
        "examples/light-complete/notes/landscape.md",
        "examples/light-complete/decision-memo.md",
        "examples/light-complete/assets/checklist.md",
        "examples/full-complete/brief.md",
        "examples/full-complete/probe/results.md",
        "examples/failing-gates/intake-missing-owner/brief.md",
    ):
        assert fixture in surface, fixture


def test_the_reciprocal_link_into_the_translation_is_not_a_finding(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "workflow.md").write_text(
        "# Workflow\n\n[Español](workflow.es.md)\n\nEnglish body.\n", encoding="utf-8"
    )

    assert validate_product_language(root) == [], (
        "the link label naming the Spanish translation is required by readme parity"
    )


def test_fixture_metadata_is_part_of_the_surface(tmp_path: Path) -> None:
    root = _write_surface(tmp_path, module=ENGLISH_MODULE, template=ENGLISH_TEMPLATE)
    fixture = root / "examples" / "light-complete"
    fixture.mkdir(parents=True, exist_ok=True)
    (fixture / "fixture.yaml").write_text(
        "slug: synthetic-light\ntitle: Evaluación sintética\n", encoding="utf-8"
    )

    findings = validate_product_language(root)

    assert ("examples/light-complete/fixture.yaml", 2) in {
        (finding.path, finding.line) for finding in findings
    }, "fixture metadata is read by the tour and must be validated"
