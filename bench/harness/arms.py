"""Arm execution: which arms an item is run in, how often, and what each run yields.

Three arms, declared once in :mod:`bench.harness.actor` and imported here:

- ``baseline``: research output produced without lifecycle gates.
- ``light``: the SDR light mode, ``intake -> explore -> transfer -> reuse``.
- ``full``: the SDR full mode, which adds ``probe``.

Applicability
-------------

A corpus item declares the lifecycle mode it was authored for, and its replayed
commands hard-code that mode. An item is therefore applicable to the arm matching
its mode and to the mode-free baseline, and to nothing else. Considering a
``light`` item for the ``full`` arm is not a failed run, and it is not a run that
was quietly skipped either: :class:`ArmOutcome` makes *not applicable* a third
outcome next to executed and errored, and one visible record is emitted so a
report can distinguish "did not apply" from "ran and found nothing".

Repetitions
-----------

``repetitions=N`` produces exactly N run records per applicable arm, each carrying
its repetition index. A non-applicable arm produces exactly one record: repeating
something that never ran would fabricate volume.

Isolation
---------

Lifecycle metadata has no concurrency control, so every run gets its own
disposable, Git-initialized research root through
:func:`bench.harness.runspace.map_isolated`. No two runs, concurrent or not, ever
share a research root or an ``sdr.yaml``.

Detection basis
---------------

Under the scripted actor the baseline arm is degenerate: no gate runs, so every
planted defect is missed by construction. That is a control constant, not a
measured detection rate (Decision 2). The actor labels it, this module refuses to
accept a scripted baseline result labelled ``measured``, and
:attr:`ArmRun.counts_toward_detection_rate` is the single predicate a report must
use before turning runs into a rate. Runs that did not execute carry no basis at
all: :attr:`ArmRun.detection_basis` is ``None`` for not-applicable and errored
runs, so they cannot be mistaken for a clean measurement.

Failure
-------

An actor that raises mid-run yields an ``errored`` record carrying the exception
text, never an executed record with empty findings. A misconfigured actor is a
different thing: :class:`bench.harness.actor.LiveActorNotConfiguredError` is not a
run failure and propagates.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import yaml

from bench.harness.actor import (
    ARMS,
    BASELINE_ARM,
    Actor,
    ActorKind,
    ActorResult,
    DetectionBasis,
    LiveActorNotConfiguredError,
    RunRequest,
    ScriptedActor,
    detection_evidence,
)
from bench.harness.corpus import Corpus, CorpusItem
from bench.harness.friction import (
    FrictionAccounting,
    claim_accounting,
    collect_friction,
    reopen_accounting,
)
from bench.harness.runspace import (
    DEFAULT_MAX_WORKERS,
    DEFAULT_PREFIX,
    META_FILE,
    ExecutionProvenance,
    Runspace,
    map_isolated,
)

#: One repetition by default. Variance is only observable above one.
DEFAULT_REPETITIONS: Final[int] = 1
ACTOR_EVIDENCE_PATH: Final[str] = "evidence/actor.json"
CORPUS_EVIDENCE_PATH: Final[str] = "evidence/corpus-item.json"
FILESYSTEM_EVIDENCE_PATH: Final[str] = "evidence/filesystem.json"
PROSE_EVIDENCE_PATH: Final[str] = "evidence/prose.json"
ERROR_EVIDENCE_PATH: Final[str] = "evidence/error.json"

_BASELINE_APPLICABLE_REASON: Final[str] = (
    "the no-SDR baseline runs no lifecycle, so it applies to every item regardless of mode"
)


class ArmExecutionError(RuntimeError):
    """Raised when arm execution is asked for something it cannot honestly produce."""


class ArmOutcome(StrEnum):
    """What happened to one planned run. Not applicable is a third outcome."""

    EXECUTED = "executed"
    NOT_APPLICABLE = "not-applicable"
    ERRORED = "errored"


@dataclass(frozen=True)
class Applicability:
    """Whether an item runs in an arm, with the reason spelled out either way."""

    applicable: bool
    reason: str


@dataclass(frozen=True)
class TerminalState:
    """The lifecycle state left behind by a run, read from its ``sdr.yaml``.

    ``recorded`` is false when no lifecycle metadata exists, which is the normal
    case for the baseline arm: it creates no investigation, so it has no stage.
    """

    recorded: bool
    stage: str | None = None
    status: str | None = None
    mode: str | None = None
    detail: str | None = None


ABSENT_TERMINAL_STATE: Final[TerminalState] = TerminalState(
    recorded=False, detail="no lifecycle metadata was written by this run"
)


@dataclass(frozen=True)
class PlannedRun:
    """One item, in one arm, at one repetition, before anything is executed."""

    item: CorpusItem
    arm: str
    repetition: int
    applicability: Applicability


@dataclass(frozen=True)
class EvidenceArtifact:
    """Canonical artifact content retained after its disposable runspace is removed."""

    path: str
    media_type: str
    content: str


@dataclass(frozen=True)
class ArmRun:
    """The record of one planned run: its identity, its outcome, and its evidence."""

    item_id: str
    arm: str
    repetition: int
    actor: ActorKind
    outcome: ArmOutcome
    terminal_state: TerminalState
    result: ActorResult | None = None
    detection_basis: DetectionBasis | None = None
    detection_basis_reason: str | None = None
    not_applicable_reason: str | None = None
    error: str | None = None
    research_root: Path | None = None
    friction: FrictionAccounting | None = None
    evidence_artifacts: tuple[EvidenceArtifact, ...] = ()

    @property
    def is_applicable(self) -> bool:
        """False only when the item does not apply to this arm at all."""
        return self.outcome is not ArmOutcome.NOT_APPLICABLE

    @property
    def is_failure(self) -> bool:
        """True only when an applicable run was attempted and could not complete."""
        return self.outcome is ArmOutcome.ERRORED

    @property
    def is_control_constant(self) -> bool:
        """True when the outcome is true by construction and must not become a rate."""
        return self.detection_basis is DetectionBasis.CONTROL_CONSTANT

    @property
    def counts_toward_detection_rate(self) -> bool:
        """The only predicate a report may use before aggregating detection."""
        return self.outcome is ArmOutcome.EXECUTED and self.detection_basis is (
            DetectionBasis.MEASURED
        )


def arm_applicability(item: CorpusItem, arm: str) -> Applicability:
    """Whether `item` runs in `arm`, with the reason stated in both directions."""
    if arm not in ARMS:
        raise ArmExecutionError(f"unknown arm {arm!r}; expected one of {ARMS}")
    if arm == BASELINE_ARM:
        return Applicability(applicable=True, reason=_BASELINE_APPLICABLE_REASON)
    if arm == item.mode:
        return Applicability(
            applicable=True,
            reason=f"item {item.id!r} declares mode {item.mode!r}, which this arm exercises",
        )
    return Applicability(
        applicable=False,
        reason=(
            f"item {item.id!r} declares mode {item.mode!r} and its replayed commands run that "
            f"mode, so the {arm!r} arm does not apply to it"
        ),
    )


def is_applicable(item: CorpusItem, arm: str) -> bool:
    """Shorthand for :func:`arm_applicability` when the reason is not needed."""
    return arm_applicability(item, arm).applicable


def plan_runs(
    items: Corpus | Iterable[CorpusItem],
    arms: Sequence[str] = ARMS,
    repetitions: int = DEFAULT_REPETITIONS,
) -> tuple[PlannedRun, ...]:
    """Enumerate every run to execute, ordered by item id, then arm, then repetition.

    Applicable arms yield `repetitions` entries; a non-applicable arm yields one,
    so it stays visible in the results without pretending to have run N times.
    Items are sorted by id, so the plan does not depend on the caller's order.
    """
    if repetitions < 1:
        raise ValueError(f"repetitions must be at least 1: {repetitions}")
    planned: list[PlannedRun] = []
    for item in sorted(_items_of(items), key=lambda entry: entry.id):
        for arm in arms:
            applicability = arm_applicability(item, arm)
            count = repetitions if applicability.applicable else 1
            planned.extend(
                PlannedRun(item=item, arm=arm, repetition=index, applicability=applicability)
                for index in range(count)
            )
    return tuple(planned)


def execute_arms(
    items: Corpus | Iterable[CorpusItem],
    actor: Actor | None = None,
    arms: Sequence[str] = ARMS,
    repetitions: int = DEFAULT_REPETITIONS,
    max_workers: int = DEFAULT_MAX_WORKERS,
    prefix: str = DEFAULT_PREFIX,
    parent: Path | None = None,
) -> tuple[ArmRun, ...]:
    """Execute every planned run in its own research root and return the records.

    Results keep the order of :func:`plan_runs`, independently of the order in
    which parallel workers finished.
    """
    executor: Actor = ScriptedActor() if actor is None else actor
    planned = plan_runs(items, arms=arms, repetitions=repetitions)
    works: list[Callable[[Runspace], ArmRun]] = [
        _work_for(executor, plan) for plan in planned if plan.applicability.applicable
    ]
    executed = iter(map_isolated(works, max_workers=max_workers, prefix=prefix, parent=parent))
    return tuple(
        (
            next(executed)
            if plan.applicability.applicable
            else _not_applicable_run(plan, executor.kind)
        )
        for plan in planned
    )


def runs_for_arm(runs: Sequence[ArmRun], arm: str) -> tuple[ArmRun, ...]:
    """Every record of one arm, in execution order."""
    return tuple(run for run in runs if run.arm == arm)


def applicable_runs(runs: Sequence[ArmRun]) -> tuple[ArmRun, ...]:
    """Every record whose item applied to its arm, executed or errored."""
    return tuple(run for run in runs if run.is_applicable)


def measured_runs(runs: Sequence[ArmRun]) -> tuple[ArmRun, ...]:
    """Every record a detection rate may be computed from, and no other."""
    return tuple(run for run in runs if run.counts_toward_detection_rate)


def control_constant_runs(runs: Sequence[ArmRun]) -> tuple[ArmRun, ...]:
    """Every executed record whose outcome is true by construction."""
    return tuple(
        run for run in runs if run.outcome is ArmOutcome.EXECUTED and run.is_control_constant
    )


def _items_of(items: Corpus | Iterable[CorpusItem]) -> tuple[CorpusItem, ...]:
    if isinstance(items, Corpus):
        return items.items
    return tuple(items)


def _work_for(actor: Actor, plan: PlannedRun) -> Callable[[Runspace], ArmRun]:
    def work(space: Runspace) -> ArmRun:
        request = RunRequest(item=plan.item, arm=plan.arm, repetition=plan.repetition, space=space)
        result: ActorResult | None = None
        try:
            result = actor.execute(request)
            _validate(request, result, actor)
            terminal_state = _terminal_state(space, result.slug)
            friction = collect_friction(result, git_root=space.path)
            evidence = (
                _capture_json(space, CORPUS_EVIDENCE_PATH, _item_observation(plan.item)),
                _capture_json(space, ACTOR_EVIDENCE_PATH, _actor_observation(result)),
                _capture_json(
                    space,
                    FILESYSTEM_EVIDENCE_PATH,
                    _filesystem_observation(terminal_state, friction),
                ),
                _capture_json(space, PROSE_EVIDENCE_PATH, _prose_observation(result, friction)),
            )
        except LiveActorNotConfiguredError:
            # A misconfigured actor is not a run failure. Fail loudly.
            raise
        except ArmExecutionError:
            # Invalid actor output is a harness contract violation, not run data.
            raise
        except Exception as error:  # noqa: BLE001 - a crashing run is data, not a harness stop
            return _errored_run(plan, space, actor.kind, error, result)
        return ArmRun(
            item_id=plan.item.id,
            arm=plan.arm,
            repetition=plan.repetition,
            actor=actor.kind,
            outcome=ArmOutcome.EXECUTED,
            terminal_state=terminal_state,
            result=result,
            detection_basis=result.detection_basis,
            detection_basis_reason=result.detection_basis_reason,
            research_root=space.root,
            friction=friction,
            evidence_artifacts=evidence,
        )

    return work


def _not_applicable_run(plan: PlannedRun, actor: ActorKind) -> ArmRun:
    return ArmRun(
        item_id=plan.item.id,
        arm=plan.arm,
        repetition=plan.repetition,
        actor=actor,
        outcome=ArmOutcome.NOT_APPLICABLE,
        terminal_state=TerminalState(
            recorded=False, detail="the item does not apply to this arm, so no run took place"
        ),
        not_applicable_reason=plan.applicability.reason,
        evidence_artifacts=(_json_artifact(CORPUS_EVIDENCE_PATH, _item_observation(plan.item)),),
    )


def _errored_run(
    plan: PlannedRun,
    space: Runspace,
    actor: ActorKind,
    error: Exception,
    result: ActorResult | None,
) -> ArmRun:
    error_text = _error_text(error)
    corpus_evidence = _json_artifact(CORPUS_EVIDENCE_PATH, _item_observation(plan.item))
    terminal_state = ABSENT_TERMINAL_STATE
    friction: FrictionAccounting | None = None
    try:
        terminal_state = _terminal_state(space, None if result is None else result.slug)
        if result is None:
            friction = FrictionAccounting(
                item_id=plan.item.id,
                arm=plan.arm,
                repetition=plan.repetition,
                reopens=reopen_accounting(space.path),
                gate_failures=(),
                unmapped=(),
                claims=claim_accounting(()),
            )
        else:
            friction = collect_friction(result, git_root=space.path)
        payload: dict[str, Any] = {
            "actor": actor.value,
            "error": error_text,
            "collection": {"state": "observed"},
            "filesystem": _filesystem_observation(terminal_state, friction),
        }
        evidence = _capture_json(space, ERROR_EVIDENCE_PATH, payload)
    except Exception as collection_error:  # noqa: BLE001 - failure handling must not fail
        unavailable = _error_text(collection_error)
        terminal_state = TerminalState(
            recorded=False,
            detail=f"failure evidence collection unavailable: {unavailable}",
        )
        friction = None
        evidence = _json_artifact(
            ERROR_EVIDENCE_PATH,
            {
                "actor": actor.value,
                "error": error_text,
                "collection": {"state": "unavailable", "reason": unavailable},
            },
        )
    return ArmRun(
        item_id=plan.item.id,
        arm=plan.arm,
        repetition=plan.repetition,
        actor=actor,
        outcome=ArmOutcome.ERRORED,
        terminal_state=terminal_state,
        error=error_text,
        research_root=space.root,
        friction=friction,
        evidence_artifacts=(corpus_evidence, evidence),
    )


def _validate(request: RunRequest, result: ActorResult, actor: Actor) -> None:
    """Refuse results that do not describe the run that was requested."""
    identity = (result.item_id, result.arm, result.repetition)
    expected = (request.item.id, request.arm, request.repetition)
    if identity != expected:
        raise ArmExecutionError(
            f"actor returned a result for {identity} while executing {expected}"
        )
    if result.actor is not actor.kind:
        raise ArmExecutionError(
            f"actor kind {actor.kind.value!r} returned result kind {result.actor.value!r}"
        )
    if (
        request.arm == BASELINE_ARM
        and actor.kind is ActorKind.SCRIPTED
        and result.detection_basis is not DetectionBasis.CONTROL_CONSTANT
    ):
        raise ArmExecutionError(
            "the scripted no-SDR baseline runs no gate, so its detection outcome is a control "
            f"constant, not a measured rate; item {result.item_id!r} reported "
            f"{result.detection_basis.value!r}"
        )


def _terminal_state(space: Runspace, slug: str | None) -> TerminalState:
    """Read the terminal lifecycle state from the run's own ``sdr.yaml``."""
    if slug is None:
        return ABSENT_TERMINAL_STATE
    path = space.meta_path(slug)
    if not path.is_file():
        return TerminalState(recorded=False, detail=f"no {META_FILE} was written for slug {slug!r}")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        return TerminalState(recorded=False, detail=f"unreadable {META_FILE}: {error}")
    if not isinstance(data, dict):
        return TerminalState(recorded=False, detail=f"{META_FILE} is not a mapping")
    return TerminalState(
        recorded=True,
        stage=_text(data.get("stage")),
        status=_text(data.get("status")),
        mode=_text(data.get("mode")),
    )


