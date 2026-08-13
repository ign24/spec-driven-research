"""Final certification regressions for the section-8 execution boundary."""

from __future__ import annotations

import hashlib
import inspect
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

import bench.harness.enforcement as enforcement


def _fake_sdr(tmp_path: Path) -> Path:
    executable = tmp_path / "sdr"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[1] == "status":
    print(json.dumps({"slug": "focal", "stage": "explore", "status": "active", "gate_passed": True, "gate_failures": [], "timebox_overdue": False}))
elif sys.argv[1] == "check":
    print(json.dumps({"slug": "focal", "stage": "explore", "passed": True, "results": [], "consistency_issues": []}))
else:
    print(json.dumps({"ok": True}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _sidecar(tmp_path: Path, protected: tuple[Path, ...]) -> enforcement.MediatorSidecar:
    runspace = tmp_path / "runspace"
    focal = runspace / "research" / "focal"
    (focal / "notes").mkdir(parents=True)
    (focal / "notes" / "research.md").write_text("before\n", encoding="utf-8")
    (focal / "sdr.yaml").write_text(
        "slug: focal\nstage: explore\nstatus: active\nvalidation: {}\n",
        encoding="utf-8",
    )
    executable = _fake_sdr(tmp_path)
    return enforcement.MediatorSidecar(
        socket_path=runspace / "mediator.sock",
        executable=executable,
        executable_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        cwd=runspace,
        environment={"PATH": os.defpath},
        slug="focal",
        focal_root=focal,
        stage="explore",
        allowed_artifacts=(Path("notes/research.md"),),
        protected_paths=protected,
        deadline=time.monotonic() + 10,
    )


def test_debug_preflight_uses_shared_deadline_and_reaps_descendants(tmp_path: Path) -> None:
    runspace = tmp_path / "runspace"
    runspace.mkdir()
    boundary = enforcement.materialize_boundary(runspace)
    executable = tmp_path / "opencode"
    child_pid = tmp_path / "debug-child.pid"
    executable.write_text(
        f"""#!/usr/bin/env python3
import pathlib
import subprocess
import time

child = subprocess.Popen(["sleep", "300"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))
time.sleep(300)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    deadline = time.monotonic() + 0.2
    parameters = inspect.signature(enforcement.preflight_opencode).parameters
    if "deadline" in parameters:
        call = lambda: enforcement.preflight_opencode(  # noqa: E731
            boundary, executable, deadline=deadline
        )
    else:
        enforcement._PREFLIGHT_TIMEOUT = 0.2  # noqa: SLF001 - RED compatibility path
        call = lambda: enforcement.preflight_opencode(boundary, executable)  # noqa: E731

    try:
        with pytest.raises((enforcement.BoundaryError, subprocess.TimeoutExpired)):
            call()
        pid = int(child_pid.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(pid, signal.SIGCONT)
    finally:
        if child_pid.exists():
            pid = int(child_pid.read_text(encoding="utf-8"))
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("target_kind", ("manifest", "seed-entry", "results-root"))
def test_protected_paths_capture_identity_and_reject_same_byte_replacement(
    tmp_path: Path, target_kind: str
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    seed_entry = seed / "evidence.md"
    seed_entry.write_text("same\n", encoding="utf-8")
    manifest = tmp_path / "live-manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    sealed = tmp_path / "sealed-live-request.json"
    sealed.write_text("{}\n", encoding="utf-8")
    results = tmp_path / "results"
    results.mkdir()
    protected = (seed, manifest, sealed, results)

    with _sidecar(tmp_path, protected) as sidecar:
        assert set(sidecar.protected_before) == {str(path) for path in protected}
        assert all(
            isinstance(identity, enforcement.ProtectedPathIdentity)
            for identity in sidecar.protected_before.values()
        )
        if target_kind == "manifest":
            replacement = tmp_path / "manifest-replacement"
            replacement.write_bytes(manifest.read_bytes())
            os.replace(replacement, manifest)
        elif target_kind == "seed-entry":
            replacement = seed / "replacement"
            replacement.write_bytes(seed_entry.read_bytes())
            os.replace(replacement, seed_entry)
        else:
            moved = tmp_path / "old-results"
            os.rename(results, moved)
            results.mkdir()
        response = sidecar.request_for_test(
            {
                "operation": "lifecycle",
                "verify": {"action": "run"},
                "argv": ["sdr", "status", "focal", "--json"],
            }
        )

    assert response["ok"] is False
    assert "identity" in response["error"]
