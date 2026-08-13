"""Offline-verifiable OpenCode mediation for bounded live harness runs."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import stat
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final, Self

import yaml

from bench.harness.runspace import REPOSITORY_ROOT, Runspace
from sdr import schema
from sdr.research import is_valid_slug


class BoundaryError(RuntimeError):
    """Raised before dispatch when the live boundary cannot be proved or enforced."""


@dataclass(frozen=True)
class FileIdentity:
    """Canonical non-symlink file identity fixed across trust boundaries."""

    path: Path
    device: int
    inode: int
    mode: int
    size: int
    sha256: str


@dataclass(frozen=True)
class ProtectedPathIdentity:
    """Canonical file/tree root identity plus recursive content and inode digest."""

    path: Path
    device: int
    inode: int
    mode: int
    kind: str
    sha256: str


def capture_protected_path_identity(path: Path) -> ProtectedPathIdentity:
    """Capture one protected file or complete tree without following symlinks."""
    absolute = path.absolute()
    try:
        initial = absolute.lstat()
    except OSError as error:
        raise BoundaryError(f"protected path is unavailable: {absolute}") from error
    if stat.S_ISLNK(initial.st_mode):
        raise BoundaryError(f"protected path must not be a symlink: {absolute}")
    if stat.S_ISREG(initial.st_mode):
        digest = _protected_file_digest(absolute, initial)
        kind = "file"
    elif stat.S_ISDIR(initial.st_mode):
        digest = _protected_tree_digest(absolute)
        kind = "tree"
    else:
        raise BoundaryError(f"protected path must be a regular file or directory: {absolute}")
    current = absolute.lstat()
    if (current.st_dev, current.st_ino, current.st_mode) != (
        initial.st_dev,
        initial.st_ino,
        initial.st_mode,
    ):
        raise BoundaryError(f"protected path identity changed while capturing: {absolute}")
    return ProtectedPathIdentity(
        absolute,
        current.st_dev,
        current.st_ino,
        stat.S_IMODE(current.st_mode),
        kind,
        digest,
    )


def revalidate_protected_path_identity(identity: ProtectedPathIdentity) -> None:
    if capture_protected_path_identity(identity.path) != identity:
        raise BoundaryError(f"protected path identity changed: {identity.path}")


def capture_file_identity(path: Path) -> FileIdentity:
    """Capture canonical path, device/inode, mode, size, and bytes hash without aliases."""
    canonical = path.resolve(strict=True)
    if canonical.is_symlink():
        raise BoundaryError(f"canonical identity path must not be a symlink: {canonical}")
    info = canonical.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise BoundaryError(f"identity path must be a regular file: {canonical}")
    descriptor = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        digest = _sha256_fd(descriptor)
        current = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    return FileIdentity(
        canonical,
        current.st_dev,
        current.st_ino,
        stat.S_IMODE(current.st_mode),
        current.st_size,
        digest,
    )


def revalidate_file_identity(identity: FileIdentity) -> None:
    """Reject same-byte replacement, symlink substitution, mutation, or path movement."""
    current = capture_file_identity(identity.path)
    if current != identity:
        raise BoundaryError(f"file identity changed at trust boundary: {identity.path}")


class ExecutableHandle:
    """Held verified descriptor used for every executable dispatch."""

    def __init__(self, identity: FileIdentity, descriptor: int) -> None:
        self.identity = identity
        self.descriptor = descriptor
        self._closed = False

    @classmethod
    def open(cls, path: Path, *, expected_identity: FileIdentity | None = None) -> ExecutableHandle:
        canonical = path.resolve(strict=True)
        descriptor = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            current = os.fstat(descriptor)
            identity = FileIdentity(
                canonical,
                current.st_dev,
                current.st_ino,
                stat.S_IMODE(current.st_mode),
                current.st_size,
                _sha256_fd(descriptor),
            )
            if not stat.S_ISREG(current.st_mode) or not current.st_mode & 0o111:
                raise BoundaryError(f"executable must be one regular executable file: {canonical}")
            if expected_identity is not None and identity != expected_identity:
                raise BoundaryError(
                    f"executable identity changed before descriptor pin: {canonical}"
                )
            revalidate_file_identity(identity)
            return cls(identity, descriptor)
        except BaseException:
            os.close(descriptor)
            raise

    @property
    def dispatch_path(self) -> str:
        if self._closed:
            raise BoundaryError("pinned executable descriptor is closed")
        return f"/proc/self/fd/{self.descriptor}"

    @property
    def pass_fds(self) -> tuple[int, ...]:
        if self._closed:
            raise BoundaryError("pinned executable descriptor is closed")
        return (self.descriptor,)

    def revalidate(self) -> None:
        if self._closed:
            raise BoundaryError("pinned executable descriptor is closed")
        current = os.fstat(self.descriptor)
        observed = FileIdentity(
            self.identity.path,
            current.st_dev,
            current.st_ino,
            stat.S_IMODE(current.st_mode),
            current.st_size,
            _sha256_fd(self.descriptor),
        )
        if observed != self.identity:
            raise BoundaryError("held executable descriptor identity changed")
        revalidate_file_identity(self.identity)

    def close(self) -> None:
        if not self._closed:
            os.close(self.descriptor)
            self._closed = True

    def __del__(self) -> None:
        self.close()


PILOT_AGENT: Final[str] = "pilot"
LIFECYCLE_TOOL: Final[str] = "sdr_lifecycle"
ARTIFACT_TOOL: Final[str] = "sdr_artifact"
_READ_ONLY_TOOLS: Final[tuple[str, ...]] = ("read", "glob", "grep")
_REQUEST_LIMIT: Final[int] = 1024 * 1024
_SOCKET_TIMEOUT: Final[float] = 30.0

_PLUGIN_BYTES: Final[bytes] = b"""import net from "node:net"
import { tool } from "@opencode-ai/plugin"

function mediate(payload) {
  const socketPath = process.env.SDR_HARNESS_SOCKET
  const token = process.env.SDR_HARNESS_TOKEN
  if (!socketPath || !token) throw new Error("SDR mediator environment is unavailable")
  return new Promise((resolve, reject) => {
    const client = net.createConnection({ path: socketPath })
    let response = ""
    client.setEncoding("utf8")
    client.setTimeout(30000, () => client.destroy(new Error("SDR mediator timeout")))
    client.on("connect", () => client.write(JSON.stringify({ ...payload, token }) + "\\n"))
    client.on("data", (chunk) => {
      response += chunk
      if (response.includes("\\n")) client.end()
    })
    client.on("error", reject)
    client.on("close", () => {
      try {
        const parsed = JSON.parse(response.trim())
        if (!parsed.ok) reject(new Error(parsed.error || "SDR mediator refused request"))
        else resolve(JSON.stringify(parsed.result))
      } catch (error) {
        reject(error)
      }
    })
  })
}

