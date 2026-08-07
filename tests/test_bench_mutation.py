"""Safe application and execution of blocking-control source mutations."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bench.harness.actor import Actor, RunRequest, ScriptedActor
from bench.harness.arms import ArmOutcome, execute_arms
from bench.harness.corpus import CorpusItem
from bench.harness.mutation import (
    BlockingControl,
    ExactReplacement,
    MutationActor,
    MutationDeclaration,
    MutationError,
    prepare_mutation,
)
from bench.harness.runspace import Runspace, run_isolated


def _source_package(tmp_path: Path, body: bytes = b"def gate():\n    return True\n") -> Path:
    package = tmp_path / "checkout" / "src" / "sdr"
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    (package / "control.py").write_bytes(body)
    return package


def _declaration(**overrides: Any) -> MutationDeclaration:
    values: dict[str, Any] = {
        "name": "disable-structural-gate",
        "blocking_control": BlockingControl.STRUCTURAL,
        "target": Path("sdr/control.py"),
        "transformation": ExactReplacement(before=b"return True", after=b"return False"),
        "defect_kinds": ("missing-required-artifact",),
    }
    values.update(overrides)
    return MutationDeclaration(**values)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for relative, body in _tree_bytes(root).items():
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


def _item() -> CorpusItem:
    return CorpusItem(
        id="mutation-item",
        mode="light",
        title="Mutation item",
        question="Does the mutated control execute?",
        planted_defects=("missing-required-artifact",),
        expected_detection={"missing-required-artifact": "structural"},
        sources=(),
        artifacts={},
        commands=(("status", "mutation-item", "--json"),),
        probe=None,
        path=Path("bench/corpus/items/mutation-item.yaml"),
    )


def test_declared_mutation_changes_only_a_disposable_package_copy(tmp_path: Path) -> None:
    source_package = _source_package(tmp_path)
    before_bytes = _tree_bytes(source_package)
    before_hash = _tree_hash(source_package)
    prepared = prepare_mutation(_declaration(), source_package=source_package)

    with prepared as applied:
        assert not applied.source_root.is_relative_to(source_package.parents[1])
        assert applied.source_root.name == "src"
        assert applied.target == applied.source_root / "sdr" / "control.py"
        assert applied.target.read_bytes() == b"def gate():\n    return False\n"
        assert _tree_bytes(source_package) == before_bytes
        assert _tree_hash(source_package) == before_hash
        disposable_root = applied.disposable_root

    assert not disposable_root.exists()
    assert prepared.checkout_before_sha256 == before_hash
    assert prepared.checkout_after_sha256 == before_hash
    assert prepared.checkout_unchanged is True
    assert _tree_bytes(source_package) == before_bytes


@pytest.mark.parametrize(
    ("transformation", "message"),
    (
        (ExactReplacement(before=b"stale text", after=b"replacement"), "matched zero times"),
        (
            ExactReplacement(before=b"return True", after=b"return False", expected_matches=2),
            "expected 2 matches, found 1",
        ),
    ),
)
def test_mutation_rejects_stale_or_wrong_exact_match_count(
    tmp_path: Path,
    transformation: ExactReplacement,
    message: str,
) -> None:
    source_package = _source_package(tmp_path)
    prepared = prepare_mutation(
        _declaration(transformation=transformation), source_package=source_package
    )

    with pytest.raises(MutationError, match=message):
        with prepared:
            pytest.fail("an invalid mutation must fail before execution")

    assert not prepared.disposable_root.exists()
    assert prepared.checkout_unchanged is True


def test_mutation_rejects_ambiguous_matches_unless_exact_count_is_declared(
    tmp_path: Path,
) -> None:
    source_package = _source_package(tmp_path, body=b"return True\nreturn True\n")

    with pytest.raises(MutationError, match="ambiguous 2 matches"):
        with prepare_mutation(_declaration(), source_package=source_package):
            pytest.fail("an ambiguous mutation must fail before execution")

    declaration = _declaration(
        transformation=ExactReplacement(
            before=b"return True", after=b"return False", expected_matches=2
        )
    )
    with prepare_mutation(declaration, source_package=source_package) as applied:
        assert applied.target.read_bytes() == b"return False\nreturn False\n"


@pytest.mark.parametrize("target", (Path("/sdr/control.py"), Path("../sdr/control.py")))
def test_mutation_rejects_absolute_and_escaping_targets(target: Path) -> None:
    with pytest.raises(MutationError, match="confined relative path"):
        _declaration(target=target)


def test_mutation_rejects_symlink_target(tmp_path: Path) -> None:
    source_package = _source_package(tmp_path)
    (source_package / "linked.py").symlink_to(source_package / "control.py")
    declaration = _declaration(target=Path("sdr/linked.py"))

    with pytest.raises(MutationError, match="symlink"):
        with prepare_mutation(declaration, source_package=source_package):
            pytest.fail("a symlink target must fail before execution")


def test_mutation_rejects_noop_replacement_and_missing_attribution() -> None:
    with pytest.raises(MutationError, match="must change bytes"):
        ExactReplacement(before=b"same", after=b"same")
    with pytest.raises(MutationError, match="defect kind"):
        _declaration(defect_kinds=())


def test_mutation_actor_uses_credential_free_environment_for_one_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_package = _source_package(tmp_path)
    checkout_before = _tree_bytes(source_package)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        source_root = Path(kwargs["env"]["PYTHONPATH"])
        assert source_root != source_package.parent
        assert not source_root.is_relative_to(source_package.parents[1])
        assert (source_root / "sdr" / "control.py").read_bytes().endswith(b"return False\n")
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert kwargs.get("shell", False) is False
        return subprocess.CompletedProcess(argv, 1, "{}\n", "not found")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    actor = MutationActor(
        declaration=_declaration(),
        source_package=source_package,
        scripted_actor=ScriptedActor(python=sys.executable),
    )

    def execute(space: Runspace):
        monkeypatch.setattr("bench.harness.actor.subprocess.run", fake_run)
        return actor.execute_with_evidence(
            RunRequest(item=_item(), arm="light", repetition=0, space=space)
        )

    execution = run_isolated(execute)

    assert len(calls) == 1
    assert execution.actor_result.commands[0].execution_provenance is not None
    assert (
        execution.actor_result.commands[0].execution_provenance.package_root
        != source_package.parent
    )
    assert execution.checkout_before_sha256 == execution.checkout_after_sha256
    assert execution.checkout_unchanged is True
    assert not execution.disposable_root.exists()
    assert _tree_bytes(source_package) == checkout_before


def test_mutation_actor_cleans_up_when_scripted_execution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_package = _source_package(tmp_path)
    disposable_roots: list[Path] = []

    def failing_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        disposable_roots.append(Path(kwargs["env"]["PYTHONPATH"]).parent)
        raise subprocess.TimeoutExpired(argv, 1)

    actor = MutationActor(
        declaration=_declaration(),
        source_package=source_package,
        scripted_actor=ScriptedActor(python=sys.executable),
    )

    def execute(space: Runspace) -> None:
        monkeypatch.setattr("bench.harness.actor.subprocess.run", failing_run)
        actor.execute_with_evidence(
            RunRequest(item=_item(), arm="light", repetition=0, space=space)
        )

    with pytest.raises(subprocess.TimeoutExpired):
        run_isolated(execute)

    assert disposable_roots and all(not path.exists() for path in disposable_roots)


def test_mutation_actor_is_actor_compatible_and_records_checkout_integrity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_package = _source_package(tmp_path)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "{}\n", "")

    monkeypatch.setattr("bench.harness.actor.subprocess.run", fake_run)
    actor = MutationActor(
        declaration=_declaration(),
        source_package=source_package,
        scripted_actor=ScriptedActor(python=sys.executable),
    )

    assert isinstance(actor, Actor)
    run = execute_arms([_item()], actor=actor, arms=("light",), max_workers=1)[0]

    assert run.outcome is ArmOutcome.EXECUTED, run.error
    assert run.result is not None
    assert run.result.checkout_integrity is not None
    assert run.result.checkout_integrity.unchanged is True
    assert run.result.checkout_integrity.before_sha256 == (
        run.result.checkout_integrity.after_sha256
    )
    assert not run.result.checkout_integrity.disposable_root.exists()
