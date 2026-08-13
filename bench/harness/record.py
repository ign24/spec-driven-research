"""Authoritative schema-v2 durable records and canonical JSON serialization."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, model_validator

from bench.harness.actor import ActorKind
from bench.harness.prompts import EvaluationQuestion

SCHEMA_VERSION: Final[int] = 2
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FIELDS: Final[frozenset[str]] = frozenset(
    {"transcript", "raw_export", "raw_host_export", "model_judgement", "model_judgment"}
)
_CONTROL_ORDER: Final[tuple[str, ...]] = (
    "structural",
    "evidential",
    "textual-anchoring",
    "executable",
    "hash-consistency",
    "human-approval",
)
_QUESTION_ORDER: Final[tuple[EvaluationQuestion, ...]] = (
    EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
    EvaluationQuestion.LIVE_SINGLE_INVESTIGATION,
    EvaluationQuestion.CROSS_RETRIEVAL,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class RecordModel(BaseModel):
    """Frozen exact-key base for every persisted schema object."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceKind(StrEnum):
    ARTIFACT = "artifact"
    COMMAND_EXIT = "command-exit"
    STRUCTURED_CLI_FIELD = "structured-cli-field"


class EvidenceRef(RecordModel):
    id: StrictStr
    kind: EvidenceKind
    artifact_path: StrictStr
    command_index: StrictInt | None
    exit_code: StrictInt | None
    structured_field: StrictStr | None

    @model_validator(mode="after")
    def validate_reference(self) -> EvidenceRef:
        _nonempty(self.id, "evidence id")
        _relative_path(self.artifact_path)
        command = self.kind in {EvidenceKind.COMMAND_EXIT, EvidenceKind.STRUCTURED_CLI_FIELD}
        if command != (self.command_index is not None and self.exit_code is not None):
            raise ValueError("command evidence requires command_index and exit_code together")
        if self.command_index is not None and self.command_index < 0:
            raise ValueError("evidence command_index must be nonnegative")
        structured = self.kind is EvidenceKind.STRUCTURED_CLI_FIELD
        if structured != bool(self.structured_field):
            raise ValueError("structured CLI evidence requires exactly one structured_field")
        return self


class ResultsRootIdentity(RecordModel):
    identity: StrictStr
    external: StrictBool


class SeedImmutability(RecordModel):
    checked: StrictBool
    unchanged: StrictBool | None
    pre_sha256: dict[StrictStr, StrictStr]
    post_sha256: dict[StrictStr, StrictStr]

    @model_validator(mode="after")
    def validate_hashes(self) -> SeedImmutability:
        for values in (self.pre_sha256, self.post_sha256):
            for identity, digest in values.items():
                _nonempty(identity, "seed identity")
                _digest(digest, "seed artifact hash")
        if self.checked:
            if self.unchanged is None or set(self.pre_sha256) != set(self.post_sha256):
                raise ValueError("checked seed immutability requires complete pre/post identities")
        elif self.unchanged is not None or self.pre_sha256 or self.post_sha256:
            raise ValueError("unchecked seed immutability cannot carry inferred results")
        return self


class CorpusProvenance(RecordModel):
    version: StrictStr
    migration_provenance_version: StrictStr
    scenario_manifest_sha256: StrictStr
    seed_artifact_sha256: dict[StrictStr, StrictStr]
    focal_investigation: StrictStr
    history_condition: Literal["history-present", "history-absent", "not-applicable"]
    seed_immutability: SeedImmutability

    @model_validator(mode="after")
    def validate_provenance(self) -> CorpusProvenance:
        for value, label in (
            (self.version, "corpus version"),
            (self.migration_provenance_version, "migration provenance version"),
            (self.focal_investigation, "focal investigation"),
        ):
            _nonempty(value, label)
        _digest(self.scenario_manifest_sha256, "scenario manifest hash")
        for digest in self.seed_artifact_sha256.values():
            _digest(digest, "seed artifact hash")
        return self


