"""Arm execution of the benchmark harness: applicability, repetitions, control constants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bench.harness.actor import (
    ARMS,
    BASELINE_ARM,
    FULL_ARM,
    LIGHT_ARM,
    SCRIPTED_TOKENS,
    ActorKind,
    ActorResult,
    DetectionBasis,
    LiveActor,
    LiveActorNotConfiguredError,
    RunRequest,
    ScriptedActor,
)
from bench.harness.arms import (
    DEFAULT_REPETITIONS,
    ArmExecutionError,
    ArmOutcome,
    ArmRun,
    arm_applicability,
    execute_arms,
    measured_runs,
    plan_runs,
    runs_for_arm,
)
from bench.harness.corpus import CorpusItem
from bench.harness.friction import ReopenTrail, reopen_trail_available

ITEM_ID = "bench-arms-item"

NOTE_BODY = """# Nota de fuente

## Fuente
- id: s1
- url: https://research.example/report
- titulo: Informe sintetico
- tier: A
- fecha: 2026-01-01

## Extracto
El informe sintetico declara una cifra inventada. [S1]
"""


def _item(item_id: str = ITEM_ID, mode: str = "light", **overrides: Any) -> CorpusItem:
    """Build a corpus item in-test, without depending on files under bench/corpus."""
    data: dict[str, Any] = {
        "id": item_id,
        "mode": mode,
        "title": "Bench arms item",
        "question": "¿Que ejecutan los brazos?",
        "planted_defects": (),
        "expected_detection": {},
        "sources": (),
        "artifacts": {},
        "commands": (),
        "probe": None,
        "path": Path("bench/corpus/items") / f"{item_id}.yaml",
    }
    data.update(overrides)
    return CorpusItem(**data)


def _scripted_item(item_id: str = ITEM_ID, mode: str = "light") -> CorpusItem:
    return _item(
        item_id,
        mode,
        artifacts={f"{item_id}/notes/s1.md": NOTE_BODY},
        commands=(
            (
                "new",
                item_id,
                "--title",
                "Bench arms item",
                "--question",
                "¿Que ejecutan los brazos?",
                "--mode",
                mode,
                "--owner",
                "bench",
                "--timebox",
                "1",
            ),
            ("check", item_id, "--json"),
        ),
    )


class _RecordingActor:
    """Actor double: records every request and reports the arm's declared basis."""

    def __init__(self, kind: ActorKind = ActorKind.SCRIPTED) -> None:
        self._kind = kind
        self.requests: list[RunRequest] = []

    @property
    def kind(self) -> ActorKind:
        return self._kind

    def execute(self, request: RunRequest) -> ActorResult:
        self.requests.append(request)
        control = request.arm == BASELINE_ARM and self._kind is ActorKind.SCRIPTED
        return ActorResult(
            actor=self._kind,
            item_id=request.item.id,
            arm=request.arm,
            repetition=request.repetition,
            slug=request.item.id,
            artifacts_written=(),
            commands=(),
            stages=(),
            tokens=SCRIPTED_TOKENS,
            detection_basis=(
                DetectionBasis.CONTROL_CONSTANT if control else DetectionBasis.MEASURED
            ),
            detection_basis_reason="double",
            duration_seconds=0.0,
            workspace=request.space.path,
            research_root=request.space.root,
        )


class _MislabellingActor(_RecordingActor):
    """Actor double that wrongly reports the scripted baseline as a measured rate."""

    def execute(self, request: RunRequest) -> ActorResult:
        result = super().execute(request)
        return ActorResult(**{**vars(result), "detection_basis": DetectionBasis.MEASURED})


class _WrongKindActor(_RecordingActor):
    def execute(self, request: RunRequest) -> ActorResult:
        result = super().execute(request)
        return ActorResult(**{**vars(result), "actor": ActorKind.LIVE})


