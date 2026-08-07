"""Fixture-only metamorphic execution for deterministic cross retrieval."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from bench.harness.reuse import (
    ArtifactFixture,
    InvestigationFixture,
    MaterializedReuseScenario,
    ReuseScenario,
    prepare_reuse_scenario,
)
from bench.harness.runspace import REPOSITORY_ROOT
from sdr.cross_investigation import (
    derive_cross_investigation_layer,
    normalize_url,
    query_source_dependencies,
)


class MetamorphicRelation(StrEnum):
    """Supported exact relations over disposable reuse-fixture inputs."""

    SEED_ORDER_INVARIANCE = "seed-order-invariance"
    URL_NORMALIZATION_INVARIANCE = "url-normalization-invariance"
    EXPLICIT_PROVENANCE_REMOVAL = "explicit-provenance-removal"
    DONE_TO_ACTIVE_EXCLUSION = "done-to-active-exclusion"


class TransformationKind(StrEnum):
    """Trust-boundary classification of one validation transformation."""

    FIXTURE_METAMORPHISM = "fixture-metamorphism"
    PACKAGE_MUTATION = "package-mutation"


@dataclass(frozen=True)
class MetamorphicFailure:
    """One inspectable exact-relation mismatch."""

    code: str
    path: str
    expected: Any
    observed: Any


@dataclass(frozen=True)
class MetamorphicResult:
    """Typed baseline/transformed evidence for one fixture relation."""

    relation: MetamorphicRelation
    classification: TransformationKind
    baseline_projection: Mapping[str, Any]
    transformed_projection: Mapping[str, Any]
    baseline_bytes: bytes
    transformed_bytes: bytes
    baseline_sha256: str
    transformed_sha256: str
    failures: tuple[MetamorphicFailure, ...]
    package_before_sha256: str
    package_after_sha256: str
    package_unchanged: bool
    cleanup_deleted: bool

    @property
    def passed(self) -> bool:
        """Whether the declared exact relation held with all boundaries intact."""
        return not self.failures and self.package_unchanged and self.cleanup_deleted


@dataclass(frozen=True)
class _DerivedOutput:
    layer: Mapping[str, Any]
    query: Mapping[str, Any]
    cleanup_deleted: bool


def execute_metamorphic_relation(
    scenario: ReuseScenario,
    relation: MetamorphicRelation,
    *,
    parent: Path | None = None,
) -> MetamorphicResult:
    """Execute a real baseline/transformed derivation without touching package code."""
    if not isinstance(relation, MetamorphicRelation):
        raise ValueError(f"unsupported metamorphic relation: {relation!r}")
    package_root = REPOSITORY_ROOT / "src" / "sdr"
    if (
        relation is MetamorphicRelation.DONE_TO_ACTIVE_EXCLUSION
        and scenario.history != "history-present"
    ):
        raise ValueError("done-to-active transformation requires history-present")
    package_before = _tree_sha256(package_root)
    baseline = _derive(scenario, parent=parent, repetition=0)
    transformed_scenario = scenario
    transform: Callable[[MaterializedReuseScenario], None] | None = None
    transformed_input_cleanup = True
    if relation is MetamorphicRelation.SEED_ORDER_INVARIANCE:
        if len(scenario.seeds) < 2:
            raise ValueError("seed-order invariance requires at least two seed investigations")
        transformed_scenario = replace(scenario, seeds=tuple(reversed(scenario.seeds)))
    elif relation is MetamorphicRelation.URL_NORMALIZATION_INVARIANCE:
        transform = _normalize_url_spelling
    elif relation is MetamorphicRelation.EXPLICIT_PROVENANCE_REMOVAL:
        transform = _remove_explicit_anchor
    if relation is MetamorphicRelation.DONE_TO_ACTIVE_EXCLUSION:
        with _active_seed_fixture(scenario, parent=parent) as (
            transformed_scenario,
            transformed_input,
        ):
            transformed = _derive(transformed_scenario, parent=parent, repetition=1)
        transformed_input_cleanup = not transformed_input.exists()
    else:
        transformed = _derive(
            transformed_scenario,
            parent=parent,
            repetition=1,
            transform=transform,
        )
    baseline_projection, transformed_projection, failures = _compare_relation(
        relation, scenario, baseline, transformed
    )
    baseline_bytes = _canonical_bytes(baseline_projection)
    transformed_bytes = _canonical_bytes(transformed_projection)
    package_after = _tree_sha256(package_root)
    if package_before != package_after:
        failures += (
            MetamorphicFailure(
                "package-tree-changed",
                "src/sdr",
                package_before,
                package_after,
            ),
        )
    cleanup_deleted = (
        baseline.cleanup_deleted and transformed.cleanup_deleted and transformed_input_cleanup
    )
    if not cleanup_deleted:
        failures += (MetamorphicFailure("temporary-root-retained", "runspace", True, False),)
    return MetamorphicResult(
        relation=relation,
        classification=TransformationKind.FIXTURE_METAMORPHISM,
        baseline_projection=baseline_projection,
        transformed_projection=transformed_projection,
        baseline_bytes=baseline_bytes,
        transformed_bytes=transformed_bytes,
        baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest(),
        transformed_sha256=hashlib.sha256(transformed_bytes).hexdigest(),
        failures=failures,
        package_before_sha256=package_before,
        package_after_sha256=package_after,
        package_unchanged=package_before == package_after,
        cleanup_deleted=cleanup_deleted,
    )


def _derive(
    scenario: ReuseScenario,
    *,
    parent: Path | None,
    repetition: int,
    transform: Callable[[MaterializedReuseScenario], None] | None = None,
) -> _DerivedOutput:
    prepared = prepare_reuse_scenario(scenario, repetition=repetition, parent=parent)
    with prepared as materialized:
        if transform is not None:
            transform(materialized)
        layer = derive_cross_investigation_layer(materialized.research_root)
        identity = normalize_url(scenario.positive_expectations[0].command[2])
        query = query_source_dependencies(layer, identity)
        layer_output = layer.to_dict()
        query_output = query.to_dict()
    assert prepared.evidence is not None
    return _DerivedOutput(
        layer=layer_output,
        query=query_output,
        cleanup_deleted=prepared.evidence.cleanup_deleted,
    )


def _normalize_url_spelling(materialized: MaterializedReuseScenario) -> None:
    root = materialized.focal_root
    note = next(path for path in root.joinpath("notes").glob("*.md"))
    data = note.read_text(encoding="utf-8")
    old_url = _declared_url(data)
    parsed = urlsplit(normalize_url(old_url))
    replacement = (
        f"http://www.{parsed.hostname}{parsed.path}/?utm_source=metamorphic#ignored-fragment"
    )
    if normalize_url(replacement) != normalize_url(old_url):
        raise ValueError("URL transformation exceeded resolver-guaranteed normalization")
    note.write_text(data.replace(old_url, replacement), encoding="utf-8")
    metadata_path = next(root.glob("notes/sources/*/meta.yaml"))
    metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    for key in ("url", "declared_url", "final_url"):
        metadata[key] = replacement
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")


def _remove_explicit_anchor(materialized: MaterializedReuseScenario) -> None:
    note = next(path for path in materialized.focal_root.joinpath("notes").glob("*.md"))
    body = note.read_text(encoding="utf-8")
    source_id = next(
        path.parent.name for path in materialized.focal_root.glob("notes/sources/*/meta.yaml")
    )
    marker = f" [{source_id}]"
    if body.count(marker) != 1:
        raise ValueError("explicit-provenance transformation requires one exact anchor marker")
    note.write_text(body.replace(marker, "", 1), encoding="utf-8")


@contextmanager
def _active_seed_fixture(
    scenario: ReuseScenario, *, parent: Path | None
) -> Iterator[tuple[ReuseScenario, Path]]:
    if len(scenario.seeds) != 1:
        raise ValueError("done-to-active transformation requires exactly one seed")
    seed = scenario.seeds[0]
    with tempfile.TemporaryDirectory(
        prefix="sdr-reuse-transformed-input-",
        dir=None if parent is None else str(parent),
    ) as temporary:
        root = Path(temporary).resolve()
        source_root = root / seed.id
        source_root.mkdir()
        for artifact in seed.artifacts:
            destination = source_root / artifact.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(artifact.source, destination)
        metadata_path = source_root / "sdr.yaml"
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("status") != "done":
            raise ValueError("done-to-active transformation requires a completed seed")
        metadata["status"] = "active"
        metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
        artifacts = tuple(
            ArtifactFixture(
                path=artifact.path,
                sha256=hashlib.sha256(source_root.joinpath(artifact.path).read_bytes()).hexdigest(),
                source=source_root / artifact.path,
            )
            for artifact in seed.artifacts
        )
        transformed_seed: InvestigationFixture = replace(
            seed,
            status="active",
            artifacts=artifacts,
        )
        yield replace(scenario, seeds=(transformed_seed,)), root


def _compare_relation(
    relation: MetamorphicRelation,
    scenario: ReuseScenario,
    baseline: _DerivedOutput,
    transformed: _DerivedOutput,
) -> tuple[dict[str, Any], dict[str, Any], tuple[MetamorphicFailure, ...]]:
    if relation is MetamorphicRelation.SEED_ORDER_INVARIANCE:
        before = dict(baseline.layer)
        after = dict(transformed.layer)
        failures = _equality_failure("normalized-output", before, after)
        return before, after, failures
    if relation is MetamorphicRelation.URL_NORMALIZATION_INVARIANCE:
        before = {
            "source_identity": baseline.query["source_identity"],
            "joins": baseline.layer["joins"],
        }
        after = {
            "source_identity": transformed.query["source_identity"],
            "joins": transformed.layer["joins"],
        }
        return before, after, _equality_failure("resolved-identity-and-joins", before, after)
    if relation is MetamorphicRelation.EXPLICIT_PROVENANCE_REMOVAL:
        joins = list(baseline.layer["joins"])
        candidates = [join for join in joins if join.get("kind") == "anchored_claim"]
        if len(candidates) != 1:
            raise ValueError("explicit-provenance relation requires one anchored-claim edge")
        target = candidates[0]
        unrelated = [join for join in joins if join != target]
        observed = list(transformed.layer["joins"])
        before = {"target_edge": target, "unrelated_joins": unrelated, "joins": joins}
        after = {"joins": observed}
        failures = _equality_failure("joins-without-target", unrelated, observed)
        return before, after, failures
    dependent = list(baseline.query["dependent_decisions"])
    seed_ids = {seed.id for seed in scenario.seeds}
    target = [decision for decision in dependent if decision.get("investigation") in seed_ids]
    if len(dependent) != 1 or len(target) != 1:
        raise ValueError(
            "done-to-active transformation requires exactly one baseline dependent decision "
            "belonging to the transformed seed"
        )
    expected: list[dict[str, Any]] = []
    baseline_lineage = list(baseline.query["lineage_statuses"])
    target_lineage = [
        status for status in baseline_lineage if status.get("investigation") in seed_ids
    ]
    unrelated_lineage = [
        status for status in baseline_lineage if status.get("investigation") not in seed_ids
    ]
    before_unrelated = {
        key: baseline.query[key] for key in ("source_identity", "citations", "claims")
    }
    before_unrelated["lineage_statuses"] = unrelated_lineage
    after_unrelated = {
        key: transformed.query[key] for key in ("source_identity", "citations", "claims")
    }
    after_lineage = list(transformed.query["lineage_statuses"])
    after_unrelated["lineage_statuses"] = [
        status for status in after_lineage if status.get("investigation") not in seed_ids
    ]
    before = {
        "target_decision": target[0],
        "target_lineage": target_lineage,
        "dependent_decisions": dependent,
        "unrelated": before_unrelated,
    }
    after = {
        "dependent_decisions": list(transformed.query["dependent_decisions"]),
        "target_lineage": [
            status for status in after_lineage if status.get("investigation") in seed_ids
        ],
        "unrelated": after_unrelated,
    }
    failures = _equality_failure(
        "dependent-decisions", expected, after["dependent_decisions"]
    ) + _equality_failure("target-lineage", [], after["target_lineage"])
    failures += _equality_failure("unrelated-query-results", before_unrelated, after_unrelated)
    return before, after, failures


def _equality_failure(path: str, expected: Any, observed: Any) -> tuple[MetamorphicFailure, ...]:
    if expected == observed:
        return ()
    return (MetamorphicFailure("exact-relation-mismatch", path, expected, observed),)


def _declared_url(note: str) -> str:
    document = yaml.safe_load(note.split("---", 2)[1])
    return str(document["sources"][0]["url"])


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
