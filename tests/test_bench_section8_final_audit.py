"""Final section-8 audit regressions, kept separate until the audit closes."""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import bench.harness.enforcement as enforcement
import bench.harness.live as live
import bench.harness.pilot as pilot
import bench.harness.runner as runner
from bench.harness.actor import TokenUsage, TokenUsageUnavailable
from bench.harness.runspace import Runspace


def _space(tmp_path: Path, **manifest_changes: object) -> Runspace:
    root = tmp_path / "runspace"
    research = root / "research"
    knowledge = root / "knowledge"
    focal = research / "focal"
    (focal / "notes").mkdir(parents=True)
    knowledge.mkdir()
    (focal / "notes" / "research.md").write_text("research\n", encoding="utf-8")
    (focal / "sdr.yaml").write_text(
        "slug: focal\nstage: explore\nstatus: active\nvalidation: {}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "identity_kind": "scenario",
        "identity": "scenario-one",
        "arm": "light",
        "focal_slug": "focal",
        "seed_slugs": [],
        "stage": "explore",
        "artifacts": ["notes/research.md"],
        "cross_argv": [],
        "resumed_reuse": False,
    }
    manifest.update(manifest_changes)
    (root / "live-manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return Runspace(root, research, knowledge)


@pytest.mark.parametrize(
    "argv",
    (
        ["sdr", "approve", "focal"],
        ["sdr", "resolve-claim", "focal", "C1"],
        ["sdr", "cross", "degraded", "--online", "--json"],
        ["sdr", "cross", "source", "source-one", "--json", "--json"],
        ["sdr", "cross", "derive"],
    ),
)
def test_manifest_rejects_non_cross_or_unsupported_cross_argv(
    tmp_path: Path, argv: list[str]
) -> None:
    space = _space(tmp_path, cross_argv=[argv])

    with pytest.raises(enforcement.BoundaryError, match="cross"):
        enforcement.load_live_manifest(space)


@pytest.mark.parametrize(
    "argv",
    (
        ["sdr", "cross", "derive", "--json"],
        ["sdr", "cross", "source", "source-one", "--json"],
    ),
)
def test_manifest_accepts_only_supported_exact_cross_argv(tmp_path: Path, argv: list[str]) -> None:
    manifest = enforcement.load_live_manifest(_space(tmp_path, cross_argv=[argv]))
    assert manifest.cross_argv == (tuple(argv),)


@pytest.mark.parametrize("slug", ("../focal", "nested/focal", "Focal", "focal_1", "."))
def test_manifest_requires_normalized_slug_direct_children(tmp_path: Path, slug: str) -> None:
    space = _space(tmp_path, focal_slug=slug)

    with pytest.raises(enforcement.BoundaryError, match="slug|child"):
        enforcement.load_live_manifest(space)


def test_manifest_rejects_seed_symlink_that_is_not_a_direct_research_child(tmp_path: Path) -> None:
    space = _space(tmp_path, seed_slugs=["seed"])
    outside = tmp_path / "outside-seed"
    outside.mkdir()
    (space.root / "seed").symlink_to(outside, target_is_directory=True)

    with pytest.raises(enforcement.BoundaryError, match="child"):
        enforcement.load_live_manifest(space)


def test_metadata_transition_rejects_correct_keys_with_forged_values() -> None:
    before = {
        "slug": "focal",
        "stage": "explore",
        "status": "active",
        "updated": "2026-08-10",
        "validation": {},
    }
    forged_advance = {
        **before,
        "stage": "transfer",
        "updated": "2099-01-01",
        "validation": {"explore": "attacker-selected"},
    }
    forged_probe = {
        **before,
        "updated": "2099-01-01",
        "verify_probe": {"result": "pass", "probe_hash": "attacker-selected"},
    }

    with pytest.raises(enforcement.BoundaryError, match="transition"):
        enforcement.validate_metadata_transition(
            before,
            forged_advance,
            ("sdr", "advance", "focal", "--offline", "--no-commit"),
            exit_code=0,
            expected_stage="transfer",
        )
    with pytest.raises(enforcement.BoundaryError, match="transition"):
        enforcement.validate_metadata_transition(
            before,
            forged_probe,
            ("sdr", "verify-probe", "focal", "--timeout", "30", "--json"),
            exit_code=0,
        )


@pytest.mark.parametrize(
    ("target", "changes"), (("manifest", {"arm": "full"}), ("request", {"repetition": 7}))
)
def test_revalidation_reparses_bytes_and_rejects_forged_typed_fields(
    tmp_path: Path, target: str, changes: dict[str, object]
) -> None:
    space = _space(tmp_path)
    manifest = enforcement.load_live_manifest(space)
    results = tmp_path / "results"
    results.mkdir()
    sealed = enforcement.seal_live_request(
        space,
        manifest,
        repetition=0,
        max_turns=4,
        wall_clock_seconds=30,
        model="fake/model",
        results_root=results,
        prompt_template_sha256="1" * 64,
        submitted_prompt_sha256="2" * 64,
    )
    if target == "manifest":
        manifest = replace(manifest, **changes)
    else:
        sealed = replace(sealed, **changes)

    with pytest.raises(enforcement.BoundaryError, match="typed|bytes"):
        enforcement.revalidate_manifest_request(manifest, sealed)


@pytest.mark.parametrize(
    ("status_changes", "check_changes"),
    (
        ({}, {"consistency_issues": ["hash mismatch"]}),
        (
            {},
            {
                "passed": True,
                "results": [
                    {"check": "required", "passed": False, "skipped": False, "detail": "missing"}
                ],
            },
        ),
        (
            {"gate_passed": False, "gate_failures": ["different"]},
            {
                "passed": False,
                "results": [
                    {"check": "required", "passed": False, "skipped": False, "detail": "missing"}
                ],
            },
        ),
    ),
)
def test_reconciliation_rejects_all_status_check_semantic_contradictions(
    status_changes: dict[str, object], check_changes: dict[str, object]
) -> None:
    status = {
        "slug": "focal",
        "stage": "explore",
        "status": "active",
        "gate_passed": True,
        "gate_failures": [],
        "timebox_overdue": False,
    }
    check = {
        "slug": "focal",
        "stage": "explore",
        "passed": True,
        "results": [],
        "consistency_issues": [],
    }
    status.update(status_changes)
    check.update(check_changes)

    with pytest.raises(enforcement.BoundaryError, match="semantics|consistency|detail"):
        enforcement.reconcile_inspections(
            status, check, expected_slug="focal", expected_stage="explore"
        )


def test_lifecycle_dispatch_never_executes_replacement_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = tmp_path / "sdr"
    trusted.write_text("#!/bin/sh\nprintf trusted\n", encoding="utf-8")
    trusted.chmod(0o755)
    marker = tmp_path / "attacker-ran"
    attacker = tmp_path / "attacker"
    attacker.write_text(f"#!/bin/sh\ntouch '{marker}'\nprintf attacker\n", encoding="utf-8")
    attacker.chmod(0o755)
    runner_instance = enforcement.BoundedProcessRunner(
        executable=enforcement.capture_file_identity(trusted),
        cwd=tmp_path,
        environment={"PATH": os.defpath},
        deadline=time.monotonic() + 5,
    )
    real_popen = enforcement.subprocess.Popen
    replaced = False

    def replace_before_exec(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal replaced
        if not replaced:
            replaced = True
            os.replace(attacker, trusted)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(enforcement.subprocess, "Popen", replace_before_exec)

    with pytest.raises(enforcement.BoundaryError, match="identity"):
        runner_instance.run(())
    assert not marker.exists()


def test_debug_host_and_export_contracts_accept_a_pinned_executable_boundary() -> None:
    assert "executable_handle" in inspect.signature(enforcement.preflight_opencode).parameters
    assert "executable_handle" in inspect.signature(live._export_exact).parameters
    assert "deadline" in inspect.signature(live._export_exact).parameters
    assert "boundary_identities" in inspect.signature(live._export_exact).parameters
    assert "executable_handle" in live.OpenCodeConnector.__dataclass_fields__


def test_cancel_all_cannot_miss_a_process_between_spawn_and_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "slow"
    executable.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    executable.chmod(0o755)
    bounded = enforcement.BoundedProcessRunner(
        executable=enforcement.capture_file_identity(executable),
        cwd=tmp_path,
        environment={"PATH": os.defpath},
        deadline=time.monotonic() + 5,
    )
    real_popen = enforcement.subprocess.Popen
    spawn_started = threading.Event()
    release_spawn = threading.Event()

    def paused_spawn(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        spawn_started.set()
        assert release_spawn.wait(timeout=2)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(enforcement.subprocess, "Popen", paused_spawn)
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(bounded.run(()))
        except BaseException as error:
            outcome.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    assert spawn_started.wait(timeout=2)
    canceller = threading.Thread(target=bounded.cancel_all)
    canceller.start()
    time.sleep(0.05)
    release_spawn.set()
    worker.join(timeout=3)
    canceller.join(timeout=3)

    assert len(outcome) == 1
    assert isinstance(outcome[0], enforcement.BoundaryError)
    assert bounded.active_processes == ()


def test_operator_decision_is_typed_immutable_and_bound_to_stopped_run_session() -> None:
    record_type = getattr(pilot, "OperatorDecisionRecord", None)
    assert record_type is not None
    record = record_type("decision-1", "run-1", "session-1", True)
    evidence = pilot.ApprovalEvidence.operator_decided(record)
    assert evidence.operator_decision is record
    with pytest.raises(enforcement.BoundaryError, match="session"):
        pilot.validate_approval_terminal(
            evidence,
            "awaiting-operator-approval",
            initial_live=False,
            stopped_run_id="run-1",
            session_id="session-2",
        )
    with pytest.raises(FrozenInstanceError):
        record.session_id = "session-2"


@pytest.mark.parametrize(
    ("tokens", "cost", "expected"),
    (
        (
            TokenUsage(10, 4, "fake/model"),
            live.MonetaryCost(live.Decimal("0.01")),
            {"usage_available": True, "cost_available": True},
        ),
        (
            TokenUsageUnavailable("export unavailable"),
            live.MonetaryCostUnavailable("export unavailable"),
            {"usage_available": False, "cost_available": False},
        ),
    ),
)
def test_runner_json_includes_usage_cost_or_explicit_unavailable_reasons(
    tokens: object, cost: object, expected: dict[str, bool]
) -> None:
    payload_builder = getattr(runner, "_pilot_report_payload", None)
    assert payload_builder is not None
    report = SimpleNamespace(
        session_id="session-1",
        terminal_state="completed",
        approval=SimpleNamespace(state=SimpleNamespace(value="not-reached")),
        attributed=True,
        wall_clock_seconds=1.5,
        tokens=tokens,
        cost=cost,
    )
    payload = payload_builder(report)
    assert payload["usage"]["available"] is expected["usage_available"]
    assert payload["cost"]["available"] is expected["cost_available"]
    if not expected["usage_available"]:
        assert payload["usage"]["reason"] == "export unavailable"
        assert payload["cost"]["reason"] == "export unavailable"
