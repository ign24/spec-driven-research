"""Exact structured scoring for captured cross-investigation invocations."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from bench.harness.actor import CommandResult
from bench.harness.cross_scoring import CrossOutcome, score_cross_consultation
from bench.harness.reuse import load_reuse_corpus, prepare_reuse_scenario
from bench.harness.runspace import REPOSITORY_ROOT
from sdr.cli import main


def _scenario():
    return load_reuse_corpus(REPOSITORY_ROOT / "bench" / "reuse-corpus").by_id(
        "software-shared-source"
    )


def _command(
    argv: tuple[str, ...],
    *,
    payload: Any | None = None,
    exit_code: int = 0,
    payload_error: str | None = None,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout="",
        stderr="",
        payload=payload,
        payload_error=payload_error,
    )


def _matching_payload() -> dict[str, Any]:
    scenario = _scenario()
    return scenario.positive_expectations[0].projection.to_dict()


def _check(score, check_id: str):
    return next(check for check in score.checks if check.id == check_id)


def test_no_cross_command_is_not_consulted_for_consultation_and_checks() -> None:
    score = score_cross_consultation(_scenario(), (), repetition=2)

    assert score.identity.scenario_id == "software-shared-source"
    assert score.identity.arm == "light"
    assert score.identity.history == "history-present"
    assert score.identity.repetition == 2
    assert score.consultation.outcome is CrossOutcome.NOT_CONSULTED
    assert all(check.outcome is CrossOutcome.NOT_CONSULTED for check in score.checks)


def test_other_cross_command_leaves_declared_checks_not_exercised() -> None:
    command = _command(
        ("cross", "source", "https://docs.queue-lab.example/other", "--json"),
        payload={"source_identity": "https://docs.queue-lab.example/other"},
    )

    score = score_cross_consultation(_scenario(), (command,), repetition=0)

    assert score.consultation.outcome is CrossOutcome.CORRECT
    assert all(check.outcome is CrossOutcome.NOT_EXERCISED for check in score.checks)


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        (_command(_scenario().positive_expectations[0].command, exit_code=2), "exit code 2"),
        (
            _command(
                _scenario().positive_expectations[0].command,
                payload_error="invalid JSON output",
            ),
            "invalid JSON output",
        ),
        (_command(_scenario().positive_expectations[0].command, payload=[]), "JSON object"),
    ],
)
def test_exercised_unhealthy_or_malformed_command_is_incorrect(
    command: CommandResult, reason: str
) -> None:
    score = score_cross_consultation(_scenario(), (command,), repetition=0)

    matching = [check for check in score.checks if check.command == command.argv]
    assert matching
    assert all(check.outcome is CrossOutcome.INCORRECT for check in matching)
    assert all(reason in check.reason for check in matching)
    assert all(
        check.outcome is CrossOutcome.NOT_EXERCISED
        for check in score.checks
        if check.command != command.argv
    )


def test_positive_projection_requires_investigation_qualification_and_stable_order() -> None:
    scenario = _scenario()
    command = _command(scenario.positive_expectations[0].command, payload=_matching_payload())
    matching = score_cross_consultation(scenario, (command,), repetition=0)

    reversed_payload = _matching_payload()
    reversed_payload["citations"].reverse()
    reversed_command = replace(command, payload=reversed_payload)
    reversed_score = score_cross_consultation(scenario, (reversed_command,), repetition=0)

    assert matching.checks[0].outcome is CrossOutcome.CORRECT
    assert reversed_score.checks[0].outcome is CrossOutcome.INCORRECT
    assert reversed_score.checks[0].expected == (
        scenario.positive_expectations[0].projection.to_dict()
    )
    assert reversed_score.checks[0].observed != reversed_score.checks[0].expected
    assert "projection mismatch" in reversed_score.checks[0].reason


def test_negative_absence_is_correct_and_injected_prohibited_record_is_incorrect() -> None:
    scenario = _scenario()
    payload = _matching_payload()
    command = _command(scenario.negative_controls[0].command, payload=payload)

    absent_score = score_cross_consultation(scenario, (command,), repetition=0)

    prohibited = scenario.negative_controls[0].absent[0]
    injected = _matching_payload()
    injected[prohibited.path[0]].append(dict(prohibited.record))
    injected_score = score_cross_consultation(
        scenario,
        (replace(command, payload=injected),),
        repetition=0,
    )

    control_id = scenario.negative_controls[0].id
    assert _check(absent_score, control_id).outcome is CrossOutcome.CORRECT
    assert _check(injected_score, control_id).outcome is CrossOutcome.INCORRECT
    assert _check(injected_score, control_id).observed == {
        "path": list(prohibited.path),
        "record": dict(prohibited.record),
    }
    assert "prohibited record present" in _check(injected_score, control_id).reason


def test_duplicate_exact_invocations_fail_without_cherry_picking() -> None:
    scenario = _scenario()
    good = _command(scenario.positive_expectations[0].command, payload=_matching_payload())
    bad = replace(good, payload={"source_identity": "mismatch"})

    score = score_cross_consultation(scenario, (good, bad), repetition=0)

    matching = [check for check in score.checks if check.command == good.argv]
    assert all(check.outcome is CrossOutcome.INCORRECT for check in matching)
    assert all("2 captured invocations" in check.reason for check in matching)
    assert all(
        check.outcome is CrossOutcome.NOT_EXERCISED
        for check in score.checks
        if check.command != good.argv
    )


def test_actual_fixture_cli_results_score_correctly(tmp_path: Path) -> None:
    scenario = _scenario()
    prepared = prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)

    with prepared as materialized:
        captured = tuple(
            CommandResult(
                argv=expectation.command,
                exit_code=(
                    result := CliRunner().invoke(
                        main,
                        list(expectation.command),
                        env={"SDR_ROOT": str(materialized.research_root)},
                    )
                ).exit_code,
                stdout=result.output,
                stderr="",
                payload=json.loads(result.output),
            )
            for expectation in scenario.positive_expectations
        )
        score = score_cross_consultation(scenario, captured, repetition=0)

    assert score.consultation.outcome is CrossOutcome.CORRECT
    assert all(check.outcome is CrossOutcome.CORRECT for check in score.checks)


def test_same_topic_negative_fails_if_prohibited_derive_join_is_injected() -> None:
    scenario = load_reuse_corpus(REPOSITORY_ROOT / "bench" / "reuse-corpus").by_id(
        "software-same-topic-no-edge"
    )
    control = scenario.negative_controls[0]
    payload = {"joins": []}
    absent = score_cross_consultation(
        scenario,
        (_command(control.command, payload=payload),),
        repetition=0,
    )
    injected_payload = {"joins": [dict(control.absent[0].record)]}
    injected = score_cross_consultation(
        scenario,
        (_command(control.command, payload=injected_payload),),
        repetition=0,
    )

    negative_index = len(scenario.positive_expectations)
    assert absent.checks[negative_index].outcome is CrossOutcome.CORRECT
    assert injected.checks[negative_index].outcome is CrossOutcome.INCORRECT