def _capture_json(space: Runspace, path: str, payload: Any) -> EvidenceArtifact:
    """Persist canonical evidence in the runspace and retain its content for teardown."""
    artifact = _json_artifact(path, payload)
    target = space.path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(artifact.content, encoding="utf-8")
    return artifact


def _json_artifact(path: str, payload: Any) -> EvidenceArtifact:
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return EvidenceArtifact(path=path, media_type="application/json", content=content)


def _item_observation(item: CorpusItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "mode": item.mode,
        "title": item.title,
        "question": item.question,
        "planted_defects": list(item.planted_defects),
        "expected_detection": dict(item.expected_detection),
    }


def _prose_observation(result: ActorResult, friction: FrictionAccounting) -> dict[str, Any]:
    entries = [
        {
            "command_index": failure.command_index,
            "exit_code": failure.exit_code,
            "reason": failure.detail,
        }
        for failure in friction.gate_failures
        if failure.source == "prose"
    ]
    entries.extend(
        {
            "command_index": failure.command_index,
            "exit_code": failure.exit_code,
            "reason": failure.reason,
        }
        for failure in friction.unmapped
        if 0 <= failure.command_index < len(result.commands)
        and result.commands[failure.command_index].verb in {"advance", "approve"}
    )
    return {"failures": sorted(entries, key=lambda entry: entry["command_index"])}