class PromptEvidence(RecordModel):
    policy: Literal["standard", "assisted", "unassisted"]
    template_id: StrictStr
    template_version: StrictStr
    template_sha256: StrictStr
    submitted_prompt_sha256: StrictStr
    built_prompt_sha256: StrictStr
    canonical_built_prompt: StrictBool
    leak_validation_passed: StrictBool

    @model_validator(mode="after")
    def validate_prompt(self) -> PromptEvidence:
        _nonempty(self.template_id, "prompt template id")
        _nonempty(self.template_version, "prompt template version")
        for value, label in (
            (self.template_sha256, "prompt template hash"),
            (self.submitted_prompt_sha256, "submitted prompt hash"),
            (self.built_prompt_sha256, "built prompt hash"),
        ):
            _digest(value, label)
        if not self.canonical_built_prompt or not self.leak_validation_passed:
            raise ValueError(
                "prompt requires canonical BuiltPrompt construction and leak validation"
            )
        if self.submitted_prompt_sha256 != self.built_prompt_sha256:
            raise ValueError("submitted prompt differs from the canonical BuiltPrompt")
        return self


class ExecutableProvenance(RecordModel):
    path: StrictStr
    sha256: StrictStr
    package_identity: StrictStr
    package_sha256: StrictStr

    @model_validator(mode="after")
    def validate_executable(self) -> ExecutableProvenance:
        _nonempty(self.path, "executable path")
        _nonempty(self.package_identity, "package identity")
        _digest(self.sha256, "executable hash")
        _digest(self.package_sha256, "package hash")
        return self


class RepositoryAudit(RecordModel):
    before_sha256: StrictStr
    after_sha256: StrictStr
    unchanged: StrictBool

    @model_validator(mode="after")
    def validate_audit(self) -> RepositoryAudit:
        _digest(self.before_sha256, "repository pre-audit hash")
        _digest(self.after_sha256, "repository post-audit hash")
        if self.unchanged != (self.before_sha256 == self.after_sha256):
            raise ValueError("repository audit result contradicts its hashes")
        return self


class ProcessGroupOutcome(RecordModel):
    identity: StrictInt | None
    reaped: StrictBool


class ConfiguredBounds(RecordModel):
    max_turns: StrictInt | None
    wall_clock_seconds: float | None

    @model_validator(mode="after")
    def validate_bounds(self) -> ConfiguredBounds:
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError("max_turns must be positive when configured")
        if self.wall_clock_seconds is not None:
            _duration(self.wall_clock_seconds, "wall-clock bound", positive=True)
        return self


class XdgRootIdentities(RecordModel):
    config: StrictStr
    data: StrictStr
    cache: StrictStr
    state: StrictStr


class BoundaryFileIdentity(RecordModel):
    path: StrictStr
    device: StrictInt
    inode: StrictInt
    sha256: StrictStr

    @model_validator(mode="after")
    def validate_identity(self) -> BoundaryFileIdentity:
        _nonempty(self.path, "boundary path")
        if self.device < 0 or self.inode < 0:
            raise ValueError("boundary device/inode identities must be nonnegative")
        _digest(self.sha256, "boundary file hash")
        return self


class BoundaryIdentities(RecordModel):
    executable: BoundaryFileIdentity
    config: BoundaryFileIdentity
    plugin: BoundaryFileIdentity


class LiveBoundaryEvidence(RecordModel):
    credential_allowlist_identity: StrictStr
    xdg_roots: XdgRootIdentities
    identities: BoundaryIdentities
    revalidation_boundaries: list[StrictStr]
    effective_config_isolated: StrictBool
    mediator_token_undisclosed: StrictBool
    subprocesses_cancelled: StrictBool
    subprocesses_joined: StrictBool

    @model_validator(mode="after")
    def validate_boundary(self) -> LiveBoundaryEvidence:
        _nonempty(self.credential_allowlist_identity, "credential allowlist identity")
        if not self.revalidation_boundaries:
            raise ValueError("live boundary requires revalidation evidence")
        if not all(
            (
                self.effective_config_isolated,
                self.mediator_token_undisclosed,
                self.subprocesses_joined,
            )
        ):
            raise ValueError("live boundary isolation and join proofs must pass")
        return self


