"""Credential boundaries and provenance for benchmark subprocess environments."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from bench.harness import runspace as runspace_module
from bench.harness.actor import RunRequest, ScriptedActor
from bench.harness.corpus import CorpusItem
from bench.harness.runspace import REPOSITORY_ROOT, Runspace, run_isolated


def _builder(name: str) -> Callable[..., Any]:
    builder = getattr(runspace_module, name, None)
    assert callable(builder), f"missing subprocess environment builder {name}"
    return builder


def _item() -> CorpusItem:
    return CorpusItem(
        id="environment-item",
        mode="light",
        title="Environment item",
        question="Which environment reaches the subprocess?",
        planted_defects=(),
        expected_detection={},
        sources=(),
        artifacts={},
        commands=(("status", "environment-item", "--json"),),
        probe=None,
        path=Path("bench/corpus/items/environment-item.yaml"),
    )


@pytest.mark.parametrize(
    "builder_name",
    (
        "build_scripted_environment",
        "build_mutation_environment",
        "build_metamorphic_environment",
    ),
)
def test_non_live_environments_are_independently_allowlisted_and_record_provenance(
    builder_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planted = {
        "ANTHROPIC_API_KEY": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
        "GITHUB_TOKEN": "secret",
        "OPENCODE_CONFIG": "/private/agent-config",
        "SSH_AUTH_SOCK": "/private/agent.sock",
        "XDG_CONFIG_HOME": "/private/config",
        "PYTHONPATH": "/private/site-packages",
        "HOME": "/private/home",
    }
    for name, value in planted.items():
        monkeypatch.setenv(name, value)

    def inspect(space: Runspace) -> None:
        prepared = _builder(builder_name)(
            space,
            executable=sys.executable,
            package_root=REPOSITORY_ROOT / "src",
        )

        assert prepared.variables == {
            "PATH": os.defpath,
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
            "SDR_KNOWLEDGE": str(space.knowledge),
            "SDR_ROOT": str(space.root),
        }
        inherited_only = set(planted) - {"PYTHONPATH"}
        assert not inherited_only & prepared.variables.keys()
        assert prepared.variables["PYTHONPATH"] != planted["PYTHONPATH"]
        assert prepared.provenance.executable_path == Path(sys.executable).resolve()
        assert (
            prepared.provenance.executable_sha256
            == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        )
        assert prepared.provenance.package_root == (REPOSITORY_ROOT / "src").resolve()
        assert prepared.provenance.package_sha256

    run_isolated(inspect)


def test_non_live_builders_return_disjoint_environment_mappings() -> None:
    def inspect(space: Runspace) -> None:
        kwargs = {
            "executable": sys.executable,
            "package_root": REPOSITORY_ROOT / "src",
        }
        scripted = _builder("build_scripted_environment")(space, **kwargs)
        mutation = _builder("build_mutation_environment")(space, **kwargs)
        metamorphic = _builder("build_metamorphic_environment")(space, **kwargs)

        assert scripted.variables is not mutation.variables
        assert mutation.variables is not metamorphic.variables
        assert metamorphic.variables is not scripted.variables

    run_isolated(inspect)


def test_live_environment_inherits_only_connector_declared_variables() -> None:
    inherited = {
        "OPENCODE_API_KEY": "connector-secret",
        "OPENCODE_CONFIG": "/connector/config",
        "ANTHROPIC_API_KEY": "undeclared-secret",
        "AWS_SECRET_ACCESS_KEY": "undeclared-secret",
        "HOME": "/private/home",
        "PATH": "/private/bin",
    }

    def inspect(space: Runspace) -> None:
        prepared = _builder("build_live_environment")(
            space,
            executable=sys.executable,
            package_root=REPOSITORY_ROOT / "src",
            connector_variables=("OPENCODE_API_KEY", "OPENCODE_CONFIG"),
            inherited=inherited,
        )

        assert prepared.variables == {
            "OPENCODE_API_KEY": "connector-secret",
            "OPENCODE_CONFIG": "/connector/config",
            "PATH": os.defpath,
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
            "SDR_KNOWLEDGE": str(space.knowledge),
            "SDR_ROOT": str(space.root),
        }
        assert "ANTHROPIC_API_KEY" not in prepared.variables
        assert "AWS_SECRET_ACCESS_KEY" not in prepared.variables
        assert prepared.provenance.executable_sha256
        assert prepared.provenance.package_sha256

    run_isolated(inspect)


def test_scripted_actor_records_provenance_before_subprocess_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    observed_environments: list[dict[str, str]] = []
    build = _builder("build_scripted_environment")

    def recording_build(*args: Any, **kwargs: Any):
        prepared = build(*args, **kwargs)
        assert prepared.provenance.executable_sha256
        assert prepared.provenance.package_sha256
        events.append("provenance")
        return prepared

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        events.append("subprocess")
        observed_environments.append(kwargs["env"])
        return subprocess.CompletedProcess(argv, 1, "{}\n", "not found")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")

    def execute(space: Runspace):
        monkeypatch.setattr("bench.harness.actor.build_scripted_environment", recording_build)
        monkeypatch.setattr("bench.harness.actor.subprocess.run", fake_run)
        return ScriptedActor().execute(
            RunRequest(item=_item(), arm="light", repetition=0, space=space)
        )

    result = run_isolated(execute)

    assert events == ["provenance", "subprocess"]
    assert len(observed_environments) == 1
    assert "ANTHROPIC_API_KEY" not in observed_environments[0]
    assert result.commands[0].execution_provenance.executable_sha256
    assert result.commands[0].execution_provenance.package_sha256