class _FailingActor(_RecordingActor):
    """Actor double that raises the way a crashing replay would."""

    def execute(self, request: RunRequest) -> ActorResult:
        super().execute(request)
        raise RuntimeError("sdr.network_policy.NetworkPolicyError: unresolvable source")


def test_applicability_is_mode_scoped_except_for_the_baseline() -> None:
    light = _item(mode="light")
    full = _item(mode="full")

    assert arm_applicability(light, BASELINE_ARM).applicable is True
    assert arm_applicability(full, BASELINE_ARM).applicable is True
    assert arm_applicability(light, LIGHT_ARM).applicable is True
    assert arm_applicability(full, FULL_ARM).applicable is True

    not_applicable = arm_applicability(light, FULL_ARM)
    assert not_applicable.applicable is False
    assert "light" in not_applicable.reason
    assert arm_applicability(full, LIGHT_ARM).applicable is False


def test_unknown_arm_is_rejected() -> None:
    with pytest.raises(ArmExecutionError, match="unknown arm"):
        arm_applicability(_item(), "no-such-arm")


def test_n_repetitions_produce_n_run_records_per_applicable_arm() -> None:
    item = _item(mode="light")
    actor = _RecordingActor()

    runs = execute_arms([item], actor=actor, repetitions=3, max_workers=1)

    assert len(runs_for_arm(runs, BASELINE_ARM)) == 3
    assert len(runs_for_arm(runs, LIGHT_ARM)) == 3
    assert [run.repetition for run in runs_for_arm(runs, BASELINE_ARM)] == [0, 1, 2]
    assert [run.repetition for run in runs_for_arm(runs, LIGHT_ARM)] == [0, 1, 2]
    assert all(run.outcome is ArmOutcome.EXECUTED for run in runs if run.arm != FULL_ARM)
    assert len(actor.requests) == 6


def test_a_light_item_is_not_applicable_in_the_full_arm_rather_than_failed() -> None:
    item = _item(mode="light")
    actor = _RecordingActor()

    runs = execute_arms([item], actor=actor, repetitions=2, max_workers=1)
    full_runs = runs_for_arm(runs, FULL_ARM)

    assert len(full_runs) == 1
    not_applicable = full_runs[0]
    assert not_applicable.outcome is ArmOutcome.NOT_APPLICABLE
    assert not_applicable.actor is ActorKind.SCRIPTED
    assert not_applicable.outcome is not ArmOutcome.ERRORED
    assert not_applicable.error is None
    assert not_applicable.result is None
    assert not_applicable.detection_basis is None
    assert not_applicable.not_applicable_reason
    assert not_applicable.is_applicable is False
    assert not_applicable.is_failure is False
    # The arm is visible in the results, never silently dropped.
    assert {run.arm for run in runs} == set(ARMS)
    assert all(request.arm != FULL_ARM for request in actor.requests)


def test_repetitions_do_not_multiply_a_non_applicable_arm() -> None:
    runs = execute_arms([_item(mode="full")], actor=_RecordingActor(), repetitions=4, max_workers=1)

    assert len(runs_for_arm(runs, LIGHT_ARM)) == 1
    assert runs_for_arm(runs, LIGHT_ARM)[0].repetition == 0
    assert len(runs_for_arm(runs, FULL_ARM)) == 4


def test_every_run_receives_its_own_research_root() -> None:
    items = [_item("bench-arms-a"), _item("bench-arms-b")]
    actor = _RecordingActor()

    runs = execute_arms(items, actor=actor, repetitions=2, max_workers=2)
    roots = [run.research_root for run in runs if run.research_root is not None]

    assert len(roots) == 8
    assert len(set(roots)) == 8
    assert all(
        request.space.root != other.space.root
        for request in actor.requests
        for other in actor.requests
        if request is not other
    )


