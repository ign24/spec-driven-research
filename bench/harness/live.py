"""Bounded live execution through a proved, harness-owned OpenCode boundary."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from bench.harness.actor import TokenAccounting, TokenUsage, TokenUsageUnavailable
from bench.harness.enforcement import (
    BoundaryError,
    BoundedProcessRunner,
    ExecutableHandle,
    FileIdentity,
    ImmutableSession,
    LiveManifest,
    MaterializedBoundary,
    MediatorSidecar,
    OpenCodePreflight,
    ProtectedPathIdentity,
    SealedRequest,
    capture_protected_path_identity,
    load_live_manifest,
    materialize_boundary,
    preflight_opencode,
    revalidate_boundary,
    revalidate_manifest_request,
    seal_live_request,
)
from bench.harness.prompts import BuiltPrompt, LivePromptEvidence, validate_live_prompt
from bench.harness.runspace import (
    REPOSITORY_ROOT,
    ExecutionProvenance,
    Runspace,
)

LIVE_ENVIRONMENT_KEY: Final[str] = "SDR_BENCH_LIVE_ACTOR"
LIVE_CLI_FLAG: Final[str] = "--live"
_POLL_SECONDS: Final[float] = 0.02
_GRACE_SECONDS: Final[float] = 0.5
SDR_EXECUTABLE: Final[Path] = Path(sys.executable).with_name("sdr")
OPENCODE_CREDENTIAL_VARIABLES: Final[tuple[str, ...]] = (
    "OPENCODE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_GENERATIVE_AI_API_KEY",
)
_LOADER_RUNTIME_INJECTION: Final[frozenset[str]] = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "BUN_OPTIONS",
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_CONFIG_DIR",
        "HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
    }
)


class LiveError(RuntimeError):
    """Raised when bounded live execution cannot proceed safely."""


class LiveOptInError(LiveError):
    """Raised before all I/O unless both live authorization keys are present."""


def build_opencode_live_environment(space: Runspace, source: Mapping[str, str]) -> dict[str, str]:
    """Admit only fixed OpenCode credential names and reject execution-shaping input."""
    injected = sorted(name for name in source if name in _LOADER_RUNTIME_INJECTION)
    if injected:
        raise LiveError("loader/runtime environment injection is forbidden: " + ", ".join(injected))
    environment = space.env()
    environment.update(
        {name: source[name] for name in OPENCODE_CREDENTIAL_VARIABLES if name in source}
    )
    return environment


class BoundKind(StrEnum):
    TURNS = "turns"
    WALL_CLOCK = "wall-clock"


@dataclass(frozen=True)
class LiveOptIn:
    environment: bool
    cli: bool

    @property
    def enabled(self) -> bool:
        return self.environment and self.cli

    @property
    def missing_keys(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, present in (("environment", self.environment), ("cli", self.cli))
            if not present
        )


def resolve_live_opt_in(
    env: Mapping[str, str] | None = None, *, cli_opt_in: bool = False
) -> LiveOptIn:
    source = os.environ if env is None else env
    enabled = source.get(LIVE_ENVIRONMENT_KEY, "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return LiveOptIn(enabled, cli_opt_in)


@dataclass(frozen=True)
class LiveBounds:
    max_turns: int
    wall_clock_seconds: float

    def __post_init__(self) -> None:
        if type(self.max_turns) is not int or self.max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        if not isinstance(self.wall_clock_seconds, int | float) or self.wall_clock_seconds <= 0:
            raise ValueError("wall_clock_seconds must be positive")


@dataclass(frozen=True)
class MonetaryCost:
    amount: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class MonetaryCostUnavailable:
    reason: str


type MonetaryAccounting = MonetaryCost | MonetaryCostUnavailable


@dataclass(frozen=True)
class SessionAttribution:
    session_id: str | None
    attributed: bool
    reason: str | None = None


@dataclass(frozen=True)
class HostProvenance:
    host: str
    host_version: str
    model: str | None
    model_version: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class RepositoryAudit:
    before_sha256: str
    after_sha256: str
    unchanged: bool


@dataclass(frozen=True)
class LiveRunRequest:
    """Canonical prompt bound to exact materialized manifest and sealed request bytes."""

    prompt: BuiltPrompt
    space: Runspace
    manifest: LiveManifest
    sealed_request: SealedRequest

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, BuiltPrompt):
            raise TypeError("live request accepts only a canonical BuiltPrompt")
        evidence = validate_live_prompt(self.prompt, self.prompt.text.encode())
        if self.manifest.runspace_root != self.space.path.resolve(strict=True):
            raise LiveError("manifest does not identify the request runspace")
        revalidate_manifest_request(self.manifest, self.sealed_request)
        if self.sealed_request.prompt_template_sha256 != evidence.template_sha256:
            raise LiveError("sealed request template hash conflicts with canonical prompt")
        if self.sealed_request.submitted_prompt_sha256 != evidence.submitted_sha256:
            raise LiveError("sealed request submitted hash conflicts with canonical prompt")

    @property
    def workspace(self) -> Path:
        return self.manifest.runspace_root

    @property
    def bounds(self) -> LiveBounds:
        return LiveBounds(self.sealed_request.max_turns, self.sealed_request.wall_clock_seconds)

    @property
    def repetition(self) -> int:
        return self.sealed_request.repetition


def create_live_request(
    space: Runspace,
    prompt: BuiltPrompt,
    *,
    bounds: LiveBounds,
    repetition: int,
    model: str,
    results_root: Path,
    verify_probe_timeout: int = 30,
) -> LiveRunRequest:
    """Internally load materialization and seal one exact scalar execution request."""
    manifest = load_live_manifest(space)
    prompt_evidence = validate_live_prompt(prompt, prompt.text.encode())
    sealed = seal_live_request(
        space,
        manifest,
        repetition=repetition,
        max_turns=bounds.max_turns,
        wall_clock_seconds=bounds.wall_clock_seconds,
        model=model,
        results_root=results_root,
        prompt_template_sha256=prompt_evidence.template_sha256,
        submitted_prompt_sha256=prompt_evidence.submitted_sha256,
        verify_probe_timeout=verify_probe_timeout,
    )
    return LiveRunRequest(prompt, space, manifest, sealed)


@dataclass(frozen=True)
class OpenCodeConnector:
    """One fixed OpenCode executable and explicit connector credential allowlist."""

    executable: Path
    model: str
    executable_identity: FileIdentity | None = None
    executable_handle: ExecutableHandle | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip() or "/" not in self.model:
            raise ValueError("OpenCode connector requires one explicit provider/model identity")
        handle = ExecutableHandle.open(self.executable)
        identity = handle.identity
        object.__setattr__(self, "executable", identity.path)
        object.__setattr__(self, "executable_identity", identity)
        object.__setattr__(self, "executable_handle", handle)

    @property
    def name(self) -> str:
        return "opencode"

    def preflight(
        self,
        boundary: MaterializedBoundary,
        environment: Mapping[str, str] | None = None,
        *,
        deadline: float,
    ) -> OpenCodePreflight:
        assert self.executable_identity is not None
        assert self.executable_handle is not None
        self.executable_handle.revalidate()
        return preflight_opencode(
            boundary,
            self.executable,
            deadline=deadline,
            environment=environment,
            executable_handle=self.executable_handle,
        )

    def run_argv(self, prompt: BuiltPrompt) -> tuple[str, ...]:
        argv = ["run", "--format", "json"]
        argv.extend(("--model", self.model))
        argv.extend(("--agent", "pilot"))
        argv.append(prompt.text)
        return tuple(argv)

    def export_argv(self, session_id: str) -> tuple[str, ...]:
        return ("export", session_id, "--sanitize")


@dataclass(frozen=True)
class LiveSessionEvidence:
    connector: str
    host: HostProvenance
    session: SessionAttribution
    tokens: TokenAccounting
    cost: MonetaryAccounting
    working_root: Path
    wall_clock_seconds: float
    turns_observed: int
    bounds: LiveBounds
    exceeded_bound: BoundKind | None
    terminal_state: str
    approval_state: str
    intentional_stop: bool
    process_group_id: int
    process_reaped: bool
    repository_audit: RepositoryAudit
    execution_provenance: ExecutionProvenance
    boundary: MaterializedBoundary
    preflight: OpenCodePreflight
    protected_identities_before: Mapping[str, ProtectedPathIdentity]
    protected_identities_after: Mapping[str, ProtectedPathIdentity]
    prompt: LivePromptEvidence
    exit_code: int | None
    transcript_persisted: bool = False


def execute_live_session(
    connector: OpenCodeConnector,
    request: LiveRunRequest,
    *,
    opt_in: LiveOptIn,
) -> LiveSessionEvidence:
    """Preflight, run one bounded session, reap, then export its immutable identity."""
    if not opt_in.enabled:
        raise LiveOptInError(
            "live execution requires both opt-in keys; missing " + ", ".join(opt_in.missing_keys)
        )
    workspace = request.workspace.resolve(strict=True)
    if workspace != request.space.path.resolve(strict=True) or workspace.is_relative_to(
        REPOSITORY_ROOT.resolve(strict=True)
    ):
        raise LiveError("live workspace must be the declared external runspace")
    manifest = request.manifest
    revalidate_manifest_request(manifest, request.sealed_request)
    prompt_evidence = validate_live_prompt(request.prompt, request.prompt.text.encode())
    started = time.monotonic()
    deadline = started + request.bounds.wall_clock_seconds
    boundary = materialize_boundary(workspace)
    protected_paths = (
        *manifest.seed_roots,
        manifest.path,
        request.sealed_request.path,
        request.sealed_request.results_root,
        boundary.config_path,
        boundary.plugin_path,
    )
    protected_identities = tuple(capture_protected_path_identity(path) for path in protected_paths)
    # Prove isolation without exposing a connector credential to OpenCode first.
    connector.preflight(boundary, deadline=deadline)
    credential_source = {
        name: os.environ[name] for name in OPENCODE_CREDENTIAL_VARIABLES if name in os.environ
    }
    environment = build_opencode_live_environment(
        request.space,
        credential_source,
    )
    environment.update(boundary.environment)
    sdr_path = SDR_EXECUTABLE.resolve(strict=True)
    sdr_sha256 = hashlib.sha256(sdr_path.read_bytes()).hexdigest()
    sdr_environment = request.space.env()
    sdr_environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    before = _audit_repository(REPOSITORY_ROOT)
    with MediatorSidecar(
        socket_path=boundary.socket_path,
        executable=sdr_path,
        executable_sha256=sdr_sha256,
        cwd=workspace,
        environment=sdr_environment,
        slug=manifest.focal_slug,
        focal_root=manifest.focal_root,
        stage=manifest.stage,
        allowed_artifacts=tuple(Path(path.as_posix()) for path in manifest.artifacts),
        protected_paths=protected_paths,
        verify_probe_timeout=request.sealed_request.verify_probe_timeout,
        resumed_reuse=manifest.resumed_reuse,
        cross_argv=manifest.cross_argv,
        arm=manifest.arm,
        deadline=deadline,
        boundary_identities=(boundary.config_identity, boundary.plugin_identity),
        protected_identities=protected_identities,
    ) as sidecar:
        control_token = sidecar.token
        exact_preflight = connector.preflight(boundary, environment, deadline=deadline)
        if time.monotonic() >= deadline:
            raise LiveError("live wall-clock bound expired during exact preflight")
        host_version = exact_preflight.version
        protected_before = sidecar.protected_before
        host_environment = dict(environment)
        host_environment.update(sidecar.environment)
        revalidate_boundary(boundary)
        assert connector.executable_identity is not None
        assert connector.executable_handle is not None
        connector.executable_handle.revalidate()
        process = subprocess.Popen(
            (str(connector.executable), *connector.run_argv(request.prompt)),
            executable=connector.executable_handle.dispatch_path,
            pass_fds=connector.executable_handle.pass_fds,
            cwd=workspace,
            env=host_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        group = process.pid
        sidecar.bind_host_process_group(group)
        immutable_session = ImmutableSession()
        intentional_stop = False
        exceeded: BoundKind | None = None
        turns = 0
        try:
            turns, intentional_stop, exceeded = _consume_events(
                process,
                immutable_session,
                request.bounds,
                started,
                sidecar,
                forbidden_token=control_token,
            )
        finally:
            _terminate_and_reap(process, group)
            connector.executable_handle.revalidate()
            revalidate_boundary(boundary)
        protected_after = sidecar.protected_identities()
    exit_code = process.returncode
    if exceeded is not None:
        reason = f"session exceeded {exceeded.value} bound"
        attribution, tokens, cost, model, provider = _unavailable(
            immutable_session.session_id, reason
        )
    else:
        revalidate_manifest_request(manifest, request.sealed_request)
        revalidate_boundary(boundary)
        assert connector.executable_identity is not None
        assert connector.executable_handle is not None
        connector.executable_handle.revalidate()
        attribution, tokens, cost, model, provider = _export_exact(
            connector,
            immutable_session.session_id,
            environment,
            workspace,
            executable_handle=connector.executable_handle,
            deadline=deadline,
            boundary_identities=(
                connector.executable_identity,
                boundary.config_identity,
                boundary.plugin_identity,
            ),
            forbidden_token=control_token,
        )
    after = _audit_repository(REPOSITORY_ROOT)
    audit = RepositoryAudit(before, after, before == after)
    if not audit.unchanged:
        reason = "repository audit changed during live execution"
        attribution, tokens, cost, model, provider = _unavailable(
            immutable_session.session_id, reason
        )
    elapsed = time.monotonic() - started
    terminal = (
        "awaiting-operator-approval"
        if intentional_stop
        else "errored"
        if exceeded is not None or not audit.unchanged or exit_code not in (0, None)
        else "completed"
    )
    return LiveSessionEvidence(
        connector.name,
        HostProvenance(connector.name, host_version, model, None, provider),
        attribution,
        tokens,
        cost,
        workspace,
        elapsed,
        turns,
        request.bounds,
        exceeded,
        terminal,
        "operator-pending" if intentional_stop else "not-reached",
        intentional_stop,
        group,
        process.poll() is not None,
        audit,
        ExecutionProvenance(
            connector.executable_identity.path,
            connector.executable_identity.sha256,
            REPOSITORY_ROOT / "src",
            _audit_tree(REPOSITORY_ROOT / "src"),
        ),
        boundary,
        exact_preflight,
        protected_before,
        protected_after,
        prompt_evidence,
        exit_code,
    )


def _consume_events(
    process: subprocess.Popen[str],
    session: ImmutableSession,
    bounds: LiveBounds,
    started: float,
    sidecar: MediatorSidecar,
    *,
    forbidden_token: str,
) -> tuple[int, bool, BoundKind | None]:
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        if process.stdout is not None:
            for line in process.stdout:
                lines.put(line)
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    turns = 0
    intentional_stop = False
    while True:
        remaining = started + bounds.wall_clock_seconds - time.monotonic()
        if remaining <= 0:
            return turns, False, BoundKind.WALL_CLOCK
        try:
            line = lines.get(timeout=min(remaining, _POLL_SECONDS))
        except queue.Empty:
            if sidecar.transfer_reached:
                intentional_stop = True
            continue
        if line is None:
            return turns, intentional_stop or sidecar.transfer_reached, None
        if forbidden_token in line:
            raise LiveError("mediator token appeared in structured host events")
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        for identity in _session_ids(event):
            session.observe_event(identity)
        if event.get("type") == "step_finish":
            turns += 1
            if turns > bounds.max_turns:
                return turns, False, BoundKind.TURNS
        if sidecar.transfer_reached:
            intentional_stop = True


def _terminate_and_reap(process: subprocess.Popen[str], group: int) -> None:
    if process.poll() is None:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=_GRACE_SECONDS)
    if process.stdout is not None:
        process.stdout.close()


def _auxiliary_text(
    argv: tuple[str, ...],
    environment: Mapping[str, str],
    cwd: Path,
    *,
    executable_handle: ExecutableHandle,
    deadline: float,
    boundary_identities: tuple[FileIdentity, ...],
) -> str | None:
    bounded = BoundedProcessRunner(
        executable=executable_handle.identity,
        executable_handle=executable_handle,
        cwd=cwd,
        environment=environment,
        deadline=deadline,
        boundary_identities=boundary_identities,
    )
    try:
        code, stdout = bounded.run(argv)
    except BoundaryError:
        return None
    finally:
        bounded.close()
    return stdout.strip() if code == 0 else None


def _export_exact(
    connector: OpenCodeConnector,
    session_id: str | None,
    environment: Mapping[str, str],
    cwd: Path,
    *,
    executable_handle: ExecutableHandle,
    deadline: float,
    boundary_identities: tuple[FileIdentity, ...],
    forbidden_token: str,
) -> tuple[SessionAttribution, TokenAccounting, MonetaryAccounting, str | None, str | None]:
    if session_id is None:
        return _unavailable(None, "no immutable event session identity")
    text = _auxiliary_text(
        connector.export_argv(session_id),
        environment,
        cwd,
        executable_handle=executable_handle,
        deadline=deadline,
        boundary_identities=boundary_identities,
    )
    if text is None:
        return _unavailable(session_id, "exact session export is unavailable")
    if forbidden_token in text:
        return _unavailable(session_id, "mediator token appeared in exact session export")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _unavailable(session_id, "exact session export returned invalid JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
        return _unavailable(session_id, "exact session export has invalid shape")
    if payload["info"].get("id") != session_id:
        return _unavailable(session_id, "export identity conflicts with immutable event session")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return _unavailable(session_id, "exact session export omitted messages")
    exported_ids = tuple(identity for message in messages for identity in _session_ids(message))
    if not exported_ids or any(identity != session_id for identity in exported_ids):
        return _unavailable(session_id, "export record identity conflicts with session")
    assistants = [
        message["info"]
        for message in messages
        if isinstance(message, dict)
        and isinstance(message.get("info"), dict)
        and message["info"].get("role") == "assistant"
    ]
    if not assistants or any(info.get("sessionID") != session_id for info in assistants):
        return _unavailable(session_id, "assistant export identity conflicts with session")
    try:
        inputs = sum(_count(info, "input") for info in assistants)
        outputs = sum(_count(info, "output") for info in assistants)
        amount = sum((Decimal(str(info["cost"])) for info in assistants), Decimal(0))
    except (KeyError, ValueError, InvalidOperation):
        return _unavailable(session_id, "exact export usage is invalid")
    providers = {info.get("providerID") for info in assistants}
    models = {info.get("modelID") for info in assistants}
    provider = next(iter(providers)) if len(providers) == 1 else None
    model_id = next(iter(models)) if len(models) == 1 else None
    model = f"{provider}/{model_id}" if provider and model_id else None
    return (
        SessionAttribution(session_id, True),
        TokenUsage(inputs, outputs, model),
        MonetaryCost(amount),
        model,
        provider if isinstance(provider, str) else None,
    )


def _session_ids(value: Any) -> tuple[str, ...]:
    identities: list[str] = []

    def visit(current: Any) -> None:
        if isinstance(current, dict):
            for key, child in current.items():
                if key in {"sessionID", "session_id"} and isinstance(child, str) and child:
                    identities.append(child)
                else:
                    visit(child)
        elif isinstance(current, list):
            for child in current:
                visit(child)

    visit(value)
    return tuple(identities)


def _count(info: Mapping[str, Any], key: str) -> int:
    tokens = info.get("tokens")
    value = tokens.get(key) if isinstance(tokens, dict) else None
    if type(value) is not int or value < 0:
        raise ValueError("invalid token count")
    return value


def _unavailable(
    session_id: str | None, reason: str
) -> tuple[SessionAttribution, TokenAccounting, MonetaryAccounting, None, None]:
    return (
        SessionAttribution(session_id, False, reason),
        TokenUsageUnavailable(reason),
        MonetaryCostUnavailable(reason),
        None,
        None,
    )


def _audit_repository(root: Path) -> str:
    """Hash Git identity plus all worktree file bytes without storing their paths."""
    digest = hashlib.sha256()
    inventories = (
        ("git", "ls-files", "-z", "--cached"),
        ("git", "ls-files", "-z", "--others", "--exclude-standard"),
        ("git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard"),
    )
    entries: set[bytes] = set()
    for argv in inventories:
        completed = subprocess.run(argv, cwd=root, capture_output=True, check=False)
        if completed.returncode != 0:
            raise LiveError("repository audit requires a Git root")
        entries.update(part for part in completed.stdout.split(b"\0") if part)
    for raw in sorted(entries):
        path = root / os.fsdecode(raw)
        digest.update(raw)
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def audit_repository(root: Path = REPOSITORY_ROOT) -> str:
    """Return the repository audit digest used by durable harness records."""
    return _audit_repository(root)


def _audit_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()