export const SDRHarnessPlugin = async () => ({
  "tool.execute.before": async (input) => {
    if (!["read", "glob", "grep", "sdr_lifecycle", "sdr_artifact"].includes(input.tool)) {
      throw new Error(`tool ${input.tool} denied by SDR harness boundary`)
    }
    await mediate({ operation: "revalidate", tool: input.tool })
  },
  tool: {
    sdr_lifecycle: tool({
      description: "Run one declared SDR argv request through the harness mediator.",
      args: {
        verify: tool.schema.object({ action: tool.schema.literal("run") }),
        argv: tool.schema.array(tool.schema.string()),
      },
      async execute(args) {
        return mediate({ operation: "lifecycle", verify: args.verify, argv: args.argv })
      },
    }),
    sdr_artifact: tool({
      description: "Replace one declared focal artifact through the SDR mediator.",
      args: {
        path: tool.schema.string(),
        content: tool.schema.string(),
      },
      async execute(args) {
        return mediate({ operation: "artifact", path: args.path, content: args.content })
      },
    }),
  },
})
"""


@dataclass(frozen=True)
class MaterializedBoundary:
    """Exact harness-owned OpenCode configuration, plugin, and isolated roots."""

    runspace: Path
    config_root: Path
    config_path: Path
    config_bytes: bytes
    config_sha256: str
    plugin_path: Path
    plugin_bytes: bytes
    plugin_sha256: str
    config_identity: FileIdentity
    plugin_identity: FileIdentity
    socket_path: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class OpenCodePreflight:
    """Parsed real OpenCode debug evidence under the exact launch environment."""

    executable: Path
    executable_sha256: str
    version: str
    resolved_config: Mapping[str, Any]
    resolved_agent: Mapping[str, Any]
    config_sha256: str
    plugin_sha256: str
    executable_identity: FileIdentity


@dataclass(frozen=True)
class LiveManifest:
    """Exact materialized manifest bytes and internally derived filesystem identities."""

    path: Path
    manifest_bytes: bytes
    manifest_sha256: str
    manifest_identity: FileIdentity
    identity_kind: str
    identity: str
    arm: str
    focal_slug: str
    stage: str
    artifacts: tuple[PurePosixPath, ...]
    cross_argv: tuple[tuple[str, ...], ...]
    resumed_reuse: bool
    repository_root: Path
    runspace_root: Path
    research_root: Path
    focal_root: Path
    seed_roots: tuple[Path, ...]


@dataclass(frozen=True)
class SealedRequest:
    """Exact execution request bytes sealed to one manifest and canonical prompt."""

    path: Path
    request_bytes: bytes
    request_sha256: str
    request_identity: FileIdentity
    manifest_sha256: str
    repetition: int
    max_turns: int
    wall_clock_seconds: float
    model: str
    results_root: Path
    prompt_template_sha256: str
    submitted_prompt_sha256: str
    verify_probe_timeout: int


def seal_live_request(
    space: Runspace,
    manifest: LiveManifest,
    *,
    repetition: int,
    max_turns: int,
    wall_clock_seconds: float,
    model: str,
    results_root: Path,
    prompt_template_sha256: str,
    submitted_prompt_sha256: str,
    verify_probe_timeout: int = 30,
) -> SealedRequest:
    """Persist one canonical scalar request and return its exact byte identity."""
    if type(repetition) is not int or repetition < 0:
        raise BoundaryError("sealed repetition must be a non-negative integer")
    if type(max_turns) is not int or max_turns < 1:
        raise BoundaryError("sealed max turns must be positive")
    if not isinstance(wall_clock_seconds, int | float) or wall_clock_seconds <= 0:
        raise BoundaryError("sealed wall-clock bound must be positive")
    if not isinstance(model, str) or "/" not in model:
        raise BoundaryError("sealed model must be one provider/model identity")
    result_path = results_root.resolve(strict=True)
    if result_path.is_relative_to(REPOSITORY_ROOT.resolve(strict=True)):
        raise BoundaryError("sealed results root must be external to the repository")
    runspace = space.path.resolve(strict=True)
    if (
        result_path == runspace
        or result_path.is_relative_to(runspace)
        or runspace.is_relative_to(result_path)
    ):
        raise BoundaryError("sealed results root must be disjoint from the live runspace")
    payload = {
        "schema_version": 1,
        "manifest_sha256": manifest.manifest_sha256,
        "repetition": repetition,
        "max_turns": max_turns,
        "wall_clock_seconds": float(wall_clock_seconds),
        "model": model,
        "results_root": str(result_path),
        "prompt_template_sha256": prompt_template_sha256,
        "submitted_prompt_sha256": submitted_prompt_sha256,
        "verify_probe_timeout": verify_probe_timeout,
    }
    body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = space.path.resolve(strict=True) / "sealed-live-request.json"
    _write_exclusive(path, body)
    return SealedRequest(
        path,
        body,
        _sha256(body),
        capture_file_identity(path),
        manifest.manifest_sha256,
        repetition,
        max_turns,
        float(wall_clock_seconds),
        model,
        result_path,
        prompt_template_sha256,
        submitted_prompt_sha256,
        verify_probe_timeout,
    )


def revalidate_manifest_request(manifest: LiveManifest, request: SealedRequest) -> None:
    """Reparse exact bytes and compare every typed manifest/request field."""
    revalidate_file_identity(manifest.manifest_identity)
    revalidate_file_identity(request.request_identity)
    space = Runspace(
        manifest.runspace_root,
        manifest.research_root,
        manifest.runspace_root / "knowledge",
    )
    reparsed_manifest = _parse_live_manifest(
        space,
        manifest.path,
        _read_identity_bytes(manifest.manifest_identity),
        manifest.manifest_identity,
    )
    reparsed_request = _parse_sealed_request(
        request.path,
        _read_identity_bytes(request.request_identity),
        request.request_identity,
        reparsed_manifest,
    )
    if reparsed_manifest != manifest or reparsed_request != request:
        raise BoundaryError("typed manifest/request fields differ from their exact bytes")
    if reparsed_request.manifest_sha256 != reparsed_manifest.manifest_sha256:
        raise BoundaryError("sealed request does not identify the materialized manifest")


def load_live_manifest(space: Runspace) -> LiveManifest:
    """Load the fixed runspace manifest and derive every root without caller paths."""
    path = space.path.resolve(strict=True) / "live-manifest.json"
    identity = capture_file_identity(path)
    body = _read_identity_bytes(identity)
    manifest = _parse_live_manifest(space, path, body, identity)
    if capture_file_identity(path) != identity:
        raise BoundaryError("live manifest identity changed while loading")
    return manifest


def _parse_live_manifest(
    space: Runspace, path: Path, body: bytes, identity: FileIdentity
) -> LiveManifest:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise BoundaryError("live manifest is not valid JSON") from error
    required = {
        "schema_version",
        "identity_kind",
        "identity",
        "arm",
        "focal_slug",
        "seed_slugs",
        "stage",
        "artifacts",
        "cross_argv",
        "resumed_reuse",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1:
        raise BoundaryError("live manifest has an invalid exact schema")
    scalar_keys = ("identity_kind", "identity", "arm", "focal_slug", "stage")
    if not all(isinstance(payload[key], str) and payload[key] for key in scalar_keys):
        raise BoundaryError("live manifest identity fields are invalid")
    if payload["identity_kind"] not in {"scenario", "item"}:
        raise BoundaryError("live manifest identity kind is invalid")
    if payload["arm"] not in {"baseline", "light", "full"}:
        raise BoundaryError("live manifest arm is invalid")
    if payload["stage"] not in {"intake", "explore", "probe", "transfer", "reuse"}:
        raise BoundaryError("live manifest stage is invalid")
    seeds = payload["seed_slugs"]
    artifacts = payload["artifacts"]
    cross = payload["cross_argv"]
    if (
        not isinstance(seeds, list)
        or not all(isinstance(value, str) and value for value in seeds)
        or len(set(seeds)) != len(seeds)
        or not isinstance(artifacts, list)
        or not all(isinstance(value, str) and value for value in artifacts)
        or not isinstance(cross, list)
        or not all(
            isinstance(argv, list) and argv and all(isinstance(arg, str) for arg in argv)
            for argv in cross
        )
        or type(payload["resumed_reuse"]) is not bool
    ):
        raise BoundaryError("live manifest collections are invalid")
    slugs = (payload["focal_slug"], *seeds)
    if any(not is_valid_slug(slug) or Path(slug).parts != (slug,) for slug in slugs):
        raise BoundaryError("live manifest slug must be one normalized kebab-case segment")
    if payload["focal_slug"] in seeds:
        raise BoundaryError("materialized focal and seed roots must be disjoint")
    root = space.root.resolve(strict=True)
    runspace = space.path.resolve(strict=True)
    if root != (runspace / "research").resolve(strict=True):
        raise BoundaryError("live research root is not the direct runspace research child")
    focal = _direct_research_child(root, payload["focal_slug"])
    seed_roots = tuple(_direct_research_child(root, slug) for slug in seeds)
    if any(seed == focal for seed in seed_roots):
        raise BoundaryError("materialized focal and seed roots must be disjoint")
    normalized_artifacts = tuple(_safe_relative(Path(value)) for value in artifacts)
    stage_prefixes = ("brief.md", "notes/", "probe/results.md", "decision-memo.md", "assets/")
    if any(
        not any(
            artifact.as_posix() == prefix or artifact.as_posix().startswith(prefix)
            for prefix in stage_prefixes
        )
        for artifact in normalized_artifacts
    ):
        raise BoundaryError("manifest artifact is not allowed for the materialized stage")
    normalized_cross = tuple(_validate_cross_argv(tuple(argv)) for argv in cross)
    return LiveManifest(
        path,
        body,
        _sha256(body),
        identity,
        payload["identity_kind"],
        payload["identity"],
        payload["arm"],
        payload["focal_slug"],
        payload["stage"],
        normalized_artifacts,
        normalized_cross,
        payload["resumed_reuse"],
        REPOSITORY_ROOT.resolve(strict=True),
        runspace,
        root,
        focal,
        seed_roots,
    )


def _parse_sealed_request(
    path: Path,
    body: bytes,
    identity: FileIdentity,
    manifest: LiveManifest,
) -> SealedRequest:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise BoundaryError("sealed request is not valid JSON") from error
    required = {
        "schema_version",
        "manifest_sha256",
        "repetition",
        "max_turns",
        "wall_clock_seconds",
        "model",
        "results_root",
        "prompt_template_sha256",
        "submitted_prompt_sha256",
        "verify_probe_timeout",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1:
        raise BoundaryError("sealed request has an invalid exact schema")
    if type(payload["repetition"]) is not int or payload["repetition"] < 0:
        raise BoundaryError("sealed repetition must be a non-negative integer")
    if type(payload["max_turns"]) is not int or payload["max_turns"] < 1:
        raise BoundaryError("sealed max turns must be positive")
    wall_clock = payload["wall_clock_seconds"]
    if not isinstance(wall_clock, int | float) or isinstance(wall_clock, bool) or wall_clock <= 0:
        raise BoundaryError("sealed wall-clock bound must be positive")
    model = payload["model"]
    if not isinstance(model, str) or not model.strip() or "/" not in model:
        raise BoundaryError("sealed model must be one provider/model identity")
    if payload["manifest_sha256"] != manifest.manifest_sha256:
        raise BoundaryError("sealed request does not identify the materialized manifest")
    for key in ("prompt_template_sha256", "submitted_prompt_sha256"):
        value = payload[key]
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise BoundaryError(f"sealed request {key} is not one SHA-256")
    timeout = payload["verify_probe_timeout"]
    if type(timeout) is not int or timeout < 1:
        raise BoundaryError("sealed verify-probe timeout must be positive")
    raw_results = payload["results_root"]
    if not isinstance(raw_results, str) or not raw_results:
        raise BoundaryError("sealed results root is invalid")
    results = Path(raw_results).resolve(strict=True)
    repository = REPOSITORY_ROOT.resolve(strict=True)
    if results.is_relative_to(repository):
        raise BoundaryError("sealed results root must be external to the repository")
    if (
        results == manifest.runspace_root
        or results.is_relative_to(manifest.runspace_root)
        or manifest.runspace_root.is_relative_to(results)
    ):
        raise BoundaryError("sealed results root must be disjoint from the live runspace")
    return SealedRequest(
        path,
        body,
        _sha256(body),
        identity,
        payload["manifest_sha256"],
        payload["repetition"],
        payload["max_turns"],
        float(wall_clock),
        model,
        results,
        payload["prompt_template_sha256"],
        payload["submitted_prompt_sha256"],
        timeout,
    )


def _direct_research_child(root: Path, slug: str) -> Path:
    candidate = root / slug
    if candidate.is_symlink():
        raise BoundaryError("materialized investigation root must be a direct child, not a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise BoundaryError("materialized investigation root is not a direct child") from error
    if resolved.parent != root or resolved.name != slug:
        raise BoundaryError("materialized investigation root is not a direct child")
    return resolved


def _validate_cross_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    valid = argv == ("sdr", "cross", "derive", "--json") or (
        len(argv) == 5
        and argv[:3] == ("sdr", "cross", "source")
        and bool(argv[3])
        and argv[3] == argv[3].strip()
        and not argv[3].startswith("-")
        and "\x00" not in argv[3]
        and argv[4] == "--json"
    )
    if not valid:
        raise BoundaryError("manifest cross argv is not one supported exact JSON query")
    return argv


@dataclass(frozen=True)
class StagePolicy:
    """Exact stateful command policy derived from independently inspected stage."""

    stage: str
    verify_probe_timeout: int
    resumed_reuse: bool

    @classmethod
    def for_stage(
        cls, stage: str, *, verify_probe_timeout: int, resumed_reuse: bool
    ) -> StagePolicy:
        if stage not in {"intake", "explore", "probe", "transfer", "reuse"}:
            raise BoundaryError("cannot construct policy for invalid stage")
        if type(verify_probe_timeout) is not int or verify_probe_timeout < 1:
            raise BoundaryError("verify-probe timeout must be a fixed positive integer")
        return cls(stage, verify_probe_timeout, resumed_reuse)

    def allows(self, argv: tuple[str, ...]) -> bool:
        if not argv or argv[0] != "sdr":
            return False
        slug = argv[2] if len(argv) > 2 else ""
        common = {
            ("sdr", "status", slug, "--json"),
            ("sdr", "check", slug, "--offline", "--json"),
        }
        advance = ("sdr", "advance", slug, "--offline", "--no-commit")
        stage_commands: set[tuple[str, ...]] = set(common)
        if self.stage in {"intake", "explore", "probe"}:
            stage_commands.add(advance)
        if self.stage == "explore":
            stage_commands.add(("sdr", "verify-claims", slug, "--json"))
        if self.stage == "probe":
            stage_commands.add(
                (
                    "sdr",
                    "verify-probe",
                    slug,
                    "--timeout",
                    str(self.verify_probe_timeout),
                    "--json",
                )
            )
        if self.stage == "reuse" and self.resumed_reuse:
            stage_commands.add(advance)
        return argv in stage_commands


def reconcile_inspections(
    status: Mapping[str, Any],
    check: Mapping[str, Any],
    *,
    expected_slug: str,
    expected_stage: str,
    expected_status: str = "active",
) -> None:
    """Require exact status/check identity and stage semantics."""
    if status.get("slug") != expected_slug or check.get("slug") != expected_slug:
        raise BoundaryError("status/check slug identity mismatch")
    if status.get("stage") != expected_stage or check.get("stage") != expected_stage:
        raise BoundaryError("status/check stage semantics mismatch")
    if status.get("status") != expected_status:
        raise BoundaryError("status/check investigation status semantics mismatch")
    if type(status.get("gate_passed")) is not bool or type(check.get("passed")) is not bool:
        raise BoundaryError("status/check gate semantics are invalid")
    if status["gate_passed"] != check["passed"]:
        raise BoundaryError("status/check gate result semantics mismatch")
    consistency = check.get("consistency_issues")
    if consistency != []:
        raise BoundaryError("status/check consistency semantics require no issues")
    results = check.get("results")
    if not isinstance(results, list):
        raise BoundaryError("status/check result semantics are invalid")
    failures: list[str] = []
    calculated_passed = True
    for result in results:
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("check"), str)
            or type(result.get("passed")) is not bool
            or type(result.get("skipped")) is not bool
            or not isinstance(result.get("detail"), str)
        ):
            raise BoundaryError("status/check result detail semantics are invalid")
        accepted = result["passed"] or result["skipped"]
        calculated_passed = calculated_passed and accepted
        if not accepted:
            failures.append(result["detail"])
    if check["passed"] != calculated_passed:
        raise BoundaryError("status/check aggregate result semantics mismatch")
    if status.get("gate_failures") != failures:
        raise BoundaryError("status/check failure detail semantics mismatch")


def validate_metadata_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    argv: tuple[str, ...],
    *,
    exit_code: int,
    expected_stage: str | None = None,
    expected_validation_hash: str | None = None,
    command_result: Mapping[str, Any] | None = None,
    expected_date: str | None = None,
) -> bool:
    """Accept only an exact command-specific CLI-owned metadata transition."""
    if exit_code != 0:
        if dict(before) != dict(after):
            raise BoundaryError("failed command produced an unexplained metadata delta")
        return True
    verb = argv[1] if len(argv) > 1 else ""
    expected = dict(before)
    if verb == "advance":
        current_stage = before.get("stage")
        if (
            expected_stage is None
            or not isinstance(current_stage, str)
            or not isinstance(expected_validation_hash, str)
            or len(expected_validation_hash) != 64
            or expected_date is None
        ):
            raise BoundaryError("advance transition lacks exact command-derived values")
        validation = before.get("validation")
        if not isinstance(validation, dict):
            raise BoundaryError("advance transition has invalid prior validation")
        expected["validation"] = {**validation, current_stage: expected_validation_hash}
        expected["updated"] = expected_date
        if expected_stage == "done":
            expected["status"] = "done"
        else:
            expected["stage"] = expected_stage
    elif verb == "verify-probe":
        if command_result is None or expected_date is None:
            raise BoundaryError("verify-probe transition lacks exact command-derived values")
        expected["verify_probe"] = {**dict(command_result), "date": expected_date}
        expected["updated"] = expected_date
    elif verb not in {"verify-claims", "status", "check", "cross"}:
        raise BoundaryError("undeclared command has no metadata transition")
    if dict(after) != expected:
        raise BoundaryError(f"{verb or 'command'} produced a non-exact metadata transition")
    return True


class BoundedProcessRunner:
    """No-shell process-group runner with shared deadline, cancellation, and reap."""

    def __init__(
        self,
        *,
        executable: FileIdentity,
        cwd: Path,
        environment: Mapping[str, str],
        deadline: float,
        boundary_identities: Sequence[FileIdentity] = (),
        executable_handle: ExecutableHandle | None = None,
    ) -> None:
        self.executable = executable
        self.executable_handle = executable_handle or ExecutableHandle.open(
            executable.path, expected_identity=executable
        )
        self._owns_executable_handle = executable_handle is None
        self.cwd = cwd.resolve(strict=True)
        self.environment = dict(environment)
        self.deadline = deadline
        self.boundary_identities = tuple(boundary_identities)
        self._active: dict[int, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()
        self._cancelled = False

    @property
    def active_processes(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._active)

    def run(self, argv: Sequence[str]) -> tuple[int, str]:
        self._revalidate()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BoundaryError("mediator wall-clock bound expired")
        with self._lock:
            if self._cancelled:
                raise BoundaryError("mediator subprocess runner is cancelled")
            process = subprocess.Popen(
                (str(self.executable.path), *argv),
                executable=self.executable_handle.dispatch_path,
                pass_fds=self.executable_handle.pass_fds,
                cwd=self.cwd,
                env=self.environment,
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            self._active[process.pid] = process
        try:
            try:
                stdout, _ = process.communicate(timeout=remaining)
            except subprocess.TimeoutExpired as error:
                self._terminate_group(process)
                raise BoundaryError("mediator subprocess exceeded shared bound") from error
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                raise BoundaryError("mediator subprocess cancelled")
            self._revalidate()
            return process.returncode, stdout
        finally:
            if process.poll() is None:
                self._terminate_group(process)
            with self._lock:
                self._active.pop(process.pid, None)

    def cancel_all(self) -> None:
        with self._lock:
            self._cancelled = True
            processes = tuple(self._active.values())
        for process in processes:
            self._terminate_group(process)

    def close(self) -> None:
        self.cancel_all()
        if self._owns_executable_handle:
            self.executable_handle.close()

    def _revalidate(self) -> None:
        self.executable_handle.revalidate()
        for identity in self.boundary_identities:
            revalidate_file_identity(identity)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)


def materialize_boundary(runspace: Path) -> MaterializedBoundary:
    """Materialize one schema-valid explicit plugin config in an external runspace."""
    root = runspace.resolve(strict=True)
    config_root = root / ".sdr-opencode"
    config_root.mkdir(mode=0o700)
    plugin_path = config_root / "plugin.js"
    config_path = config_root / "opencode.json"
    isolation = root / ".sdr-opencode-isolation"
    xdg_config = isolation / "config"
    xdg_data = isolation / "data"
    xdg_cache = isolation / "cache"
    xdg_state = isolation / "state"
    home = isolation / "home"
    for path in (isolation, xdg_config, xdg_data, xdg_cache, xdg_state, home):
        path.mkdir(mode=0o700)
    config = {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "share": "disabled",
        "snapshot": False,
        "formatter": False,
        "lsp": False,
        "mcp": {},
        "plugin": [plugin_path.as_uri()],
        "default_agent": PILOT_AGENT,
        "tools": {
            "*": False,
            "read": True,
            "glob": True,
            "grep": True,
            LIFECYCLE_TOOL: True,
            ARTIFACT_TOOL: True,
        },
        "permission": {
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            LIFECYCLE_TOOL: "allow",
            ARTIFACT_TOOL: "allow",
            "bash": "deny",
            "edit": "deny",
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": "deny",
        },
        "agent": {
            PILOT_AGENT: {
                "description": "Dedicated isolated SDR live pilot.",
                "mode": "primary",
                "prompt": "Use only declared read-only and mediated SDR tools.",
                "tools": {
                    "*": False,
                    "read": True,
                    "glob": True,
                    "grep": True,
                    LIFECYCLE_TOOL: True,
                    ARTIFACT_TOOL: True,
                },
                "permission": {
                    "*": "deny",
                    "read": "allow",
                    "glob": "allow",
                    "grep": "allow",
                    LIFECYCLE_TOOL: "allow",
                    ARTIFACT_TOOL: "allow",
                    "bash": "deny",
                    "edit": "deny",
                    "task": "deny",
                    "webfetch": "deny",
                    "websearch": "deny",
                    "external_directory": "deny",
                },
            }
        },
    }
    config_bytes = (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_exclusive(plugin_path, _PLUGIN_BYTES)
    _write_exclusive(config_path, config_bytes)
    config_identity = capture_file_identity(config_path)
    plugin_identity = capture_file_identity(plugin_path)
    environment = MappingProxyType(
        {
            "PATH": os.defpath,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_STATE_HOME": str(xdg_state),
            "OPENCODE_CONFIG": str(config_path),
            "OPENCODE_CONFIG_DIR": str(config_root),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
        }
    )
    return MaterializedBoundary(
        root,
        config_root,
        config_path,
        config_bytes,
        _sha256(config_bytes),
        plugin_path,
        _PLUGIN_BYTES,
        _sha256(_PLUGIN_BYTES),
        config_identity,
        plugin_identity,
        config_root / "mediator.sock",
        environment,
    )


def revalidate_boundary(boundary: MaterializedBoundary) -> None:
    """Revalidate exact config/plugin identity and bytes at every OpenCode boundary."""
    revalidate_file_identity(boundary.config_identity)
    revalidate_file_identity(boundary.plugin_identity)
    if boundary.config_identity.sha256 != boundary.config_sha256:
        raise BoundaryError("OpenCode config identity hash is inconsistent")
    if boundary.plugin_identity.sha256 != boundary.plugin_sha256:
        raise BoundaryError("OpenCode plugin identity hash is inconsistent")


def preflight_opencode(
    boundary: MaterializedBoundary,
    executable: Path,
    *,
    deadline: float,
    environment: Mapping[str, str] | None = None,
    executable_handle: ExecutableHandle | None = None,
) -> OpenCodePreflight:
    """Run and parse fixed real OpenCode config and pilot-agent debug preflights."""
    owned_handle = executable_handle is None
    handle = executable_handle or ExecutableHandle.open(executable)
    executable_identity = handle.identity
    resolved_executable = executable_identity.path
    launch = dict(boundary.environment if environment is None else environment)
    if launch.get("OPENCODE_CONFIG") != str(boundary.config_path):
        raise BoundaryError("launch environment does not select the harness config")
    revalidate_boundary(boundary)
    _verify_isolated_environment(boundary, launch)
    runner = BoundedProcessRunner(
        executable=executable_identity,
        executable_handle=handle,
        cwd=boundary.runspace,
        environment=launch,
        deadline=deadline,
        boundary_identities=(boundary.config_identity, boundary.plugin_identity),
    )
    try:
        version = _run_debug(runner, ("--version",))
        config = _parse_debug_json(
            _run_debug(runner, ("debug", "config")),
            "resolved config",
        )
        agent = _parse_debug_json(
            _run_debug(runner, ("debug", "agent", PILOT_AGENT)),
            "resolved pilot agent",
        )
        _verify_resolved_config(boundary, config, agent)
        return OpenCodePreflight(
            resolved_executable,
            executable_identity.sha256,
            version.strip(),
            MappingProxyType(config),
            MappingProxyType(agent),
            boundary.config_sha256,
            boundary.plugin_sha256,
            executable_identity,
        )
    finally:
        runner.close()
        if owned_handle:
            handle.close()


class MediatorSidecar:
    """Serialized authenticated Unix-socket owner of SDR execution and writes."""

    def __init__(
        self,
        *,
        socket_path: Path,
        executable: Path,
        executable_sha256: str,
        cwd: Path,
        environment: Mapping[str, str],
        slug: str,
        focal_root: Path,
        stage: str,
        allowed_artifacts: Sequence[Path],
        protected_paths: Sequence[Path],
        host_process_group: int | None = None,
        verify_probe_timeout: int = 30,
        resumed_reuse: bool = False,
        cross_argv: Sequence[tuple[str, ...]] = (),
        arm: str = "light",
        deadline: float | None = None,
        boundary_identities: Sequence[FileIdentity] = (),
        protected_identities: Sequence[ProtectedPathIdentity] | None = None,
    ) -> None:
        self.socket_path = socket_path
        self.executable = executable.resolve(strict=True)
        self.executable_sha256 = executable_sha256
        self.cwd = cwd.resolve(strict=True)
        self._subprocess_environment = dict(environment)
        self.slug = slug
        self.stage = stage
        self.focal_root = focal_root.resolve(strict=True)
        self.allowed_artifacts = frozenset(_safe_relative(path) for path in allowed_artifacts)
        self.protected_paths = tuple(
            (self.focal_root / path if not path.is_absolute() else path).absolute()
            for path in protected_paths
        )
        self.host_process_group = host_process_group
        self.verify_probe_timeout = verify_probe_timeout
        self.resumed_reuse = resumed_reuse
        self.cross_argv = frozenset(_validate_cross_argv(tuple(argv)) for argv in cross_argv)
        if arm not in {"baseline", "light", "full"}:
            raise BoundaryError("mediated lifecycle arm is invalid")
        self.arm = arm
        self.deadline = time.monotonic() + _SOCKET_TIMEOUT if deadline is None else deadline
        self.boundary_identities = tuple(boundary_identities)
        self._declared_protected_identities = (
            None if protected_identities is None else tuple(protected_identities)
        )
        if self._declared_protected_identities is not None and {
            identity.path for identity in self._declared_protected_identities
        } != set(self.protected_paths):
            raise BoundaryError("protected identity set differs from protected paths")
        self.executable_identity = capture_file_identity(self.executable)
        if self.executable_identity.sha256 != executable_sha256:
            raise BoundaryError("resolved SDR executable hash is inconsistent")
        self.policy = StagePolicy.for_stage(
            stage,
            verify_probe_timeout=verify_probe_timeout,
            resumed_reuse=resumed_reuse,
        )
        self._runner = BoundedProcessRunner(
            executable=self.executable_identity,
            cwd=self.cwd,
            environment=self._subprocess_environment,
            deadline=self.deadline,
            boundary_identities=self.boundary_identities,
        )
        self.token = secrets.token_hex(32)
        self.environment = MappingProxyType(
            {"SDR_HARNESS_SOCKET": str(socket_path), "SDR_HARNESS_TOKEN": self.token}
        )
        self.transfer_reached = False
        self._closed_reason: str | None = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._request_lock = threading.Lock()
        self._focal_fd: int | None = None
        self._protected_before: Mapping[str, ProtectedPathIdentity] = MappingProxyType({})
        self._metadata_before: dict[str, Any] = {}
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()

    def __enter__(self) -> Self:
        revalidate_file_identity(self.executable_identity)
        for identity in self.boundary_identities:
            revalidate_file_identity(identity)
        self.socket_path.parent.resolve(strict=True)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            raise BoundaryError("mediator socket path already exists")
        captured = (
            tuple(capture_protected_path_identity(path) for path in self.protected_paths)
            if self._declared_protected_identities is None
            else self._declared_protected_identities
        )
        for identity in captured:
            revalidate_protected_path_identity(identity)
        self._protected_before = MappingProxyType(
            {str(identity.path): identity for identity in captured}
        )
        self._focal_fd = os.open(
            self.focal_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        self._metadata_before = self._read_metadata()
        status = self._inspect_status()
        check = self._inspect_check()
        reconcile_inspections(status, check, expected_slug=self.slug, expected_stage=self.stage)
        if (
            self._metadata_before.get("slug") != self.slug
            or self._metadata_before.get("stage") != self.stage
        ):
            raise BoundaryError("initial metadata conflicts with manifest stage identity")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(0.1)
        self._server = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=2):
            self.__exit__(None, None, None)
            raise BoundaryError("mediator sidecar did not start")
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._runner.cancel_all()
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        with self._workers_lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=2)
            if worker.is_alive():
                raise BoundaryError("mediator worker did not join before teardown")
        self._runner.close()
        if self._focal_fd is not None:
            os.close(self._focal_fd)
            self._focal_fd = None
        try:
            self.socket_path.unlink(missing_ok=True)
        except OSError:
            pass

    def protected_identities(self) -> Mapping[str, ProtectedPathIdentity]:
        """Capture path/hash/device/inode identities for every protected file or tree."""
        return MappingProxyType(
            {
                str(path): capture_protected_path_identity(path)
                for path in sorted(self.protected_paths)
            }
        )

    @property
    def protected_before(self) -> Mapping[str, ProtectedPathIdentity]:
        return self._protected_before

    def bind_host_process_group(self, process_group: int) -> None:
        """Bind transfer interruption once, immediately after host process creation."""
        if self.host_process_group is not None or process_group <= 0:
            raise BoundaryError("host process group binding is invalid or already set")
        self.host_process_group = process_group

    def request_for_test(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Exercise the authenticated socket path without bypassing mediation."""
        payload = dict(request)
        payload["token"] = self.token
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(_SOCKET_TIMEOUT)
            client.connect(str(self.socket_path))
            client.sendall(json.dumps(payload).encode() + b"\n")
            response = client.makefile("rb").readline(_REQUEST_LIMIT + 1)
        return json.loads(response)

    def _serve(self) -> None:
        self._ready.set()
        while not self._stop.is_set():
            try:
                assert self._server is not None
                connection, _ = self._server.accept()
            except (TimeoutError, OSError):
                continue
            worker = threading.Thread(target=self._handle_connection, args=(connection,))
            with self._workers_lock:
                self._workers.add(worker)
            worker.start()

    def _handle_connection(self, connection: socket.socket) -> None:
        try:
            with connection:
                connection.settimeout(_SOCKET_TIMEOUT)
                response = self._read_and_dispatch(connection)
                try:
                    connection.sendall(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass
        finally:
            with self._workers_lock:
                self._workers.discard(threading.current_thread())

    def _read_and_dispatch(self, connection: socket.socket) -> Mapping[str, Any]:
        try:
            raw = connection.makefile("rb").readline(_REQUEST_LIMIT + 1)
            if not raw.endswith(b"\n") or len(raw) > _REQUEST_LIMIT:
                raise BoundaryError("mediator request is incomplete or oversized")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise BoundaryError("mediator request must be an object")
            if not secrets.compare_digest(str(request.get("token", "")), self.token):
                raise BoundaryError("mediator socket authentication failed")
            if not self._request_lock.acquire(blocking=False):
                raise BoundaryError("concurrent mediator request denied before dispatch")
            try:
                result = self._dispatch(request)
            finally:
                self._request_lock.release()
            return {"ok": True, "result": result}
        except (BoundaryError, json.JSONDecodeError, OSError, TypeError, ValueError) as error:
            return {"ok": False, "error": str(error)}

    def _dispatch(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._closed_reason is not None:
            raise BoundaryError(f"mediator is closed: {self._closed_reason}")
        if self.transfer_reached:
            raise BoundaryError("transfer reached; every subsequent tool is denied")
        revalidate_file_identity(self.executable_identity)
        for identity in self.boundary_identities:
            revalidate_file_identity(identity)
        self._verify_protected_invariants()
        if self._read_metadata() != self._metadata_before:
            self._closed_reason = "direct or unexplained lifecycle metadata delta"
            raise BoundaryError(self._closed_reason)
        operation = request.get("operation")
        if operation == "revalidate":
            tool_name = request.get("tool")
            if tool_name not in {*_READ_ONLY_TOOLS, LIFECYCLE_TOOL, ARTIFACT_TOOL}:
                raise BoundaryError("tool identity revalidation requested for an undeclared tool")
            result = {"tool": tool_name, "revalidated": True}
        elif operation == "lifecycle":
            result = self._lifecycle(request)
        elif operation == "artifact":
            result = self._artifact(request)
        else:
            raise BoundaryError("undeclared mediator operation")
        self._verify_protected_invariants()
        return result

    def _lifecycle(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        verify = request.get("verify")
        raw_argv = request.get("argv")
        if verify != {"action": "run"}:
            raise BoundaryError("lifecycle request requires verify.action=run")
        if not isinstance(raw_argv, list) or not all(isinstance(arg, str) for arg in raw_argv):
            raise BoundaryError("lifecycle request requires an argv string array")
        argv = tuple(raw_argv)
        self._validate_argv(argv)
        before_metadata = self._read_metadata()
        validation_hash = (
            self._stage_artifact_hash(str(before_metadata.get("stage")))
            if len(argv) > 1 and argv[1] == "advance"
            else None
        )
        exit_code, stdout = self._run(argv[1:])
        try:
            status = self._inspect_status()
            check = self._inspect_check()
            after_metadata = self._read_metadata()
            expected_stage = self._expected_next_stage() if argv[1] == "advance" else None
            command_result = (
                _json_object(stdout, "verify-probe")
                if argv[1] == "verify-probe" and exit_code == 0
                else None
            )
            validate_metadata_transition(
                before_metadata,
                after_metadata,
                argv,
                exit_code=exit_code,
                expected_stage=expected_stage,
                expected_validation_hash=validation_hash,
                command_result=command_result,
                expected_date=date.today().isoformat(),
            )
            observed_stage = str(after_metadata.get("stage"))
            reconcile_inspections(
                status,
                check,
                expected_slug=self.slug,
                expected_stage=observed_stage,
                expected_status=str(after_metadata.get("status", "active")),
            )
        except BoundaryError as error:
            self._closed_reason = str(error)
            raise
        self._metadata_before = after_metadata
        self.stage = observed_stage
        self.policy = StagePolicy.for_stage(
            observed_stage,
            verify_probe_timeout=self.verify_probe_timeout,
            resumed_reuse=self.resumed_reuse,
        )
        transfer = observed_stage == "transfer"
        if transfer:
            self.transfer_reached = True
            self._closed_reason = "transfer reached"
            if self.host_process_group is not None:
                try:
                    os.killpg(self.host_process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "status": status,
            "check": check,
            "transfer": transfer,
        }

    def _artifact(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        path = request.get("path")
        content = request.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise BoundaryError("artifact request requires path and exact text content")
        relative = _safe_relative(Path(path))
        if relative not in self.allowed_artifacts:
            raise BoundaryError("write target is not a declared current-stage focal artifact")
        stage_prefixes = {
            "intake": ("brief.md",),
            "explore": ("notes/",),
            "probe": ("probe/results.md",),
            "transfer": (),
            "reuse": ("assets/",),
        }[self.stage]
        if not any(
            relative.as_posix() == prefix or relative.as_posix().startswith(prefix)
            for prefix in stage_prefixes
        ):
            raise BoundaryError("write target is not allowed in the independently observed stage")
        body = content.encode("utf-8")
        self._safe_replace(relative, body)
        return {"path": relative.as_posix(), "sha256": _sha256(body), "bytes": len(body)}

    def _validate_argv(self, argv: tuple[str, ...]) -> None:
        if self.arm == "baseline" and len(argv) > 1 and argv[1] not in {"status", "check", "cross"}:
            raise BoundaryError("baseline arm exposes no lifecycle mutation")
        allowed_cross = argv in self.cross_argv and _validate_cross_argv(argv) == argv
        if not self.policy.allows(argv) and not allowed_cross:
            raise BoundaryError("executable, action, target, or flags are not allowlisted")

    def _run(self, argv: Sequence[str]) -> tuple[int, str]:
        return self._runner.run(argv)

    def _inspect_status(self) -> dict[str, Any]:
        code, stdout = self._run(("status", self.slug, "--json"))
        payload = _json_object(stdout, "status")
        if code != 0:
            raise BoundaryError("post-command status inspection failed")
        if (
            payload.get("slug") != self.slug
            or payload.get("stage") not in {"intake", "explore", "probe", "transfer", "reuse"}
            or payload.get("status") not in {"active", "done", "dropped"}
            or type(payload.get("gate_passed")) is not bool
            or not isinstance(payload.get("gate_failures"), list)
            or type(payload.get("timebox_overdue")) is not bool
        ):
            raise BoundaryError("post-command status inspection has invalid semantics")
        return payload

    def _inspect_check(self) -> dict[str, Any]:
        code, stdout = self._run(("check", self.slug, "--offline", "--json"))
        payload = _json_object(stdout, "check")
        if code not in {0, 1}:
            raise BoundaryError("post-command check inspection failed")
        if (
            payload.get("slug") != self.slug
            or payload.get("stage") not in {"intake", "explore", "probe", "transfer", "reuse"}
            or type(payload.get("passed")) is not bool
            or not isinstance(payload.get("results"), list)
            or not isinstance(payload.get("consistency_issues"), list)
        ):
            raise BoundaryError("post-command check inspection has invalid semantics")
        return payload

    def _verify_protected_invariants(self) -> None:
        current = self.protected_identities()
        if current != self._protected_before:
            self._closed_reason = "protected path identity invariant changed"
            raise BoundaryError(self._closed_reason)

    def _read_metadata(self) -> dict[str, Any]:
        path = self.focal_root / "sdr.yaml"
        if path.is_symlink():
            raise BoundaryError("lifecycle metadata path must not be a symlink")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise BoundaryError("lifecycle metadata is unreadable") from error
        if not isinstance(payload, dict):
            raise BoundaryError("lifecycle metadata must be an object")
        return payload

    def _expected_next_stage(self) -> str | None:
        order = (
            ("intake", "explore", "probe", "transfer", "reuse")
            if self.arm == "full"
            else ("intake", "explore", "transfer", "reuse")
        )
        try:
            index = order.index(self.stage)
        except ValueError:
            return None
        return order[index + 1] if index + 1 < len(order) else "done"

    def _stage_artifact_hash(self, stage: str) -> str:
        metadata = self._read_metadata()
        schema_version = metadata.get("schema_version", 1)
        if type(schema_version) is not int:
            raise BoundaryError("metadata schema version is invalid")
        try:
            specification = schema.artifact_for(stage, schema_version=schema_version)
        except (KeyError, ValueError) as error:
            raise BoundaryError("cannot derive the exact stage artifact hash") from error
        if specification.collection_dir:
            directory = self.focal_root / specification.collection_dir
            paths = sorted(directory.glob("*.md")) if directory.is_dir() else []
        else:
            path = self.focal_root / specification.primary_file
            paths = [path] if path.exists() else []
        digest = hashlib.sha256()
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise BoundaryError("stage artifact hash encountered an invalid path")
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def _safe_replace(self, relative: PurePosixPath, body: bytes) -> None:
        if self._focal_fd is None:
            raise BoundaryError("mediator focal directory is unavailable")
        parent_fd = os.dup(self._focal_fd)
        temporary: str | None = None
        try:
            for component in relative.parts[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = next_fd
            target = relative.name
            try:
                target_stat = os.stat(target, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                target_stat = None
            if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
                raise BoundaryError("artifact target must be a regular file or absent")
            temporary = f".sdr-write-{secrets.token_hex(16)}"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                _write_all(descriptor, body)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, target, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temporary = None
            os.fsync(parent_fd)
            descriptor = os.open(
                target,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            try:
                if _sha256_fd(descriptor) != _sha256(body):
                    raise BoundaryError("artifact post-write hash invariant failed")
            finally:
                os.close(descriptor)
        except OSError as error:
            raise BoundaryError(f"race-resistant artifact write refused: {error}") from error
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
            os.close(parent_fd)


class ImmutableSession:
    """Capture the first event session identifier and reject every conflict."""

    def __init__(self) -> None:
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def observe_event(self, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            return
        if self._session_id is None:
            self._session_id = session_id
        elif self._session_id != session_id:
            raise BoundaryError("conflicting event session identity")


def _verify_materialized_bytes(boundary: MaterializedBoundary) -> None:
    if boundary.config_path.read_bytes() != boundary.config_bytes:
        raise BoundaryError("harness OpenCode config bytes changed")
    if boundary.plugin_path.read_bytes() != boundary.plugin_bytes:
        raise BoundaryError("harness OpenCode plugin bytes changed")
    if _sha256(boundary.config_bytes) != boundary.config_sha256:
        raise BoundaryError("harness OpenCode config hash is invalid")
    if _sha256(boundary.plugin_bytes) != boundary.plugin_sha256:
        raise BoundaryError("harness OpenCode plugin hash is invalid")


def _verify_isolated_environment(
    boundary: MaterializedBoundary, environment: Mapping[str, str]
) -> None:
    required = {
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
        "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    }
    if any(environment.get(name) != value for name, value in required.items()):
        raise BoundaryError("OpenCode isolation environment is incomplete")
    if "OPENCODE_CONFIG_CONTENT" in environment:
        raise BoundaryError("inherited inline OpenCode config is forbidden")
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        value = environment.get(name)
        if not value or not Path(value).resolve(strict=True).is_relative_to(boundary.runspace):
            raise BoundaryError(f"{name} is not isolated in the runspace")


def _verify_resolved_config(
    boundary: MaterializedBoundary, config: Mapping[str, Any], agent: Mapping[str, Any]
) -> None:
    failures: list[str] = []
    if config.get("plugin") != [boundary.plugin_path.as_uri()]:
        failures.append("explicit plugin")
    if config.get("mcp") != {}:
        failures.append("MCP isolation")
    if config.get("default_agent") != PILOT_AGENT:
        failures.append("pilot agent")
    expected_tools = {
        "*": False,
        "read": True,
        "glob": True,
        "grep": True,
        LIFECYCLE_TOOL: True,
        ARTIFACT_TOOL: True,
    }
    if config.get("tools") != expected_tools:
        failures.append("exact tool set")
    permission = config.get("permission")
    if not isinstance(permission, dict) or any(
        permission.get(name) != "deny"
        for name in ("*", "bash", "edit", "task", "webfetch", "websearch", "external_directory")
    ):
        failures.append("deny-by-default permissions")
    if config.get("formatter") is not False or config.get("lsp") is not False:
        failures.append("execution facilities")
    if agent.get("name") != PILOT_AGENT:
        failures.append("resolved pilot identity")
    agent_tools = agent.get("tools")
    if agent_tools is not None and (
        not isinstance(agent_tools, dict)
        or {name for name, enabled in agent_tools.items() if enabled}
        != {*_READ_ONLY_TOOLS, LIFECYCLE_TOOL, ARTIFACT_TOOL}
    ):
        failures.append("resolved pilot tools")
    rules = agent.get("permission")
    if not isinstance(rules, list) or any(
        _last_agent_action(rules, name) != action
        for name, action in {
            "*": "deny",
            "bash": "deny",
            "edit": "deny",
            "task": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "external_directory": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            LIFECYCLE_TOOL: "allow",
            ARTIFACT_TOOL: "allow",
        }.items()
    ):
        failures.append("resolved pilot permissions")
    if failures:
        raise BoundaryError(
            "effective OpenCode config does not prove isolation: " + ", ".join(failures)
        )


def _last_agent_action(rules: Sequence[Any], permission: str) -> str | None:
    matches = [
        rule.get("action")
        for rule in rules
        if isinstance(rule, dict)
        and rule.get("permission") == permission
        and rule.get("pattern") == "*"
    ]
    return matches[-1] if matches else None


def _run_debug(runner: BoundedProcessRunner, argv: Sequence[str]) -> str:
    returncode, stdout = runner.run(argv)
    if returncode != 0:
        detail = stdout.strip() or "no diagnostic"
        raise BoundaryError(f"OpenCode preflight failed: {detail}")
    return stdout


def _parse_debug_json(text: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise BoundaryError(f"{label} is not machine-readable JSON") from error
    if not isinstance(payload, dict):
        raise BoundaryError(f"{label} must be a JSON object")
    return payload


def _json_object(text: str, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise BoundaryError(f"post-command {label} inspection returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise BoundaryError(f"post-command {label} inspection returned a non-object")
    return payload


def _safe_relative(path: Path) -> PurePosixPath:
    raw = PurePosixPath(path.as_posix())
    if raw.is_absolute() or not raw.parts or any(part in {"", ".", ".."} for part in raw.parts):
        raise BoundaryError("artifact path must be a normalized relative path")
    if raw.name == "sdr.yaml":
        raise BoundaryError("lifecycle metadata is never an artifact write target")
    return raw


def _write_exclusive(path: Path, body: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        _write_all(descriptor, body)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, body: bytes) -> None:
    view = memoryview(body)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]


def _read_identity_bytes(identity: FileIdentity) -> bytes:
    descriptor = os.open(identity.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = os.fstat(descriptor)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        body = b"".join(chunks)
        observed = FileIdentity(
            identity.path,
            current.st_dev,
            current.st_ino,
            stat.S_IMODE(current.st_mode),
            current.st_size,
            _sha256(body),
        )
        if observed != identity:
            raise BoundaryError(f"file identity changed while reading exact bytes: {identity.path}")
    finally:
        os.close(descriptor)
    revalidate_file_identity(identity)
    return body


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _protected_file_digest(path: Path, expected: os.stat_result) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino, current.st_mode, current.st_size) != (
            expected.st_dev,
            expected.st_ino,
            expected.st_mode,
            expected.st_size,
        ):
            raise BoundaryError(f"protected file identity changed while hashing: {path}")
        return _sha256_fd(descriptor)
    finally:
        os.close(descriptor)


def _protected_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in (*directories, *files):
            entry = current_path / name
            relative = entry.relative_to(root).as_posix().encode()
            info = entry.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise BoundaryError(f"protected tree contains a symlink: {entry}")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(info.st_dev.to_bytes(8, "big", signed=False))
            digest.update(info.st_ino.to_bytes(8, "big", signed=False))
            digest.update(info.st_mode.to_bytes(8, "big", signed=False))
            if stat.S_ISREG(info.st_mode):
                digest.update(bytes.fromhex(_protected_file_digest(entry, info)))
            elif not stat.S_ISDIR(info.st_mode):
                raise BoundaryError(f"protected tree contains a special path: {entry}")
    return digest.hexdigest()


def _path_hash_no_follow(path: Path) -> str:
    try:
        root_stat = path.lstat()
    except FileNotFoundError:
        return _sha256(b"missing")
    if stat.S_ISLNK(root_stat.st_mode):
        return _sha256(b"symlink:" + os.readlink(path).encode())
    if stat.S_ISREG(root_stat.st_mode):
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            return _sha256_fd(descriptor)
        finally:
            os.close(descriptor)
    if not stat.S_ISDIR(root_stat.st_mode):
        return _sha256(f"special:{root_stat.st_mode}".encode())
    digest = hashlib.sha256()
    for current, directories, files in os.walk(path, followlinks=False):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in (*directories, *files):
            entry = current_path / name
            relative = entry.relative_to(path).as_posix().encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            entry_stat = entry.lstat()
            if stat.S_ISLNK(entry_stat.st_mode):
                digest.update(b"L" + os.readlink(entry).encode())
            elif stat.S_ISREG(entry_stat.st_mode):
                descriptor = os.open(entry, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                try:
                    digest.update(bytes.fromhex(_sha256_fd(descriptor)))
                finally:
                    os.close(descriptor)
            else:
                digest.update(f"M{entry_stat.st_mode}".encode())
    return digest.hexdigest()