def test_scripted_baseline_is_a_control_constant_never_a_measured_rate() -> None:
    runs = execute_arms([_item(mode="light")], actor=_RecordingActor(), max_workers=1)
    baseline = runs_for_arm(runs, BASELINE_ARM)[0]
    light = runs_for_arm(runs, LIGHT_ARM)[0]

    assert baseline.detection_basis is DetectionBasis.CONTROL_CONSTANT
    assert baseline.is_control_constant is True
    assert baseline.counts_toward_detection_rate is False
    assert light.counts_toward_detection_rate is True
    assert baseline not in measured_runs(runs)
    assert light in measured_runs(runs)


def test_a_scripted_baseline_reported_as_measured_is_refused() -> None:
    with pytest.raises(ArmExecutionError, match="control constant"):
        execute_arms([_item(mode="light")], actor=_MislabellingActor(), max_workers=1)


def test_an_actor_result_cannot_change_the_executors_actor_kind() -> None:
    with pytest.raises(ArmExecutionError, match="actor kind"):
        execute_arms([_item(mode="light")], actor=_WrongKindActor(), max_workers=1)


def test_an_actor_crash_is_recorded_as_errored_not_as_a_clean_run() -> None:
    runs = execute_arms([_item(mode="light")], actor=_FailingActor(), max_workers=1)

    assert all(run.outcome is ArmOutcome.ERRORED for run in runs if run.is_applicable)
    errored = runs_for_arm(runs, LIGHT_ARM)[0]
    assert errored.error is not None
    assert errored.actor is ActorKind.SCRIPTED
    assert "NetworkPolicyError" in errored.error
    assert errored.result is None
    assert errored.detection_basis is None
    assert errored.is_failure is True
    assert errored.counts_toward_detection_rate is False
    assert measured_runs(runs) == ()


def test_error_collection_failure_still_returns_an_errored_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collection_failure(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise PermissionError("evidence denied")

    monkeypatch.setattr("bench.harness.arms._terminal_state", collection_failure)

    run = runs_for_arm(
        execute_arms([_item()], actor=_FailingActor(), arms=(LIGHT_ARM,), max_workers=1),
        LIGHT_ARM,
    )[0]

    assert run.outcome is ArmOutcome.ERRORED
    assert run.error is not None and "NetworkPolicyError" in run.error
    assert run.evidence_artifacts
    fallback = next(
        artifact for artifact in run.evidence_artifacts if artifact.path == "evidence/error.json"
    )
    assert '"state":"unavailable"' in fallback.content
    assert "evidence denied" in fallback.content


def test_an_unconfigured_live_actor_fails_loudly_instead_of_being_recorded() -> None:
    with pytest.raises(LiveActorNotConfiguredError):
        execute_arms([_item(mode="light")], actor=LiveActor(), max_workers=1)


def test_plan_and_results_order_by_item_then_arm_then_repetition() -> None:
    items = [_item("bench-arms-b", mode="full"), _item("bench-arms-a", mode="light")]

    planned = plan_runs(items, repetitions=2)
    runs = execute_arms(items, actor=_RecordingActor(), repetitions=2, max_workers=1)

    assert [(run.item_id, run.arm, run.repetition) for run in runs] == [
        (plan.item.id, plan.arm, plan.repetition) for plan in planned
    ]
    assert [(run.item_id, run.arm, run.repetition) for run in runs] == [
        ("bench-arms-a", BASELINE_ARM, 0),
        ("bench-arms-a", BASELINE_ARM, 1),
        ("bench-arms-a", LIGHT_ARM, 0),
        ("bench-arms-a", LIGHT_ARM, 1),
        ("bench-arms-a", FULL_ARM, 0),
        ("bench-arms-b", BASELINE_ARM, 0),
        ("bench-arms-b", BASELINE_ARM, 1),
        ("bench-arms-b", LIGHT_ARM, 0),
        ("bench-arms-b", FULL_ARM, 0),
        ("bench-arms-b", FULL_ARM, 1),
    ]


def test_repetition_count_must_be_positive() -> None:
    with pytest.raises(ValueError, match="repetitions"):
        execute_arms([_item()], actor=_RecordingActor(), repetitions=0)
    assert DEFAULT_REPETITIONS >= 1


def test_scripted_arms_run_end_to_end_offline_and_record_the_terminal_state() -> None:
    item = _scripted_item()

    runs = execute_arms([item], actor=ScriptedActor(), repetitions=1, max_workers=1)

    baseline = runs_for_arm(runs, BASELINE_ARM)[0]
    light = runs_for_arm(runs, LIGHT_ARM)[0]

    assert baseline.outcome is ArmOutcome.EXECUTED
    assert baseline.is_control_constant is True
    assert baseline.terminal_state.recorded is False
    assert baseline.terminal_state.stage is None

    assert light.outcome is ArmOutcome.EXECUTED, light.error
    assert light.detection_basis is DetectionBasis.MEASURED
    assert light.terminal_state.recorded is True
    assert light.terminal_state.stage == "intake"
    assert light.terminal_state.status == "active"
    assert light.terminal_state.mode == "light"
    assert light.result is not None
    assert light.result.commands[0].exit_code == 0, light.result.commands[0].stderr
    assert light.result.commands[1].offline_injected is True


def test_execute_arms_returns_a_tuple_of_arm_run_records() -> None:
    runs = execute_arms([_item()], actor=_RecordingActor(), max_workers=1)

    assert isinstance(runs, tuple)
    assert all(isinstance(run, ArmRun) for run in runs)


class _ReopeningActor(_RecordingActor):
    def execute(self, request: RunRequest) -> ActorResult:
        import subprocess

        marker = request.space.path / "reopen-evidence.txt"
        marker.write_text("evidence\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "reopen-evidence.txt"],
            cwd=request.space.path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"research({request.item.id}): reopen explore -> intake",
            ],
            cwd=request.space.path,
            check=True,
            capture_output=True,
            text=True,
        )
        return super().execute(request)


