"""Adversarial contracts from the corrected section-8 review."""

from __future__ import annotations

import inspect
import json
import os
import time
from datetime import date
from pathlib import Path

import pytest

import bench.harness.enforcement as enforcement
import bench.harness.live as live
import bench.harness.pilot as pilot
from bench.harness.prompts import (
    EvaluationQuestion,
    PromptInputs,
    PromptLeakSignals,
    build_prompt,
)
from bench.harness.runner import EXIT_USAGE, build_parser, main
from bench.harness.runspace import REPOSITORY_ROOT, Runspace


def _prompt():  # type: ignore[no-untyped-def]
    return build_prompt(
        PromptInputs(
            EvaluationQuestion.LIVE_SINGLE_INVESTIGATION,
            "Investigate one synthetic question.",
            "light",
            None,
            None,
            stop_at_transfer=True,
        ),
        PromptLeakSignals(),
    )


def _space(tmp_path: Path, *, stage: str = "explore") -> tuple[Runspace, Path]:
    root = tmp_path / "runspace"
    research = root / "research"
    knowledge = root / "knowledge"
    focal = research / "focal"
    (focal / "notes").mkdir(parents=True)
    knowledge.mkdir()
    (focal / "notes" / "research.md").write_text("before\n", encoding="utf-8")
    (focal / "sdr.yaml").write_text(
        f"slug: focal\nstage: {stage}\nstatus: active\nvalidation: {{}}\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "identity_kind": "scenario",
        "identity": "scenario-one",
        "arm": "light",
        "focal_slug": "focal",
        "seed_slugs": [],
        "stage": stage,
        "artifacts": ["notes/research.md"],
        "cross_argv": [],
        "resumed_reuse": False,
    }
    (root / "live-manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return Runspace(root, research, knowledge), focal


def test_connector_and_live_entry_points_have_no_environment_or_repository_overrides() -> None:
    connector_parameters = inspect.signature(live.OpenCodeConnector).parameters
    execute_parameters = inspect.signature(live.execute_live_session).parameters
    pilot_parameters = inspect.signature(pilot.execute_pilot).parameters

    assert "credential_variables" not in connector_parameters
    assert "inherited" not in execute_parameters
    assert "repository_root" not in execute_parameters
    assert "inherited" not in pilot_parameters
    assert "repository_root" not in pilot_parameters
    assert live.OPENCODE_CREDENTIAL_VARIABLES


@pytest.mark.parametrize(
    "name",
    (
        "PYTHONPATH",
        "PYTHONHOME",
        "NODE_OPTIONS",
        "BUN_OPTIONS",
        "LD_PRELOAD",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
    ),
)
def test_live_environment_rejects_loader_runtime_and_config_injection(
    tmp_path: Path, name: str
) -> None:
    space, _ = _space(tmp_path)

    with pytest.raises(live.LiveError, match="injection"):
        live.build_opencode_live_environment(
            space, {name: "attacker", "OPENCODE_API_KEY": "synthetic"}
        )


def test_boundary_captures_distinct_xdg_and_path_device_inode_hash_identity(tmp_path: Path) -> None:
    space, _ = _space(tmp_path)
    boundary = enforcement.materialize_boundary(space.path)

    xdg = [
        Path(boundary.environment[name])
        for name in (
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "XDG_STATE_HOME",
        )
    ]
    assert len(set(xdg)) == 4
    assert boundary.config_identity.path == boundary.config_path
    assert boundary.plugin_identity.path == boundary.plugin_path
    assert boundary.config_identity.device == boundary.config_path.stat().st_dev
    assert boundary.config_identity.inode == boundary.config_path.stat().st_ino
    assert boundary.plugin_identity.sha256 == boundary.plugin_sha256


def test_same_byte_config_or_plugin_replacement_fails_inode_revalidation(tmp_path: Path) -> None:
    space, _ = _space(tmp_path)
    boundary = enforcement.materialize_boundary(space.path)
    replacement = boundary.plugin_path.with_suffix(".replacement")
    replacement.write_bytes(boundary.plugin_bytes)
    replacement.chmod(0o600)
    os.replace(replacement, boundary.plugin_path)

    with pytest.raises(enforcement.BoundaryError, match="identity"):
        enforcement.revalidate_boundary(boundary)


def test_manifest_derives_roots_and_rejects_seed_focal_aliases(tmp_path: Path) -> None:
    space, focal = _space(tmp_path)
    manifest = enforcement.load_live_manifest(space)

    assert manifest.focal_root == focal
    assert manifest.repository_root == REPOSITORY_ROOT
    assert manifest.manifest_sha256
    payload = json.loads(manifest.manifest_bytes)
    payload["seed_slugs"] = ["focal"]
    manifest.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(enforcement.BoundaryError, match="disjoint"):
        enforcement.load_live_manifest(space)


@pytest.mark.parametrize(
    ("stage", "allowed", "denied"),
    (
        (
            "intake",
            ("sdr", "advance", "focal", "--offline", "--no-commit"),
            ("sdr", "verify-claims", "focal", "--json"),
        ),
        (
            "explore",
            ("sdr", "verify-claims", "focal", "--json"),
            ("sdr", "verify-probe", "focal", "--timeout", "30", "--json"),
        ),
        (
            "probe",
            ("sdr", "verify-probe", "focal", "--timeout", "30", "--json"),
            ("sdr", "verify-claims", "focal", "--json"),
        ),
        ("transfer", None, ("sdr", "advance", "focal", "--offline", "--no-commit")),
        ("reuse", None, ("sdr", "advance", "focal", "--offline", "--no-commit")),
    ),
)
def test_command_policy_is_exact_and_stage_specific(
    stage: str, allowed: tuple[str, ...] | None, denied: tuple[str, ...]
) -> None:
    policy = enforcement.StagePolicy.for_stage(stage, verify_probe_timeout=30, resumed_reuse=False)
    if allowed is not None:
        assert policy.allows(allowed)
    assert not policy.allows(denied)
    assert not policy.allows(("sdr", "approve", "focal"))
    assert not policy.allows(("sdr", "resolve-claim", "focal", "C1"))


def test_reuse_advance_requires_separately_authorized_resumption() -> None:
    argv = ("sdr", "advance", "focal", "--offline", "--no-commit")
    assert not enforcement.StagePolicy.for_stage(
        "reuse", verify_probe_timeout=30, resumed_reuse=False
    ).allows(argv)
    assert enforcement.StagePolicy.for_stage(
        "reuse", verify_probe_timeout=30, resumed_reuse=True
    ).allows(argv)


def test_status_check_semantics_require_offline_check_and_exact_stage_agreement() -> None:
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
        "stage": "probe",
        "passed": True,
        "results": [],
        "consistency_issues": [],
    }
    with pytest.raises(enforcement.BoundaryError, match="stage"):
        enforcement.reconcile_inspections(
            status, check, expected_slug="focal", expected_stage="explore"
        )


