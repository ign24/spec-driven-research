"""Deterministic detection scoring from SDR's structured command outputs.

The scorer never treats a non-zero process exit by itself as a detection. A planted
defect is caught only when a structured finding from ``check``, ``verify-claims`` or
``verify-probe`` identifies the corresponding control failure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from bench.harness.actor import CommandResult, DetectionBasis
from bench.harness.arms import ArmOutcome, ArmRun
from bench.harness.controls import DefectControl, control_for_defect
from bench.harness.corpus import CorpusItem


class DetectionScoringError(ValueError):
    """Raised when a run cannot be scored without inventing evidence."""


class DetectionOutcome(StrEnum):
    """Observed disposition of one planted defect."""

    CAUGHT = "caught"
    MISSED = "missed"
    NOT_EXERCISED = "not-exercised"


@dataclass(frozen=True)
class ControlFinding:
    """One blocking structured finding and the exact field that reported it."""

    interface: str
    control: str
    command_index: int
    json_path: str
    exit_code: int
    detail: str


@dataclass(frozen=True)
class DefectScore:
    """Observed classification for one declared planted defect."""

    defect: str
    outcome: DetectionOutcome
    finding: ControlFinding | None = None
    reporting_control: str | None = None
    reason: str | None = None
    skipped_observation: ReportingControlObservation | None = None


@dataclass(frozen=True)
class ReportingControlObservation:
    """One structured observation that a reporting control ran or was skipped."""

    interface: str
    control: str
    command_index: int
    json_path: str
    exit_code: int
    skipped: bool
    detail: str


@dataclass(frozen=True)
class ReportingControlExecution:
    """Health and exercise evidence for one defect's exact reporting surface."""

    defect: str
    interface: str
    control: str
    command_index: int | None
    healthy: bool
    exercised: bool
    detail: str


@dataclass(frozen=True)
class DetectionScore:
    """Detection metrics for one executed arm run."""

    item_id: str
    arm: str
    repetition: int
    basis: DetectionBasis
    defects: tuple[DefectScore, ...]
    false_positives: tuple[ControlFinding, ...]
    reporting_executions: tuple[ReportingControlExecution, ...] = ()


_PASSING_CLAIM_STATES: Final[frozenset[str]] = frozenset({"verified", "human_reviewed"})


def score_detection(item: CorpusItem, run: ArmRun) -> DetectionScore:
    """Score one executed run using only its structured command payloads.

    The first matching finding in command order is retained for stable one-row-per-defect
    scoring. ``expected_detection`` is intentionally ignored: expectations must not decide
    observed outcomes.
    """
    if run.outcome is not ArmOutcome.EXECUTED or run.result is None:
        raise DetectionScoringError(
            f"run {run.item_id!r}/{run.arm}/{run.repetition} did not execute and has no "
            "structured detection evidence"
        )
    if (run.item_id, run.result.item_id) != (item.id, item.id):
        raise DetectionScoringError(
            f"item {item.id!r} does not match run evidence for {run.result.item_id!r}"
        )
    if run.detection_basis is None:
        raise DetectionScoringError("an executed run must declare its detection basis")

    findings = _blocking_findings(run.result.commands)
    observations = reporting_control_observations(run.result.commands)
    defects = tuple(
        _score_defect(defect, findings, observations, run) for defect in item.planted_defects
    )
    false_positives = findings if item.is_clean else ()
    reporting_executions = tuple(
        execution
        for defect in item.planted_defects
        if (control := control_for_defect(defect)) is not None
        for execution in (_reporting_execution(run.result.commands, defect, control),)
    )
    return DetectionScore(
        item_id=run.item_id,
        arm=run.arm,
        repetition=run.repetition,
        basis=run.detection_basis,
        defects=defects,
        false_positives=false_positives,
        reporting_executions=reporting_executions,
    )


