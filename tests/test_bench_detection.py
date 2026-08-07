"""Deterministic planted-defect detection scoring from structured SDR output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.harness.actor import (
    SCRIPTED_TOKENS,
    ActorKind,
    ActorResult,
    CommandResult,
    DetectionBasis,
)
from bench.harness.arms import ArmOutcome, ArmRun, TerminalState
from bench.harness.corpus import CorpusItem
from bench.harness.detection import DetectionOutcome, score_detection


def _item(*defects: str) -> CorpusItem:
    return CorpusItem(
        id="detection-item",
        mode="full",
        title="Detection item",
        question="Which planted defects do the controls report?",
        planted_defects=defects,
        expected_detection={defect: "caught" for defect in defects},
        sources=(),
        artifacts={},
        commands=(),
        probe=None,
        path=Path("bench/corpus/items/detection-item.yaml"),
    )


def _command(verb: str, payload: Any, *, exit_code: int = 1) -> CommandResult:
    return CommandResult(
        argv=(verb, "detection-item", "--json"),
        exit_code=exit_code,
        stdout="",
        stderr="",
        payload=payload,
    )


def _run(
    *commands: CommandResult,
    terminal_state: TerminalState | None = None,
) -> ArmRun:
    result = ActorResult(
        actor=ActorKind.SCRIPTED,
        item_id="detection-item",
        arm="full",
        repetition=0,
        slug="detection-item",
        artifacts_written=(),
        commands=commands,
        stages=(),
        tokens=SCRIPTED_TOKENS,
        detection_basis=DetectionBasis.MEASURED,
        detection_basis_reason="test fixture",
        duration_seconds=0.0,
        workspace=Path("/tmp/detection-item"),
        research_root=Path("/tmp/detection-item/research"),
    )
    return ArmRun(
        item_id="detection-item",
        arm="full",
        repetition=0,
        actor=ActorKind.SCRIPTED,
        outcome=ArmOutcome.EXECUTED,
        terminal_state=terminal_state
        or TerminalState(recorded=True, stage="probe", status="active", mode="full"),
        result=result,
        detection_basis=DetectionBasis.MEASURED,
        detection_basis_reason=result.detection_basis_reason,
        research_root=result.research_root,
    )


def test_each_planted_defect_is_caught_or_missed_and_caught_names_its_control() -> None:
    item = _item("unanchored-claim", "contradictory-sources")
    run = _run(
        _command(
            "verify-claims",
            {
                "passed": False,
                "items": [
                    {
                        "claim_id": "C1",
                        "state": "not_anchored",
                    }
                ],
            },
        )
    )

    score = score_detection(item, run)

    assert [entry.defect for entry in score.defects] == list(item.planted_defects)
    caught, missed = score.defects
    assert caught.outcome is DetectionOutcome.CAUGHT
    assert caught.finding is not None
    assert caught.finding.interface == "verify-claims"
    assert caught.finding.control == "textual-anchoring"
    assert missed.outcome is DetectionOutcome.MISSED
    assert missed.finding is None


def test_check_and_verify_probe_structured_findings_detect_their_defects() -> None:
    item = _item("unreachable-source", "probe-expectation-mismatch")
    run = _run(
        _command(
            "check",
            {
                "passed": False,
                "results": [
                    {
                        "check": "links_resolve",
                        "passed": False,
                        "skipped": False,
                        "detail": "synthetic source is unreachable",
                    }
                ],
                "consistency_issues": [],
            },
        ),
        _command(
            "verify-probe",
            {
                "result": "fail",
                "exit_code": 0,
                "output": "ACTUAL",
                "expect": "EXPECTED",
                "error_code": None,
                "error": None,
            },
        ),
    )

    score = score_detection(item, run)

    assert [entry.outcome for entry in score.defects] == [
        DetectionOutcome.CAUGHT,
        DetectionOutcome.CAUGHT,
    ]
    assert score.defects[0].finding is not None
    assert score.defects[0].finding.control == "links_resolve"
    assert score.defects[1].finding is not None
    assert score.defects[1].finding.interface == "verify-probe"
    assert score.defects[1].finding.control == "executable"


def test_failure_for_an_unrelated_control_is_not_scored_as_detection() -> None:
    item = _item("unanchored-claim")
    run = _run(
        _command(
            "check",
            {
                "passed": False,
                "results": [
                    {
                        "check": "structure",
                        "passed": False,
                        "skipped": False,
                        "detail": "missing required artifact",
                    }
                ],
                "consistency_issues": [],
            },
        )
    )

    score = score_detection(item, run)

    assert score.defects[0].outcome is DetectionOutcome.MISSED
    assert score.defects[0].finding is None
    assert score.false_positives == ()


def test_blocking_finding_on_a_clean_item_is_a_false_positive_with_its_control() -> None:
    item = _item()
    run = _run(
        _command(
            "check",
            {
                "passed": False,
                "results": [
                    {
                        "check": "structure",
                        "passed": False,
                        "skipped": False,
                        "detail": "missing required artifact",
                    }
                ],
                "consistency_issues": [],
            },
        )
    )

    score = score_detection(item, run)

    assert score.defects == ()
    assert len(score.false_positives) == 1
    assert score.false_positives[0].interface == "check"
    assert score.false_positives[0].control == "structure"


def test_explicitly_skipped_reporting_control_is_not_exercised_with_reason() -> None:
    item = _item("unreachable-source")
    run = _run(
        _command(
            "check",
            {
                "passed": True,
                "results": [
                    {
                        "check": "links_resolve",
                        "passed": True,
                        "skipped": True,
                        "detail": "offline mode skips link resolution",
                    }
                ],
            },
            exit_code=0,
        )
    )

    defect = score_detection(item, run).defects[0]

    assert defect.outcome is DetectionOutcome.NOT_EXERCISED
    assert defect.reporting_control == "check:links_resolve"
    assert defect.reason == "offline mode skips link resolution"
    assert defect.finding is None


def test_lifecycle_not_entered_is_not_exercised_with_terminal_reason() -> None:
    item = _item("unanchored-claim")
    run = _run(
        terminal_state=TerminalState(
            recorded=False,
            detail="no lifecycle metadata was written by this run",
        )
    )

    defect = score_detection(item, run).defects[0]

    assert defect.outcome is DetectionOutcome.NOT_EXERCISED
    assert defect.reporting_control is None
    assert defect.reason == "no lifecycle metadata was written by this run"


def test_a_reporting_control_that_runs_and_reports_wins_over_unrelated_skips() -> None:
    item = _item("unanchored-claim")
    run = _run(
        _command(
            "check",
            {
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
            exit_code=0,
        ),
        _command(
            "verify-claims",
            {"passed": False, "items": [{"claim_id": "C1", "state": "not_anchored"}]},
        ),
    )

    defect = score_detection(item, run).defects[0]

    assert defect.outcome is DetectionOutcome.CAUGHT
    assert defect.reporting_control == "verify-claims:textual-anchoring"
    assert defect.reason is None


def test_unrelated_skipped_control_does_not_suppress_a_miss() -> None:
    item = _item("unanchored-claim")
    run = _run(
        _command(
            "check",
            {
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
            exit_code=0,
        )
    )

    defect = score_detection(item, run).defects[0]

    assert defect.outcome is DetectionOutcome.MISSED
    assert defect.reporting_control == "verify-claims:textual-anchoring"
    assert defect.reason is None
