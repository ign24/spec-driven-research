"""Canonical blocking-control attribution for lifecycle defect scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class BlockingControl(StrEnum):
    """Canonical blocking-control types in their documented order."""

    STRUCTURAL = "structural"
    EVIDENTIAL = "evidential"
    TEXTUAL_ANCHORING = "textual-anchoring"
    EXECUTABLE = "executable"
    HASH_CONSISTENCY = "hash-consistency"
    HITL = "hitl"


@dataclass(frozen=True)
class DefectControl:
    """One defect's blocking control and exact structured reporting surface."""

    blocking_control: BlockingControl
    interface: str
    reporting_control: str

    @property
    def reporting_identity(self) -> str:
        """Return the exact interface/control identity used by scoring."""
        return f"{self.interface}:{self.reporting_control}"


_DEFECT_CONTROLS = {
    "missing-required-artifact": DefectControl(BlockingControl.STRUCTURAL, "check", "structure"),
    "unreachable-source": DefectControl(BlockingControl.EVIDENTIAL, "check", "links_resolve"),
    "unanchored-claim": DefectControl(
        BlockingControl.TEXTUAL_ANCHORING, "verify-claims", "textual-anchoring"
    ),
    "unreproducible-result": DefectControl(
        BlockingControl.EVIDENTIAL, "check", "benchmark_reproducible"
    ),
    "probe-expectation-mismatch": DefectControl(
        BlockingControl.EXECUTABLE, "verify-probe", "executable"
    ),
    "uncovered-criterion": DefectControl(
        BlockingControl.EVIDENTIAL, "check", "criteria_cross_reference"
    ),
    "stale-validation": DefectControl(
        BlockingControl.HASH_CONSISTENCY, "check", "hash-consistency"
    ),
}

DEFECT_CONTROLS: Final[Mapping[str, DefectControl]] = MappingProxyType(_DEFECT_CONTROLS)


def control_for_defect(defect: str) -> DefectControl | None:
    """Return the sole declared control attribution for a defect kind."""
    return DEFECT_CONTROLS.get(defect)