def test_metadata_transition_rejects_unexplained_fields_and_accepts_exact_advance() -> None:
    before = {
        "slug": "focal",
        "stage": "explore",
        "status": "active",
        "updated": "2026-08-10",
        "validation": {},
    }
    today = date.today().isoformat()
    stage_hash = "1" * 64
    exact = {
        **before,
        "stage": "transfer",
        "updated": today,
        "validation": {"explore": stage_hash},
    }
    assert enforcement.validate_metadata_transition(
        before,
        exact,
        ("sdr", "advance", "focal", "--offline", "--no-commit"),
        exit_code=0,
        expected_stage="transfer",
        expected_validation_hash=stage_hash,
        expected_date=today,
    )
    with pytest.raises(enforcement.BoundaryError, match="metadata"):
        enforcement.validate_metadata_transition(
            before,
            {**exact, "approval": {"by": "agent"}},
            ("sdr", "advance", "focal", "--offline", "--no-commit"),
            exit_code=0,
            expected_stage="transfer",
            expected_validation_hash=stage_hash,
            expected_date=today,
        )


def test_initial_live_approval_requires_exact_terminal_pair_and_decision_record() -> None:
    pilot.validate_approval_terminal(
        pilot.ApprovalEvidence.not_reached(), "completed", initial_live=True
    )
    pilot.validate_approval_terminal(
        pilot.ApprovalEvidence.operator_pending(),
        "awaiting-operator-approval",
        initial_live=True,
    )
    with pytest.raises(enforcement.BoundaryError, match="terminal"):
        pilot.validate_approval_terminal(
            pilot.ApprovalEvidence.operator_pending(), "completed", initial_live=True
        )
    with pytest.raises(enforcement.BoundaryError, match="initial"):
        record = pilot.OperatorDecisionRecord("operator-record-1", "run-1", "session-1", True)
        pilot.validate_approval_terminal(
            pilot.ApprovalEvidence.operator_decided(record),
            "awaiting-operator-approval",
            initial_live=True,
        )


