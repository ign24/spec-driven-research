"""Durable, traceable machine-readable benchmark run records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.harness.actor import ActorKind, CommandResult, ScriptedActor
from bench.harness.arms import ArmOutcome, execute_arms, runs_for_arm
from bench.harness.corpus import CorpusItem
from bench.harness.record import (
    EvidenceKind,
    MetricState,
    RunRecord,
    RunRecordSet,
    build_run_record,
    build_run_record_set,
)

ITEM_ID = "bench-record-item"


def _item(**overrides: Any) -> CorpusItem:
    data: dict[str, Any] = {
        "id": ITEM_ID,
        "mode": "light",
        "title": "Bench record item",
        "question": "What makes a run record traceable?",
        "planted_defects": (),
        "expected_detection": {},
        "sources": (),
        "artifacts": {},
        "commands": (),
        "probe": None,
        "path": Path("bench/corpus/items") / f"{ITEM_ID}.yaml",
    }
    data.update(overrides)
    return CorpusItem(**data)


class _SkippedCheckActor(ScriptedActor):
    def execute(self, request):  # type: ignore[no-untyped-def]
        result = super().execute(request)
        skipped = CommandResult(
            argv=("check", ITEM_ID, "--json", "--offline"),
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "passed": True,
                "results": [
                    {
                        "check": "links_resolve",
                        "passed": True,
                        "skipped": True,
                        "detail": "offline",
                    }
                ],
            },
        )
        return type(result)(**{**vars(result), "commands": (*result.commands, skipped)})


def _assert_metric_is_traceable(metric) -> None:  # type: ignore[no-untyped-def]
    assert metric.evidence
    for evidence in metric.evidence:
        assert evidence.kind in {
            EvidenceKind.ARTIFACT,
            EvidenceKind.COMMAND_EXIT,
            EvidenceKind.STRUCTURED_CLI_FIELD,
        }


def test_every_run_record_metric_has_a_declared_evidence_source() -> None:
    item = _item()
    run = runs_for_arm(
        execute_arms([item], actor=_SkippedCheckActor(), arms=("light",), max_workers=1),
        "light",
    )[0]

    record = build_run_record(item, run)

    assert record.actor is ActorKind.SCRIPTED
    assert record.outcome is ArmOutcome.EXECUTED
    for metric in record.metrics:
        _assert_metric_is_traceable(metric)
    assert record.skipped_checks.state is MetricState.OBSERVED
    assert record.skipped_checks.value == [
        {"check": "links_resolve", "detail": "offline", "command_index": 0}
    ]
    assert record.skipped_checks.evidence[0].structured_field == "results[0].skipped"


def test_detection_record_retains_not_exercised_control_and_skip_reason() -> None:
    item = _item(
        planted_defects=("unreachable-source",),
        expected_detection={"unreachable-source": "caught"},
        commands=(
            (
                "new",
                ITEM_ID,
                "--title",
                "Bench record item",
                "--question",
                "What makes a run record traceable?",
                "--mode",
                "light",
                "--owner",
                "bench",
                "--timebox",
                "1",
            ),
        ),
    )
    run = runs_for_arm(
        execute_arms([item], actor=_SkippedCheckActor(), arms=("light",), max_workers=1),
        "light",
    )[0]

    record = build_run_record(item, run)
    defect = record.detection.value["defects"][0]  # type: ignore[index]

    assert defect == {
        "defect": "unreachable-source",
        "outcome": "not-exercised",
        "reporting_control": "check:links_resolve",
        "reason": "offline",
        "finding": None,
    }
    assert RunRecord.from_json(record.to_json()).detection == record.detection


def test_every_evidence_artifact_reference_resolves_inside_the_record() -> None:
    item = _item()
    runs = execute_arms(
        [item], actor=ScriptedActor(), arms=("baseline", "light", "full"), max_workers=1
    )

    for run in runs:
        record = build_run_record(item, run)
        paths = {artifact.path for artifact in record.artifacts}
        assert paths
        assert all(
            evidence.artifact_path in paths
            for metric in record.metrics
            for evidence in metric.evidence
            if evidence.artifact_path is not None
        )


class _ProseFailureActor(ScriptedActor):
    def execute(self, request):  # type: ignore[no-untyped-def]
        result = super().execute(request)
        blocked = CommandResult(
            argv=("advance", ITEM_ID, "--offline"),
            exit_code=1,
            stdout=(
                "avance bloqueado: un motivo exacto solo disponible como prosa\n"
                "[FALLA] contenido no controlado que no debe persistirse\n"
            ),
            stderr="diagnostico no controlado que no debe persistirse\n",
        )
        approve = CommandResult(
            argv=("approve", ITEM_ID, "--by", "Bench Reviewer"),
            exit_code=2,
            stdout="salida no controlada que no debe persistirse\n",
            stderr="Error: razon exacta de approve\n",
        )
        return type(result)(**{**vars(result), "commands": (*result.commands, blocked, approve)})


def test_prose_gate_evidence_persists_only_the_exact_extracted_reason_and_exit() -> None:
    item = _item()
    run = runs_for_arm(
        execute_arms([item], actor=_ProseFailureActor(), arms=("light",), max_workers=1),
        "light",
    )[0]

    record = build_run_record(item, run)
    prose = next(
        artifact for artifact in record.artifacts if artifact.path == "evidence/prose.json"
    )
    assert "un motivo exacto solo disponible como prosa" in prose.content
    assert "razon exacta de approve" in prose.content
    assert "contenido no controlado" not in prose.content
    assert "diagnostico no controlado" not in prose.content
    assert "salida no controlada" not in prose.content
    assert {ref.kind for ref in record.gate_failures.evidence} >= {
        EvidenceKind.ARTIFACT,
        EvidenceKind.COMMAND_EXIT,
    }
    assert any(ref.artifact_path == prose.path for ref in record.gate_failures.evidence)
    assert any(
        ref.command_index == 0 and ref.exit_code == 1 for ref in record.gate_failures.evidence
    )
    assert any(
        ref.command_index == 1 and ref.exit_code == 2 for ref in record.gate_failures.evidence
    )


def test_run_record_serialization_is_canonical_and_round_trippable() -> None:
    item = _item(commands=(("status", ITEM_ID, "--json"),))
    run = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("light",), max_workers=1),
        "light",
    )[0]
    record = build_run_record(item, run)

    encoded = record.to_json()

    assert encoded == record.to_json()
    assert encoded.endswith("\n")
    assert " /tmp/" not in encoded
    assert RunRecord.from_json(encoded) == record
    assert RunRecord.from_json(encoded).to_json() == encoded

    actor_evidence = next(
        artifact for artifact in record.artifacts if artifact.path == "evidence/actor.json"
    )
    commands = json.loads(actor_evidence.content)["commands"]
    assert commands
    assert all(command["execution_provenance"]["executable"]["sha256"] for command in commands)
    assert all(command["execution_provenance"]["package"]["sha256"] for command in commands)
    assert str(Path.home()) not in encoded
    assert str(Path.cwd()) not in encoded


def test_record_remains_self_contained_after_runspace_and_corpus_item_are_deleted(
    tmp_path: Path,
) -> None:
    corpus_path = tmp_path / f"{ITEM_ID}.yaml"
    corpus_path.write_text("temporary corpus source\n", encoding="utf-8")
    item = _item(
        path=corpus_path,
        planted_defects=("unreachable-source",),
        expected_detection={"unreachable-source": "caught"},
    )
    full = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("full",), max_workers=1), "full"
    )[0]
    corpus_path.unlink()

    record = build_run_record(item, full)

    assert full.outcome is ArmOutcome.NOT_APPLICABLE
    assert not corpus_path.exists()
    corpus_evidence = next(
        artifact for artifact in record.artifacts if artifact.path == "evidence/corpus-item.json"
    )
    assert json.loads(corpus_evidence.content) == {
        "expected_detection": {"unreachable-source": "caught"},
        "id": ITEM_ID,
        "mode": "light",
        "planted_defects": ["unreachable-source"],
        "question": "What makes a run record traceable?",
        "title": "Bench record item",
    }
    assert all(
        ref.artifact_path == corpus_evidence.path
        for metric in record.metrics
        for ref in metric.evidence
    )


def _record_data() -> dict[str, Any]:
    item = _item()
    run = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("light",), max_workers=1), "light"
    )[0]
    return json.loads(build_run_record(item, run).to_json())


class _ErroredActor(ScriptedActor):
    def execute(self, request):  # type: ignore[no-untyped-def]
        raise RuntimeError("intentional record failure")


def test_errored_record_embeds_corpus_and_error_evidence_after_teardown(tmp_path: Path) -> None:
    corpus_path = tmp_path / f"{ITEM_ID}.yaml"
    corpus_path.write_text("temporary corpus source\n", encoding="utf-8")
    item = _item(path=corpus_path)
    run = runs_for_arm(
        execute_arms([item], actor=_ErroredActor(), arms=("light",), max_workers=1), "light"
    )[0]
    corpus_path.unlink()

    record = build_run_record(item, run)

    assert record.outcome is ArmOutcome.ERRORED
    assert not run.research_root.exists()  # type: ignore[union-attr]
    assert {artifact.path for artifact in record.artifacts} == {
        "evidence/corpus-item.json",
        "evidence/error.json",
    }
    assert all(
        ref.artifact_path in {artifact.path for artifact in record.artifacts}
        for metric in record.metrics
        for ref in metric.evidence
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda raw: raw.__setitem__("schema_version", 999), "schema_version"),
        (lambda raw: raw.__setitem__("actor", "unknown"), "actor"),
        (lambda raw: raw.__setitem__("arm", "unknown"), "arm"),
        (lambda raw: raw.__setitem__("outcome", "unknown"), "outcome"),
        (lambda raw: raw.__setitem__("repetition", -1), "repetition"),
        (lambda raw: raw["detection"].__setitem__("name", "tokens"), "metric name"),
        (
            lambda raw: (
                raw["detection"].__setitem__("state", "not-run"),
                raw["detection"].__setitem__("value", None),
                raw["detection"].__setitem__("reason", "contradictory absence"),
            ),
            "executed",
        ),
        (lambda raw: raw["artifacts"][0].__setitem__("path", "../escape"), "artifact path"),
        (lambda raw: raw["artifacts"].append(dict(raw["artifacts"][0])), "duplicate artifact"),
        (
            lambda raw: raw["detection"]["evidence"][0].__setitem__(
                "artifact_path", "evidence/missing.json"
            ),
            "embedded artifact",
        ),
        (
            lambda raw: raw["gate_failures"]["evidence"][0].__setitem__("command_index", -1),
            "command_index",
        ),
        (lambda raw: raw["total_wall_clock"].__setitem__("value", -0.1), "duration"),
        (lambda raw: raw.__setitem__("error", "contradicts executed"), "error"),
        (lambda raw: raw.__setitem__("unexpected", True), "unexpected"),
    ],
)
def test_run_record_deserialization_rejects_malformed_or_contradictory_records(
    mutate,
    message: str,  # type: ignore[no-untyped-def]
) -> None:
    raw = _record_data()
    mutate(raw)

    with pytest.raises(ValueError, match=message):
        RunRecord.from_json(json.dumps(raw))


def test_run_record_deserialization_rejects_non_finite_durations() -> None:
    raw = _record_data()
    raw["total_wall_clock"]["value"] = float("nan")

    with pytest.raises(ValueError, match="non-finite"):
        RunRecord.from_json(json.dumps(raw))


def test_run_record_set_carries_corpus_version_and_repetitions_canonically() -> None:
    item = _item()
    runs = execute_arms([item], actor=ScriptedActor(), repetitions=2, max_workers=1)

    envelope = build_run_record_set(
        corpus_version="2026-07-review", repetitions=2, items=(item,), runs=runs
    )
    encoded = envelope.to_json()

    assert envelope.corpus_version == "2026-07-review"
    assert envelope.repetitions == 2
    assert RunRecordSet.from_json(encoded) == envelope
    assert RunRecordSet.from_json(encoded).to_json() == encoded


def test_run_record_set_rejects_duplicate_record_identities() -> None:
    item = _item()
    run = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("light",), max_workers=1), "light"
    )[0]
    record = build_run_record(item, run)

    with pytest.raises(ValueError, match="duplicate run record identity"):
        RunRecordSet(
            schema_version=1,
            corpus_version="2026-07-review",
            repetitions=1,
            records=(record, record),
        )


def test_not_run_metrics_are_distinct_from_observed_zero_or_clean() -> None:
    item = _item()
    full = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("full",), max_workers=1),
        "full",
    )[0]

    record = build_run_record(item, full)

    assert record.actor is ActorKind.SCRIPTED
    assert record.outcome is ArmOutcome.NOT_APPLICABLE
    assert all(metric.state is MetricState.NOT_RUN for metric in record.metrics)
    assert all(metric.value is None for metric in record.metrics)
    assert all(metric.reason for metric in record.metrics)


def test_executed_baseline_marks_lifecycle_metrics_not_run() -> None:
    item = _item()
    baseline = runs_for_arm(
        execute_arms([item], actor=ScriptedActor(), arms=("baseline",), max_workers=1),
        "baseline",
    )[0]

    record = build_run_record(item, baseline)

    assert record.total_wall_clock.state is MetricState.OBSERVED
    assert record.detection.state is MetricState.OBSERVED
    assert record.stage_costs.state is MetricState.NOT_RUN
    assert record.reopens.state is MetricState.NOT_RUN
    assert record.gate_failures.state is MetricState.NOT_RUN
    assert record.claims.state is MetricState.NOT_RUN
    assert record.skipped_checks.state is MetricState.NOT_RUN
