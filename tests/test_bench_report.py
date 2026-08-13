"""Deterministic question-specific reporting from schema-v2 records only."""

from __future__ import annotations

import copy
import json

import pytest

from bench.harness.record import RunRecord, RunRecordSet
from bench.harness.report import render_report
from tests.bench_record_v2_fixtures import complete_record

QUESTIONS = (
    "lifecycle-control-observability",
    "live-single-investigation",
    "cross-retrieval",
)
CROSS_OUTCOMES = ("not-consulted", "not-exercised", "incorrect", "correct")


def _record(raw: dict[str, object]) -> RunRecord:
    return RunRecord.from_json(json.dumps(raw))


def _records(*raw: dict[str, object]) -> RunRecordSet:
    return RunRecordSet(schema_version=2, records=tuple(_record(value) for value in raw))


def test_report_has_three_fixed_separate_question_sections() -> None:
    records = _records(*(complete_record(question) for question in QUESTIONS))

    rendered = render_report(records)

    headings = [line for line in rendered.splitlines() if line.startswith("## Question:")]
    assert headings == [f"## Question: {question}" for question in QUESTIONS]
    assert "combined effectiveness" not in rendered.casefold()


def test_cross_report_renders_all_four_outcomes_without_semantic_judgement() -> None:
    values = []
    for repetition, outcome in enumerate(CROSS_OUTCOMES):
        raw = complete_record("cross-retrieval")
        raw["run_id"] = f"cross-{outcome}"
        raw["repetition"] = repetition
        raw["cross"]["consultation"] = outcome
        raw["cross"]["checks"][0]["outcome"] = outcome
        if outcome == "not-consulted":
            raw["cross"]["command_identity"] = None
            raw["cross"]["query_identity"] = None
        values.append(raw)

    rendered = render_report(_records(*values))

    for outcome in CROSS_OUTCOMES:
        assert outcome in rendered
    assert "model judgement" not in rendered.casefold()


@pytest.mark.parametrize(
    ("path", "left", "right"),
    [
        (("actor",), "scripted", "live"),
        (("prompt", "policy"), "assisted", "unassisted"),
        (("corpus", "history_condition"), "history-present", "history-absent"),
        (("prompt", "template_version"), "1", "2"),
        (("corpus", "version"), "corpus-a", "corpus-b"),
        (("scenario_id",), "scenario-a", "scenario-b"),
        (("cross", "resolver_chain_identity"), "resolver-a", "resolver-b"),
        (("live", "host"), "host-a", "host-b"),
        (("live", "model"), "provider/model-a", "provider/model-b"),
        (("corpus", "scenario_manifest_sha256"), "a" * 64, "c" * 64),
    ],
)
def test_report_never_aggregates_incompatible_grouping_dimensions(
    path: tuple[str, ...], left: str, right: str
) -> None:
    question = "live-single-investigation" if path[0] == "live" else "cross-retrieval"
    first = complete_record(question)
    second = copy.deepcopy(first)
    first["run_id"] = f"group-left-{'-'.join(path)}"
    second["run_id"] = f"group-right-{'-'.join(path)}"
    second["repetition"] = 1
    for raw, value in ((first, left), (second, right)):
        selected = raw
        for segment in path[:-1]:
            selected = selected[segment]
        selected[path[-1]] = value
    if path == ("actor",):
        # Cross records permit both actors, but live actors require complete live attribution.
        second["live"] = copy.deepcopy(complete_record("live-single-investigation")["live"])
        second["execution"]["live_boundary"] = copy.deepcopy(
            complete_record("live-single-investigation")["execution"]["live_boundary"]
        )
        second["execution"]["bounds"] = copy.deepcopy(
            complete_record("live-single-investigation")["execution"]["bounds"]
        )
        second["mediation"] = copy.deepcopy(
            complete_record("live-single-investigation")["mediation"]
        )
        second["approval"] = copy.deepcopy(complete_record("live-single-investigation")["approval"])
        second["terminal_state"] = "awaiting-operator-approval"

    rendered = render_report(_records(first, second))

    assert left in rendered
    assert right in rendered
    assert rendered.count("group:") == 2


def test_report_has_stable_record_order_and_explicit_non_claims_block() -> None:
    raw = [complete_record(question) for question in reversed(QUESTIONS)]
    records = _records(*raw)

    first = render_report(records)
    second = render_report(RunRecordSet.from_json(records.to_json()))

    assert first.encode() == second.encode()
    assert (
        first.index("run-lifecycle-control-observability")
        < first.index("run-live-single-investigation")
        < first.index("run-cross-retrieval")
    )
    assert "## Explicit Non-Claims" in first
    for prohibited in (
        "semantic applicability",
        "recommendation quality",
        "criterion-level reuse",
        "statistical significance",
        "causal effect",
        "value of the cross CLI",
    ):
        assert prohibited in first


def test_report_rejects_any_non_v2_record_set_before_rendering() -> None:
    raw = {"schema_version": 1, "records": []}
    with pytest.raises(ValueError, match="unsupported run record set schema version 1"):
        render_report(RunRecordSet.from_json(json.dumps(raw)))