class ExecutionBoundary(RecordModel):
    environment_policy: StrictStr
    environment_policy_version: StrictStr
    executable: ExecutableProvenance
    repository_audit: RepositoryAudit
    process_group: ProcessGroupOutcome
    bounds: ConfiguredBounds
    exceeded_bound: Literal["turns", "wall-clock"] | None
    live_boundary: LiveBoundaryEvidence | None


class LiveAttribution(RecordModel):
    host: StrictStr
    host_version: StrictStr
    model: StrictStr
    model_version: StrictStr | None
    session_id: StrictStr
    session_attribution_valid: StrictBool
    session_attribution_reason: StrictStr | None
    transcript_persisted: StrictBool

    @model_validator(mode="after")
    def validate_live(self) -> LiveAttribution:
        for value, label in (
            (self.host, "live host"),
            (self.host_version, "live host version"),
            (self.model, "live model"),
            (self.session_id, "live session id"),
        ):
            _nonempty(value, label)
        if self.transcript_persisted:
            raise ValueError("transcript persistence must be false")
        if self.session_attribution_valid == bool(self.session_attribution_reason):
            raise ValueError("session attribution needs either success or one unavailable reason")
        return self


class StageDuration(RecordModel):
    stage: StrictStr
    seconds: float

    @model_validator(mode="after")
    def validate_stage(self) -> StageDuration:
        _nonempty(self.stage, "stage duration identity")
        _duration(self.seconds, "stage duration")
        return self


class TokenAccounting(RecordModel):
    state: Literal["observed", "unavailable"]
    input: StrictInt | None
    output: StrictInt | None
    reason: StrictStr | None

    @model_validator(mode="after")
    def validate_tokens(self) -> TokenAccounting:
        if self.state == "observed":
            if (
                self.input is None
                or self.output is None
                or min(self.input, self.output) < 0
                or self.reason
            ):
                raise ValueError("observed tokens require nonnegative counts and no reason")
        elif self.input is not None or self.output is not None or not self.reason:
            raise ValueError("unavailable tokens require no counts and one reason")
        return self


class MonetaryAccounting(RecordModel):
    state: Literal["observed", "unavailable"]
    amount: StrictStr | None
    currency: StrictStr | None
    reason: StrictStr | None

    @model_validator(mode="after")
    def validate_money(self) -> MonetaryAccounting:
        if self.state == "observed":
            if not self.amount or not self.currency or self.reason:
                raise ValueError("observed monetary usage requires amount/currency and no reason")
        elif self.amount is not None or self.currency is not None or not self.reason:
            raise ValueError("unavailable monetary usage requires no value and one reason")
        return self


class UsageEvidence(RecordModel):
    wall_clock_seconds: float
    stage_durations: list[StageDuration]
    tokens: TokenAccounting
    money: MonetaryAccounting

    @model_validator(mode="after")
    def validate_usage(self) -> UsageEvidence:
        _duration(self.wall_clock_seconds, "wall-clock duration")
        stages = [entry.stage for entry in self.stage_durations]
        if len(stages) != len(set(stages)):
            raise ValueError("stage durations have duplicate identities")
        return self


class FrictionEvidence(RecordModel):
    control: Literal[
        "structural",
        "evidential",
        "textual-anchoring",
        "executable",
        "hash-consistency",
        "human-approval",
    ]
    events: list[StrictStr]
    evidence_ref_ids: list[StrictStr]


class DetectionEvidence(RecordModel):
    defect: StrictStr
    outcome: Literal["caught", "missed", "not-exercised"]
    reporting_control: StrictStr | None
    reason: StrictStr | None
    evidence_ref_ids: list[StrictStr]

    @model_validator(mode="after")
    def validate_detection(self) -> DetectionEvidence:
        if self.outcome == "caught" and not self.reporting_control:
            raise ValueError("caught lifecycle detection requires a reporting control")
        if self.outcome == "not-exercised" and not self.reason:
            raise ValueError("not-exercised lifecycle detection requires a reason")
        return self