def test_model_is_never_filled_from_connector_when_export_is_unavailable() -> None:
    attribution, _, _, model, provider = live._unavailable("session-1", "export unavailable")
    assert attribution.attributed is False
    assert model is None
    assert provider is None


def test_runner_exposes_exact_scalar_live_form_and_rejects_matrix_flags(tmp_path: Path) -> None:
    parser = build_parser()
    actions = {option for action in parser._actions for option in action.option_strings}
    assert "--live" in actions
    required = {
        "--live-item",
        "--live-arm",
        "--live-repetition",
        "--live-host",
        "--live-host-version",
        "--live-model",
        "--live-prompt-policy",
        "--live-template-version",
        "--live-max-turns",
        "--live-wall-clock",
        "--live-results-root",
    }
    assert required <= actions

    stderr: list[str] = []

    class Sink:
        def write(self, value: str) -> int:
            stderr.append(value)
            return len(value)

    code = main(
        [
            "--live",
            "--live-item",
            "item-one",
            "--live-arm",
            "light",
            "--live-repetition",
            "0",
            "--live-host",
            "opencode",
            "--live-host-version",
            "1.18.16",
            "--live-model",
            "fake/model",
            "--live-prompt-policy",
            "assisted",
            "--live-template-version",
            "1",
            "--live-max-turns",
            "4",
            "--live-wall-clock",
            "30",
            "--live-results-root",
            str(tmp_path),
            "--arm",
            "light",
        ],
        stderr=Sink(),  # type: ignore[arg-type]
    )
    assert code == EXIT_USAGE
    assert "matrix" in "".join(stderr)


def test_mediator_cancels_process_group_and_joins_descendants_on_timeout(tmp_path: Path) -> None:
    script = tmp_path / "hang-sdr"
    child_pid = tmp_path / "child.pid"
    script.write_text(
        """#!/usr/bin/env python3
import pathlib, subprocess, sys, time
child = subprocess.Popen(["sleep", "300"])
(pathlib.Path.cwd() / "child.pid").write_text(str(child.pid))
time.sleep(300)
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    runner = enforcement.BoundedProcessRunner(
        executable=enforcement.capture_file_identity(script),
        cwd=tmp_path,
        environment={"PATH": os.defpath},
        deadline=time.monotonic() + 0.2,
    )

    with pytest.raises(enforcement.BoundaryError, match="bound"):
        runner.run(("status", "focal", "--json"))
    pid = int(child_pid.read_text())
    state_path = Path(f"/proc/{pid}/stat")
    for _ in range(100):
        if not state_path.exists() or state_path.read_text().split()[2] == "Z":
            break
        time.sleep(0.01)
    else:
        pytest.fail("mediator descendant remained live after process-group teardown")
    assert runner.active_processes == ()


def test_pilot_identity_api_accepts_no_scalar_target_attestation() -> None:
    assert "target" not in inspect.signature(pilot.execute_pilot).parameters
    assert tuple(pilot.IdentityEvidence.__dataclass_fields__) == (
        "manifest",
        "sealed_request",
        "live",
    )
