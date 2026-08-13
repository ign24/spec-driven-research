"""Independent attribution and exact one-session pilot orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bench.harness.actor import ARMS
from bench.harness.enforcement import (
    BoundaryError,
    LiveManifest,
    SealedRequest,
    revalidate_manifest_request,
)
from bench.harness.live import (
    LiveBounds,
    LiveOptIn,
    LiveRunRequest,
    LiveSessionEvidence,
    OpenCodeConnector,
    execute_live_session,
)
from bench.harness.prompts import (
    BuiltPrompt,
    EvaluationQuestion,
    PromptPolicy,
    validate_live_prompt,
)
from bench.harness.runspace import REPOSITORY_ROOT


class PilotAttributionError(ValueError):
    """Raised when materialized evidence cannot independently identify a pilot."""


class ApprovalState(StrEnum):
    NOT_REACHED = "not-reached"
    OPERATOR_PENDING = "operator-pending"
    OPERATOR_APPROVED = "operator-approved"
    OPERATOR_REJECTED = "operator-rejected"
    SYNTHETIC_APPROVED = "synthetic-approved"
    SYNTHETIC_REJECTED = "synthetic-rejected"


class ApprovalProvenance(StrEnum):
    NOT_REACHED = "not-reached"
    OPERATOR = "operator"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class OperatorDecisionRecord:
    """Immutable real operator decision bound to one stopped run and session."""

    record_id: str
    stopped_run_id: str
    session_id: str
    approved: bool

    def __post_init__(self) -> None:
        if not all(
            _nonempty(value) for value in (self.record_id, self.stopped_run_id, self.session_id)
        ):
            raise ValueError("operator decision identities must be non-empty")
        if type(self.approved) is not bool:
            raise TypeError("operator decision must contain one boolean decision")


@dataclass(frozen=True)
class ApprovalEvidence:
    """Approval state with an explicit and internally consistent source."""

    state: ApprovalState
    provenance: ApprovalProvenance
    operator_decision: OperatorDecisionRecord | None = None
    synthetic_reference: str | None = None

    def __post_init__(self) -> None:
        expected = {
            ApprovalState.NOT_REACHED: ApprovalProvenance.NOT_REACHED,
            ApprovalState.OPERATOR_PENDING: ApprovalProvenance.OPERATOR,
            ApprovalState.OPERATOR_APPROVED: ApprovalProvenance.OPERATOR,
            ApprovalState.OPERATOR_REJECTED: ApprovalProvenance.OPERATOR,
            ApprovalState.SYNTHETIC_APPROVED: ApprovalProvenance.SYNTHETIC,
            ApprovalState.SYNTHETIC_REJECTED: ApprovalProvenance.SYNTHETIC,
        }
        if expected.get(self.state) is not self.provenance:
            raise ValueError("approval state and provenance are inconsistent")
        operator_decided = self.state in {
            ApprovalState.OPERATOR_APPROVED,
            ApprovalState.OPERATOR_REJECTED,
        }
        synthetic_decided = self.state in {
            ApprovalState.SYNTHETIC_APPROVED,
            ApprovalState.SYNTHETIC_REJECTED,
        }
        if operator_decided != isinstance(self.operator_decision, OperatorDecisionRecord):
            raise ValueError("operator-decided approval requires one typed operator record")
        if not operator_decided and self.operator_decision is not None:
            raise ValueError("non-decided approval cannot contain an operator record")
        if synthetic_decided != _nonempty(self.synthetic_reference):
            raise ValueError("synthetic approval requires exactly one fixture reference")
        if isinstance(self.operator_decision, OperatorDecisionRecord):
            expected_approved = self.state is ApprovalState.OPERATOR_APPROVED
            if self.operator_decision.approved is not expected_approved:
                raise ValueError("operator decision value conflicts with approval state")

    @classmethod
    def not_reached(cls) -> ApprovalEvidence:
        return cls(ApprovalState.NOT_REACHED, ApprovalProvenance.NOT_REACHED)

    @classmethod
    def operator_pending(cls) -> ApprovalEvidence:
        return cls(ApprovalState.OPERATOR_PENDING, ApprovalProvenance.OPERATOR)

    @classmethod
    def operator_decided(cls, record: OperatorDecisionRecord) -> ApprovalEvidence:
        if not isinstance(record, OperatorDecisionRecord):
            raise TypeError("operator approval requires one typed immutable decision record")
        state = (
            ApprovalState.OPERATOR_APPROVED if record.approved else ApprovalState.OPERATOR_REJECTED
        )
        return cls(state, ApprovalProvenance.OPERATOR, record)

    @classmethod
    def synthetic_decided(cls, approved: bool, reference: str) -> ApprovalEvidence:
        state = ApprovalState.SYNTHETIC_APPROVED if approved else ApprovalState.SYNTHETIC_REJECTED
        return cls(state, ApprovalProvenance.SYNTHETIC, None, reference)


def validate_initial_live_approval(evidence: ApprovalEvidence) -> None:
    """Synthetic fixture decisions are never initial live operator evidence."""
    if evidence.provenance is ApprovalProvenance.SYNTHETIC or evidence.state in {
        ApprovalState.OPERATOR_APPROVED,
        ApprovalState.OPERATOR_REJECTED,
    }:
        raise BoundaryError("operator-decided or synthetic approval is not initial live evidence")


def validate_approval_terminal(
    evidence: ApprovalEvidence,
    terminal_state: str,
    *,
    initial_live: bool,
    stopped_run_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """Validate the exact approval/terminal matrix without normalization."""
    if initial_live:
        validate_initial_live_approval(evidence)
    if evidence.state is ApprovalState.OPERATOR_PENDING:
        if terminal_state != "awaiting-operator-approval":
            raise BoundaryError("operator-pending approval requires transfer terminal state")
        return
    if evidence.state is ApprovalState.NOT_REACHED:
        if terminal_state == "awaiting-operator-approval":
            raise BoundaryError("not-reached approval conflicts with transfer terminal state")
        return
    if evidence.state in {ApprovalState.OPERATOR_APPROVED, ApprovalState.OPERATOR_REJECTED}:
        record = evidence.operator_decision
        if terminal_state != "awaiting-operator-approval" or record is None:
            raise BoundaryError("operator decision requires a separate record for the stopped run")
        if record.stopped_run_id != stopped_run_id:
            raise BoundaryError("operator decision does not identify the stopped run")
        if record.session_id != session_id:
            raise BoundaryError("operator decision does not identify the stopped session")
        return
    if terminal_state in {"awaiting-operator-approval", "completed", "errored"}:
        raise BoundaryError("synthetic approval cannot pair with a live terminal state")


@dataclass(frozen=True)
class ObservedPilotIdentity:
    scenario_id: str | None
    item_id: str | None
    arm: str
    repetition: int
    host: str
    host_version: str
    model: str
    model_version: str | None
    prompt_policy: PromptPolicy | None
    prompt_template_identifier: str
    prompt_template_version: str
    prompt_template_sha256: str
    submitted_prompt_sha256: str
    bounds: LiveBounds
    results_root: Path


@dataclass(frozen=True)
class PilotPlan:
    """Operator authorization for one scalar session, never an expansion plan."""

    scenario_id: str | None
    item_id: str | None
    arm: str
    repetition: int
    host: str
    host_version: str
    model: str
    model_version: str | None
    prompt: BuiltPrompt
    bounds: LiveBounds
    results_root: Path

    def __post_init__(self) -> None:
        if sum(_nonempty(value) for value in (self.scenario_id, self.item_id)) != 1:
            raise ValueError("pilot requires exactly one scenario or item")
        if type(self.arm) is not str or self.arm not in ARMS:
            raise ValueError("pilot arm must be one scalar declared arm")
        if type(self.repetition) is not int or self.repetition < 0:
            raise TypeError("pilot repetition must be one non-negative scalar integer")
        for name, value in (
            ("host", self.host),
            ("host version", self.host_version),
            ("model", self.model),
        ):
            if not _nonempty(value):
                raise TypeError(f"pilot {name} must be one non-empty scalar string")
        if self.model_version is not None and not _nonempty(self.model_version):
            raise TypeError("pilot model version must be one scalar string")
        if not isinstance(self.prompt, BuiltPrompt):
            raise TypeError("pilot prompt must be one canonical BuiltPrompt")
        if self.prompt.treatment.arm != self.arm:
            raise ValueError("pilot arm differs from prompt treatment")
        validate_live_prompt(self.prompt, self.prompt.text.encode())
        if not isinstance(self.bounds, LiveBounds):
            raise TypeError("pilot bounds must be one scalar LiveBounds")
        if not isinstance(self.results_root, Path):
            raise TypeError("pilot results root must be one Path")
        root = self.results_root.resolve(strict=True)
        if root.is_relative_to(REPOSITORY_ROOT.resolve(strict=True)):
            raise ValueError("pilot results root must be external to the repository")
        object.__setattr__(self, "results_root", root)
        if (
            self.prompt.treatment.evaluation_question is EvaluationQuestion.CROSS_RETRIEVAL
            and self.prompt.treatment.policy is not PromptPolicy.ASSISTED
        ):
            raise ValueError("the initial reuse pilot accepts assisted policy only")

    @property
    def identity(self) -> ObservedPilotIdentity:
        """Plan projection used only as final comparison input."""
        prompt = validate_live_prompt(self.prompt, self.prompt.text.encode())
        return ObservedPilotIdentity(
            self.scenario_id,
            self.item_id,
            self.arm,
            self.repetition,
            self.host,
            self.host_version,
            self.model,
            self.model_version,
            self.prompt.treatment.policy,
            self.prompt.template.identifier,
            self.prompt.template.version,
            self.prompt.template.sha256,
            prompt.submitted_sha256,
            self.bounds,
            self.results_root,
        )


@dataclass(frozen=True)
class IdentityEvidence:
    """Typed materialization, sealed request, runspace, and live/export evidence."""

    manifest: LiveManifest
    sealed_request: SealedRequest
    live: LiveSessionEvidence


def derive_observed_identity(evidence: IdentityEvidence) -> ObservedPilotIdentity:
    """Derive identity without accepting a PilotPlan as a source or fallback."""
    if not isinstance(evidence.manifest, LiveManifest):
        raise PilotAttributionError("observed target lacks exact materialized manifest evidence")
    if not isinstance(evidence.sealed_request, SealedRequest):
        raise PilotAttributionError("observed request lacks exact sealed request evidence")
    if not isinstance(evidence.live, LiveSessionEvidence):
        raise PilotAttributionError("observed host/export lacks typed live evidence")
    revalidate_manifest_request(evidence.manifest, evidence.sealed_request)
    if evidence.manifest.runspace_root.resolve(strict=True) != evidence.live.working_root.resolve(
        strict=True
    ):
        raise PilotAttributionError("observed runspace conflicts with live working root")
    prompt_evidence = evidence.live.prompt
    host = evidence.live.host.host
    host_version = evidence.live.host.host_version
    model = evidence.live.host.model
    model_version = evidence.live.host.model_version
    if not _nonempty(host) or not _nonempty(host_version):
        raise PilotAttributionError("observed host identity lacks executable/version evidence")
    if not _nonempty(model):
        raise PilotAttributionError("observed model identity lacks exact export evidence")
    return ObservedPilotIdentity(
        evidence.manifest.identity if evidence.manifest.identity_kind == "scenario" else None,
        evidence.manifest.identity if evidence.manifest.identity_kind == "item" else None,
        evidence.manifest.arm,
        evidence.sealed_request.repetition,
        host,
        host_version,
        model,
        model_version,
        _prompt_policy_from_live(evidence.live),
        _template_identifier_from_live(evidence.live),
        _template_version_from_live(evidence.live),
        prompt_evidence.template_sha256,
        prompt_evidence.submitted_sha256,
        LiveBounds(
            evidence.sealed_request.max_turns,
            evidence.sealed_request.wall_clock_seconds,
        ),
        evidence.sealed_request.results_root,
    )


def validate_pilot_attribution(
    plan: PilotPlan, observed: ObservedPilotIdentity
) -> ObservedPilotIdentity:
    """Compare independent observation with authorization field by field."""
    expected = plan.identity
    if observed != expected:
        differences = [
            name
            for name in ObservedPilotIdentity.__dataclass_fields__
            if getattr(observed, name) != getattr(expected, name)
        ]
        raise PilotAttributionError("pilot identity mismatch: " + ", ".join(differences))
    return observed


@dataclass(frozen=True)
class PilotExitReport:
    run_id: str
    identity: ObservedPilotIdentity
    approval: ApprovalEvidence
    session_id: str | None
    tokens: object
    cost: object
    wall_clock_seconds: float
    terminal_state: str
    attributed: bool
    transcript_persisted: bool


def execute_pilot(
    plan: PilotPlan,
    *,
    request: LiveRunRequest,
    connector: OpenCodeConnector,
    opt_in: LiveOptIn,
) -> PilotExitReport:
    """Execute the one sealed live request directly through the enforced boundary."""
    live = execute_live_session(
        connector,
        request,
        opt_in=opt_in,
    )
    observed = derive_observed_identity(
        IdentityEvidence(request.manifest, request.sealed_request, live)
    )
    validate_pilot_attribution(plan, observed)
    approval = (
        ApprovalEvidence.operator_pending()
        if live.approval_state == ApprovalState.OPERATOR_PENDING
        else ApprovalEvidence.not_reached()
        if live.approval_state == ApprovalState.NOT_REACHED
        else None
    )
    if approval is None:
        raise BoundaryError("live connector returned an inconsistent approval state")
    validate_approval_terminal(approval, live.terminal_state, initial_live=True)
    if live.transcript_persisted:
        raise BoundaryError("pilot evidence must never persist a transcript")
    return PilotExitReport(
        request.sealed_request.request_sha256,
        observed,
        approval,
        live.session.session_id,
        live.tokens,
        live.cost,
        live.wall_clock_seconds,
        live.terminal_state,
        live.session.attributed,
        False,
    )


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _template_identifier_from_live(live: LiveSessionEvidence) -> str:
    text = live.prompt.submitted_bytes.decode("utf-8")
    prefix = "template_id: "
    line = next((value for value in text.splitlines() if value.startswith(prefix)), None)
    if line is None:
        raise PilotAttributionError("submitted prompt omitted template identity")
    return line.removeprefix(prefix)


def _prompt_policy_from_live(live: LiveSessionEvidence) -> PromptPolicy | None:
    text = live.prompt.submitted_bytes.decode("utf-8")
    prefix = "policy: "
    line = next((value for value in text.splitlines() if value.startswith(prefix)), None)
    if line is None:
        raise PilotAttributionError("submitted prompt omitted treatment policy")
    value = line.removeprefix(prefix)
    return None if value == "not-applicable" else PromptPolicy(value)


def _template_version_from_live(live: LiveSessionEvidence) -> str:
    text = live.prompt.submitted_bytes.decode("utf-8")
    prefix = "template_version: "
    line = next((value for value in text.splitlines() if value.startswith(prefix)), None)
    if line is None:
        raise PilotAttributionError("submitted prompt omitted template version")
    return line.removeprefix(prefix)
