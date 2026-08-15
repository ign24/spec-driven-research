"""Deterministic lifecycle-friction accounting from run evidence.

Structured CLI payloads are authoritative when available. Prose attribution is
restricted to commands whose current public interface exposes only prose, and every
unknown or ambiguous reason remains visible in the unmapped bucket.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, TypeGuard

from bench.harness.actor import ActorResult, CommandResult

ADVANCE_BLOCKED_PREFIX: Final[str] = "advance blocked: "
_GIT_TIMEOUT_SECONDS: Final[float] = 10.0
_REOPEN_SUBJECT = re.compile(
    r"^research\((?P<slug>[^()\r\n]+)\): reopen "
    r"(?P<from_stage>[a-z][a-z0-9_-]*) -> (?P<to_stage>[a-z][a-z0-9_-]*)$"
)


class ControlType(StrEnum):
    """The exact public control vocabulary, in its documented order."""

    STRUCTURAL = "structural"
    EVIDENTIAL = "evidential"
    TEXTUAL_ANCHORING = "textual-anchoring"
    EXECUTABLE = "executable"
    HASH_CONSISTENCY = "hash-consistency"
    HUMAN_APPROVAL = "human-approval"


CONTROL_VOCABULARY: Final[tuple[ControlType, ...]] = tuple(ControlType)

STRUCTURAL_CHECKS: Final[frozenset[str]] = frozenset({"structure", "frontmatter", "section"})
EVIDENTIAL_CHECKS: Final[frozenset[str]] = frozenset(
    {
        "min_evaluation_criteria",
        "source_tiers",
        "source_dates",
        "source_triangulation",
        "links_resolve",
        "tier_plausibility",
        "claim_citation_coverage",
        "criteria_cross_reference",
        "benchmark_reproducible",
        "probe_artifacts_exist",
        "y_statement",
        "ring_backed_by_evidence",
        "asset_metadata",
    }
)


@dataclass(frozen=True)
class ReopenTransition:
    """One exact SDR reopen subject observed in the Git trail."""

    slug: str
    from_stage: str
    to_stage: str
    subject: str


@dataclass(frozen=True)
class ReopenTrail:
    """Available Git trail, including the valid reopen transitions it contained."""

    transitions: tuple[ReopenTransition, ...]

    @property
    def count(self) -> int:
        """Number of reopen transitions in this trail."""
        return len(self.transitions)


@dataclass(frozen=True)
class ReopenTrailUnavailable:
    """The Git trail could not be read, so its count must not be reported as zero."""

    reason: str


type ReopenAccounting = ReopenTrail | ReopenTrailUnavailable


def reopen_trail_available(accounting: ReopenAccounting) -> TypeGuard[ReopenTrail]:
    """True when reopen accounting carries a readable Git trail."""
    return isinstance(accounting, ReopenTrail)


def reopen_accounting(git_root: Path, slug: str | None = None) -> ReopenAccounting:
    """Read exact reopen transitions from Git commit subjects without invoking a shell."""
    if not git_root.is_dir():
        return ReopenTrailUnavailable(reason=f"Git root does not exist: {git_root}")
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=git_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ReopenTrailUnavailable(reason=f"could not inspect Git root {git_root}: {error}")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        detail = probe.stderr.strip() or probe.stdout.strip() or "not a Git work tree"
        return ReopenTrailUnavailable(reason=f"unavailable Git trail at {git_root}: {detail}")

    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=git_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ReopenTrailUnavailable(reason=f"could not inspect Git HEAD at {git_root}: {error}")
    if head.returncode != 0:
        unborn = _has_unborn_head(git_root)
        if unborn is True:
            return ReopenTrail(transitions=())
        detail = head.stderr.strip() or head.stdout.strip() or "git could not verify HEAD"
        if isinstance(unborn, str):
            detail = f"{detail}; {unborn}"
        return ReopenTrailUnavailable(reason=f"unavailable Git HEAD at {git_root}: {detail}")

    try:
        history = subprocess.run(
            ["git", "log", "--reverse", "--format=%s", "--no-color"],
            cwd=git_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return ReopenTrailUnavailable(reason=f"could not read Git trail at {git_root}: {error}")
    if history.returncode != 0:
        detail = history.stderr.strip() or history.stdout.strip() or "git log failed"
        return ReopenTrailUnavailable(reason=f"unavailable Git trail at {git_root}: {detail}")

    transitions: list[ReopenTransition] = []
    for subject in history.stdout.splitlines():
        match = _REOPEN_SUBJECT.fullmatch(subject)
        if match is None or (slug is not None and match["slug"] != slug):
            continue
        transitions.append(
            ReopenTransition(
                slug=match["slug"],
                from_stage=match["from_stage"],
                to_stage=match["to_stage"],
                subject=subject,
            )
        )
    return ReopenTrail(transitions=tuple(transitions))


def _has_unborn_head(git_root: Path) -> bool | str:
    """Return true only when symbolic HEAD names a branch ref that does not exist."""
    try:
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=git_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        ref = symbolic.stdout.strip()
        if symbolic.returncode != 0 or not ref:
            return "HEAD is not an unborn symbolic branch"
        exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=git_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"could not distinguish unborn HEAD: {error}"
    if exists.returncode == 1:
        return True
    if exists.returncode == 0:
        return "HEAD branch ref exists but cannot be verified"
    detail = exists.stderr.strip() or exists.stdout.strip() or "git show-ref failed"
    return f"could not inspect HEAD branch ref: {detail}"


@dataclass(frozen=True)
class GateFailure:
    """One gate failure attributed to a documented control."""

    control: ControlType
    check: str
    detail: str
    source: str
    command_index: int
    exit_code: int


@dataclass(frozen=True)
class ProseAttribution:
    """Result of the explicit prose mapping, including every matching candidate."""

    control: ControlType | None
    candidates: tuple[ControlType, ...]
    pattern: str | None


@dataclass(frozen=True)
class UnmappedFailure:
    """A failure that cannot be attributed without guessing."""

    reason: str
    pattern: str | None = None
    candidates: tuple[ControlType, ...] = ()
    command_index: int = -1
    exit_code: int = 1

    @property
    def is_ambiguous(self) -> bool:
        """True when the evidence names more than one documented control."""
        return len(self.candidates) > 1


# Every pattern matches the prose `sdr.lifecycle` produces, except `transfer gates`,
# which matches the refusal `sdr approve` raises. `structural` and `evidential` stay
# separate entries so that a reason naming both stays ambiguous instead of being
# resolved into one control: the reason genuinely does not distinguish them.
_PROSE_PATTERNS: Final[tuple[tuple[str, ControlType], ...]] = (
    ("hash consistency failed", ControlType.HASH_CONSISTENCY),
    ("anchored verification failed", ControlType.TEXTUAL_ANCHORING),
    ("missing a stored passing sdr verify-probe", ControlType.EXECUTABLE),
    ("probe verification is stale", ControlType.HASH_CONSISTENCY),
    ("missing human approval", ControlType.HUMAN_APPROVAL),
    ("transfer gates", ControlType.STRUCTURAL),
    ("transfer gates", ControlType.EVIDENTIAL),
    ("structural", ControlType.STRUCTURAL),
    ("evidential", ControlType.EVIDENTIAL),
)


def attribute_check(check: str) -> ControlType | None:
    """Map an exact structured ``check`` name to its public control."""
    if check in STRUCTURAL_CHECKS:
        return ControlType.STRUCTURAL
    if check in EVIDENTIAL_CHECKS:
        return ControlType.EVIDENTIAL
    return None


def attribute_prose(reason: str) -> ProseAttribution:
    """Map a prose-only blocked reason, refusing to choose between candidates."""
    matches = tuple((pattern, control) for pattern, control in _PROSE_PATTERNS if pattern in reason)
    candidates = tuple(dict.fromkeys(control for _, control in matches))
    return ProseAttribution(
        control=candidates[0] if len(candidates) == 1 else None,
        candidates=candidates,
        pattern=matches[0][0] if len(matches) == 1 else None,
    )


def gate_failures(
    commands: Sequence[CommandResult],
) -> tuple[tuple[GateFailure, ...], tuple[UnmappedFailure, ...]]:
    """Attribute every observable gate failure, preferring structured JSON evidence."""
    attributed: list[GateFailure] = []
    unmapped: list[UnmappedFailure] = []
    for index, command in enumerate(commands):
        if command.verb in {"check", "verify-claims", "verify-probe"}:
            found, unknown = _structured_failures(command, index)
            attributed.extend(found)
            unmapped.extend(unknown)
            continue
        if command.exit_code == 0:
            continue
        reason = _prose_reason(command)
        if reason is None:
            continue
        mapping = attribute_prose(reason)
        if mapping.control is None:
            unmapped.append(
                UnmappedFailure(
                    reason=reason,
                    pattern=mapping.pattern,
                    candidates=mapping.candidates,
                    command_index=index,
                    exit_code=command.exit_code,
                )
            )
        else:
            attributed.append(
                GateFailure(
                    control=mapping.control,
                    check=mapping.pattern or "prose",
                    detail=reason,
                    source="prose",
                    command_index=index,
                    exit_code=command.exit_code,
                )
            )
    return tuple(attributed), tuple(unmapped)


def _structured_failures(
    command: CommandResult, index: int
) -> tuple[tuple[GateFailure, ...], tuple[UnmappedFailure, ...]]:
    if command.payload_error is not None:
        return (), (_unmapped(command.payload_error, command, index),)
    if not isinstance(command.payload, Mapping):
        if command.exit_code != 0:
            return (), (_unmapped("structured JSON payload is unavailable", command, index),)
        return (), ()
    if command.verb == "check":
        return _check_failures(command, index)
    if command.verb == "verify-claims":
        return _claim_failures(command, index)
    return _probe_failures(command, index)


def _check_failures(
    command: CommandResult, index: int
) -> tuple[tuple[GateFailure, ...], tuple[UnmappedFailure, ...]]:
    payload = command.payload
    assert isinstance(payload, Mapping)
    attributed: list[GateFailure] = []
    unmapped: list[UnmappedFailure] = []
    results = payload.get("results")
    if isinstance(results, list):
        for raw in results:
            if not isinstance(raw, Mapping):
                continue
            if raw.get("passed") is not False or raw.get("skipped") is True:
                continue
            check = raw.get("check")
            detail = _text(raw.get("detail"))
            control = attribute_check(check) if isinstance(check, str) else None
            if control is None:
                label = check if isinstance(check, str) and check else "<missing check>"
                unmapped.append(_unmapped(f"structured check {label!r}: {detail}", command, index))
                continue
            attributed.append(GateFailure(control, check, detail, "json", index, command.exit_code))
    issues = payload.get("consistency_issues")
    if isinstance(issues, list):
        attributed.extend(
            GateFailure(
                ControlType.HASH_CONSISTENCY,
                "consistency",
                _text(issue),
                "json",
                index,
                command.exit_code,
            )
            for issue in issues
        )
    return tuple(attributed), tuple(unmapped)


def _claim_failures(
    command: CommandResult, index: int
) -> tuple[tuple[GateFailure, ...], tuple[UnmappedFailure, ...]]:
    payload = command.payload
    assert isinstance(payload, Mapping)
    items = payload.get("items")
    if not isinstance(items, list):
        if command.exit_code != 0:
            return (), (_unmapped("verify-claims JSON has no items list", command, index),)
        return (), ()
    attributed: list[GateFailure] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            continue
        state = raw.get("state")
        if not isinstance(state, str) or state in {"verified", "human_reviewed"}:
            continue
        claim_id = _text(raw.get("claim_id"))
        attributed.append(
            GateFailure(
                ControlType.TEXTUAL_ANCHORING,
                claim_id,
                f"claim {claim_id} has state {state}",
                "json",
                index,
                command.exit_code,
            )
        )
    return tuple(attributed), ()


def _probe_failures(
    command: CommandResult, index: int
) -> tuple[tuple[GateFailure, ...], tuple[UnmappedFailure, ...]]:
    payload = command.payload
    assert isinstance(payload, Mapping)
    if payload.get("result") != "fail":
        return (), ()
    check = _text(payload.get("error_code") or "probe-failed")
    detail = _text(payload.get("error") or payload.get("error_code") or "probe failed")
    return (
        GateFailure(
            ControlType.EXECUTABLE,
            check,
            detail,
            "json",
            index,
            command.exit_code,
        ),
    ), ()


def _prose_reason(command: CommandResult) -> str | None:
    if command.verb == "advance":
        for line in command.stdout.splitlines():
            if line.startswith(ADVANCE_BLOCKED_PREFIX):
                return line.removeprefix(ADVANCE_BLOCKED_PREFIX).strip()
        return None
    if command.verb == "approve":
        reason = command.stderr.strip() or command.stdout.strip()
        return reason.removeprefix("Error: ").strip() or None
    return None


def _unmapped(reason: str, command: CommandResult, index: int) -> UnmappedFailure:
    return UnmappedFailure(reason=reason, command_index=index, exit_code=command.exit_code)


@dataclass(frozen=True)
class ResolveClaimCall:
    """One observed resolve-claim invocation and whether it exited successfully."""

    claim_id: str
    succeeded: bool
    command_index: int
    exit_code: int


@dataclass(frozen=True)
class ClaimAccounting:
    """Anchoring outcomes kept separate from explicit human claim closures."""

    recorded: bool
    passed_anchoring: tuple[str, ...]
    human_reviewed: tuple[str, ...]
    unresolved: tuple[str, ...]
    resolve_claim_calls: tuple[ResolveClaimCall, ...]

    @property
    def passed_anchoring_count(self) -> int:
        """Claims whose final structured state is verified."""
        return len(self.passed_anchoring)

    @property
    def human_review_count(self) -> int:
        """Claims whose final structured state is human reviewed."""
        return len(self.human_reviewed)

    @property
    def resolved_claim_ids(self) -> tuple[str, ...]:
        """Unique claims targeted by successful resolve-claim calls, in call order."""
        return _unique(call.claim_id for call in self.resolve_claim_calls if call.succeeded)


def claim_accounting(commands: Sequence[CommandResult]) -> ClaimAccounting:
    """Account for successful resolve-claim calls and the last verify-claims payload."""
    calls: list[ResolveClaimCall] = []
    last_items: list[Any] | None = None
    for index, command in enumerate(commands):
        if command.verb == "resolve-claim":
            claim_id = command.argv[2] if len(command.argv) > 2 else ""
            calls.append(
                ResolveClaimCall(
                    claim_id=claim_id,
                    succeeded=command.exit_code == 0,
                    command_index=index,
                    exit_code=command.exit_code,
                )
            )
        elif command.verb == "verify-claims" and isinstance(command.payload, Mapping):
            items = command.payload.get("items")
            if isinstance(items, list):
                last_items = items

    verified: list[str] = []
    reviewed: list[str] = []
    unresolved: list[str] = []
    for raw in last_items or []:
        if not isinstance(raw, Mapping):
            continue
        claim_id = raw.get("claim_id")
        state = raw.get("state")
        if not isinstance(claim_id, str) or not claim_id:
            continue
        if state == "verified":
            verified.append(claim_id)
        elif state == "human_reviewed":
            reviewed.append(claim_id)
        else:
            unresolved.append(claim_id)
    return ClaimAccounting(
        recorded=last_items is not None,
        passed_anchoring=_unique(verified),
        human_reviewed=_unique(reviewed),
        unresolved=_unique(unresolved),
        resolve_claim_calls=tuple(calls),
    )


@dataclass(frozen=True)
class FrictionAccounting:
    """All lifecycle-friction metrics for one actor result."""

    item_id: str
    arm: str
    repetition: int
    reopens: ReopenAccounting
    gate_failures: tuple[GateFailure, ...]
    unmapped: tuple[UnmappedFailure, ...]
    claims: ClaimAccounting

    def failures_by_control(self) -> dict[ControlType, int]:
        """Count failures in exact vocabulary order, including zero entries."""
        counts = {control: 0 for control in CONTROL_VOCABULARY}
        for failure in self.gate_failures:
            counts[failure.control] += 1
        return counts

    @property
    def unmapped_count(self) -> int:
        """Number of unattributed or ambiguous failures."""
        return len(self.unmapped)


def collect_friction(result: ActorResult, *, git_root: Path | None = None) -> FrictionAccounting:
    """Collect reopen, gate-failure, and claim accounting for one actor result."""
    attributed, unmapped = gate_failures(result.commands)
    return FrictionAccounting(
        item_id=result.item_id,
        arm=result.arm,
        repetition=result.repetition,
        reopens=reopen_accounting(git_root or result.workspace, slug=result.slug),
        gate_failures=attributed,
        unmapped=unmapped,
        claims=claim_accounting(result.commands),
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else repr(value)
