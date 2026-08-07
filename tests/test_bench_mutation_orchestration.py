"""Production offline orchestration of every feasible blocking-control mutation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import bench.harness.mutation as mutation
from bench.harness.corpus import load_corpus


def test_registry_plan_requires_current_corpus_provenance() -> None:
    plan = getattr(mutation, "plan_registry_mutations", None)
    assert plan is not None, "registry mutation planning is not implemented"
    corpus = load_corpus()

    planned = plan(corpus)

    assert planned.corpus_version == corpus.version
    assert planned.items == corpus.items
    assert planned.declarations == tuple(
        declaration
        for entry in mutation.BLOCKING_CONTROL_COVERAGE
        for declaration in entry.mutations
    )
    stale = replace(
        corpus,
        baseline_provenance=replace(corpus.baseline_provenance, snapshot_schema_version=1),
    )
    with pytest.raises(mutation.MutationError, match="current provenance"):
        plan(stale)


def test_every_feasible_registry_mutation_executes_and_validates_offline(
    tmp_path: Path,
) -> None:
    execute = getattr(mutation, "execute_registry_mutations", None)
    assert execute is not None, "registry mutation orchestration is not implemented"

    corpus = load_corpus()
    results = execute(corpus, parent=tmp_path, max_workers=1)
    declared = tuple(
        declaration.name
        for entry in mutation.BLOCKING_CONTROL_COVERAGE
        for declaration in entry.mutations
    )

    assert tuple(result.declaration.name for result in results) == declared
    assert all(result.validation.lost_catches for result in results)
    assert all(result.baseline_scores for result in results)
    assert all(result.mutated_scores for result in results)
    assert all(
        evidence.unchanged
        and evidence.before_sha256 == evidence.after_sha256
        and not evidence.disposable_root.exists()
        for result in results
        for evidence in result.checkout_integrity
    )
