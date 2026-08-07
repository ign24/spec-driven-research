"""Actor interface of the benchmark harness: who produces the research work of one run.

SDR observes artifacts and exit codes; it never observes an agent's reasoning or its
token usage. The harness therefore delegates "do the research work" to a pluggable
actor and asks the actor to report what the harness cannot see: stage boundaries and
token usage.

Two implementations, per Decision 1 of the change design:

- :class:`ScriptedActor` (default). Replays the artifact writes and CLI invocations
  declared in the corpus item. Deterministic, offline, free, CI-runnable. It measures
  the controls, not the workflow.
- :class:`LiveActor` (opt-in, off by default). Boundary for a real agent session that
  reports token usage and real stage durations. This module defines the boundary, the
  opt-in flag and a clear failure when it is invoked unconfigured. Driving an actual
  model is out of scope here.

Every :class:`ActorResult` names the :class:`ActorKind` that produced it, so a caller
can refuse to aggregate scripted and live results into one number.

Replay order
------------

The CLI creates the investigation tree, so ``sdr new`` must run before any artifact
exists. The scripted actor therefore replays in a fixed, documented order:

1. the leading ``new`` invocation, when the item's first declared command is ``new``;
2. every declared artifact write, in sorted path order;
3. the remaining declared invocations, in declared order.

Stage boundaries are derived from structured signals only: the timeline opens the
first stage of the item's mode once the investigation exists, and closes it and opens
the next one on every ``advance`` invocation that exits zero. A blocked ``advance``
moves nothing, exactly as the lifecycle does.

Token accounting
----------------

:data:`TokenAccounting` is a union of :class:`TokenUsage` and
:class:`TokenUsageUnavailable`. Unavailability is a distinct type, never a sentinel
integer: a run whose agent host reported nothing must never be readable as a run that
consumed zero tokens.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol, TypeGuard, runtime_checkable

from bench.harness.corpus import CorpusItem
from bench.harness.runspace import (
    REPOSITORY_ROOT,
    ExecutionProvenance,
    Runspace,
    SubprocessEnvironment,
    build_scripted_environment,
)
from sdr.schema import stage_order

STAGES: Final[tuple[str, ...]] = ("intake", "explore", "probe", "transfer", "reuse")

BASELINE_ARM: Final[str] = "baseline"
LIGHT_ARM: Final[str] = "light"
FULL_ARM: Final[str] = "full"
ARMS: Final[tuple[str, ...]] = (BASELINE_ARM, LIGHT_ARM, FULL_ARM)

#: Environment variable that opts into the live actor. Absent or falsy means off.
LIVE_ACTOR_ENV_FLAG: Final[str] = "SDR_BENCH_LIVE_ACTOR"
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})

#: Argv used to start the CLI in a subprocess without depending on an installed script.
CLI_BOOTSTRAP: Final[str] = "from sdr.cli import main; main()"

#: Commands that accept ``--offline``. The scripted actor appends it when running
#: offline so that link checks are skipped by declaration rather than by timeout.
OFFLINE_CAPABLE_COMMANDS: Final[frozenset[str]] = frozenset({"check", "advance", "approve"})

DEFAULT_COMMAND_TIMEOUT: Final[float] = 300.0

_SCRIPTED_TOKENS_REASON: Final[str] = (
    "the scripted actor replays declared commands and drives no model, so no token usage exists"
)
_BASELINE_CONTROL_CONSTANT_REASON: Final[str] = (
    "under the scripted actor the no-SDR baseline runs no gate, so every planted defect is "
    "missed by construction; this is a control constant, not a measured detection rate"
)
_REDACTED_ROOT: Final[str] = "<runspace>"


class ActorKind(StrEnum):
    """Which implementation produced a result. Results are never mixed across kinds."""

    SCRIPTED = "scripted"
    LIVE = "live"


class DetectionBasis(StrEnum):
    """Whether a result's detection outcome was measured or is true by construction."""

    MEASURED = "measured"
    CONTROL_CONSTANT = "control-constant"


