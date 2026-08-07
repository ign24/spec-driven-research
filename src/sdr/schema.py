"""Fuente única de verdad del framework SDR.

Define las etapas del ciclo de investigación, los artefactos de cada etapa
(archivos, frontmatter y secciones obligatorias) y las reglas deterministas de
gate. Tanto la validación como la generación/verificación de plantillas consumen
estas estructuras, de modo que no puedan divergir.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

STAGES: tuple[str, ...] = ("intake", "explore", "probe", "transfer", "reuse")

MODES: tuple[str, ...] = ("full", "light")

# Etapas que se omiten en cada modo.
_MODE_SKIPS: dict[str, frozenset[str]] = {
    "full": frozenset(),
    "light": frozenset({"probe"}),
}

SOURCE_TIERS: tuple[str, ...] = ("T1", "T2", "T3")
DEFAULT_SOURCE_MAX_AGE: dict[str, int] = {"T1": 730, "T2": 365, "T3": 180}

RECOMMENDATION_RINGS: tuple[str, ...] = ("adopt", "trial", "assess", "hold")

ASSET_TYPES: tuple[str, ...] = (
    "playbook",
    "template",
    "post",
    "carousel",
    "script",
    "executive-summary",
    "other",
)
ASSET_AUDIENCES: tuple[str, ...] = ("internal", "external")

# Anillos que exigen una etapa probe aprobada con resultados.
RINGS_REQUIRING_PROBE: frozenset[str] = frozenset({"adopt", "trial"})
_CLAIM_ID_RE = re.compile(r"^claim-[0-9a-f]{64}$")


@dataclass(frozen=True)
class ArtifactSpec:
    """Contrato estructural del artefacto de una etapa."""

    stage: str
    primary_file: str
    required_sections: tuple[str, ...]
    frontmatter_required: tuple[str, ...] = ()
    # Directorio que debe contener al menos un artefacto (explore, reuse).
    collection_dir: str | None = None
    # Verificaciones evidenciales/cruzadas que corren además de la estructura.
    checks: tuple[str, ...] = ()


def stage_order(mode: str) -> tuple[str, ...]:
    """Etapas activas para un modo, en orden."""
    if mode not in _MODE_SKIPS:
        raise ValueError(f"modo desconocido: {mode!r}; use uno de {MODES}")
    skips = _MODE_SKIPS[mode]
    return tuple(stage for stage in STAGES if stage not in skips)


def next_stage(current: str, mode: str) -> str | None:
    """Etapa siguiente en el modo dado, o None si `current` es la última."""
    order = stage_order(mode)
    if current not in order:
        raise ValueError(f"etapa {current!r} no pertenece al modo {mode!r}")
    idx = order.index(current)
    if idx + 1 >= len(order):
        return None
    return order[idx + 1]


_ARTIFACTS: dict[str, ArtifactSpec] = {
    "intake": ArtifactSpec(
        stage="intake",
        primary_file="brief.md",
        frontmatter_required=("research", "date", "stage", "owner", "timebox"),
        required_sections=(
            "Pregunta",
            "Hipótesis",
            "Contexto",
            "Alcance",
            "Criterios de evaluación",
            "Riesgos de adopción",
        ),
        checks=("min_evaluation_criteria",),
    ),
    "explore": ArtifactSpec(
        stage="explore",
        primary_file="notes",
        collection_dir="notes",
        frontmatter_required=("research", "date", "stage", "sources"),
        required_sections=(
            "Alternativas evaluadas",
            "Madurez",
            "Costos",
            "Riesgos",
        ),
        checks=("source_tiers", "source_dates", "source_triangulation", "links_resolve"),
    ),
    "probe": ArtifactSpec(
        stage="probe",
        primary_file="probe/results.md",
        frontmatter_required=("research", "date", "stage"),
        required_sections=(
            "Resultados por criterio",
            "Reproducción",
        ),
        checks=("criteria_cross_reference", "benchmark_reproducible", "probe_artifacts_exist"),
    ),
    "transfer": ArtifactSpec(
        stage="transfer",
        primary_file="decision-memo.md",
        frontmatter_required=(
            "research",
            "date",
            "stage",
            "ring",
            "audience",
            "evidence_claim_ids",
        ),
        required_sections=(
            "Recomendación",
            "Alternativas evaluadas",
            "Criterios de selección",
            "Riesgos y limitaciones",
            "Próximos pasos",
            "Audiencia",
        ),
        checks=("y_statement", "ring_backed_by_evidence", "evidence_claim_ids"),
    ),
    "reuse": ArtifactSpec(
        stage="reuse",
        primary_file="assets",
        collection_dir="assets",
        frontmatter_required=("research", "date", "stage", "type", "audience"),
        required_sections=(),
        checks=("asset_metadata",),
    ),
}

# Cantidad mínima de criterios de evaluación verificables en el brief.
MIN_EVALUATION_CRITERIA: int = 2

# Cantidad mínima de hosts declarados distintos en las fuentes de explore.
MIN_DISTINCT_DECLARED_HOSTS: int = 2


# Plantilla que alimenta el artefacto de cada etapa (viven en `templates/`).
TEMPLATE_FILES: dict[str, str] = {
    "intake": "brief.md",
    "explore": "note.md",
    "probe": "probe-results.md",
    "transfer": "decision-memo.md",
    "reuse": "asset.md",
}


def artifact_for(stage: str, schema_version: int = 1) -> ArtifactSpec:
    """ArtifactSpec de una etapa."""
    try:
        spec = _ARTIFACTS[stage]
    except KeyError:
        raise ValueError(f"etapa desconocida: {stage!r}") from None
    if stage == "explore" and schema_version >= 2:
        return replace(
            spec,
            required_sections=(*spec.required_sections, "Contra-evidencia"),
            checks=(*spec.checks, "tier_plausibility", "claim_citation_coverage"),
        )
    if stage == "transfer" and schema_version < 2:
        return replace(
            spec,
            frontmatter_required=tuple(
                field for field in spec.frontmatter_required if field != "evidence_claim_ids"
            ),
        )
    return spec


def template_for(stage: str) -> str:
    """Nombre del archivo de plantilla que alimenta la etapa."""
    return TEMPLATE_FILES[stage]


def validate_evidence_claim_ids(value: object) -> tuple[str, ...]:
    """Validate and return a decision memo's exact persisted claim IDs."""
    if not isinstance(value, list):
        raise ValueError("evidence_claim_ids must be a list")
    claim_ids: list[str] = []
    seen: set[str] = set()
    for index, claim_id in enumerate(value):
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"evidence_claim_ids[{index}] must not be empty")
        if not _CLAIM_ID_RE.fullmatch(claim_id):
            raise ValueError(f"malformed claim ID: {claim_id}")
        if claim_id in seen:
            raise ValueError(f"duplicate claim ID: {claim_id}")
        seen.add(claim_id)
        claim_ids.append(claim_id)
    return tuple(claim_ids)
