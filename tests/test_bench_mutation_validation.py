"""Exact mutation detection comparison and blocking-control coverage audit."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import bench.harness.mutation as mutation
from bench.harness.actor import DetectionBasis
from bench.harness.detection import (
    ControlFinding,
    DefectScore,
    DetectionOutcome,
    DetectionScore,
    ReportingControlExecution,
)


def _finding(control: str) -> ControlFinding:
    interface, name = control.split(":", 1)
    return ControlFinding(
        interface=interface,
        control=name,
        command_index=0,
        json_path="result",
        exit_code=1,
        detail=f"{control} rejected the fixture",
    )


def _defect(
    name: str,
    outcome: DetectionOutcome,
    control: str | None,
) -> DefectScore:
    return DefectScore(
        defect=name,
        outcome=outcome,
        finding=_finding(control) if outcome is DetectionOutcome.CAUGHT and control else None,
        reporting_control=control,
    )


def _score(
    item: str,
    arm: str,
    repetition: int,
    *defects: DefectScore,
    basis: DetectionBasis = DetectionBasis.MEASURED,
    false_positives: tuple[ControlFinding, ...] = (),
    reporting_executions: tuple[ReportingControlExecution, ...] | None = None,
) -> DetectionScore:
    if reporting_executions is None:
        reporting_executions = tuple(
            ReportingControlExecution(
                defect=defect.defect,
                interface=defect.reporting_control.split(":", 1)[0],
                control=defect.reporting_control.split(":", 1)[1],
                command_index=0,
                healthy=True,
                exercised=True,
                detail="",
            )
            for defect in defects
            if defect.reporting_control is not None
        )
    return DetectionScore(
        item_id=item,
        arm=arm,
        repetition=repetition,
        basis=basis,
        defects=defects,
        false_positives=false_positives,
        reporting_executions=reporting_executions,
    )


def _declaration(*defect_kinds: str) -> mutation.MutationDeclaration:
    return mutation.MutationDeclaration(
        name="disable-textual-anchoring",
        blocking_control=mutation.BlockingControl.TEXTUAL_ANCHORING,
        target=Path("sdr/verification.py"),
        transformation=mutation.ExactReplacement(before=b"before", after=b"after"),
        defect_kinds=defect_kinds,
    )


def _validation_api():
    validate = getattr(mutation, "validate_mutation_detection", None)
    assert validate is not None, "mutation detection validation is not implemented"
    return validate


def _coverage_api():
    audit = getattr(mutation, "audit_blocking_control_coverage", None)
    assert audit is not None, "blocking-control coverage audit is not implemented"
    return audit


def test_mutation_loses_exact_attributable_catches_by_full_detection_identity() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
            _defect(
                "probe-expectation-mismatch",
                DetectionOutcome.CAUGHT,
                "verify-probe:executable",
            ),
        ),
        _score(
            "item-a",
            "full",
            1,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
                baseline[0].defects[1],
            ),
        ),
        replace(
            baseline[1],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    result = _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert [entry.as_tuple() for entry in result.lost_catches] == [
        ("item-a", "full", 0, "unanchored-claim"),
        ("item-a", "full", 1, "unanchored-claim"),
    ]
    assert [entry.as_tuple() for entry in result.unchanged] == [
        ("item-a", "full", 0, "probe-expectation-mismatch")
    ]


def test_mutation_rejects_a_changed_outcome_attributable_only_to_another_control() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
            _defect(
                "probe-expectation-mismatch",
                DetectionOutcome.CAUGHT,
                "verify-probe:executable",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
                _defect(
                    "probe-expectation-mismatch",
                    DetectionOutcome.MISSED,
                    "verify-probe:executable",
                ),
            ),
        ),
    )

    error_type = getattr(mutation, "MutationValidationError", mutation.MutationError)
    with pytest.raises(error_type) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "unrelated-outcome-changed"
    assert raised.value.identities[0].as_tuple() == (
        "item-a",
        "full",
        0,
        "probe-expectation-mismatch",
    )
    assert "caught -> missed" in str(raised.value)


def test_mutation_rejects_an_identical_detection_projection_visibly() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )

    error_type = getattr(mutation, "MutationValidationError", mutation.MutationError)
    with pytest.raises(error_type) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, baseline)

    assert raised.value.code.value == "unchanged-detection-projection"
    assert "inert control or unread scorer" in str(raised.value)


def test_mutation_comparison_rejects_missing_or_extra_exact_identities() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )

    error_type = getattr(mutation, "MutationValidationError", mutation.MutationError)
    with pytest.raises(error_type) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, ())

    assert raised.value.code.value == "identity-mismatch"
    assert raised.value.identities[0].as_tuple() == (
        "item-a",
        "full",
        0,
        "unanchored-claim",
    )


@pytest.mark.parametrize(
    "mutated_defect",
    (
        _defect(
            "unanchored-claim",
            DetectionOutcome.MISSED,
            "verify-claims:textual-anchoring",
        ),
        DefectScore(
            defect="unanchored-claim",
            outcome=DetectionOutcome.NOT_EXERCISED,
            reporting_control="verify-claims:textual-anchoring",
            reason="invalid JSON output caused by ImportError",
        ),
    ),
)
def test_mutation_rejects_catch_loss_without_healthy_exact_reporting_execution(
    mutated_defect: DefectScore,
) -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(mutated_defect,),
            reporting_executions=(
                ReportingControlExecution(
                    defect="unanchored-claim",
                    interface="verify-claims",
                    control="textual-anchoring",
                    command_index=None,
                    healthy=False,
                    exercised=False,
                    detail=mutated_defect.reason or "required structured command was absent",
                ),
            ),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "unhealthy-reporting-control"


def test_mutation_declaration_rejects_defect_label_laundering() -> None:
    with pytest.raises(mutation.MutationError, match="unanchored-claim.*textual-anchoring"):
        mutation.MutationDeclaration(
            name="launder-textual-defect-as-hitl",
            blocking_control=mutation.BlockingControl.HITL,
            target=Path("sdr/verification.py"),
            transformation=mutation.ExactReplacement(before=b"before", after=b"after"),
            defect_kinds=("unanchored-claim",),
        )


def test_mutation_rejects_baseline_attribution_that_does_not_match_canonical_control() -> None:
    baseline_defect = DefectScore(
        defect="unanchored-claim",
        outcome=DetectionOutcome.CAUGHT,
        finding=_finding("verify-probe:executable"),
        reporting_control="verify-claims:textual-anchoring",
    )
    baseline = (_score("item-a", "full", 0, baseline_defect),)
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "attribution-mismatch"


def test_mutation_projection_rejects_detection_basis_drift() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            basis=DetectionBasis.CONTROL_CONSTANT,
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "detection-basis-changed"


def test_mutation_projection_rejects_clean_item_false_positive_changes() -> None:
    caught = _defect(
        "unanchored-claim",
        DetectionOutcome.CAUGHT,
        "verify-claims:textual-anchoring",
    )
    baseline = (
        _score("defect-item", "full", 0, caught),
        _score("clean-item", "full", 0),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
        replace(
            baseline[1],
            false_positives=(_finding("check:structure"),),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "clean-false-positives-changed"


def test_mutation_projection_rejects_a_missing_clean_item_identity() -> None:
    baseline = (
        _score(
            "defect-item",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
        _score("clean-item", "full", 0),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "identity-mismatch"


def test_mutation_rejects_not_exercised_as_catch_loss_even_with_healthy_metadata() -> None:
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.NOT_EXERCISED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    with pytest.raises(mutation.MutationValidationError) as raised:
        _validation_api()(_declaration("unanchored-claim"), baseline, mutated)

    assert raised.value.code.value == "unhealthy-reporting-control"


def test_coverage_registry_is_complete_and_in_canonical_control_order() -> None:
    registry = getattr(mutation, "BLOCKING_CONTROL_COVERAGE", None)
    assert registry is not None, "blocking-control coverage registry is not implemented"

    report = _coverage_api()(registry)

    assert tuple(entry.control for entry in report.entries) == tuple(mutation.BlockingControl)
    assert all(bool(entry.mutations) ^ bool(entry.infeasibility_reason) for entry in report.entries)
    assert all(
        isinstance(declaration, mutation.MutationDeclaration)
        for entry in report.entries
        for declaration in entry.mutations
    )


def test_coverage_audit_rejects_uncovered_and_out_of_order_controls() -> None:
    entry_type = getattr(mutation, "BlockingControlCoverage", None)
    assert entry_type is not None, "typed blocking-control coverage entries are not implemented"
    entries = (
        entry_type(
            control=mutation.BlockingControl.EVIDENTIAL,
            infeasibility_reason="specific fixture limitation",
        ),
        entry_type(control=mutation.BlockingControl.STRUCTURAL),
    )

    error_type = getattr(mutation, "MutationCoverageError", mutation.MutationError)
    with pytest.raises(error_type) as raised:
        _coverage_api()(entries)

    assert raised.value.code.value == "noncanonical-control-order"
    assert "structural, evidential, textual-anchoring, executable, hash-consistency, hitl" in str(
        raised.value
    )


def test_validation_and_coverage_audit_do_not_access_host_network_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("offline mutation validation crossed a host boundary")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-be-read")
    baseline = (
        _score(
            "item-a",
            "full",
            0,
            _defect(
                "unanchored-claim",
                DetectionOutcome.CAUGHT,
                "verify-claims:textual-anchoring",
            ),
        ),
    )
    mutated = (
        replace(
            baseline[0],
            defects=(
                _defect(
                    "unanchored-claim",
                    DetectionOutcome.MISSED,
                    "verify-claims:textual-anchoring",
                ),
            ),
        ),
    )

    _validation_api()(_declaration("unanchored-claim"), baseline, mutated)
    _coverage_api()(mutation.BLOCKING_CONTROL_COVERAGE)
