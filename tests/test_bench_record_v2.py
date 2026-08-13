"""Complete strict schema-v2 records for all three evaluation questions."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from bench.harness.record import RunRecord, RunRecordSet
from tests.bench_record_v2_fixtures import complete_record

QUESTIONS = (
    "lifecycle-control-observability",
    "live-single-investigation",
    "cross-retrieval",
)


def _objects(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _objects(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _objects(child, (*path, index))


@pytest.mark.parametrize("question", QUESTIONS)
def test_complete_decision_11_record_round_trips_canonically(question: str) -> None:
    raw = complete_record(question)

    record = RunRecord.from_json(json.dumps(raw))

    assert record.schema_version == 2
    assert record.evaluation_question.value == question
    assert record.to_json() == RunRecord.from_json(record.to_json()).to_json()
    assert record.to_json().endswith("\n")


@pytest.mark.parametrize("question", QUESTIONS)
def test_every_decision_11_object_rejects_unknown_keys(question: str) -> None:
    raw = complete_record(question)
    for path, _target in list(_objects(raw)):
        if any(
            segment
            in {
                "observed_projection",
                "expected_projection",
                "expected",
                "observed",
                "seed_artifact_sha256",
                "pre_sha256",
                "post_sha256",
            }
            for segment in path
        ):
            continue
        mutated = copy.deepcopy(raw)
        selected: Any = mutated
        for segment in path:
            selected = selected[segment]
        selected["unexpected"] = True
        with pytest.raises(ValueError, match="unexpected keys"):
            RunRecord.from_json(json.dumps(mutated))


def test_version_one_is_explicitly_unsupported_without_coercion() -> None:
    raw = complete_record("lifecycle-control-observability")
    raw["schema_version"] = 1

    with pytest.raises(ValueError, match="unsupported run record schema version 1"):
        RunRecord.from_json(json.dumps(raw))


def test_duplicate_json_keys_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key 'schema_version'"):
        RunRecord.from_json('{"schema_version":2,"schema_version":2}')


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.__setitem__("live", None), "live attribution"),
        (lambda raw: raw["live"].__setitem__("transcript_persisted", True), "transcript"),
        (lambda raw: raw["approval"].__setitem__("state", "not-reached"), "approval"),
        (lambda raw: raw["approval"].__setitem__("synthetic_reference", "fixture-a"), "synthetic"),
        (lambda raw: raw["prompt"].__setitem__("policy", "assisted"), "prompt treatment"),
        (lambda raw: raw["execution"].__setitem__("live_boundary", None), "live boundary"),
    ],
)
def test_live_record_rejects_missing_or_incompatible_attribution(mutate, message: str) -> None:  # type: ignore[no-untyped-def]
    raw = complete_record("live-single-investigation")
    mutate(raw)
    with pytest.raises(ValueError, match=message):
        RunRecord.from_json(json.dumps(raw))


@pytest.mark.parametrize("forbidden", ["transcript", "raw_export", "model_judgement"])
def test_forbidden_persisted_payload_names_are_rejected_at_any_depth(forbidden: str) -> None:
    raw = complete_record("cross-retrieval")
    raw["cross"][forbidden] = "forbidden"
    with pytest.raises(ValueError, match="forbidden durable field"):
        RunRecord.from_json(json.dumps(raw))


def test_cross_treatment_and_provenance_are_conditionally_strict() -> None:
    raw = complete_record("cross-retrieval")
    raw["prompt"]["policy"] = "standard"
    with pytest.raises(ValueError, match="cross-retrieval prompt treatment"):
        RunRecord.from_json(json.dumps(raw))

    raw = complete_record("cross-retrieval")
    raw["corpus"]["history_condition"] = "not-applicable"
    with pytest.raises(ValueError, match="history condition"):
        RunRecord.from_json(json.dumps(raw))


def test_record_set_rejects_duplicate_stable_identities_and_orders_records() -> None:
    records = tuple(
        RunRecord.from_json(json.dumps(complete_record(question)))
        for question in reversed(QUESTIONS)
    )
    record_set = RunRecordSet(schema_version=2, records=records)
    assert tuple(record.evaluation_question.value for record in record_set.records) == QUESTIONS

    with pytest.raises(ValueError, match="duplicate run_id"):
        RunRecordSet(schema_version=2, records=(records[0], records[0]))