@dataclass(frozen=True)
class CheckoutIntegrityEvidence:
    """Immutable post-cleanup evidence that a disposable actor preserved the checkout."""

    disposable_root: Path
    before_sha256: str
    after_sha256: str
    unchanged: bool


class ActorError(RuntimeError):
    """Raised when an actor cannot execute a run."""


class LiveActorNotConfiguredError(ActorError):
    """Raised when the live actor is invoked without an explicit opt-in and a session."""


@dataclass(frozen=True)
class TokenUsage:
    """Token usage reported by the executing agent host."""

    input_tokens: int
    output_tokens: int
    model: str | None = None

    @property
    def total_tokens(self) -> int:
        """Sum of reported input and output tokens."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class TokenUsageUnavailable:
    """Explicit absence of token accounting. Deliberately not a number."""

    reason: str


#: Either usage was reported, or it was explicitly not available. Never zero-as-unknown.
type TokenAccounting = TokenUsage | TokenUsageUnavailable

SCRIPTED_TOKENS: Final[TokenUsageUnavailable] = TokenUsageUnavailable(
    reason=_SCRIPTED_TOKENS_REASON
)


def token_usage_available(accounting: TokenAccounting) -> TypeGuard[TokenUsage]:
    """True when the accounting carries reported usage rather than an unavailable marker."""
    return isinstance(accounting, TokenUsage)


@dataclass(frozen=True)
class StageBoundary:
    """One lifecycle stage's start and end, as observed by the actor."""

    stage: str
    started_at: float
    ended_at: float

    @property
    def duration_seconds(self) -> float:
        """Wall-clock seconds the stage remained open."""
        return self.ended_at - self.started_at


@dataclass(frozen=True)
class CommandResult:
    """One replayed CLI invocation: exit code plus structured output, never prose parsing."""

    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    payload: Any | None = None
    payload_error: str | None = None
    stage: str | None = None
    offline_injected: bool = False
    duration_seconds: float = 0.0
    execution_provenance: ExecutionProvenance | None = None

    @property
    def verb(self) -> str:
        """The CLI subcommand this invocation ran."""
        return self.argv[0] if self.argv else ""

    @property
    def json_requested(self) -> bool:
        """True when the invocation asked the CLI for machine-readable output."""
        return "--json" in self.argv


@dataclass(frozen=True)
class RunRequest:
    """One unit of work: an item, in an arm, at a repetition, inside an isolated root."""

    item: CorpusItem
    arm: str
    repetition: int
    space: Runspace


@dataclass(frozen=True)
class ActorResult:
    """Everything an actor observed for one run. The unit groups 6 to 9 build on."""

    actor: ActorKind
    item_id: str
    arm: str
    repetition: int
    slug: str | None
    artifacts_written: tuple[str, ...]
    commands: tuple[CommandResult, ...]
    stages: tuple[StageBoundary, ...]
    tokens: TokenAccounting
    detection_basis: DetectionBasis
    detection_basis_reason: str
    duration_seconds: float
    workspace: Path
    research_root: Path
    checkout_integrity: CheckoutIntegrityEvidence | None = None

    @property
    def is_control_constant(self) -> bool:
        """True when detection is true by construction and must not be reported as a rate."""
        return self.detection_basis is DetectionBasis.CONTROL_CONSTANT

    def stage_durations(self) -> dict[str, float]:
        """Wall-clock seconds per stage, summed over repeated visits after a reopen."""
        durations: dict[str, float] = {}
        for boundary in self.stages:
            durations[boundary.stage] = durations.get(boundary.stage, 0.0) + (
                boundary.duration_seconds
            )
        return durations


@runtime_checkable
class Actor(Protocol):
    """Produces the research work of one run and reports what the harness cannot see."""

    @property
    def kind(self) -> ActorKind:
        """Which implementation this is. Results are labelled with it."""

    def execute(self, request: RunRequest) -> ActorResult:
        """Execute one run and report stage boundaries and token usage or unavailability."""


