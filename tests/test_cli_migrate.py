"""`sdr migrate` carries an investigation created under the Spanish contract forward.

The fixture is a light-mode investigation whose artifacts carry the retired
section headings and whose `intake` validation hash was recorded against them,
which is what a research root created before the English artifact contract looks
like on disk.
"""

import json

import pytest
from click.testing import CliRunner

from sdr import lifecycle, snapshot
from sdr.cli import main
from sdr.research import Research

SLUG = "legacy-cards"

LEGACY_BRIEF = """---
research: legacy-cards
date: 2026-07-01
stage: intake
owner: Example Researcher
timebox: 2
---

## Pregunta
¿Conviene evaluar un clasificador determinístico para ordenar tarjetas ficticias?

## Hipótesis
El clasificador puede ordenar todos los casos sintéticos sin agregar operación compleja.

## Contexto
Un equipo ficticio necesita una decisión reproducible para un ejercicio de capacitación.

## Alcance
Incluye reglas locales y datos inventados. No incluye datos personales ni producción.

## Criterios de evaluación
- C1: documentar al menos dos alternativas con fuentes trazables.
- C2: producir una recomendación limitada al anillo assess.

## Riesgos de adopción
Las entradas reales podrían no parecerse al conjunto sintético.
"""

LEGACY_NOTE = """---
research: legacy-cards
date: 2026-07-01
stage: explore
sources:
  - id: S1
    url: https://docs.cards.example/rules
    tier: T1
    date: 2026-06-15
  - id: S2
    url: https://bench.methods.test/comparison
    tier: T2
    date: 2026-06-20
---

## Alternativas evaluadas
Se comparan reglas ordenadas y una tabla de decisión dentro del ejercicio [cf. S1].

## Madurez
La nota sintética trata ambas alternativas como material de capacitación [cf. S1].

## Costos
La comparación ficticia considera solamente tiempo local de mantenimiento [cf. S2].

## Riesgos
La simplificación del ejercicio puede ocultar excepciones de casos reales [cf. S2].

## Contra-evidencia
Se revisa la posibilidad de que ninguna alternativa generalice fuera del ejercicio [cf. S2].
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))
    monkeypatch.setattr(snapshot, "capture_declared_sources", lambda research: ())
    runner = CliRunner()

    def _run(*args):
        return runner.invoke(main, list(args), catch_exceptions=False)

    _run.base = tmp_path
    return _run


def _legacy_root(run) -> Research:
    """Materialize a research root as it existed under the Spanish contract."""
    research = Research.create(
        base=run.base,
        slug=SLUG,
        title="Legacy cards",
        question="legacy question",
        mode="light",
        owner="Example Researcher",
        timebox=2,
    )
    research.artifact_path("brief.md").write_text(LEGACY_BRIEF, encoding="utf-8")
    notes = research.artifact_path("notes")
    notes.mkdir(exist_ok=True)
    (notes / "landscape.md").write_text(LEGACY_NOTE, encoding="utf-8")
    # The root left intake under the previous contract, so its stored hash is the
    # hash of the Spanish brief.
    research.meta.validation["intake"] = lifecycle.stage_hash(research, "intake")
    research.meta.stage = "explore"
    research.save()
    return research


def test_migrated_investigation_advances_under_the_current_gates(run):
    _legacy_root(run)

    blocked = run("advance", SLUG, "--offline", "--no-commit")
    migrated = run("migrate", SLUG)
    advanced = run("advance", SLUG, "--offline", "--no-commit")

    assert blocked.exit_code == 1
    assert blocked.output.startswith("advance blocked: ")
    assert migrated.exit_code == 0
    assert advanced.exit_code == 0
    assert advanced.output == f"{SLUG}: advanced to stage transfer (status active)\n"


def test_migration_reports_the_headings_it_changed_and_is_idempotent(run):
    _legacy_root(run)

    first = run("migrate", SLUG, "--json")
    second = run("migrate", SLUG, "--json")
    readable = run("migrate", SLUG)
    changed = json.loads(first.output)["heading_changes"]

    assert first.exit_code == 0
    assert changed == [
        "brief.md: Pregunta -> Question",
        "brief.md: Hipótesis -> Hypothesis",
        "brief.md: Contexto -> Context",
        "brief.md: Alcance -> Scope",
        "brief.md: Criterios de evaluación -> Evaluation criteria",
        "brief.md: Riesgos de adopción -> Adoption risks",
        "notes/landscape.md: Alternativas evaluadas -> Alternatives evaluated",
        "notes/landscape.md: Madurez -> Maturity",
        "notes/landscape.md: Costos -> Costs",
        "notes/landscape.md: Riesgos -> Risks",
        "notes/landscape.md: Contra-evidencia -> Counter-evidence",
    ]
    assert second.exit_code == 0
    assert json.loads(second.output)["heading_changes"] == []
    assert "  headings: no change required" in readable.output


def test_user_authored_prose_is_left_byte_identical(run):
    research = _legacy_root(run)
    brief = research.artifact_path("brief.md")
    note = research.artifact_path("notes/landscape.md")
    prose_before = {
        "brief.md": _prose_bytes(brief.read_bytes()),
        "landscape.md": _prose_bytes(note.read_bytes()),
    }

    result = run("migrate", SLUG)

    assert result.exit_code == 0
    assert _prose_bytes(brief.read_bytes()) == prose_before["brief.md"]
    assert _prose_bytes(note.read_bytes()) == prose_before["landscape.md"]


def _prose_bytes(content: bytes) -> list[bytes]:
    """Every line that is not an ATX heading, byte for byte."""
    return [line for line in content.split(b"\n") if not line.startswith(b"#")]