class LifecycleEvidence(RecordModel):
    detections: list[DetectionEvidence]
    mutation_identity: StrictStr | None
    baseline_run_id: StrictStr | None

    @model_validator(mode="after")
    def validate_mutation(self) -> LifecycleEvidence:
        if (self.mutation_identity is None) != (self.baseline_run_id is None):
            raise ValueError("mutation identity and baseline reference must appear together")
        return self


class CrossCheckEvidence(RecordModel):
    id: StrictStr
    kind: Literal["positive", "negative"]
    command_identity: StrictStr
    outcome: Literal["not-consulted", "not-exercised", "incorrect", "correct"]
    reason: StrictStr
    expected_projection: JsonValue
    expected_projection_sha256: StrictStr | None
    observed_projection: JsonValue
    observed_projection_sha256: StrictStr | None
    evidence_ref_ids: list[StrictStr]

    @model_validator(mode="after")
    def validate_projections(self) -> CrossCheckEvidence:
        _nonempty(self.command_identity, "cross check command identity")
        for value, digest, label in (
            (
                self.expected_projection,
                self.expected_projection_sha256,
                "cross check expected projection",
            ),
            (
                self.observed_projection,
                self.observed_projection_sha256,
                "cross check observed projection",
            ),
        ):
            if value is None:
                if digest is not None:
                    raise ValueError(f"{label} hash exists without a projection")
            elif digest != _json_sha256(value):
                raise ValueError(f"{label} hash does not match canonical JSON")
        return self


class NegativeControlEvidence(RecordModel):
    id: StrictStr
    outcome: Literal["not-consulted", "not-exercised", "incorrect", "correct"]
    evidence_ref_ids: list[StrictStr]


class CrossEvidence(RecordModel):
    consultation: Literal["not-consulted", "not-exercised", "incorrect", "correct"]
    command_identity: StrictStr | None
    query_identity: StrictStr | None
    observed_projection: JsonValue
    observed_projection_sha256: StrictStr | None
    expected_projection: JsonValue
    expected_projection_sha256: StrictStr | None
    checks: list[CrossCheckEvidence]
    negative_controls: list[NegativeControlEvidence]
    metamorphic_relation: StrictStr | None
    source_run_ids: list[StrictStr]
    resolver_chain_identity: StrictStr

    @model_validator(mode="after")
    def validate_cross(self) -> CrossEvidence:
        _nonempty(self.resolver_chain_identity, "resolver-chain identity")
        for value, digest, label in (
            (self.observed_projection, self.observed_projection_sha256, "observed projection"),
            (self.expected_projection, self.expected_projection_sha256, "expected projection"),
        ):
            if value is None:
                if digest is not None:
                    raise ValueError(f"{label} hash exists without a projection")
            elif digest != _json_sha256(value):
                raise ValueError(f"{label} hash does not match canonical JSON")
        if self.consultation == "not-consulted" and (
            self.command_identity is not None or self.query_identity is not None
        ):
            raise ValueError("not-consulted cross evidence cannot claim a command/query")
        check_ids = [entry.id for entry in self.checks]
        negative_ids = [entry.id for entry in self.negative_controls]
        if len(check_ids) != len(set(check_ids)) or len(negative_ids) != len(set(negative_ids)):
            raise ValueError("cross check identities must be unique")
        return self


class ApprovalEvidence(RecordModel):
    provenance: Literal["not-reached", "operator", "synthetic"]
    state: Literal[
        "not-reached",
        "operator-pending",
        "operator-approved",
        "operator-rejected",
        "synthetic-approved",
        "synthetic-rejected",
    ]
    operator_record_ref: StrictStr | None
    synthetic_reference: StrictStr | None
    synthetic_excluded_from_live: StrictBool

    @model_validator(mode="after")
    def validate_approval(self) -> ApprovalEvidence:
        expected = {
            "not-reached": "not-reached",
            "operator-pending": "operator",
            "operator-approved": "operator",
            "operator-rejected": "operator",
            "synthetic-approved": "synthetic",
            "synthetic-rejected": "synthetic",
        }
        if self.provenance != expected[self.state]:
            raise ValueError("approval state and provenance are inconsistent")
        decided = self.state in {"operator-approved", "operator-rejected"}
        synthetic = self.state in {"synthetic-approved", "synthetic-rejected"}
        if decided != bool(self.operator_record_ref):
            raise ValueError("operator-decided approval requires one operator record reference")
        if synthetic != bool(self.synthetic_reference):
            raise ValueError("synthetic approval requires exactly one synthetic reference")
        return self


