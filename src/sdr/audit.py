"""Deterministic audit state shown in status and INDEX."""

from __future__ import annotations

from dataclasses import dataclass

from sdr.research import Research
from sdr.verification import VerificationItem, verify_explore_claims
from sdr.verification_ledger import load_ledger


@dataclass(frozen=True)
class ClaimAudit:
    states: dict[str, int]
    ledger: str
    passed: bool
    has_claims: bool


def audit_markers(research: Research) -> list[str]:
    """Devuelve marcadores humanos vigentes, sin reinterpretar metadata legacy."""
    markers: list[str] = []
    for override in research.meta.overrides:
        stage = override.get("stage", "?")
        reason = override.get("reason", "")
        markers.append(f"override:{stage}:{reason}")
    if (
        research.meta.approval
        and research.meta.owner
        and research.meta.approval.by == research.meta.owner
    ):
        markers.append("self-approved")
    return markers


def claim_state_summary(research: Research) -> dict[str, int]:
    """Count the current states of active claims without modifying the ledger."""
    return evaluate_claims(research).states


def evaluate_claims(research: Research) -> ClaimAudit:
    """Evaluate claims against local artifacts and compare the stored ledger."""
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    report = verify_explore_claims(research, persist=False)
    if not report.items:
        states = _state_counts(ledger["claims"])
        passed = not states or claims_passed(states)
        return ClaimAudit(states=states, ledger="current", passed=passed, has_claims=False)

    states = _state_counts(item.to_dict() for item in report.items)
    persisted = {
        str(entry.get("claim_id") or ""): entry
        for entry in ledger["claims"]
        if isinstance(entry, dict)
    }
    current = len(persisted) == len(report.items) and all(
        _item_is_current(item, persisted.get(item.claim_id)) for item in report.items
    )
    return ClaimAudit(
        states=states,
        ledger="current" if current else "stale",
        passed=current and report.passed,
        has_claims=True,
    )


def _state_counts(entries: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        state = str(entry.get("state") or "unknown")
        counts[state] = counts.get(state, 0) + 1
    return dict(sorted(counts.items()))


def _item_is_current(item: VerificationItem, persisted: object) -> bool:
    if not isinstance(persisted, dict):
        return False
    return all(persisted.get(key) == value for key, value in item.to_dict().items())


def claims_passed(states: dict[str, int]) -> bool:
    """Report whether every stored claim is in an approving state."""
    return bool(states) and all(state in {"verified", "human_reviewed"} for state in states)
