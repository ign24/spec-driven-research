"""Typed durable run records with canonical JSON serialization and metric evidence."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Final

from bench.harness.actor import ARMS, ActorKind, TokenUsage, TokenUsageUnavailable
from bench.harness.arms import (
    ACTOR_EVIDENCE_PATH,
    CORPUS_EVIDENCE_PATH,
    ERROR_EVIDENCE_PATH,
    FILESYSTEM_EVIDENCE_PATH,
    PROSE_EVIDENCE_PATH,
    ArmOutcome,
    ArmRun,
    EvidenceArtifact,
)
from bench.harness.corpus import CorpusItem
from bench.harness.cost import CostUnavailable, RunCost, StageElapsed, cost_for_run
from bench.harness.detection import reporting_control_observations, score_detection
from bench.harness.friction import ReopenTrail

SCHEMA_VERSION: Final[int] = 1

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


class MetricState(StrEnum):
    """Whether a metric was observed, unavailable, or never run."""

    OBSERVED = "observed"
    UNAVAILABLE = "unavailable"
    NOT_RUN = "not-run"


class EvidenceKind(StrEnum):
    """The three evidence sources permitted by the harness contract."""

    ARTIFACT = "artifact"
    COMMAND_EXIT = "command-exit"
    STRUCTURED_CLI_FIELD = "structured-cli-field"


@dataclass(frozen=True)
class EvidenceRef:
    """A stable pointer to an artifact, command exit, or structured CLI field."""

    kind: EvidenceKind
    artifact_path: str | None = None
    command_index: int | None = None
    exit_code: int | None = None
    structured_field: str | None = None

    def __post_init__(self) -> None:
        if self.artifact_path is not None:
            _validate_artifact_path(self.artifact_path, "evidence artifact path")
        if self.command_index is not None and self.command_index < 0:
            raise ValueError("evidence command_index must be nonnegative")
        valid = (
            (
                self.kind is EvidenceKind.ARTIFACT
                and self.artifact_path is not None
                and self.command_index is None
                and self.exit_code is None
                and self.structured_field is None
            )
            or (
                self.kind is EvidenceKind.COMMAND_EXIT
                and self.artifact_path is not None
                and self.command_index is not None
                and self.exit_code is not None
                and self.structured_field is None
            )
            or (
                self.kind is EvidenceKind.STRUCTURED_CLI_FIELD
                and self.artifact_path is not None
                and self.command_index is not None
                and self.exit_code is not None
                and bool(self.structured_field)
            )
        )
        if not valid:
            raise ValueError(f"incomplete {self.kind.value} evidence reference")


@dataclass(frozen=True)
class Metric:
    """One metric whose absence cannot be confused with an observed zero or clean result."""

    name: str
    state: MetricState
    value: JsonValue
    evidence: tuple[EvidenceRef, ...]
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("metric name must be a non-empty string")
        if not isinstance(self.state, MetricState):
            raise ValueError(f"metric {self.name!r} has an invalid state")
        if not self.evidence:
            raise ValueError(f"metric {self.name!r} has no traceable evidence")
        if self.state is MetricState.OBSERVED and self.reason is not None:
            raise ValueError(f"observed metric {self.name!r} must not carry an absence reason")
        if self.state is not MetricState.OBSERVED and (self.value is not None or not self.reason):
            raise ValueError(f"absent metric {self.name!r} needs no value and an explicit reason")
        _validate_json_value(self.value, f"metric {self.name!r} value")


@dataclass(frozen=True)
class RunRecord:
    """Self-contained durable input for later report generation."""

    schema_version: int
    actor: ActorKind
    item_id: str
    arm: str
    repetition: int
    outcome: ArmOutcome
    terminal_state: Metric
    detection: Metric
    total_wall_clock: Metric
    stage_costs: Metric
    tokens: Metric
    reopens: Metric
    gate_failures: Metric
    claims: Metric
    skipped_checks: Metric
    artifacts: tuple[EvidenceArtifact, ...]
    error: str | None = None

    def __post_init__(self) -> None:
        _validate_record(self)

    @property
    def metrics(self) -> tuple[Metric, ...]:
        """Every metric field, in stable schema order."""
        return (
            self.terminal_state,
            self.detection,
            self.total_wall_clock,
            self.stage_costs,
            self.tokens,
            self.reopens,
            self.gate_failures,
            self.claims,
            self.skipped_checks,
        )

    def to_json(self) -> str:
        """Serialize as canonical UTF-8 JSON text with one trailing newline."""
        return (
            json.dumps(
                _record_data(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, text: str) -> RunRecord:
        """Load a record serialized by :meth:`to_json`, validating its typed states."""
        raw = _loads_json(text)
        if not isinstance(raw, Mapping):
            raise ValueError("run record must be a JSON object")
        return _record(raw)


@dataclass(frozen=True)
class RunRecordSet:
    """Canonical durable envelope for record-only reporting."""

    schema_version: int
    corpus_version: str
    repetitions: int
    records: tuple[RunRecord, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version must be exactly {SCHEMA_VERSION}, got {self.schema_version!r}"
            )
        if not isinstance(self.corpus_version, str) or not self.corpus_version.strip():
            raise ValueError("corpus_version must be a non-empty string")
        if (
            not isinstance(self.repetitions, int)
            or isinstance(self.repetitions, bool)
            or self.repetitions < 1
        ):
            raise ValueError("repetitions must be a positive integer")
        ordered = tuple(sorted(self.records, key=_record_identity))
        identities = tuple(_record_identity(record) for record in ordered)
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate run record identity in record set")
        if any(
            record.outcome is not ArmOutcome.NOT_APPLICABLE
            and record.repetition >= self.repetitions
            for record in ordered
        ):
            raise ValueError("run record repetition exceeds configured repetitions")
        object.__setattr__(self, "records", ordered)

    def to_json(self) -> str:
        """Serialize envelope metadata and records canonically."""
        return (
            json.dumps(
                {
                    "schema_version": self.schema_version,
                    "corpus_version": self.corpus_version,
                    "repetitions": self.repetitions,
                    "records": [_record_data(record) for record in self.records],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, text: str) -> RunRecordSet:
        """Deserialize an envelope at the reporting trust boundary."""
        raw = _loads_json(text)
        if not isinstance(raw, Mapping):
            raise ValueError("run record set must be a JSON object")
        _expect_keys(
            raw,
            {"schema_version", "corpus_version", "repetitions", "records"},
            "run record set",
        )
        return cls(
            schema_version=_integer(raw, "schema_version"),
            corpus_version=_string(raw, "corpus_version"),
            repetitions=_integer(raw, "repetitions"),
            records=tuple(_record(entry) for entry in _list(raw, "records")),
        )


def build_run_record_set(
    *,
    corpus_version: str,
    repetitions: int,
    items: tuple[CorpusItem, ...],
    runs: tuple[ArmRun, ...],
) -> RunRecordSet:
    """Build the durable reporting envelope without retaining corpus paths."""
    by_id = {item.id: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("duplicate corpus item identity")
    try:
        records = tuple(build_run_record(by_id[run.item_id], run) for run in runs)
    except KeyError as error:
        raise ValueError(f"run references unknown corpus item {error.args[0]!r}") from error
    return RunRecordSet(SCHEMA_VERSION, corpus_version, repetitions, records)


def build_run_record(item: CorpusItem, run: ArmRun) -> RunRecord:
    """Project one arm outcome into a traceable record independent of its runspace."""
    if item.id != run.item_id:
        raise ValueError(f"item {item.id!r} does not match run {run.item_id!r}")
    if run.outcome is not ArmOutcome.EXECUTED:
        state = (
            MetricState.NOT_RUN
            if run.outcome is ArmOutcome.NOT_APPLICABLE
            else MetricState.UNAVAILABLE
        )
        reason = run.not_applicable_reason or run.error or "the run produced no metrics"
        evidence = _absence_evidence(item, run)
        absent = {
            name: Metric(name=name, state=state, value=None, evidence=evidence, reason=reason)
            for name in _METRIC_NAMES
        }
        return RunRecord(
            schema_version=SCHEMA_VERSION,
            actor=run.actor,
            item_id=run.item_id,
            arm=run.arm,
            repetition=run.repetition,
            outcome=run.outcome,
            terminal_state=absent["terminal_state"],
            detection=absent["detection"],
            total_wall_clock=absent["total_wall_clock"],
            stage_costs=absent["stage_costs"],
            tokens=absent["tokens"],
            reopens=absent["reopens"],
            gate_failures=absent["gate_failures"],
            claims=absent["claims"],
            skipped_checks=absent["skipped_checks"],
            artifacts=run.evidence_artifacts,
            error=run.error,
        )

    if run.result is None or run.friction is None:
        raise ValueError("executed run lacks actor or pre-teardown friction evidence")
    actor_ref = _artifact_ref(ACTOR_EVIDENCE_PATH)
    filesystem_ref = _artifact_ref(FILESYSTEM_EVIDENCE_PATH)
    command_refs = tuple(
        EvidenceRef(
            kind=EvidenceKind.COMMAND_EXIT,
            artifact_path=ACTOR_EVIDENCE_PATH,
            command_index=index,
            exit_code=command.exit_code,
        )
        for index, command in enumerate(run.result.commands)
    )
    fallback = command_refs or (actor_ref,)
    cost = cost_for_run(run)
    if isinstance(cost, CostUnavailable):
        raise ValueError("executed run unexpectedly has unavailable cost")
    assert isinstance(cost, RunCost)

    detection = score_detection(item, run)
    detection_value: JsonValue = {
        "basis": detection.basis.value,
        "defects": [
            {
                "defect": defect.defect,
                "outcome": defect.outcome.value,
                "reporting_control": defect.reporting_control,
                "reason": defect.reason,
                "finding": None if defect.finding is None else _finding_data(defect.finding),
            }
            for defect in detection.defects
        ],
        "false_positives": [_finding_data(finding) for finding in detection.false_positives],
    }
    detection_refs = tuple(
        _cli_ref(finding.command_index, finding.exit_code, finding.json_path)
        for finding in (
            *(defect.finding for defect in detection.defects if defect.finding is not None),
            *detection.false_positives,
        )
    ) + tuple(
        _cli_ref(
            defect.skipped_observation.command_index,
            defect.skipped_observation.exit_code,
            f"{defect.skipped_observation.json_path}.skipped",
        )
        for defect in detection.defects
        if defect.skipped_observation is not None
    )
    detection_refs = detection_refs or (
        _artifact_ref(CORPUS_EVIDENCE_PATH),
        _artifact_ref(FILESYSTEM_EVIDENCE_PATH),
        *fallback,
    )

    skipped_value, skipped_refs = _skipped_checks(run)
    friction = run.friction
    if run.arm == "baseline":
        reopens = _not_run("reopens", filesystem_ref, "the baseline runs no SDR lifecycle")
    elif isinstance(friction.reopens, ReopenTrail):
        reopens = Metric(
            name="reopens",
            state=MetricState.OBSERVED,
            value=[
                {
                    "slug": entry.slug,
                    "from_stage": entry.from_stage,
                    "to_stage": entry.to_stage,
                    "subject": entry.subject,
                }
                for entry in friction.reopens.transitions
            ],
            evidence=(filesystem_ref,),
        )
    else:
        reopens = Metric(
            name="reopens",
            state=MetricState.UNAVAILABLE,
            value=None,
            evidence=(filesystem_ref,),
            reason=friction.reopens.reason,
        )

    stage_costs = (
        _not_run("stage_costs", actor_ref, "the baseline has no lifecycle stages")
        if run.arm == "baseline"
        else Metric(
            "stage_costs",
            MetricState.OBSERVED,
            [
                (
                    {"stage": stage.stage, "state": "observed", "seconds": stage.seconds}
                    if isinstance(stage, StageElapsed)
                    else {"stage": stage.stage, "state": "not-run", "reason": stage.reason}
                )
                for stage in cost.stages
            ],
            (actor_ref,),
        )
    )
    verbs = {command.verb for command in run.result.commands}
    prose_failures = tuple(
        failure
        for failure in (*friction.gate_failures, *friction.unmapped)
        if (
            getattr(failure, "source", None) == "prose"
            or (
                0 <= failure.command_index < len(run.result.commands)
                and run.result.commands[failure.command_index].verb in {"advance", "approve"}
            )
        )
    )
    prose_refs = (_artifact_ref(PROSE_EVIDENCE_PATH),) if prose_failures else ()
    gate_exit_refs = tuple(
        command_refs[index]
        for index in dict.fromkeys(failure.command_index for failure in prose_failures)
    )
    gate_failures = (
        Metric(
            "gate_failures",
            MetricState.OBSERVED,
            {
                "attributed": [
                    {
                        "control": failure.control.value,
                        "check": failure.check,
                        "detail": failure.detail,
                        "source": failure.source,
                        "command_index": failure.command_index,
                        "exit_code": failure.exit_code,
                    }
                    for failure in friction.gate_failures
                ],
                "unmapped": [
                    {
                        "reason": failure.reason,
                        "command_index": failure.command_index,
                        "exit_code": failure.exit_code,
                    }
                    for failure in friction.unmapped
                ],
            },
            prose_refs + gate_exit_refs + (command_refs or (actor_ref,)),
        )
        if verbs & {"check", "verify-claims", "verify-probe", "advance", "approve"}
        else _not_run("gate_failures", actor_ref, "no gate command ran")
    )
    claims = (
        Metric(
            "claims",
            MetricState.OBSERVED,
            {
                "recorded": friction.claims.recorded,
                "passed_anchoring": list(friction.claims.passed_anchoring),
                "human_reviewed": list(friction.claims.human_reviewed),
                "unresolved": list(friction.claims.unresolved),
                "resolve_claim_calls": [
                    {
                        "claim_id": call.claim_id,
                        "succeeded": call.succeeded,
                        "command_index": call.command_index,
                        "exit_code": call.exit_code,
                    }
                    for call in friction.claims.resolve_claim_calls
                ],
            },
            command_refs or (actor_ref,),
        )
        if verbs & {"verify-claims", "resolve-claim"}
        else _not_run("claims", actor_ref, "no claim-accounting command ran")
    )
    skipped_checks = (
        Metric("skipped_checks", MetricState.OBSERVED, skipped_value, skipped_refs or fallback)
        if "check" in verbs
        else _not_run("skipped_checks", actor_ref, "no check command ran")
    )

    return RunRecord(
        schema_version=SCHEMA_VERSION,
        actor=run.actor,
        item_id=run.item_id,
        arm=run.arm,
        repetition=run.repetition,
        outcome=run.outcome,
        terminal_state=_terminal_metric(run, filesystem_ref),
        detection=Metric("detection", MetricState.OBSERVED, detection_value, detection_refs),
        total_wall_clock=Metric(
            "total_wall_clock", MetricState.OBSERVED, cost.total_seconds, (actor_ref,)
        ),
        stage_costs=stage_costs,
        tokens=_token_metric(cost.tokens, actor_ref),
        reopens=reopens,
        gate_failures=gate_failures,
        claims=claims,
        skipped_checks=skipped_checks,
        artifacts=run.evidence_artifacts,
        error=run.error,
    )


_METRIC_NAMES: Final[tuple[str, ...]] = (
    "terminal_state",
    "detection",
    "total_wall_clock",
    "stage_costs",
    "tokens",
    "reopens",
    "gate_failures",
    "claims",
    "skipped_checks",
)


def _absence_evidence(item: CorpusItem, run: ArmRun) -> tuple[EvidenceRef, ...]:
    del item
    if any(artifact.path == CORPUS_EVIDENCE_PATH for artifact in run.evidence_artifacts):
        return (_artifact_ref(CORPUS_EVIDENCE_PATH),)
    raise ValueError("non-executed run lacks embedded corpus-item evidence")


def _artifact_ref(path: str) -> EvidenceRef:
    return EvidenceRef(kind=EvidenceKind.ARTIFACT, artifact_path=path)


def _cli_ref(index: int, exit_code: int, field: str) -> EvidenceRef:
    return EvidenceRef(
        kind=EvidenceKind.STRUCTURED_CLI_FIELD,
        artifact_path=ACTOR_EVIDENCE_PATH,
        command_index=index,
        exit_code=exit_code,
        structured_field=field,
    )


def _terminal_metric(run: ArmRun, evidence: EvidenceRef) -> Metric:
    terminal = run.terminal_state
    if terminal.recorded:
        return Metric(
            "terminal_state",
            MetricState.OBSERVED,
            {"stage": terminal.stage, "status": terminal.status, "mode": terminal.mode},
            (evidence,),
        )
    state = MetricState.NOT_RUN if run.arm == "baseline" else MetricState.UNAVAILABLE
    return Metric(
        "terminal_state",
        state,
        None,
        (evidence,),
        terminal.detail or "no terminal lifecycle state was recorded",
    )


def _token_metric(tokens: TokenUsage | TokenUsageUnavailable, evidence: EvidenceRef) -> Metric:
    if isinstance(tokens, TokenUsage):
        return Metric(
            "tokens",
            MetricState.OBSERVED,
            {
                "input_tokens": tokens.input_tokens,
                "output_tokens": tokens.output_tokens,
                "total_tokens": tokens.total_tokens,
                "model": tokens.model,
            },
            (evidence,),
        )
    return Metric("tokens", MetricState.UNAVAILABLE, None, (evidence,), reason=tokens.reason)


def _not_run(name: str, evidence: EvidenceRef, reason: str) -> Metric:
    return Metric(name, MetricState.NOT_RUN, None, (evidence,), reason=reason)


def _finding_data(finding: Any) -> dict[str, JsonValue]:
    return {
        "interface": finding.interface,
        "control": finding.control,
        "command_index": finding.command_index,
        "structured_field": finding.json_path,
        "exit_code": finding.exit_code,
        "detail": finding.detail,
    }


def _skipped_checks(run: ArmRun) -> tuple[list[JsonValue], tuple[EvidenceRef, ...]]:
    assert run.result is not None
    skipped: list[JsonValue] = []
    evidence: list[EvidenceRef] = []
    for observation in reporting_control_observations(run.result.commands):
        if observation.interface != "check" or not observation.skipped:
            continue
        skipped.append(
            {
                "check": observation.control,
                "detail": observation.detail,
                "command_index": observation.command_index,
            }
        )
        evidence.append(
            _cli_ref(
                observation.command_index,
                observation.exit_code,
                f"{observation.json_path}.skipped",
            )
        )
    return skipped, tuple(evidence)


def _record_data(record: RunRecord) -> dict[str, JsonValue]:
    return {
        "schema_version": record.schema_version,
        "actor": record.actor.value,
        "item_id": record.item_id,
        "arm": record.arm,
        "repetition": record.repetition,
        "outcome": record.outcome.value,
        "terminal_state": _metric_data(record.terminal_state),
        "detection": _metric_data(record.detection),
        "total_wall_clock": _metric_data(record.total_wall_clock),
        "stage_costs": _metric_data(record.stage_costs),
        "tokens": _metric_data(record.tokens),
        "reopens": _metric_data(record.reopens),
        "gate_failures": _metric_data(record.gate_failures),
        "claims": _metric_data(record.claims),
        "skipped_checks": _metric_data(record.skipped_checks),
        "artifacts": [
            {"path": artifact.path, "media_type": artifact.media_type, "content": artifact.content}
            for artifact in record.artifacts
        ],
        "error": record.error,
    }


def _record(raw: Any) -> RunRecord:
    if not isinstance(raw, Mapping):
        raise ValueError("run record must be a JSON object")
    _expect_keys(
        raw,
        {
            "schema_version",
            "actor",
            "item_id",
            "arm",
            "repetition",
            "outcome",
            *_METRIC_NAMES,
            "artifacts",
            "error",
        },
        "run record",
    )
    try:
        actor = ActorKind(_string(raw, "actor"))
    except ValueError as error:
        raise ValueError(f"invalid actor: {raw.get('actor')!r}") from error
    try:
        outcome = ArmOutcome(_string(raw, "outcome"))
    except ValueError as error:
        raise ValueError(f"invalid outcome: {raw.get('outcome')!r}") from error
    return RunRecord(
        schema_version=_integer(raw, "schema_version"),
        actor=actor,
        item_id=_string(raw, "item_id"),
        arm=_string(raw, "arm"),
        repetition=_integer(raw, "repetition"),
        outcome=outcome,
        terminal_state=_metric(raw, "terminal_state"),
        detection=_metric(raw, "detection"),
        total_wall_clock=_metric(raw, "total_wall_clock"),
        stage_costs=_metric(raw, "stage_costs"),
        tokens=_metric(raw, "tokens"),
        reopens=_metric(raw, "reopens"),
        gate_failures=_metric(raw, "gate_failures"),
        claims=_metric(raw, "claims"),
        skipped_checks=_metric(raw, "skipped_checks"),
        artifacts=tuple(_artifact(entry) for entry in _list(raw, "artifacts")),
        error=_optional_string(raw.get("error")),
    )


def _record_identity(record: RunRecord) -> tuple[str, str, str, int]:
    return record.actor.value, record.item_id, record.arm, record.repetition


def _validate_record(record: RunRecord) -> None:
    if record.schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"schema_version must be exactly {SCHEMA_VERSION}, got {record.schema_version!r}"
        )
    if not isinstance(record.actor, ActorKind):
        raise ValueError("invalid actor")
    if not isinstance(record.item_id, str) or not record.item_id:
        raise ValueError("item_id must be a non-empty string")
    if record.arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}, got {record.arm!r}")
    if (
        not isinstance(record.repetition, int)
        or isinstance(record.repetition, bool)
        or record.repetition < 0
    ):
        raise ValueError("repetition must be a nonnegative integer")
    if not isinstance(record.outcome, ArmOutcome):
        raise ValueError("invalid outcome")

    for expected_name, metric in zip(_METRIC_NAMES, record.metrics, strict=True):
        if metric.name != expected_name:
            raise ValueError(
                f"metric name for {expected_name!r} must be {expected_name!r}, got {metric.name!r}"
            )
    if record.outcome is ArmOutcome.NOT_APPLICABLE:
        if record.error is not None or any(
            metric.state is not MetricState.NOT_RUN for metric in record.metrics
        ):
            raise ValueError("not-applicable outcome requires no error and all metrics not-run")
    elif record.outcome is ArmOutcome.ERRORED:
        if not record.error or any(
            metric.state is not MetricState.UNAVAILABLE for metric in record.metrics
        ):
            raise ValueError("errored outcome requires an error and all metrics unavailable")
    else:
        if record.error is not None:
            raise ValueError("executed outcome must not carry an error")
        allowed_states = {
            "terminal_state": {MetricState.OBSERVED, MetricState.UNAVAILABLE, MetricState.NOT_RUN},
            "detection": {MetricState.OBSERVED},
            "total_wall_clock": {MetricState.OBSERVED},
            "stage_costs": {MetricState.OBSERVED, MetricState.NOT_RUN},
            "tokens": {MetricState.OBSERVED, MetricState.UNAVAILABLE},
            "reopens": {MetricState.OBSERVED, MetricState.UNAVAILABLE, MetricState.NOT_RUN},
            "gate_failures": {MetricState.OBSERVED, MetricState.NOT_RUN},
            "claims": {MetricState.OBSERVED, MetricState.NOT_RUN},
            "skipped_checks": {MetricState.OBSERVED, MetricState.NOT_RUN},
        }
        for metric in record.metrics:
            if metric.state not in allowed_states[metric.name]:
                raise ValueError(
                    f"executed outcome is incompatible with {metric.name!r} state "
                    f"{metric.state.value!r}"
                )

    paths: set[str] = set()
    artifacts: dict[str, EvidenceArtifact] = {}
    for artifact in record.artifacts:
        _validate_artifact_path(artifact.path, "artifact path")
        if artifact.path in paths:
            raise ValueError(f"duplicate artifact path {artifact.path!r}")
        if not artifact.media_type or not isinstance(artifact.content, str):
            raise ValueError(f"artifact {artifact.path!r} is malformed")
        paths.add(artifact.path)
        artifacts[artifact.path] = artifact
    required_paths = {CORPUS_EVIDENCE_PATH}
    if record.outcome is ArmOutcome.EXECUTED:
        required_paths.update({ACTOR_EVIDENCE_PATH, FILESYSTEM_EVIDENCE_PATH, PROSE_EVIDENCE_PATH})
    elif record.outcome is ArmOutcome.ERRORED:
        required_paths.add(ERROR_EVIDENCE_PATH)
    if missing := required_paths - paths:
        raise ValueError(f"run record is missing required embedded artifacts: {sorted(missing)}")
    for metric in record.metrics:
        for evidence in metric.evidence:
            if evidence.artifact_path not in artifacts:
                raise ValueError(
                    f"metric {metric.name!r} references missing embedded artifact "
                    f"{evidence.artifact_path!r}"
                )
            if evidence.command_index is not None:
                _validate_command_evidence(evidence, artifacts[evidence.artifact_path])

    if record.total_wall_clock.state is MetricState.OBSERVED:
        _validate_duration(record.total_wall_clock.value, "total wall-clock duration")
    if record.stage_costs.state is MetricState.OBSERVED:
        if not isinstance(record.stage_costs.value, list):
            raise ValueError("stage_costs value must be a list")
        for index, stage in enumerate(record.stage_costs.value):
            if not isinstance(stage, Mapping):
                raise ValueError(f"stage duration {index} must be an object")
            if stage.get("state") == "observed":
                _validate_duration(stage.get("seconds"), f"stage duration {index}")


def _validate_command_evidence(evidence: EvidenceRef, artifact: EvidenceArtifact) -> None:
    if artifact.media_type != "application/json":
        raise ValueError("command evidence artifact must be JSON")
    payload = _loads_json(artifact.content)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("commands"), list):
        raise ValueError("command evidence artifact has no commands list")
    commands = payload["commands"]
    assert evidence.command_index is not None
    if evidence.command_index >= len(commands):
        raise ValueError("evidence command_index is outside the embedded command list")
    command = commands[evidence.command_index]
    if not isinstance(command, Mapping) or command.get("exit_code") != evidence.exit_code:
        raise ValueError("command exit evidence contradicts the embedded command")


def _validate_duration(value: Any, label: str) -> None:
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{label} must be a finite nonnegative duration")


def _validate_artifact_path(path: str, label: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path:
        raise ValueError(f"{label} must be a stable relative POSIX path: {path!r}")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or ".." in parsed.parts
        or path != parsed.as_posix()
        or (parsed.parts and parsed.parts[0].endswith(":"))
    ):
        raise ValueError(f"{label} must be a stable relative POSIX path: {path!r}")


def _validate_json_value(value: Any, label: str) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for entry in value:
            _validate_json_value(entry, label)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for entry in value.values():
            _validate_json_value(entry, label)
        return
    raise ValueError(f"{label} is not valid JSON data")


def _metric_data(metric: Metric) -> dict[str, JsonValue]:
    return {
        "name": metric.name,
        "state": metric.state.value,
        "value": metric.value,
        "evidence": [
            {
                "kind": entry.kind.value,
                "artifact_path": entry.artifact_path,
                "command_index": entry.command_index,
                "exit_code": entry.exit_code,
                "structured_field": entry.structured_field,
            }
            for entry in metric.evidence
        ],
        "reason": metric.reason,
    }


def _metric(raw: Mapping[str, Any], key: str) -> Metric:
    value = raw.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"run record field {key!r} must be an object")
    _expect_keys(value, {"name", "state", "value", "evidence", "reason"}, f"metric {key!r}")
    evidence_raw = value.get("evidence")
    if not isinstance(evidence_raw, list):
        raise ValueError(f"metric {key!r} evidence must be a list")
    return Metric(
        name=_string(value, "name"),
        state=MetricState(_string(value, "state")),
        value=value.get("value"),
        evidence=tuple(_evidence(entry) for entry in evidence_raw),
        reason=_optional_string(value.get("reason")),
    )


def _evidence(raw: Any) -> EvidenceRef:
    if not isinstance(raw, Mapping):
        raise ValueError("evidence reference must be an object")
    _expect_keys(
        raw,
        {"kind", "artifact_path", "command_index", "exit_code", "structured_field"},
        "evidence reference",
    )
    return EvidenceRef(
        kind=EvidenceKind(_string(raw, "kind")),
        artifact_path=_optional_string(raw.get("artifact_path")),
        command_index=_optional_integer(raw.get("command_index")),
        exit_code=_optional_integer(raw.get("exit_code")),
        structured_field=_optional_string(raw.get("structured_field")),
    )


def _artifact(raw: Any) -> EvidenceArtifact:
    if not isinstance(raw, Mapping):
        raise ValueError("evidence artifact must be an object")
    _expect_keys(raw, {"path", "media_type", "content"}, "evidence artifact")
    return EvidenceArtifact(
        path=_string(raw, "path"),
        media_type=_string(raw, "media_type"),
        content=_string(raw, "content"),
    )


def _string(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"field {key!r} must be a string")
    return value


def _integer(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"field {key!r} must be an integer")
    return value


def _list(raw: Mapping[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"field {key!r} must be a list")
    return value


def _optional_string(value: Any) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError("optional string field has the wrong type")
    return value


def _optional_integer(value: Any) -> int | None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError("optional integer field has the wrong type")
    return value


def _expect_keys(raw: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{label} has unexpected keys {unexpected} and missing keys {missing}")


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
