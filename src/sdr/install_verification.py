"""Verify by execution that a pinned repository revision installs and runs.

The verification installs the project from an explicit revision of the canonical
repository into a throwaway environment created outside the source checkout, with
the checkout absent from the import path and every publication credential removed
from the child environment. Results are machine readable so an unavailable
verification records an explicit skip reason instead of disappearing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PUBLICATION_CREDENTIAL_VARIABLES = frozenset(
    {
        "FLIT_PASSWORD",
        "FLIT_USERNAME",
        "HATCH_INDEX_AUTH",
        "HATCH_INDEX_USER",
        "POETRY_HTTP_BASIC_PYPI_PASSWORD",
        "POETRY_PYPI_TOKEN_PYPI",
        "PYPI_API_TOKEN",
        "PYPI_PASSWORD",
        "PYPI_TOKEN",
        "PYPI_USERNAME",
        "TWINE_PASSWORD",
        "TWINE_REPOSITORY",
        "TWINE_REPOSITORY_URL",
        "TWINE_USERNAME",
        "UV_PUBLISH_PASSWORD",
        "UV_PUBLISH_TOKEN",
        "UV_PUBLISH_URL",
        "UV_PUBLISH_USERNAME",
    }
)
IMPORT_PATH_VARIABLES = ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP")
REPOSITORY_URL_KEYS = ("Repository", "Source", "Homepage")

STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

COMMAND_TIMEOUT = 900.0
LIGHT_FIXTURE_NAME = "light-complete"
FIXTURE_MANIFEST = "fixture.yaml"
LIGHT_STAGES = ("intake", "explore", "transfer", "reuse")
STAGE_ARTIFACTS = {
    "intake": ("brief.md",),
    "explore": ("notes",),
    "transfer": ("decision-memo.md",),
    "reuse": ("assets",),
}
_IMPORT_PROBE = (
    "import json, sys, sdr\nprint(json.dumps({'module': sdr.__file__, 'path': sys.path}))\n"
)
_PACKAGED_BRIEF_PROBE = (
    "import hashlib\n"
    "from importlib.resources import files\n"
    "print(hashlib.sha256(files('sdr').joinpath('templates/brief.md').read_bytes()).hexdigest())\n"
)


@dataclass(frozen=True)
class CommandResult:
    """One executed command of the verification, recorded for inspection."""

    name: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable form of the command record."""
        return {
            "name": self.name,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class InstallVerification:
    """Machine-readable outcome of one installation verification attempt."""

    status: str
    python_version: str
    repository: str | None = None
    revision: str | None = None
    requirement: str | None = None
    skip_reason: str | None = None
    failure_reason: str | None = None
    module_path: str | None = None
    import_path: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    terminal_status: str | None = None
    packaged_brief_digest: str | None = None
    created_brief_digest: str | None = None
    commands: tuple[CommandResult, ...] = field(default=())

    @property
    def performed(self) -> bool:
        """Return whether the verification actually ran."""
        return self.status != STATUS_SKIPPED

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-serializable form of the verification record."""
        return {
            "status": self.status,
            "python_version": self.python_version,
            "repository": self.repository,
            "revision": self.revision,
            "requirement": self.requirement,
            "skip_reason": self.skip_reason,
            "failure_reason": self.failure_reason,
            "module_path": self.module_path,
            "import_path": list(self.import_path),
            "stages": list(self.stages),
            "terminal_status": self.terminal_status,
            "packaged_brief_digest": self.packaged_brief_digest,
            "created_brief_digest": self.created_brief_digest,
            "commands": [command.to_dict() for command in self.commands],
        }


def installation_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a child environment without the import path or publication credentials."""
    source = dict(os.environ if base is None else base)
    for name in IMPORT_PATH_VARIABLES:
        source.pop(name, None)
    for name in PUBLICATION_CREDENTIAL_VARIABLES:
        source.pop(name, None)
    return source


def declared_repository(root: Path) -> str | None:
    """Return the canonical repository coordinate declared in ``[project.urls]``."""
    manifest = root / "pyproject.toml"
    if not manifest.is_file():
        return None
    urls = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("urls", {})
    for key in REPOSITORY_URL_KEYS:
        value = urls.get(key)
        if isinstance(value, str) and value:
            return value.rstrip("/")
    return None


def head_revision(root: Path) -> str | None:
    """Return the commit the checkout currently has at ``HEAD``."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and len(revision) == 40 else None


def revision_is_published(repository: str, revision: str, *, timeout: float = 60.0) -> bool:
    """Return whether ``revision`` is advertised by the canonical repository."""
    try:
        completed = subprocess.run(
            ["git", "ls-remote", repository],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=installation_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    advertised = {line.split("\t", 1)[0] for line in completed.stdout.splitlines() if line.strip()}
    return revision in advertised


def _run(
    name: str,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        check=False,
    )
    return CommandResult(
        name=name,
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def verify_installation(
    *,
    root: Path,
    workspace: Path,
    python_version: str,
    repository: str | None = None,
    revision: str | None = None,
    fixture: Path | None = None,
) -> InstallVerification:
    """Install the project from a pinned revision and drive a lifecycle through it."""
    root = root.resolve()
    workspace = workspace.resolve()
    if workspace == root or workspace.is_relative_to(root):
        raise ValueError(f"the verification workspace must be outside the checkout {root}")

    repository = repository or declared_repository(root)
    if repository is None:
        return InstallVerification(
            status=STATUS_SKIPPED,
            python_version=python_version,
            skip_reason="no canonical repository coordinate is declared in [project.urls]",
        )

    revision = revision or head_revision(root)
    if revision is None:
        return InstallVerification(
            status=STATUS_SKIPPED,
            python_version=python_version,
            repository=repository,
            skip_reason=f"the revision under test could not be resolved from the checkout {root}",
        )

    uv = shutil.which("uv")
    if uv is None:
        return InstallVerification(
            status=STATUS_SKIPPED,
            python_version=python_version,
            repository=repository,
            revision=revision,
            skip_reason="uv is not available to build the installation environment",
        )

    if not revision_is_published(repository, revision):
        return InstallVerification(
            status=STATUS_SKIPPED,
            python_version=python_version,
            repository=repository,
            revision=revision,
            skip_reason=(
                f"revision {revision} is not reachable at {repository}; "
                "network access is unavailable or the revision is unpublished"
            ),
        )

    fixture = (fixture or root / "examples" / LIGHT_FIXTURE_NAME).resolve()
    requirement = f"git+{repository}@{revision}"
    environment_root = workspace / "environment"
    work_root = workspace / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    research_root = work_root / "research"
    child_env = installation_environment()
    child_env["SDR_ROOT"] = str(research_root)

    interpreter = environment_root / "bin" / "python"
    console_script = environment_root / "bin" / "sdr"
    commands: list[CommandResult] = []

    def failed(reason: str) -> InstallVerification:
        return InstallVerification(
            status=STATUS_FAILED,
            python_version=python_version,
            repository=repository,
            revision=revision,
            requirement=requirement,
            failure_reason=reason,
            commands=tuple(commands),
        )

    def step(name: str, argv: Sequence[str]) -> bool:
        commands.append(_run(name, argv, cwd=work_root, env=child_env))
        return commands[-1].returncode == 0

    if not (
        step("create-environment", [uv, "venv", "--python", python_version, str(environment_root)])
        and step(
            "install-revision",
            [uv, "pip", "install", "--python", str(interpreter), requirement],
        )
        and step("import-path", [str(interpreter), "-I", "-c", _IMPORT_PROBE])
    ):
        return failed(str(_first_failure(commands)))
    probe = json.loads(commands[-1].stdout)

    if not (fixture / FIXTURE_MANIFEST).is_file():
        return failed(f"the lifecycle fixture {fixture} has no {FIXTURE_MANIFEST}")
    config = yaml.safe_load((fixture / FIXTURE_MANIFEST).read_text(encoding="utf-8"))
    slug = str(config["slug"])
    mode = str(config.get("mode", "light"))
    if mode != "light":
        return failed(f"the lifecycle fixture {fixture} declares mode {mode!r}, not 'light'")

    if not step(
        "create-investigation",
        [
            str(console_script),
            "new",
            slug,
            "--title",
            str(config["title"]),
            "--question",
            str(config["question"]),
            "--mode",
            mode,
            "--owner",
            str(config.get("owner", "")),
            "--timebox",
            str(config.get("timebox", 0)),
            "--no-commit",
            "--json",
        ],
    ):
        return failed(str(_first_failure(commands)))

    investigation = research_root / slug
    created_brief = investigation / "brief.md"
    if not created_brief.is_file():
        return failed("the installed console script created no brief from packaged resources")
    if not step("packaged-brief", [str(interpreter), "-I", "-c", _PACKAGED_BRIEF_PROBE]):
        return failed(str(_first_failure(commands)))
    packaged_digest = commands[-1].stdout.strip()
    created_digest = hashlib.sha256(created_brief.read_bytes()).hexdigest()
    if created_digest != packaged_digest:
        return failed("the created brief does not match the packaged brief template")

    for stage in LIGHT_STAGES:
        _copy_stage(fixture, investigation, stage)
        if stage == "transfer":
            approval = config["approval"]
            if not step(
                "approve-transfer",
                [str(console_script), "approve", slug, "--by", str(approval["by"]), "--offline"],
            ):
                return failed(str(_first_failure(commands)))
        if not step(
            f"advance-{stage}",
            [str(console_script), "advance", slug, "--offline", "--no-commit"],
        ):
            return failed(str(_first_failure(commands)))

    if not step("final-status", [str(console_script), "status", slug, "--json"]):
        return failed(str(_first_failure(commands)))
    final = json.loads(commands[-1].stdout)

    return InstallVerification(
        status=STATUS_PASSED,
        python_version=python_version,
        repository=repository,
        revision=revision,
        requirement=requirement,
        module_path=str(probe["module"]),
        import_path=tuple(str(entry) for entry in probe["path"] if entry),
        stages=tuple(final.get("validation", ())),
        terminal_status=str(final.get("status", "")),
        packaged_brief_digest=packaged_digest,
        created_brief_digest=created_digest,
        commands=tuple(commands),
    )


def _copy_stage(fixture: Path, investigation: Path, stage: str) -> None:
    for relative in STAGE_ARTIFACTS[stage]:
        source = fixture / relative
        target = investigation / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _first_failure(commands: Sequence[CommandResult]) -> str | None:
    for command in commands:
        if command.returncode != 0:
            detail = (command.stderr or command.stdout).strip().splitlines()
            tail = detail[-1] if detail else ""
            return f"{command.name} exited {command.returncode}: {tail}"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Run one installation verification and report its machine-readable result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Source checkout under test")
    parser.add_argument(
        "--workspace", type=Path, required=True, help="Empty directory outside the checkout"
    )
    parser.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
        help="Python minor version to install into",
    )
    parser.add_argument("--repository", default=None, help="Override the declared coordinate")
    parser.add_argument("--revision", default=None, help="Override the revision under test")
    parser.add_argument("--json", action="store_true", help="Emit the full result document")
    arguments = parser.parse_args(argv)

    result = verify_installation(
        root=arguments.root,
        workspace=arguments.workspace,
        python_version=arguments.python_version,
        repository=arguments.repository,
        revision=arguments.revision,
    )
    if arguments.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"status: {result.status} (python {result.python_version})")
        for detail in (result.skip_reason, result.failure_reason):
            if detail:
                print(f"reason: {detail}")
    return 1 if result.status == STATUS_FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