def _score_defect(
    defect: str,
    findings: Sequence[ControlFinding],
    observations: Sequence[ReportingControlObservation],
    run: ArmRun,
) -> DefectScore:
    declared = control_for_defect(defect)
    expected_control = (
        None if declared is None else (declared.interface, declared.reporting_control)
    )
    if expected_control is not None:
        for finding in findings:
            if (finding.interface, finding.control) == expected_control:
                return DefectScore(
                    defect=defect,
                    outcome=DetectionOutcome.CAUGHT,
                    finding=finding,
                    reporting_control=_control_name(expected_control),
                )
    if not run.terminal_state.recorded:
        return DefectScore(
            defect=defect,
            outcome=DetectionOutcome.NOT_EXERCISED,
            reason=run.terminal_state.detail or "the lifecycle was not entered",
        )
    if expected_control is not None:
        matching = tuple(
            observation
            for observation in observations
            if (observation.interface, observation.control) == expected_control
        )
        if matching and all(observation.skipped for observation in matching):
            skipped = matching[0]
            return DefectScore(
                defect=defect,
                outcome=DetectionOutcome.NOT_EXERCISED,
                reporting_control=_control_name(expected_control),
                reason=skipped.detail or "reporting control was explicitly skipped",
                skipped_observation=skipped,
            )
    return DefectScore(
        defect=defect,
        outcome=DetectionOutcome.MISSED,
        reporting_control=(None if expected_control is None else _control_name(expected_control)),
    )


def reporting_control_observations(
    commands: Sequence[CommandResult],
) -> tuple[ReportingControlObservation, ...]:
    """Extract structured run/skip evidence without depending on durable records."""
    observations: list[ReportingControlObservation] = []
    for command_index, command in enumerate(commands):
        if command.payload_error is not None or not isinstance(command.payload, dict):
            continue
        if command.verb == "check":
            results = command.payload.get("results")
            if not isinstance(results, list):
                continue
            for result_index, raw in enumerate(results):
                if not isinstance(raw, dict):
                    continue
                control = raw.get("check")
                if not isinstance(control, str) or not control:
                    continue
                observations.append(
                    ReportingControlObservation(
                        interface="check",
                        control=control,
                        command_index=command_index,
                        json_path=f"results[{result_index}]",
                        exit_code=command.exit_code,
                        skipped=raw.get("skipped") is True,
                        detail=_detail(raw.get("detail")) if raw.get("detail") is not None else "",
                    )
                )
        elif command.verb in {"verify-claims", "verify-probe"}:
            control = "textual-anchoring" if command.verb == "verify-claims" else "executable"
            observations.append(
                ReportingControlObservation(
                    interface=command.verb,
                    control=control,
                    command_index=command_index,
                    json_path="",
                    exit_code=command.exit_code,
                    skipped=False,
                    detail="",
                )
            )
    return tuple(observations)


def _control_name(control: tuple[str, str]) -> str:
    return f"{control[0]}:{control[1]}"


def _reporting_execution(
    commands: Sequence[CommandResult], defect: str, control: DefectControl
) -> ReportingControlExecution:
    candidates = tuple(
        (index, command)
        for index, command in enumerate(commands)
        if command.verb == control.interface
    )
    if not candidates:
        return ReportingControlExecution(
            defect=defect,
            interface=control.interface,
            control=control.reporting_control,
            command_index=None,
            healthy=False,
            exercised=False,
            detail=f"required structured command {control.interface!r} was absent",
        )
    failures: list[ReportingControlExecution] = []
    for index, command in candidates:
        execution = _command_execution(index, command, defect, control)
        if execution.healthy and execution.exercised:
            return execution
        failures.append(execution)
    return failures[0]