class EnvironmentBuilder(Protocol):
    """Construct one actor subprocess environment and capture its provenance."""

    def __call__(
        self, space: Runspace, *, executable: str | Path, package_root: Path
    ) -> SubprocessEnvironment: ...


@dataclass(frozen=True)
class CommandEvidence:
    """The deterministic, timing-free projection of one invocation used for scoring."""

    argv: tuple[str, ...]
    exit_code: int
    payload_json: str | None
    payload_error: str | None


@dataclass(frozen=True)
class DetectionEvidence:
    """Everything detection scoring may read. Excludes durations and temporary paths."""

    actor: ActorKind
    item_id: str
    arm: str
    basis: DetectionBasis
    artifacts_written: tuple[str, ...]
    commands: tuple[CommandEvidence, ...]


def detection_evidence(result: ActorResult) -> DetectionEvidence:
    """Project a result onto exit codes and structured output, with run paths redacted.

    Two scripted runs of one item differ only in their temporary root and their
    durations, so both are removed here. Whatever this returns is what detection
    scoring is allowed to depend on.
    """
    replacements = _path_replacements(result)
    commands = tuple(
        CommandEvidence(
            argv=tuple(_redact(token, replacements) for token in command.argv),
            exit_code=command.exit_code,
            payload_json=(
                None
                if command.payload is None
                else _redact(_canonical_json(command.payload), replacements)
            ),
            payload_error=(
                None
                if command.payload_error is None
                else _redact(command.payload_error, replacements)
            ),
        )
        for command in result.commands
    )
    return DetectionEvidence(
        actor=result.actor,
        item_id=result.item_id,
        arm=result.arm,
        basis=result.detection_basis,
        artifacts_written=result.artifacts_written,
        commands=commands,
    )


@dataclass(frozen=True)
class ScriptedActor:
    """Replays the artifact writes and CLI invocations declared in a corpus item."""

    python: str = sys.executable
    source_root: Path = REPOSITORY_ROOT / "src"
    offline: bool = True
    timeout: float = DEFAULT_COMMAND_TIMEOUT
    clock: Callable[[], float] = time.monotonic
    environment_builder: EnvironmentBuilder | None = None

    @property
    def kind(self) -> ActorKind:
        """This actor always produces scripted results."""
        return ActorKind.SCRIPTED

    def execute(self, request: RunRequest) -> ActorResult:
        """Replay the item inside the request's isolated root and report what happened."""
        started = self.clock()
        if request.arm == BASELINE_ARM:
            written = self._write_artifacts(request)
            return self._result(
                request,
                artifacts_written=written,
                commands=(),
                stages=(),
                basis=DetectionBasis.CONTROL_CONSTANT,
                reason=_BASELINE_CONTROL_CONSTANT_REASON,
                duration=self.clock() - started,
            )

        declared = list(request.item.commands)
        bootstrap = declared.pop(0) if declared and _verb(declared[0]) == "new" else None
        timeline = _StageTimeline(stage_order(request.item.mode), self.clock)
        commands: list[CommandResult] = []

        if bootstrap is not None:
            created = self._invoke(request, bootstrap, timeline.current)
            commands.append(created)
            if created.exit_code == 0:
                timeline.open()
        written = self._write_artifacts(request)
        if bootstrap is None and declared:
            timeline.open()
        for argv in declared:
            result = self._invoke(request, argv, timeline.current)
            commands.append(result)
            timeline.observe(result)

        return self._result(
            request,
            artifacts_written=written,
            commands=tuple(commands),
            stages=timeline.close(),
            basis=DetectionBasis.MEASURED,
            reason="detection measured from the controls exercised by the replayed commands",
            duration=self.clock() - started,
        )

    def _result(
        self,
        request: RunRequest,
        *,
        artifacts_written: tuple[str, ...],
        commands: tuple[CommandResult, ...],
        stages: tuple[StageBoundary, ...],
        basis: DetectionBasis,
        reason: str,
        duration: float,
    ) -> ActorResult:
        return ActorResult(
            actor=self.kind,
            item_id=request.item.id,
            arm=request.arm,
            repetition=request.repetition,
            slug=_declared_slug(request.item),
            artifacts_written=artifacts_written,
            commands=commands,
            stages=stages,
            tokens=SCRIPTED_TOKENS,
            detection_basis=basis,
            detection_basis_reason=reason,
            duration_seconds=duration,
            workspace=request.space.path,
            research_root=request.space.root,
        )

    def _write_artifacts(self, request: RunRequest) -> tuple[str, ...]:
        written: list[str] = []
        for relative in sorted(request.item.artifacts):
            body = request.item.artifacts[relative]
            target = request.space.root / relative
            if not target.resolve().is_relative_to(request.space.root.resolve()):
                raise ActorError(f"artifact {relative!r} escapes the run root")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            written.append(relative)
        return tuple(written)

    def _invoke(self, request: RunRequest, argv: Sequence[str], stage: str | None) -> CommandResult:
        tokens = _normalize_argv(argv)
        offline_injected = False
        if self.offline and tokens and tokens[0] in OFFLINE_CAPABLE_COMMANDS:
            if "--offline" not in tokens:
                tokens = (*tokens, "--offline")
                offline_injected = True
        builder = self.environment_builder or build_scripted_environment
        prepared = builder(
            request.space,
            executable=self.python,
            package_root=self.source_root,
        )
        started = self.clock()
        completed = subprocess.run(
            [self.python, "-c", CLI_BOOTSTRAP, *tokens],
            cwd=request.space.path,
            env=prepared.variables,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout,
        )
        duration = self.clock() - started
        payload, payload_error = _parse_payload(tokens, completed.stdout)
        return CommandResult(
            argv=tokens,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            payload=payload,
            payload_error=payload_error,
            stage=stage,
            offline_injected=offline_injected,
            duration_seconds=duration,
            execution_provenance=prepared.provenance,
        )


