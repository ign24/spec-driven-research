"""Single source of truth of the SDR framework.

Declares the stages of the investigation lifecycle, the artifacts of each stage
(files, frontmatter and required sections) and the deterministic gate rules.
Validation and template generation/verification both consume these structures,
so that they cannot diverge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

STAGES: tuple[str, ...] = ("intake", "explore", "probe", "transfer", "reuse")

MODES: tuple[str, ...] = ("full", "light")

# Stages skipped in each mode.
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

# Rings that require an approved probe stage with results.
RINGS_REQUIRING_PROBE: frozenset[str] = frozenset({"adopt", "trial"})
_CLAIM_ID_RE = re.compile(r"^claim-[0-9a-f]{64}$")


SECTION_QUESTION = "Question"
SECTION_HYPOTHESIS = "Hypothesis"
SECTION_CONTEXT = "Context"
SECTION_SCOPE = "Scope"
SECTION_EVALUATION_CRITERIA = "Evaluation criteria"
SECTION_ADOPTION_RISKS = "Adoption risks"
SECTION_ALTERNATIVES = "Alternatives evaluated"
SECTION_MATURITY = "Maturity"
SECTION_COSTS = "Costs"
SECTION_RISKS = "Risks"
SECTION_COUNTER_EVIDENCE = "Counter-evidence"
SECTION_CRITERIA_RESULTS = "Results by criterion"
SECTION_REPRODUCTION = "Reproduction"
SECTION_RECOMMENDATION = "Recommendation"
SECTION_SELECTION_CRITERIA = "Selection criteria"
SECTION_RISKS_AND_LIMITS = "Risks and limitations"
SECTION_NEXT_STEPS = "Next steps"
SECTION_AUDIENCE = "Audience"

_SECTIONS: tuple[str, ...] = (
    SECTION_QUESTION,
    SECTION_HYPOTHESIS,
    SECTION_CONTEXT,
    SECTION_SCOPE,
    SECTION_EVALUATION_CRITERIA,
    SECTION_ADOPTION_RISKS,
    SECTION_ALTERNATIVES,
    SECTION_MATURITY,
    SECTION_COSTS,
    SECTION_RISKS,
    SECTION_COUNTER_EVIDENCE,
    SECTION_CRITERIA_RESULTS,
    SECTION_REPRODUCTION,
    SECTION_RECOMMENDATION,
    SECTION_SELECTION_CRITERIA,
    SECTION_RISKS_AND_LIMITS,
    SECTION_NEXT_STEPS,
    SECTION_AUDIENCE,
)


def declared_sections() -> tuple[str, ...]:
    """Return every artifact section name the product declares."""
    return _SECTIONS


@dataclass(frozen=True)
class ArtifactSpec:
    """Structural contract of a stage's artifact."""

    stage: str
    primary_file: str
    required_sections: tuple[str, ...]
    frontmatter_required: tuple[str, ...] = ()
    # Directory that must contain at least one artifact (explore, reuse).
    collection_dir: str | None = None
    # Evidential and cross-artifact checks that run on top of the structure.
    checks: tuple[str, ...] = ()


def stage_order(mode: str) -> tuple[str, ...]:
    """Active stages for a mode, in order."""
    if mode not in _MODE_SKIPS:
        raise ValueError(f"unknown mode: {mode!r}; use one of {MODES}")
    skips = _MODE_SKIPS[mode]
    return tuple(stage for stage in STAGES if stage not in skips)


def next_stage(current: str, mode: str) -> str | None:
    """Next stage in the given mode, or None if `current` is the last one."""
    order = stage_order(mode)
    if current not in order:
        raise ValueError(f"stage {current!r} does not belong to mode {mode!r}")
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
            SECTION_QUESTION,
            SECTION_HYPOTHESIS,
            SECTION_CONTEXT,
            SECTION_SCOPE,
            SECTION_EVALUATION_CRITERIA,
            SECTION_ADOPTION_RISKS,
        ),
        checks=("min_evaluation_criteria",),
    ),
    "explore": ArtifactSpec(
        stage="explore",
        primary_file="notes",
        collection_dir="notes",
        frontmatter_required=("research", "date", "stage", "sources"),
        required_sections=(
            SECTION_ALTERNATIVES,
            SECTION_MATURITY,
            SECTION_COSTS,
            SECTION_RISKS,
        ),
        checks=("source_tiers", "source_dates", "source_triangulation", "links_resolve"),
    ),
    "probe": ArtifactSpec(
        stage="probe",
        primary_file="probe/results.md",
        frontmatter_required=("research", "date", "stage"),
        required_sections=(
            SECTION_CRITERIA_RESULTS,
            SECTION_REPRODUCTION,
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
            SECTION_RECOMMENDATION,
            SECTION_ALTERNATIVES,
            SECTION_SELECTION_CRITERIA,
            SECTION_RISKS_AND_LIMITS,
            SECTION_NEXT_STEPS,
            SECTION_AUDIENCE,
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

# Minimum number of verifiable evaluation criteria in the brief.
MIN_EVALUATION_CRITERIA: int = 2

# Minimum number of distinct declared hosts across the explore sources.
MIN_DISTINCT_DECLARED_HOSTS: int = 2


# Template that feeds each stage's artifact (they live in `templates/`).
TEMPLATE_FILES: dict[str, str] = {
    "intake": "brief.md",
    "explore": "note.md",
    "probe": "probe-results.md",
    "transfer": "decision-memo.md",
    "reuse": "asset.md",
}


def artifact_for(stage: str, schema_version: int = 1) -> ArtifactSpec:
    """ArtifactSpec of a stage."""
    try:
        spec = _ARTIFACTS[stage]
    except KeyError:
        raise ValueError(f"unknown stage: {stage!r}") from None
    if stage == "explore" and schema_version >= 2:
        return replace(
            spec,
            required_sections=(*spec.required_sections, SECTION_COUNTER_EVIDENCE),
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
    """Name of the template file that feeds the stage."""
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
