"""Fixture-only metamorphic validation of deterministic cross retrieval."""

from __future__ import annotations

import socket
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from bench.harness.controls import BlockingControl
from bench.harness.metamorphic import (
    MetamorphicRelation,
    TransformationKind,
    execute_metamorphic_relation,
)
from bench.harness.mutation import (
    BlockingControlCoverage,
    MutationCoverageCode,
    MutationCoverageError,
    audit_blocking_control_coverage,
)
from bench.harness.reuse import load_reuse_corpus
from bench.harness.runspace import REPOSITORY_ROOT


def _scenarios():
    corpus = load_reuse_corpus(REPOSITORY_ROOT / "bench" / "reuse-corpus")
    return (
        corpus.by_id("software-shared-source"),
        corpus.by_id("horticulture-shared-source"),
    )


def test_seed_materialization_order_preserves_normalized_output_bytes(tmp_path: Path) -> None:
    software, garden = _scenarios()
    scenario = replace(software, seeds=(software.seeds[0], garden.seeds[0]))

    result = execute_metamorphic_relation(
        scenario,
        MetamorphicRelation.SEED_ORDER_INVARIANCE,
        parent=tmp_path,
    )

    assert result.passed, result.failures
    assert result.baseline_sha256 == result.transformed_sha256
    assert result.baseline_bytes == result.transformed_bytes
    assert result.baseline_projection == result.transformed_projection


def test_supported_url_normalization_preserves_identity_and_exact_joins(tmp_path: Path) -> None:
    scenario, _ = _scenarios()

    result = execute_metamorphic_relation(
        scenario,
        MetamorphicRelation.URL_NORMALIZATION_INVARIANCE,
        parent=tmp_path,
    )

    assert result.passed, result.failures
    assert result.baseline_sha256 == result.transformed_sha256
    assert result.baseline_projection["source_identity"] == (
        "https://docs.queue-lab.example/retry-window"
    )
    assert result.baseline_projection["joins"] == result.transformed_projection["joins"]


def test_removing_sole_explicit_anchor_provenance_removes_only_that_edge(
    tmp_path: Path,
) -> None:
    scenario, _ = _scenarios()

    result = execute_metamorphic_relation(
        scenario,
        MetamorphicRelation.EXPLICIT_PROVENANCE_REMOVAL,
        parent=tmp_path,
    )

    assert result.passed, result.failures
    removed = result.baseline_projection["target_edge"]
    assert removed["kind"] == "anchored_claim"
    assert removed not in result.transformed_projection["joins"]
    assert result.transformed_projection["joins"] == result.baseline_projection["unrelated_joins"]
    assert all(
        join.get("provenance") == "explicit" for join in result.transformed_projection["joins"]
    )


def test_done_to_active_excludes_dependent_decision_and_preserves_unrelated_results(
    tmp_path: Path,
) -> None:
    scenario, _ = _scenarios()

    result = execute_metamorphic_relation(
        scenario,
        MetamorphicRelation.DONE_TO_ACTIVE_EXCLUSION,
        parent=tmp_path,
    )

    assert result.passed, result.failures
    assert result.baseline_projection["dependent_decisions"] == [
        {
            "investigation": "software-seed",
            "claim_id": "claim-177493d06c4bd97a27eb4fdb473d649e2575a1c67c652c983ac5449cad37fb2e",
        }
    ]
    assert result.transformed_projection["dependent_decisions"] == []
    assert result.baseline_projection["unrelated"] == result.transformed_projection["unrelated"]


def test_done_to_active_rejects_history_absent_instead_of_passing_vacuously(
    tmp_path: Path,
) -> None:
    scenario, _ = _scenarios()

    with pytest.raises(ValueError, match="history-present"):
        execute_metamorphic_relation(
            replace(scenario, history="history-absent"),
            MetamorphicRelation.DONE_TO_ACTIVE_EXCLUSION,
            parent=tmp_path,
        )


def test_done_to_active_requires_one_baseline_seed_dependent_decision(
    tmp_path: Path,
) -> None:
    scenario, _ = _scenarios()
    expectation = scenario.positive_expectations[0]
    unrelated = replace(
        expectation,
        command=("cross", "source", "https://docs.queue-lab.example/unrelated", "--json"),
    )

    with pytest.raises(ValueError, match="exactly one baseline dependent decision"):
        execute_metamorphic_relation(
            replace(scenario, positive_expectations=(unrelated,)),
            MetamorphicRelation.DONE_TO_ACTIVE_EXCLUSION,
            parent=tmp_path,
        )


def test_metamorphisms_are_fixture_only_not_package_mutations_or_control_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario, _ = _scenarios()

    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("offline metamorphic validation crossed a host boundary")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-read")
    results = tuple(
        execute_metamorphic_relation(scenario, relation, parent=tmp_path)
        for relation in MetamorphicRelation
        if relation is not MetamorphicRelation.SEED_ORDER_INVARIANCE
    )

    assert all(
        result.classification is TransformationKind.FIXTURE_METAMORPHISM for result in results
    )
    assert all(result.package_before_sha256 == result.package_after_sha256 for result in results)
    assert all(result.package_unchanged and result.cleanup_deleted for result in results)
    entries = tuple(
        BlockingControlCoverage(control=control, mutations=(results[0],))  # type: ignore[arg-type]
        for control in BlockingControl
    )
    with pytest.raises(MutationCoverageError) as raised:
        audit_blocking_control_coverage(entries)
    assert raised.value.code is MutationCoverageCode.MUTATION_CONTROL_MISMATCH
    assert "fixture-metamorphism" in str(raised.value)