@dataclass(frozen=True)
class LiveSessionOutcome:
    """What a real agent session reports back to the harness."""

    commands: tuple[CommandResult, ...] = ()
    stages: tuple[StageBoundary, ...] = ()
    artifacts_written: tuple[str, ...] = ()
    tokens: TokenAccounting = field(
        default_factory=lambda: TokenUsageUnavailable(
            reason="the live session reported no token usage"
        )
    )
    slug: str | None = None


class LiveSession(Protocol):
    """Drives a real agent session for one run. Implemented outside this change."""

    def run(self, request: RunRequest) -> LiveSessionOutcome:
        """Execute the run with a real agent and report commands, stages and usage."""


@dataclass(frozen=True)
class LiveActor:
    """Opt-in actor boundary for a real agent session. Off by default.

    Constructing this actor performs no I/O, reads no API key and opens no connection.
    It refuses to execute unless the operator both opted in and supplied a session,
    so an unconfigured harness fails loudly instead of silently going online.
    """

    enabled: bool = False
    session: LiveSession | None = None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, session: LiveSession | None = None
    ) -> LiveActor:
        """Build the actor from the explicit opt-in flag, defaulting to disabled."""
        source = os.environ if env is None else env
        raw = source.get(LIVE_ACTOR_ENV_FLAG, "")
        return cls(enabled=raw.strip().lower() in _TRUTHY, session=session)

    @property
    def kind(self) -> ActorKind:
        """This actor always produces live results."""
        return ActorKind.LIVE

    def execute(self, request: RunRequest) -> ActorResult:
        """Delegate the run to the configured session, or refuse explicitly."""
        if not self.enabled:
            raise LiveActorNotConfiguredError(
                f"the live actor is opt-in and disabled; set {LIVE_ACTOR_ENV_FLAG}=1 to enable it"
            )
        if self.session is None:
            raise LiveActorNotConfiguredError(
                f"the live actor is enabled by {LIVE_ACTOR_ENV_FLAG} but no session was supplied; "
                "driving a real agent is not implemented by the harness itself"
            )
        outcome = self.session.run(request)
        return ActorResult(
            actor=self.kind,
            item_id=request.item.id,
            arm=request.arm,
            repetition=request.repetition,
            slug=outcome.slug or _declared_slug(request.item),
            artifacts_written=outcome.artifacts_written,
            commands=outcome.commands,
            stages=outcome.stages,
            tokens=outcome.tokens,
            detection_basis=DetectionBasis.MEASURED,
            detection_basis_reason="detection measured from a real agent session",
            duration_seconds=sum(stage.duration_seconds for stage in outcome.stages),
            workspace=request.space.path,
            research_root=request.space.root,
        )


