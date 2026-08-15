"""Deterministic orchestration of stage advancement.

`advance` runs the validation layers in order: structure/evidence, textual
anchoring in explore, stored verification in probe, and human approval in
transfer. It stops at the first failure and only then moves the stage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sdr import gates, probe_verify, schema, verification
from sdr import snapshot as snapshot_mod
from sdr.gates import GateReport
from sdr.research import Research


@dataclass
class AdvanceResult:
    ok: bool
    stage: str
    gate_report: GateReport | None = None
    blocked_reason: str = ""


def _stage_files(research: Research, stage: str) -> list[Path]:
    spec = schema.artifact_for(stage, schema_version=research.meta.schema_version)
    if spec.collection_dir:
        directory = research.artifact_path(spec.collection_dir)
        return sorted(directory.glob("*.md")) if directory.is_dir() else []
    path = research.artifact_path(spec.primary_file)
    return [path] if path.exists() else []


def stage_hash(research: Research, stage: str) -> str:
    """sha256 hash of the contents of the stage artifacts."""
    digest = hashlib.sha256()
    for path in _stage_files(research, stage):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def advance(
    research: Research,
    *,
    offline: bool = False,
) -> AdvanceResult:
    """Validate the current stage and advance only if every layer passes."""
    stage = research.meta.stage

    consistency_issues = check_consistency(research)
    if consistency_issues:
        return AdvanceResult(
            False,
            stage,
            blocked_reason="hash consistency failed: " + "; ".join(consistency_issues),
        )

    if stage == "explore" and research.meta.schema_version >= 2:
        snapshot_mod.ensure_explore_snapshots(research, offline=offline)

    # Layers 1-2: structure and evidence.
    report = gates.check_stage(research, stage=stage, offline=offline)
    if not report.passed:
        return AdvanceResult(
            False,
            stage,
            gate_report=report,
            blocked_reason="structural/evidential gates failed",
        )

    # Layer 3: anchored claim-versus-source verification for explore v2.
    if stage == "explore" and research.meta.schema_version >= 2:
        anchored = verification.verify_explore_claims(research)
        if not anchored.passed:
            return AdvanceResult(
                False,
                stage,
                gate_report=report,
                blocked_reason="anchored verification failed: " + "; ".join(anchored.failures),
            )

    # Probe runs only through `sdr verify-probe`; advance consumes its evidence.
    if stage == "probe":
        stored = research.meta.verify_probe or {}
        current_hash = probe_verify.hash_probe_dir(research)
        if stored.get("result") != "pass":
            return AdvanceResult(
                False,
                stage,
                gate_report=report,
                blocked_reason="missing a stored passing sdr verify-probe",
            )
        if stored.get("probe_hash") != current_hash:
            return AdvanceResult(
                False,
                stage,
                gate_report=report,
                blocked_reason="probe verification is stale; run sdr verify-probe",
            )

    # Transfer requires a recorded human approval.
    if stage == "transfer" and research.meta.approval is None:
        return AdvanceResult(
            False,
            stage,
            gate_report=report,
            blocked_reason="missing human approval (sdr approve)",
        )

    # Store the validation evidence and advance.
    research.meta.validation[stage] = stage_hash(research, stage)
    research.advance_stage()
    return AdvanceResult(
        True,
        research.meta.stage,
        gate_report=report,
    )


def reopen(research: Research, to: str, reason: str) -> None:
    """Move the investigation back to an earlier stage with a recorded reason.

    Invalidates the validation hashes of the target stage and every stage after
    it: that evidence must be re-validated on the way forward again. Reactivates
    investigations in the done state.
    """
    from datetime import date

    from sdr.research import Reopen

    if not reason.strip():
        raise ValueError("reopen requires a reason")
    order = schema.stage_order(research.meta.mode)
    if to not in order:
        raise ValueError(f"stage {to!r} does not belong to mode {research.meta.mode!r}")
    current = research.meta.stage
    if order.index(to) >= order.index(current):
        raise ValueError(
            f"reopen only moves backwards: {to!r} is not before the current stage {current!r}"
        )
    research.meta.reopens.append(
        Reopen(from_stage=current, to_stage=to, reason=reason, date=date.today().isoformat())
    )
    # Evidence from the target stage onwards is no longer validated.
    for stage in order[order.index(to) :]:
        research.meta.validation.pop(stage, None)
    research.meta.stage = to
    if research.meta.status == "done":
        research.meta.status = "active"
    research.save()


def consistency_issue_prefix(stage: str) -> str:
    """Prefix of a consistency issue for a stage.

    Consumers match consistency issues by prefix. Deriving it here keeps them
    from repeating the prose, which silently stops matching when it changes.
    """
    return f"stage {stage!r}: "


def check_consistency(research: Research) -> list[str]:
    """Detect stages whose validated hash no longer matches the current content."""
    issues: list[str] = []
    for stage, stored in research.meta.validation.items():
        current = stage_hash(research, stage)
        if current != stored:
            issues.append(
                consistency_issue_prefix(stage) + "the artifact changed after validation "
                f"(hash {stored[:8]} != {current[:8]}); re-validate with sdr advance"
            )
    return issues
