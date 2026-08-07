"""Exact scoring of captured structured cross-investigation CLI invocations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from bench.harness.actor import CommandResult
from bench.harness.reuse import NegativeControl, PositiveExpectation, ReuseScenario


class CrossOutcome(StrEnum):
    """The only outcomes assigned to consultation and declared cross checks."""

    NOT_CONSULTED = "not-consulted"
    NOT_EXERCISED = "not-exercised"
    INCORRECT = "incorrect"
    CORRECT = "correct"


@dataclass(frozen=True)
class CrossScoreIdentity:
    """Scenario and orthogonal treatment identity for one in-memory score."""

    scenario_id: str
    arm: str
    history: str
    repetition: int


@dataclass(frozen=True)
class CrossConsultationScore:
    """Whether any captured invocation consulted the cross CLI."""

    outcome: CrossOutcome
    invocation_count: int
    reason: str


@dataclass(frozen=True)
class CrossCheckScore:
    """One declared positive projection or negative absence result."""

    id: str
    kind: Literal["positive", "negative"]
    command: tuple[str, ...]
    outcome: CrossOutcome
    reason: str
    expected: Any | None = None
    observed: Any | None = None


@dataclass(frozen=True)
class CrossConsultationResult:
    """Consultation and declared checks in stable fixture order."""

    identity: CrossScoreIdentity
    consultation: CrossConsultationScore
    checks: tuple[CrossCheckScore, ...]


def score_cross_consultation(
    scenario: ReuseScenario,
    commands: Sequence[CommandResult],
    *,
    repetition: int,
) -> CrossConsultationResult:
    """Score fixture declarations from captured command results without prose judgement."""
    if repetition < 0:
        raise ValueError("repetition must be non-negative")
    cross_commands = tuple(command for command in commands if _is_cross_command(command.argv))
    identity = CrossScoreIdentity(scenario.id, scenario.arm, scenario.history, repetition)
    if not cross_commands:
        consultation = CrossConsultationScore(
            CrossOutcome.NOT_CONSULTED,
            0,
            "no captured cross CLI invocation",
        )
        checks = tuple(
            _unavailable_check(expectation.id, "positive", expectation.command)
            for expectation in scenario.positive_expectations
        ) + tuple(
            _unavailable_check(control.id, "negative", control.command)
            for control in scenario.negative_controls
        )
        return CrossConsultationResult(identity, consultation, checks)

    consultation = CrossConsultationScore(
        CrossOutcome.CORRECT,
        len(cross_commands),
        f"captured {len(cross_commands)} cross CLI invocation(s)",
    )
    checks = tuple(
        _score_positive(expectation, cross_commands)
        for expectation in scenario.positive_expectations
    ) + tuple(_score_negative(control, cross_commands) for control in scenario.negative_controls)
    return CrossConsultationResult(identity, consultation, checks)


def _is_cross_command(argv: Sequence[str]) -> bool:
    return bool(argv) and argv[0] == "cross"


def _unavailable_check(
    check_id: str,
    kind: Literal["positive", "negative"],
    command: tuple[str, ...],
) -> CrossCheckScore:
    return CrossCheckScore(
        check_id,
        kind,
        command,
        CrossOutcome.NOT_CONSULTED,
        "no cross CLI command was captured",
    )


def _matching_command(
    check_id: str,
    kind: Literal["positive", "negative"],
    declared: tuple[str, ...],
    commands: Sequence[CommandResult],
) -> CommandResult | CrossCheckScore:
    matches = tuple(command for command in commands if command.argv == declared)
    if not matches:
        return CrossCheckScore(
            check_id,
            kind,
            declared,
            CrossOutcome.NOT_EXERCISED,
            "cross was consulted but the exact declared command was not captured",
        )
    if len(matches) != 1:
        return CrossCheckScore(
            check_id,
            kind,
            declared,
            CrossOutcome.INCORRECT,
            f"exact declared command has {len(matches)} captured invocations; expected exactly 1",
            observed={"invocation_count": len(matches)},
        )
    return matches[0]


def _command_problem(command: CommandResult) -> str | None:
    if command.exit_code != 0:
        return f"exact declared command exited with exit code {command.exit_code}"
    if command.payload_error is not None:
        return f"exact declared command has structured payload error: {command.payload_error}"
    if not isinstance(command.payload, Mapping):
        return "exact declared command payload must be a JSON object"
    return None


def _score_positive(
    expectation: PositiveExpectation, commands: Sequence[CommandResult]
) -> CrossCheckScore:
    selected = _matching_command(expectation.id, "positive", expectation.command, commands)
    if isinstance(selected, CrossCheckScore):
        return selected
    problem = _command_problem(selected)
    if problem is not None:
        return CrossCheckScore(
            expectation.id,
            "positive",
            expectation.command,
            CrossOutcome.INCORRECT,
            problem,
            expected=expectation.projection,
            observed=selected.payload,
        )
    expected = expectation.projection.to_dict()
    try:
        observed = _project_exact(selected.payload, expected)
    except (KeyError, TypeError, ValueError) as error:
        return CrossCheckScore(
            expectation.id,
            "positive",
            expectation.command,
            CrossOutcome.INCORRECT,
            f"structured projection is malformed: {error}",
            expected=expected,
            observed=selected.payload,
        )
    if observed != expected:
        return CrossCheckScore(
            expectation.id,
            "positive",
            expectation.command,
            CrossOutcome.INCORRECT,
            "exact structured projection mismatch",
            expected=expected,
            observed=observed,
        )
    return CrossCheckScore(
        expectation.id,
        "positive",
        expectation.command,
        CrossOutcome.CORRECT,
        "exact structured projection matched",
        expected=expected,
        observed=observed,
    )


def _project_exact(payload: Any, declaration: Any) -> Any:
    if isinstance(declaration, Mapping):
        if not isinstance(payload, Mapping):
            raise TypeError("expected an object at a declared projection path")
        missing = tuple(key for key in declaration if key not in payload)
        if missing:
            raise KeyError(f"missing declared key(s): {', '.join(missing)}")
        return {key: _project_exact(payload[key], value) for key, value in declaration.items()}
    if isinstance(declaration, list):
        if not isinstance(payload, list):
            raise TypeError("expected an array at a declared projection path")
        if len(payload) != len(declaration):
            raise ValueError(
                f"declared array length {len(declaration)} differs from observed {len(payload)}"
            )
        return [
            _project_exact(actual, expected)
            for actual, expected in zip(payload, declaration, strict=True)
        ]
    return payload


def _score_negative(control: NegativeControl, commands: Sequence[CommandResult]) -> CrossCheckScore:
    selected = _matching_command(control.id, "negative", control.command, commands)
    expected = tuple(
        {"path": list(absence.path), "record": dict(absence.record)} for absence in control.absent
    )
    if isinstance(selected, CrossCheckScore):
        return CrossCheckScore(
            selected.id,
            selected.kind,
            selected.command,
            selected.outcome,
            selected.reason,
            expected=expected,
            observed=selected.observed,
        )
    problem = _command_problem(selected)
    if problem is not None:
        return CrossCheckScore(
            control.id,
            "negative",
            control.command,
            CrossOutcome.INCORRECT,
            problem,
            expected=expected,
            observed=selected.payload,
        )
    for absence in control.absent:
        value: Any = selected.payload
        try:
            for segment in absence.path:
                if not isinstance(value, Mapping) or segment not in value:
                    raise KeyError(segment)
                value = value[segment]
            if not isinstance(value, list):
                raise TypeError("negative-control path must resolve to an array")
        except (KeyError, TypeError) as error:
            return CrossCheckScore(
                control.id,
                "negative",
                control.command,
                CrossOutcome.INCORRECT,
                f"negative-control structured path is malformed: {error}",
                expected=expected,
                observed=selected.payload,
            )
        if dict(absence.record) in value:
            observed = {"path": list(absence.path), "record": dict(absence.record)}
            return CrossCheckScore(
                control.id,
                "negative",
                control.command,
                CrossOutcome.INCORRECT,
                "prohibited record present at the declared structured path",
                expected=expected,
                observed=observed,
            )
    return CrossCheckScore(
        control.id,
        "negative",
        control.command,
        CrossOutcome.CORRECT,
        "every prohibited record is absent at its declared structured path",
        expected=expected,
        observed=(),
    )