class _StageTimeline:
    """Derives stage boundaries from successful ``advance`` invocations."""

    def __init__(self, order: Sequence[str], clock: Callable[[], float]) -> None:
        self._order = tuple(order)
        self._clock = clock
        self._index = -1
        self._started: float | None = None
        self._closed: list[StageBoundary] = []

    @property
    def current(self) -> str | None:
        """The stage that is open right now, if any."""
        if self._started is None or not 0 <= self._index < len(self._order):
            return None
        return self._order[self._index]

    def open(self) -> None:
        """Open the first stage of the mode, once the investigation exists."""
        if self._started is not None or not self._order:
            return
        self._index = 0
        self._started = self._clock()

    def observe(self, result: CommandResult) -> None:
        """Close the open stage and open the next one on an ``advance`` that exited zero."""
        if result.verb != "advance" or result.exit_code != 0:
            return
        self._close_open()
        if self._index + 1 < len(self._order):
            self._index += 1
            self._started = self._clock()

    def close(self) -> tuple[StageBoundary, ...]:
        """Close any open stage and return every boundary in order."""
        self._close_open()
        return tuple(self._closed)

    def _close_open(self) -> None:
        stage = self.current
        if stage is None or self._started is None:
            return
        self._closed.append(
            StageBoundary(stage=stage, started_at=self._started, ended_at=self._clock())
        )
        self._started = None


def _verb(argv: Sequence[str]) -> str:
    tokens = _normalize_argv(argv)
    return tokens[0] if tokens else ""


def _normalize_argv(argv: Sequence[str]) -> tuple[str, ...]:
    """Drop a leading ``sdr`` so corpus items may declare either form."""
    tokens = tuple(str(token) for token in argv)
    if tokens and Path(tokens[0]).name in {"sdr", "sdr.exe"}:
        tokens = tokens[1:]
    return tokens


def _declared_slug(item: CorpusItem) -> str | None:
    """The slug the item's ``new`` invocation creates, when it declares one."""
    for argv in item.commands:
        tokens = _normalize_argv(argv)
        if tokens and tokens[0] == "new":
            for token in tokens[1:]:
                if not token.startswith("-"):
                    return token
    return None


def _parse_payload(argv: Sequence[str], stdout: str) -> tuple[Any | None, str | None]:
    """Parse the CLI's JSON output when it was requested. No prose parsing anywhere."""
    if "--json" not in argv:
        return None, None
    text = stdout.strip()
    if not text:
        return None, "no output to parse as JSON"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as error:
        return None, f"invalid JSON output: {error}"


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _path_replacements(result: ActorResult) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    for path, label in (
        (result.research_root, f"{_REDACTED_ROOT}/research"),
        (result.workspace, _REDACTED_ROOT),
    ):
        for variant in {str(path), str(Path(path).resolve()), shlex.quote(str(path))}:
            candidates.append((variant, label))
    # Longest first so the research root is redacted before its parent workspace.
    return tuple(sorted(candidates, key=lambda pair: len(pair[0]), reverse=True))


def _redact(text: str, replacements: Sequence[tuple[str, str]]) -> str:
    for needle, label in replacements:
        text = text.replace(needle, label)
    return text