class ProtectedFileHash(RecordModel):
    path: StrictStr
    before: StrictStr
    after: StrictStr


class MetadataTransition(RecordModel):
    command: StrictStr
    expected: dict[str, JsonValue]
    observed: dict[str, JsonValue]


class MediationEvidence(RecordModel):
    outcomes: list[StrictStr]
    protected_file_hashes: list[ProtectedFileHash]
    intentional_transfer_stop: StrictBool
    metadata_transitions: list[MetadataTransition]
    status_check_consistent: StrictBool
    manifest_sha256: StrictStr
    sealed_request_sha256: StrictStr
    observed_identity_evidence_ref_ids: list[StrictStr]

    @model_validator(mode="after")
    def validate_mediation(self) -> MediationEvidence:
        _digest(self.manifest_sha256, "mediation manifest hash")
        _digest(self.sealed_request_sha256, "mediation sealed-request hash")
        for value in self.protected_file_hashes:
            _digest(value.before, "protected-file pre hash")
            _digest(value.after, "protected-file post hash")
        return self


class RunRecord(RecordModel):
    """One complete durable record under the only supported schema version."""

    schema_version: StrictInt
    run_id: StrictStr
    evaluation_question: EvaluationQuestion
    actor: ActorKind
    scenario_id: StrictStr | None
    item_id: StrictStr | None
    arm: Literal["baseline", "light", "full"]
    repetition: StrictInt
    started_at: StrictStr
    terminal_state: StrictStr
    results_root: ResultsRootIdentity
    corpus: CorpusProvenance
    prompt: PromptEvidence
    execution: ExecutionBoundary
    live: LiveAttribution | None
    usage: UsageEvidence
    friction: list[FrictionEvidence]
    lifecycle: LifecycleEvidence | None
    cross: CrossEvidence | None
    approval: ApprovalEvidence
    mediation: MediationEvidence | None
    evidence: list[EvidenceRef]

    @model_validator(mode="after")
    def validate_record(self) -> RunRecord:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported run record schema version {self.schema_version}")
        for value, label in (
            (self.run_id, "run id"),
            (self.started_at, "started_at"),
            (self.terminal_state, "terminal state"),
            (self.results_root.identity, "results-root identity"),
        ):
            _nonempty(value, label)
        if not self.results_root.external:
            raise ValueError("results root must have an external identity")
        if self.repetition < 0:
            raise ValueError("repetition must be nonnegative")
        if (self.scenario_id is None) == (self.item_id is None):
            raise ValueError("record requires exactly one scenario_id or item_id")
        if [entry.control for entry in self.friction] != list(_CONTROL_ORDER):
            raise ValueError("friction controls must appear once in documented order")
        evidence_ids = [entry.id for entry in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence reference identity")
        referenced = {identity for entry in self.friction for identity in entry.evidence_ref_ids}
        if self.lifecycle is not None:
            referenced.update(
                identity
                for detection in self.lifecycle.detections
                for identity in detection.evidence_ref_ids
            )
        if self.cross is not None:
            referenced.update(
                identity for check in self.cross.checks for identity in check.evidence_ref_ids
            )
            referenced.update(
                identity
                for control in self.cross.negative_controls
                for identity in control.evidence_ref_ids
            )
        if self.mediation is not None:
            referenced.update(self.mediation.observed_identity_evidence_ref_ids)
        if missing := referenced - set(evidence_ids):
            raise ValueError(f"record references missing traceable evidence ids: {sorted(missing)}")
        self._validate_question_matrix()
        self._validate_approval_matrix()
        return self

    def _validate_question_matrix(self) -> None:
        cross = self.evaluation_question is EvaluationQuestion.CROSS_RETRIEVAL
        live_question = self.evaluation_question is EvaluationQuestion.LIVE_SINGLE_INVESTIGATION
        if cross:
            if self.cross is None or self.lifecycle is not None or self.scenario_id is None:
                raise ValueError("cross-retrieval requires only cross scenario evidence")
            if self.prompt.policy not in {"assisted", "unassisted"}:
                raise ValueError("cross-retrieval prompt treatment must be assisted or unassisted")
            if self.corpus.history_condition == "not-applicable":
                raise ValueError("cross-retrieval requires a reuse history condition")
            if not self.corpus.seed_immutability.checked:
                raise ValueError("cross-retrieval requires checked seed immutability")
        else:
            if self.cross is not None or self.scenario_id is not None:
                raise ValueError("non-cross records cannot carry cross scenario evidence")
            if (
                self.prompt.policy != "standard"
                or self.corpus.history_condition != "not-applicable"
            ):
                raise ValueError(
                    "non-cross prompt treatment/provenance must be standard/not-applicable"
                )
            if live_question != (self.actor is ActorKind.LIVE):
                raise ValueError("live-single-investigation requires exactly the live actor")
            if self.lifecycle is None:
                raise ValueError(
                    "single-investigation and lifecycle records require lifecycle evidence"
                )
        if self.actor is ActorKind.LIVE:
            if self.live is None:
                raise ValueError("live attribution is required for a live actor")
            if self.execution.live_boundary is None or self.mediation is None:
                raise ValueError(
                    "live boundary and mediation evidence are required for a live actor"
                )
            if (
                self.execution.bounds.max_turns is None
                or self.execution.bounds.wall_clock_seconds is None
            ):
                raise ValueError("live actor requires configured turn and wall-clock bounds")
        elif (
            self.live is not None
            or self.execution.live_boundary is not None
            or self.mediation is not None
        ):
            raise ValueError("non-live actor cannot carry live attribution, boundary, or mediation")

    def _validate_approval_matrix(self) -> None:
        state = self.approval.state
        awaiting = self.terminal_state == "awaiting-operator-approval"
        if state == "operator-pending" and not awaiting:
            raise ValueError("operator-pending approval requires awaiting-operator-approval")
        if state == "not-reached" and awaiting:
            raise ValueError("approval not-reached conflicts with awaiting-operator-approval")
        if state in {"operator-approved", "operator-rejected"} and not awaiting:
            raise ValueError("operator decision must reference an awaiting stopped run")
        if self.actor is ActorKind.LIVE:
            if state.startswith("synthetic") or self.approval.synthetic_reference is not None:
                raise ValueError("synthetic approval is prohibited in live evidence")
            if not self.approval.synthetic_excluded_from_live:
                raise ValueError("live evidence must affirm synthetic approval exclusion")
        elif self.approval.synthetic_excluded_from_live:
            raise ValueError("non-live evidence cannot claim live synthetic exclusion")

    def to_json(self) -> str:
        """Serialize canonical UTF-8 JSON with exactly one trailing newline."""
        return _canonical_json(self.model_dump(mode="json")) + "\n"

    @classmethod
    def from_json(cls, text: str) -> RunRecord:
        """Parse only exact schema-v2 JSON; version 1 has no migration path."""
        raw = _loads_json(text)
        if not isinstance(raw, Mapping):
            raise ValueError("run record must be a JSON object")
        _reject_forbidden_fields(raw)
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported run record schema version {version}")
        _validate_exact_keys(raw, _RUN_RECORD_SCHEMA, "run record")
        try:
            return cls.model_validate(raw)
        except ValueError as error:
            raise ValueError(str(error)) from error


class RunRecordSet(RecordModel):
    """Canonical record-only report input with duplicate stable identity rejection."""

    schema_version: StrictInt
    records: tuple[RunRecord, ...]

    @model_validator(mode="after")
    def validate_set(self) -> RunRecordSet:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported run record set schema version {self.schema_version}")
        run_ids = [record.run_id for record in self.records]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("duplicate run_id in record set")
        identities = [_record_identity(record) for record in self.records]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate stable run identity in record set")
        object.__setattr__(self, "records", tuple(sorted(self.records, key=_record_order)))
        return self

    def to_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json")) + "\n"

    @classmethod
    def from_json(cls, text: str) -> RunRecordSet:
        raw = _loads_json(text)
        if not isinstance(raw, Mapping):
            raise ValueError("run record set must be a JSON object")
        _reject_forbidden_fields(raw)
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(f"unsupported run record set schema version {version}")
        _validate_exact_keys(raw, _RUN_RECORD_SET_SCHEMA, "run record set")
        records = raw.get("records")
        if not isinstance(records, list):
            raise ValueError("run record set records must be a list")
        return cls(
            schema_version=SCHEMA_VERSION,
            records=tuple(RunRecord.model_validate(v) for v in records),
        )


_RUN_RECORD_SCHEMA: Final[dict[str, Any]] = {
    "schema_version": None,
    "run_id": None,
    "evaluation_question": None,
    "actor": None,
    "scenario_id": None,
    "item_id": None,
    "arm": None,
    "repetition": None,
    "started_at": None,
    "terminal_state": None,
    "results_root": {"identity": None, "external": None},
    "corpus": {
        "version": None,
        "migration_provenance_version": None,
        "scenario_manifest_sha256": None,
        "seed_artifact_sha256": None,
        "focal_investigation": None,
        "history_condition": None,
        "seed_immutability": {
            "checked": None,
            "unchanged": None,
            "pre_sha256": None,
            "post_sha256": None,
        },
    },
    "prompt": {
        "policy": None,
        "template_id": None,
        "template_version": None,
        "template_sha256": None,
        "submitted_prompt_sha256": None,
        "built_prompt_sha256": None,
        "canonical_built_prompt": None,
        "leak_validation_passed": None,
    },
    "execution": {
        "environment_policy": None,
        "environment_policy_version": None,
        "executable": {
            "path": None,
            "sha256": None,
            "package_identity": None,
            "package_sha256": None,
        },
        "repository_audit": {"before_sha256": None, "after_sha256": None, "unchanged": None},
        "process_group": {"identity": None, "reaped": None},
        "bounds": {"max_turns": None, "wall_clock_seconds": None},
        "exceeded_bound": None,
        "live_boundary": {
            "credential_allowlist_identity": None,
            "xdg_roots": {"config": None, "data": None, "cache": None, "state": None},
            "identities": {
                "executable": {"path": None, "device": None, "inode": None, "sha256": None},
                "config": {"path": None, "device": None, "inode": None, "sha256": None},
                "plugin": {"path": None, "device": None, "inode": None, "sha256": None},
            },
            "revalidation_boundaries": None,
            "effective_config_isolated": None,
            "mediator_token_undisclosed": None,
            "subprocesses_cancelled": None,
            "subprocesses_joined": None,
        },
    },
    "live": {
        "host": None,
        "host_version": None,
        "model": None,
        "model_version": None,
        "session_id": None,
        "session_attribution_valid": None,
        "session_attribution_reason": None,
        "transcript_persisted": None,
    },
    "usage": {
        "wall_clock_seconds": None,
        "stage_durations": {"[]": {"stage": None, "seconds": None}},
        "tokens": {"state": None, "input": None, "output": None, "reason": None},
        "money": {"state": None, "amount": None, "currency": None, "reason": None},
    },
    "friction": {"[]": {"control": None, "events": None, "evidence_ref_ids": None}},
    "lifecycle": {
        "detections": {
            "[]": {
                "defect": None,
                "outcome": None,
                "reporting_control": None,
                "reason": None,
                "evidence_ref_ids": None,
            }
        },
        "mutation_identity": None,
        "baseline_run_id": None,
    },
    "cross": {
        "consultation": None,
        "command_identity": None,
        "query_identity": None,
        "observed_projection": None,
        "observed_projection_sha256": None,
        "expected_projection": None,
        "expected_projection_sha256": None,
        "checks": {
            "[]": {
                "id": None,
                "kind": None,
                "command_identity": None,
                "outcome": None,
                "reason": None,
                "expected_projection": None,
                "expected_projection_sha256": None,
                "observed_projection": None,
                "observed_projection_sha256": None,
                "evidence_ref_ids": None,
            }
        },
        "negative_controls": {"[]": {"id": None, "outcome": None, "evidence_ref_ids": None}},
        "metamorphic_relation": None,
        "source_run_ids": None,
        "resolver_chain_identity": None,
    },
    "approval": {
        "provenance": None,
        "state": None,
        "operator_record_ref": None,
        "synthetic_reference": None,
        "synthetic_excluded_from_live": None,
    },
    "mediation": {
        "outcomes": None,
        "protected_file_hashes": {"[]": {"path": None, "before": None, "after": None}},
        "intentional_transfer_stop": None,
        "metadata_transitions": {"[]": {"command": None, "expected": None, "observed": None}},
        "status_check_consistent": None,
        "manifest_sha256": None,
        "sealed_request_sha256": None,
        "observed_identity_evidence_ref_ids": None,
    },
    "evidence": {
        "[]": {
            "id": None,
            "kind": None,
            "artifact_path": None,
            "command_index": None,
            "exit_code": None,
            "structured_field": None,
        }
    },
}
_RUN_RECORD_SET_SCHEMA: Final[dict[str, Any]] = {
    "schema_version": None,
    "records": {"[]": _RUN_RECORD_SCHEMA},
}


def _validate_exact_keys(value: Any, schema: Any, label: str) -> None:
    if value is None:
        return
    if isinstance(schema, Mapping) and "[]" in schema:
        if not isinstance(value, list):
            raise ValueError(f"{label} must be a list")
        for index, entry in enumerate(value):
            _validate_exact_keys(entry, schema["[]"], f"{label}[{index}]")
        return
    if not isinstance(schema, Mapping):
        return
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    actual = set(value)
    expected = set(schema)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{label} has unexpected keys {unexpected} and missing keys {missing}")
    for key, child_schema in schema.items():
        _validate_exact_keys(value[key], child_schema, f"{label}.{key}")


def _reject_forbidden_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in _FORBIDDEN_FIELDS:
                raise ValueError(f"forbidden durable field {key!r}")
            _reject_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_forbidden_fields(child)


def _loads_json(text: str) -> Any:
    def object_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            text,
            object_pairs_hook=object_hook,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number {value}")
            ),
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_sha256(value: JsonValue) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _record_identity(record: RunRecord) -> tuple[object, ...]:
    return (
        record.evaluation_question,
        record.actor,
        record.scenario_id,
        record.item_id,
        record.arm,
        record.repetition,
        record.corpus.version,
        record.corpus.scenario_manifest_sha256,
        record.prompt.policy,
        record.prompt.template_id,
        record.prompt.template_version,
        record.prompt.template_sha256,
        None if record.cross is None else record.cross.resolver_chain_identity,
        None if record.live is None else record.live.host,
        None if record.live is None else record.live.host_version,
        None if record.live is None else record.live.model,
        None if record.live is None else record.live.model_version,
    )


def _record_order(record: RunRecord) -> tuple[object, ...]:
    return (
        _QUESTION_ORDER.index(record.evaluation_question),
        record.actor.value,
        record.scenario_id or "",
        record.item_id or "",
        ("baseline", "light", "full").index(record.arm),
        record.repetition,
        record.run_id,
    )


def _nonempty(value: str, label: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _digest(value: str, label: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be one lowercase SHA-256 digest")


def _duration(value: float, label: str, *, positive: bool = False) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    if value < 0 or (positive and value <= 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}")


def _relative_path(path: str) -> None:
    if not path or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
        raise ValueError("evidence artifact path must be stable and relative")
