"""Disposable, Git-initialized research roots for benchmark runs.

Every harness run works in a temporary directory outside the repository tree.
The directory is a Git repository of its own so the SDR trail can write one
commit per lifecycle transition, which turns reopen counting into a `git log`
query instead of prose parsing. The repository under test is never written to
and never committed to.

Lifecycle metadata has no concurrency control: nothing locks `sdr.yaml`.
Isolation, not locking, is therefore the only safe form of parallelism here.
Every concurrent run owns a disjoint research root, and the worker count is
bounded.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RESEARCH_DIRNAME = "research"
KNOWLEDGE_DIRNAME = "knowledge"
META_FILE = "sdr.yaml"
DEFAULT_PREFIX = "sdr-bench-"
DEFAULT_MAX_WORKERS = 4

_FIXED_ENVIRONMENT = {
    "PATH": os.defpath,
    "PYTHONNOUSERSITE": "1",
}
_RESERVED_ENVIRONMENT_NAMES = frozenset(
    {*_FIXED_ENVIRONMENT, "PYTHONPATH", "SDR_ROOT", "SDR_KNOWLEDGE"}
)

_TRAIL_IDENTITY = (
    ("user.name", "SDR bench harness"),
    ("user.email", "bench@localhost"),
    ("commit.gpgsign", "false"),
)


class RunspaceError(RuntimeError):
    """Raised when an isolated run root cannot be prepared."""


@dataclass(frozen=True)
class ExecutionProvenance:
    """Executable and package identities captured before subprocess execution."""

    executable_path: Path
    executable_sha256: str
    package_root: Path
    package_sha256: str


@dataclass(frozen=True)
class SubprocessEnvironment:
    """A constructed environment paired with its pre-execution provenance."""

    variables: dict[str, str]
    provenance: ExecutionProvenance


@dataclass(frozen=True)
class Runspace:
    """One run's isolated filesystem: a Git root holding research and knowledge."""

    path: Path
    root: Path
    knowledge: Path

    def env(self) -> dict[str, str]:
        """Return the fixed, credential-free environment for this run's roots."""
        environment = dict(_FIXED_ENVIRONMENT)
        environment["SDR_ROOT"] = str(self.root)
        environment["SDR_KNOWLEDGE"] = str(self.knowledge)
        return environment

    def research_path(self, slug: str) -> Path:
        """Return the directory of one investigation inside this run."""
        return self.root / slug

    def meta_path(self, slug: str) -> Path:
        """Return the lifecycle metadata file of one investigation."""
        return self.research_path(slug) / META_FILE


def build_scripted_environment(
    space: Runspace, *, executable: str | Path, package_root: Path
) -> SubprocessEnvironment:
    """Build one credential-free scripted subprocess environment."""
    return _build_non_live_environment(space, executable=executable, package_root=package_root)


def build_mutation_environment(
    space: Runspace, *, executable: str | Path, package_root: Path
) -> SubprocessEnvironment:
    """Build one credential-free mutation subprocess environment."""
    return _build_non_live_environment(space, executable=executable, package_root=package_root)


def build_metamorphic_environment(
    space: Runspace, *, executable: str | Path, package_root: Path
) -> SubprocessEnvironment:
    """Build one credential-free metamorphic subprocess environment."""
    return _build_non_live_environment(space, executable=executable, package_root=package_root)


def build_live_environment(
    space: Runspace,
    *,
    executable: str | Path,
    package_root: Path,
    connector_variables: Sequence[str],
    inherited: Mapping[str, str],
) -> SubprocessEnvironment:
    """Build a live environment inheriting only connector-declared variable names."""
    declared = tuple(dict.fromkeys(connector_variables))
    reserved = sorted(set(declared) & _RESERVED_ENVIRONMENT_NAMES)
    if reserved:
        raise ValueError(f"connector variables cannot replace execution variables: {reserved}")
    prepared = _build_non_live_environment(space, executable=executable, package_root=package_root)
    variables = dict(prepared.variables)
    variables.update({name: inherited[name] for name in declared if name in inherited})
    return SubprocessEnvironment(variables=variables, provenance=prepared.provenance)


def _build_non_live_environment(
    space: Runspace, *, executable: str | Path, package_root: Path
) -> SubprocessEnvironment:
    provenance = _execution_provenance(executable, package_root)
    variables = space.env()
    variables["PYTHONPATH"] = str(provenance.package_root)
    return SubprocessEnvironment(variables=variables, provenance=provenance)


def _execution_provenance(executable: str | Path, package_root: Path) -> ExecutionProvenance:
    executable_path = Path(executable).resolve(strict=True)
    resolved_package_root = package_root.resolve(strict=True)
    if not executable_path.is_file():
        raise RunspaceError(f"subprocess executable is not a file: {executable_path}")
    if not resolved_package_root.is_dir():
        raise RunspaceError(f"subprocess package root is not a directory: {resolved_package_root}")
    return ExecutionProvenance(
        executable_path=executable_path,
        executable_sha256=_sha256_file(executable_path),
        package_root=resolved_package_root,
        package_sha256=_sha256_tree(resolved_package_root),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown git failure"
        raise RunspaceError(f"git {' '.join(args)} failed in {cwd}: {detail}")


def _init_git(path: Path) -> None:
    _git(path, "init", "--quiet", "--initial-branch", "main")
    for key, value in _TRAIL_IDENTITY:
        _git(path, "config", key, value)


@contextmanager
def runspace(prefix: str = DEFAULT_PREFIX, parent: Path | None = None) -> Iterator[Runspace]:
    """Create a disposable Git-initialized run root and remove it afterwards."""
    base = Path(
        tempfile.mkdtemp(prefix=prefix, dir=None if parent is None else str(parent))
    ).resolve()
    try:
        if base.is_relative_to(REPOSITORY_ROOT):
            raise RunspaceError(f"run root must live outside the repository tree: {base}")
        space = Runspace(
            path=base,
            root=base / RESEARCH_DIRNAME,
            knowledge=base / KNOWLEDGE_DIRNAME,
        )
        space.root.mkdir()
        space.knowledge.mkdir()
        _init_git(base)
        yield space
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run_isolated[T](
    work: Callable[[Runspace], T],
    prefix: str = DEFAULT_PREFIX,
    parent: Path | None = None,
) -> T:
    """Execute one unit of work in its own disposable run root."""
    with runspace(prefix=prefix, parent=parent) as space:
        return work(space)


def map_isolated[T](
    works: Sequence[Callable[[Runspace], T]],
    max_workers: int = DEFAULT_MAX_WORKERS,
    prefix: str = DEFAULT_PREFIX,
    parent: Path | None = None,
) -> list[T]:
    """Execute units of work with bounded parallelism over disjoint run roots.

    Results keep the order of ``works``. Each unit receives a run root that no
    other unit can observe, so no two concurrent runs share a research root or a
    lifecycle metadata file.
    """
    if max_workers < 1:
        raise ValueError(f"max_workers must be at least 1: {max_workers}")
    if not works:
        return []

    def execute(work: Callable[[Runspace], T]) -> T:
        return run_isolated(work, prefix=prefix, parent=parent)

    if max_workers == 1 or len(works) == 1:
        return [execute(work) for work in works]

    with ThreadPoolExecutor(max_workers=min(max_workers, len(works))) as executor:
        return list(executor.map(execute, works))
