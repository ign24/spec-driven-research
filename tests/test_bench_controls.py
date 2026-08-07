"""Consistency tests for canonical lifecycle-control attribution."""

from bench.harness.controls import DEFECT_CONTROLS, BlockingControl
from bench.harness.friction import attribute_check


def test_named_check_defect_attributions_match_public_control_vocabulary() -> None:
    named_check_attributions = {
        defect: control
        for defect, control in DEFECT_CONTROLS.items()
        if control.interface == "check" and control.reporting_control != "hash-consistency"
    }

    assert named_check_attributions
    for defect, control in named_check_attributions.items():
        public_control = attribute_check(control.reporting_control)
        assert public_control is not None, defect
        assert control.blocking_control.value == public_control.value, defect


def test_blocking_control_names_remain_in_canonical_order() -> None:
    assert tuple(BlockingControl) == (
        BlockingControl.STRUCTURAL,
        BlockingControl.EVIDENTIAL,
        BlockingControl.TEXTUAL_ANCHORING,
        BlockingControl.EXECUTABLE,
        BlockingControl.HASH_CONSISTENCY,
        BlockingControl.HITL,
    )
