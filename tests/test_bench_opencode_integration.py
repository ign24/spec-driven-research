"""Real offline OpenCode enforcement boundary for revised section 8."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import bench.harness.enforcement as enforcement

OPENCODE = Path(shutil.which("opencode") or "")


def _debug(
    boundary: Any,
    *argv: str,
    sidecar: Any | None = None,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    environment = dict(boundary.environment)
    if sidecar is not None:
        environment.update(sidecar.environment)
    return subprocess.run(
        (str(OPENCODE), *argv),
        cwd=boundary.runspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _fake_sdr(
    tmp_path: Path,
    *,
    stage: str = "explore",
    malformed: str | None = None,
    failed: str | None = None,
    unexplained_metadata: bool = False,
) -> Path:
    executable = tmp_path / "sdr"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import hashlib
import os
import pathlib
import sys
from datetime import date

root = pathlib.Path(os.environ["FAKE_ROOT"])
with (root / "calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
state = json.loads((root / "state.json").read_text(encoding="utf-8"))
metadata = root / "runspace" / "research" / "focal" / "sdr.yaml"
if state.get("failed") == sys.argv[1]:
    raise SystemExit(2)
if sys.argv[1] == "verify-claims":
    print(json.dumps({"slug": "focal", "verified": True}))
elif sys.argv[1] == "advance":
    state["stage"] = "transfer"
    (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
    stage_hash = hashlib.sha256((metadata.parent / "notes" / "research.md").read_bytes()).hexdigest()
    body = metadata.read_text().replace("stage: explore", "stage: transfer")
    body = body.replace("validation: {}", f"validation:\\n  explore: {stage_hash}")
    body = body.replace("updated: '2026-08-10'", f"updated: '{date.today().isoformat()}'")
    metadata.write_text(body)
    if state.get("unexplained_metadata"):
        metadata.write_text(metadata.read_text() + "approval: {by: agent}\\n")
    print("advanced")
elif sys.argv[1] == "status":
    if state.get("malformed") == "status":
        print("not-json")
    else:
        print(json.dumps({"slug": "focal", "stage": state["stage"], "status": "active", "gate_passed": True, "gate_failures": [], "timebox_overdue": False}))
elif sys.argv[1] == "check":
    if state.get("malformed") == "check":
        print("not-json")
    else:
        print(json.dumps({"slug": "focal", "stage": state["stage"], "passed": True, "results": [], "consistency_issues": []}))
else:
    print(json.dumps({"ok": True}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    (tmp_path / "calls.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "malformed": malformed,
                "failed": failed,
                "unexplained_metadata": unexplained_metadata,
            }
        ),
        encoding="utf-8",
    )
    return executable


def _materialized(tmp_path: Path) -> tuple[Any, Path, Path]:
    runspace = tmp_path / "runspace"
    focal = runspace / "research" / "focal"
    (focal / "notes").mkdir(parents=True)
    (focal / "sdr.yaml").write_text(
        "slug: focal\nstage: explore\nstatus: active\nupdated: '2026-08-10'\nvalidation: {}\n",
        encoding="utf-8",
    )
    artifact = focal / "notes" / "research.md"
    artifact.write_text("before\n", encoding="utf-8")
    return enforcement.materialize_boundary(runspace), focal, artifact


def _sidecar(tmp_path: Path, boundary: Any, focal: Path, artifact: Path, **changes: Any) -> Any:
    executable = _fake_sdr(
        tmp_path,
        stage=changes.pop("stage", "explore"),
        malformed=changes.pop("malformed", None),
        failed=changes.pop("failed", None),
        unexplained_metadata=changes.pop("unexplained_metadata", False),
    )
    sidecar_type = enforcement.MediatorSidecar
    values: dict[str, Any] = {
        "socket_path": boundary.socket_path,
        "executable": executable,
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "cwd": boundary.runspace,
        "environment": {"PATH": os.defpath, "FAKE_ROOT": str(tmp_path)},
        "slug": "focal",
        "focal_root": focal,
        "stage": "explore",
        "allowed_artifacts": (Path("notes/research.md"),),
        "protected_paths": (boundary.config_path, boundary.plugin_path),
        "boundary_identities": (boundary.config_identity, boundary.plugin_identity),
        "deadline": time.monotonic() + 180,
    }
    values.update(changes)
    return sidecar_type(**values)


def _tool_params(argv: list[str]) -> str:
    return json.dumps({"verify": {"action": "run"}, "argv": argv})


def _debug_result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    payload = json.loads(completed.stdout)
    return json.loads(payload["result"]["output"])


def _calls(tmp_path: Path) -> list[list[str]]:
    return [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_generated_config_is_schema_valid_and_resolves_to_one_isolated_pilot(
    tmp_path: Path,
) -> None:
    boundary, _, _ = _materialized(tmp_path)

    completed = _debug(boundary, "debug", "config")

    assert completed.returncode == 0, completed.stderr
    resolved = json.loads(completed.stdout)
    assert resolved["plugin"] == [boundary.plugin_path.as_uri()]
    assert resolved["default_agent"] == "pilot"
    assert resolved["mcp"] == {}
    assert resolved["tools"] == {
        "*": False,
        "read": True,
        "glob": True,
        "grep": True,
        "sdr_lifecycle": True,
        "sdr_artifact": True,
    }
    assert resolved["permission"]["*"] == "deny"
    assert resolved["permission"]["bash"] == "deny"
    assert resolved["permission"]["edit"] == "deny"
    assert boundary.config_path.read_bytes() == boundary.config_bytes
    assert boundary.plugin_path.read_bytes() == boundary.plugin_bytes
    assert stat.S_IMODE(boundary.config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(boundary.plugin_path.stat().st_mode) == 0o600
    assert boundary.environment["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert boundary.environment["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "1"
    assert boundary.environment["OPENCODE_DISABLE_EXTERNAL_SKILLS"] == "1"
    assert boundary.environment["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] == "1"
    assert Path(boundary.environment["XDG_CONFIG_HOME"]).is_relative_to(boundary.runspace)

    preflight = enforcement.preflight_opencode(boundary, OPENCODE, deadline=time.monotonic() + 300)
    assert preflight.config_sha256 == boundary.config_sha256
    assert preflight.plugin_sha256 == boundary.plugin_sha256
    assert preflight.resolved_agent["name"] == "pilot"


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_real_preflight_refuses_hash_consistent_external_plugin_and_bash_tool(
    tmp_path: Path,
) -> None:
    boundary, _, _ = _materialized(tmp_path)
    external = boundary.config_root / "external.js"
    external.write_text("export const External = async () => ({})\n", encoding="utf-8")
    external.chmod(0o600)
    config = json.loads(boundary.config_bytes)
    config["plugin"].append(external.as_uri())
    config["tools"]["bash"] = True
    body = (json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n").encode()
    boundary.config_path.write_bytes(body)
    tampered = replace(
        boundary,
        config_bytes=body,
        config_sha256=hashlib.sha256(body).hexdigest(),
        config_identity=enforcement.capture_file_identity(boundary.config_path),
    )

    with pytest.raises(enforcement.BoundaryError, match="isolation"):
        enforcement.preflight_opencode(tampered, OPENCODE, deadline=time.monotonic() + 300)


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_real_plugin_executes_lifecycle_through_authenticated_python_sidecar(
    tmp_path: Path,
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        completed = _debug(
            boundary,
            "debug",
            "agent",
            "pilot",
            "--tool",
            "sdr_lifecycle",
            "--params",
            _tool_params(["sdr", "status", "focal", "--json"]),
            sidecar=sidecar,
        )
        assert sidecar.token not in completed.stdout
        assert sidecar.token not in completed.stderr

    assert completed.returncode == 0, completed.stderr
    payload = _debug_result(completed)
    assert payload["exit_code"] == 0
    assert payload["status"]["slug"] == "focal"
    assert payload["check"]["slug"] == "focal"
    assert _calls(tmp_path) == [
        ["status", "focal", "--json"],
        ["check", "focal", "--offline", "--json"],
        ["status", "focal", "--json"],
        ["status", "focal", "--json"],
        ["check", "focal", "--offline", "--json"],
    ]


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_real_plugin_executes_stage_specific_verify_claims_with_exact_transition(
    tmp_path: Path,
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        completed = _debug(
            boundary,
            "debug",
            "agent",
            "pilot",
            "--tool",
            "sdr_lifecycle",
            "--params",
            _tool_params(["sdr", "verify-claims", "focal", "--json"]),
            sidecar=sidecar,
        )

    assert completed.returncode == 0, completed.stderr
    assert _debug_result(completed)["exit_code"] == 0
    assert "validation" in (focal / "sdr.yaml").read_text(encoding="utf-8")


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
@pytest.mark.parametrize(
    "params",
    (
        {"command": "sdr status focal --json"},
        {"verify": {"action": "read"}, "argv": ["sdr", "status", "focal", "--json"]},
        {"verify": {"action": "run"}, "argv": ["/usr/bin/sdr", "status", "focal", "--json"]},
        {"verify": {"action": "run"}, "argv": ["sdr", "approve", "focal"]},
        {"verify": {"action": "run"}, "argv": ["sdr", "resolve-claim", "focal", "C1"]},
        {"verify": {"action": "run"}, "argv": ["sdr", "status", "focal", "--yaml"]},
    ),
)
def test_real_plugin_denies_invalid_or_prohibited_lifecycle_requests(
    tmp_path: Path, params: dict[str, Any]
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        completed = _debug(
            boundary,
            "debug",
            "agent",
            "pilot",
            "--tool",
            "sdr_lifecycle",
            "--params",
            json.dumps(params),
            sidecar=sidecar,
        )

    assert completed.returncode != 0
    assert _calls(tmp_path) == [
        ["status", "focal", "--json"],
        ["check", "focal", "--offline", "--json"],
    ]


def test_socket_rejects_missing_or_incorrect_unpersisted_token(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        assert stat.S_IMODE(boundary.socket_path.stat().st_mode) == 0o600
        for token in (None, "wrong"):
            request = {
                "operation": "lifecycle",
                "verify": {"action": "run"},
                "argv": ["sdr", "status", "focal", "--json"],
            }
            if token is not None:
                request["token"] = token
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(boundary.socket_path))
                client.sendall(json.dumps(request).encode() + b"\n")
                response = json.loads(client.makefile("rb").readline())
            assert response["ok"] is False
        assert sidecar.token not in boundary.config_bytes.decode()
        assert sidecar.token not in boundary.plugin_bytes.decode()
    assert not boundary.socket_path.exists()
    assert _calls(tmp_path) == [
        ["status", "focal", "--json"],
        ["check", "focal", "--offline", "--json"],
    ]


def test_socket_denies_concurrent_request_before_subprocess_dispatch(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        sidecar._request_lock.acquire()  # noqa: SLF001 - holds the in-flight request boundary
        try:
            response = sidecar.request_for_test(
                {
                    "operation": "lifecycle",
                    "verify": {"action": "run"},
                    "argv": ["sdr", "status", "focal", "--json"],
                }
            )
        finally:
            sidecar._request_lock.release()  # noqa: SLF001

    assert response["ok"] is False
    assert "concurrent" in response["error"]
    assert _calls(tmp_path) == [
        ["status", "focal", "--json"],
        ["check", "focal", "--offline", "--json"],
    ]


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_artifact_tool_uses_server_side_safe_write_and_rejects_symlink_swap(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    artifact.unlink()
    artifact.symlink_to(outside)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        completed = _debug(
            boundary,
            "debug",
            "agent",
            "pilot",
            "--tool",
            "sdr_artifact",
            "--params",
            json.dumps({"path": "notes/research.md", "content": "attacker\n"}),
            sidecar=sidecar,
        )

    assert completed.returncode != 0
    assert outside.read_text(encoding="utf-8") == "outside\n"


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
@pytest.mark.parametrize("malformed", ("status", "check"))
def test_malformed_initial_status_or_check_refuses_sidecar_before_tools(
    tmp_path: Path, malformed: str
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with pytest.raises(enforcement.BoundaryError):
        with _sidecar(tmp_path, boundary, focal, artifact, malformed=malformed):
            pass
    assert len(_calls(tmp_path)) == (1 if malformed == "status" else 2)


@pytest.mark.parametrize("failed", ("status", "check"))
def test_failed_post_command_inspection_permanently_closes_sidecar(
    tmp_path: Path, failed: str
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with pytest.raises(enforcement.BoundaryError):
        with _sidecar(tmp_path, boundary, focal, artifact, failed=failed):
            pass


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_transfer_from_parsed_status_interrupts_bound_host_group_and_closes_tools(
    tmp_path: Path,
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    host = subprocess.Popen(
        ("sleep", "300"),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with _sidecar(tmp_path, boundary, focal, artifact, host_process_group=host.pid) as sidecar:
            completed = _debug(
                boundary,
                "debug",
                "agent",
                "pilot",
                "--tool",
                "sdr_lifecycle",
                "--params",
                _tool_params(["sdr", "advance", "focal", "--offline", "--no-commit"]),
                sidecar=sidecar,
            )
            host.wait(timeout=2)
            denied = _debug(
                boundary,
                "debug",
                "agent",
                "pilot",
                "--tool",
                "sdr_lifecycle",
                "--params",
                _tool_params(["sdr", "status", "focal", "--json"]),
                sidecar=sidecar,
            )
        assert completed.returncode == 0, completed.stderr
        assert _debug_result(completed)["transfer"] is True
        assert host.returncode == -signal.SIGTERM
        assert denied.returncode != 0
    finally:
        if host.poll() is None:
            os.killpg(host.pid, signal.SIGKILL)
            host.wait(timeout=2)


def test_sidecar_refuses_changed_sdr_executable_hash_before_dispatch(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    sidecar = _sidecar(tmp_path, boundary, focal, artifact)
    sidecar.executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sidecar.executable.chmod(0o755)
    with pytest.raises(enforcement.BoundaryError, match="identity"):
        with sidecar:
            pass


def test_artifact_write_survives_parent_rename_without_escaping_focal_dir(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    notes = focal / "notes"
    moved = focal / "moved-notes"
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        os.rename(notes, moved)
        (focal / "notes").mkdir()
        replacement = focal / "notes" / "research.md"
        replacement.symlink_to(tmp_path / "outside.md")
        response = sidecar.request_for_test(
            {"operation": "artifact", "path": "notes/research.md", "content": "after\n"}
        )

    assert response["ok"] is False
    assert not (tmp_path / "outside.md").exists()
    assert (moved / "research.md").read_text(encoding="utf-8") == "before\n"


def test_sidecar_revalidates_same_byte_plugin_replacement_before_dispatch(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        replacement = boundary.plugin_path.with_suffix(".replacement")
        replacement.write_bytes(boundary.plugin_bytes)
        replacement.chmod(0o600)
        os.replace(replacement, boundary.plugin_path)
        response = sidecar.request_for_test(
            {
                "operation": "lifecycle",
                "verify": {"action": "run"},
                "argv": ["sdr", "status", "focal", "--json"],
            }
        )

    assert response["ok"] is False
    assert "identity" in response["error"]


@pytest.mark.skipif(not OPENCODE.is_file(), reason="installed OpenCode executable required")
def test_real_plugin_loads_authenticated_revalidation_for_every_allowed_tool(
    tmp_path: Path,
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    plugin = boundary.plugin_bytes.decode("utf-8")
    assert 'await mediate({ operation: "revalidate", tool: input.tool })' in plugin
    assert '["read", "glob", "grep", "sdr_lifecycle", "sdr_artifact"]' in plugin
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        replacement = boundary.config_path.with_suffix(".replacement")
        replacement.write_bytes(boundary.config_bytes)
        replacement.chmod(0o600)
        os.replace(replacement, boundary.config_path)
        completed = _debug(
            boundary,
            "debug",
            "agent",
            "pilot",
            "--tool",
            "sdr_lifecycle",
            "--params",
            _tool_params(["sdr", "status", "focal", "--json"]),
            sidecar=sidecar,
        )

    assert completed.returncode != 0
    assert "identity" in completed.stderr


def test_direct_metadata_write_is_denied_but_exact_advance_transition_is_allowed(
    tmp_path: Path,
) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact) as sidecar:
        denied = sidecar.request_for_test(
            {"operation": "artifact", "path": "sdr.yaml", "content": "stage: done\n"}
        )
        allowed = sidecar.request_for_test(
            {
                "operation": "lifecycle",
                "verify": {"action": "run"},
                "argv": ["sdr", "advance", "focal", "--offline", "--no-commit"],
            }
        )

    assert denied["ok"] is False
    assert allowed["ok"] is True
    assert allowed["result"]["transfer"] is True


def test_unexplained_metadata_delta_closes_mediator(tmp_path: Path) -> None:
    boundary, focal, artifact = _materialized(tmp_path)
    with _sidecar(tmp_path, boundary, focal, artifact, unexplained_metadata=True) as sidecar:
        response = sidecar.request_for_test(
            {
                "operation": "lifecycle",
                "verify": {"action": "run"},
                "argv": ["sdr", "advance", "focal", "--offline", "--no-commit"],
            }
        )

    assert response["ok"] is False
    assert "metadata" in response["error"]