def _actor_observation(result: ActorResult) -> dict[str, Any]:
    if hasattr(result.tokens, "input_tokens"):
        tokens: dict[str, Any] = {
            "state": "observed",
            "input_tokens": result.tokens.input_tokens,
            "output_tokens": result.tokens.output_tokens,
            "model": result.tokens.model,
        }
    else:
        tokens = {"state": "unavailable", "reason": result.tokens.reason}
    commands = detection_evidence(result).commands
    return {
        "actor": result.actor.value,
        "commands": [
            {
                "argv": list(command.argv),
                "exit_code": command.exit_code,
                "payload": (
                    None if command.payload_json is None else json.loads(command.payload_json)
                ),
                "payload_error": command.payload_error,
                "execution_provenance": _execution_provenance_observation(
                    result.commands[index].execution_provenance
                ),
            }
            for index, command in enumerate(commands)
        ],
        "timing": {
            "total_seconds": result.duration_seconds,
            "stages": [
                {
                    "stage": boundary.stage,
                    "started_at": boundary.started_at,
                    "ended_at": boundary.ended_at,
                    "duration_seconds": boundary.duration_seconds,
                }
                for boundary in result.stages
            ],
        },
        "tokens": tokens,
    }


def _execution_provenance_observation(
    provenance: ExecutionProvenance | None,
) -> dict[str, dict[str, str]] | None:
    if provenance is None:
        return None
    return {
        "executable": {
            "identity": provenance.executable_path.name,
            "sha256": provenance.executable_sha256,
        },
        "package": {
            "identity": provenance.package_root.name,
            "sha256": provenance.package_sha256,
        },
    }


def _filesystem_observation(
    terminal: TerminalState, friction: FrictionAccounting
) -> dict[str, Any]:
    if hasattr(friction.reopens, "transitions"):
        reopens: dict[str, Any] = {
            "state": "observed",
            "transitions": [
                {
                    "slug": transition.slug,
                    "from_stage": transition.from_stage,
                    "to_stage": transition.to_stage,
                    "subject": transition.subject,
                }
                for transition in friction.reopens.transitions
            ],
        }
    else:
        reopens = {"state": "unavailable", "reason": friction.reopens.reason}
    return {
        "terminal_state": {
            "recorded": terminal.recorded,
            "stage": terminal.stage,
            "status": terminal.status,
            "mode": terminal.mode,
            "detail": terminal.detail,
        },
        "reopens": reopens,
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _error_text(error: BaseException) -> str:
    try:
        detail = str(error)
    except Exception:  # noqa: BLE001 - even a malformed exception must be recordable
        detail = "<unprintable exception>"
    return f"{type(error).__name__}: {detail}"