def test_filesystem_evidence_is_captured_before_runspace_teardown() -> None:
    run = runs_for_arm(
        execute_arms([_item()], actor=_ReopeningActor(), arms=(LIGHT_ARM,), max_workers=1),
        LIGHT_ARM,
    )[0]

    assert run.research_root is not None
    assert not run.research_root.exists()
    assert run.friction is not None
    assert reopen_trail_available(run.friction.reopens)
    assert isinstance(run.friction.reopens, ReopenTrail)
    assert run.friction.reopens.count == 1
    assert run.evidence_artifacts
    assert all(not Path(artifact.path).is_absolute() for artifact in run.evidence_artifacts)
    actor_evidence = next(
        artifact for artifact in run.evidence_artifacts if artifact.path == "evidence/actor.json"
    )
    assert '"actor":"scripted"' in actor_evidence.content
    assert '"tokens"' in actor_evidence.content
    assert '"timing"' in actor_evidence.content


def test_actor_evidence_durably_records_sanitized_execution_provenance() -> None:
    run = runs_for_arm(
        execute_arms([_scripted_item()], actor=ScriptedActor(), arms=(LIGHT_ARM,), max_workers=1),
        LIGHT_ARM,
    )[0]

    actor_evidence = next(
        artifact for artifact in run.evidence_artifacts if artifact.path == "evidence/actor.json"
    )
    payload = json.loads(actor_evidence.content)
    provenance = [command["execution_provenance"] for command in payload["commands"]]

    assert provenance
    for entry in provenance:
        assert set(entry) == {"executable", "package"}
        assert set(entry["executable"]) == {"identity", "sha256"}
        assert set(entry["package"]) == {"identity", "sha256"}
        assert entry["executable"]["identity"]
        assert entry["package"]["identity"]
        assert len(entry["executable"]["sha256"]) == 64
        assert len(entry["package"]["sha256"]) == 64
        assert "/" not in entry["executable"]["identity"]
        assert "/" not in entry["package"]["identity"]
    assert str(Path.home()) not in actor_evidence.content
    assert str(Path.cwd()) not in actor_evidence.content