def _command_execution(
    index: int, command: CommandResult, defect: str, control: DefectControl
) -> ReportingControlExecution:
    detail = command.payload_error or ""
    healthy = command.payload_error is None and isinstance(command.payload, dict)
    exercised = False
    if healthy:
        payload = command.payload
        assert isinstance(payload, dict)
        if control.interface == "check":
            results = payload.get("results")
            healthy = isinstance(payload.get("passed"), bool) and isinstance(results, list)
            if healthy:
                matching = tuple(
                    raw
                    for raw in results
                    if isinstance(raw, dict) and raw.get("check") == control.reporting_control
                )
                healthy = bool(matching) and all(
                    isinstance(raw.get("passed"), bool) and isinstance(raw.get("skipped"), bool)
                    for raw in matching
                )
                exercised = healthy and any(raw.get("skipped") is False for raw in matching)
        elif control.interface == "verify-claims":
            healthy = isinstance(payload.get("passed"), bool) and isinstance(
                payload.get("items"), list
            )
            if healthy:
                healthy = all(
                    isinstance(raw, dict)
                    and isinstance(raw.get("claim_id"), str)
                    and isinstance(raw.get("state"), str)
                    for raw in payload["items"]
                )
            exercised = healthy
        elif control.interface == "verify-probe":
            healthy = (
                payload.get("result") in {"pass", "fail"}
                and "exit_code" in payload
                and "error_code" in payload
            )
            exercised = healthy
    if not detail and not healthy:
        detail = f"{control.interface!r} returned a malformed structured payload"
    if healthy and not exercised:
        detail = f"{control.reporting_identity!r} was not exercised"
    return ReportingControlExecution(
        defect=defect,
        interface=control.interface,
        control=control.reporting_control,
        command_index=index,
        healthy=healthy,
        exercised=exercised,
        detail=detail,
    )


def _blocking_findings(commands: Sequence[CommandResult]) -> tuple[ControlFinding, ...]:
    findings: list[ControlFinding] = []
    for index, command in enumerate(commands):
        if command.payload_error is not None or not isinstance(command.payload, dict):
            continue
        if command.verb == "check":
            findings.extend(_check_findings(index, command))
        elif command.verb == "verify-claims":
            findings.extend(_claim_findings(index, command))
        elif command.verb == "verify-probe":
            finding = _probe_finding(index, command)
            if finding is not None:
                findings.append(finding)
    return tuple(findings)


def _check_findings(index: int, command: CommandResult) -> tuple[ControlFinding, ...]:
    payload = command.payload
    assert isinstance(payload, dict)
    findings: list[ControlFinding] = []
    results = payload.get("results")
    if isinstance(results, list):
        for result_index, raw in enumerate(results):
            if not isinstance(raw, dict):
                continue
            if raw.get("passed") is not False or raw.get("skipped") is True:
                continue
            control = raw.get("check")
            if not isinstance(control, str) or not control:
                continue
            findings.append(
                ControlFinding(
                    interface="check",
                    control=control,
                    command_index=index,
                    json_path=f"results[{result_index}]",
                    exit_code=command.exit_code,
                    detail=_detail(raw.get("detail")),
                )
            )
    issues = payload.get("consistency_issues")
    if isinstance(issues, list):
        findings.extend(
            ControlFinding(
                interface="check",
                control="hash-consistency",
                command_index=index,
                json_path=f"consistency_issues[{issue_index}]",
                exit_code=command.exit_code,
                detail=_detail(issue),
            )
            for issue_index, issue in enumerate(issues)
        )
    return tuple(findings)


def _claim_findings(index: int, command: CommandResult) -> tuple[ControlFinding, ...]:
    payload = command.payload
    assert isinstance(payload, dict)
    items = payload.get("items")
    if not isinstance(items, list):
        return ()
    findings: list[ControlFinding] = []
    for item_index, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        state = raw.get("state")
        if not isinstance(state, str) or state in _PASSING_CLAIM_STATES:
            continue
        claim_id = raw.get("claim_id")
        findings.append(
            ControlFinding(
                interface="verify-claims",
                control="textual-anchoring",
                command_index=index,
                json_path=f"items[{item_index}].state",
                exit_code=command.exit_code,
                detail=f"claim {_detail(claim_id)} has state {state}",
            )
        )
    return tuple(findings)


def _probe_finding(index: int, command: CommandResult) -> ControlFinding | None:
    payload = command.payload
    assert isinstance(payload, dict)
    if payload.get("result") != "fail":
        return None
    return ControlFinding(
        interface="verify-probe",
        control="executable",
        command_index=index,
        json_path="result",
        exit_code=command.exit_code,
        detail=_detail(payload.get("error") or payload.get("error_code") or "probe failed"),
    )


def _detail(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value)
