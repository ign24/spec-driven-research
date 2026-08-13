"""Bounded live connector wired to the real mediation architecture."""

from __future__ import annotations

import inspect
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bench.harness.enforcement import BoundaryError
from bench.harness.live import (
    LiveBounds,
    LiveOptIn,
    LiveOptInError,
    OpenCodeConnector,
    create_live_request,
    execute_live_session,
)
from bench.harness.pilot import (
    IdentityEvidence,
    PilotAttributionError,
    PilotPlan,
    derive_observed_identity,
    execute_pilot,
    validate_pilot_attribution,
)
from bench.harness.prompts import (
    EvaluationQuestion,
    PromptInputs,
    PromptLeakSignals,
    build_prompt,
)
from bench.harness.runspace import Runspace, run_isolated

OPENCODE_HOST = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import socket
import sys
import time

harness_root = pathlib.Path(os.environ["OPENCODE_CONFIG"]).parent.parent
record = harness_root / "host-calls.jsonl"
with record.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "argv": sys.argv[1:],
        "credential": os.environ.get("OPENCODE_API_KEY"),
        "foreign": os.environ.get("FOREIGN_CREDENTIAL"),
        "config": os.environ.get("OPENCODE_CONFIG"),
        "token": os.environ.get("SDR_HARNESS_TOKEN"),
    }) + "\n")
if sys.argv[1:] == ["--version"]:
    print("fake-opencode-1")
elif sys.argv[1:] == ["debug", "config"]:
    print(pathlib.Path(os.environ["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
elif sys.argv[1:] == ["debug", "agent", "pilot"]:
    config = json.loads(pathlib.Path(os.environ["OPENCODE_CONFIG"]).read_text())
    selected = config["agent"]["pilot"]
    rules = [
        {"permission": key, "action": value, "pattern": "*"}
        for key, value in selected["permission"].items()
    ]
    print(json.dumps({"name": "pilot", "mode": "primary", "tools": selected["tools"], "permission": rules}))
elif sys.argv[1:2] == ["run"]:
    print(json.dumps({"type": "step_finish", "sessionID": "session-1"}), flush=True)
    if (harness_root / "conflicting-event").exists():
        print(json.dumps({"type": "message.part.updated", "part": {"sessionID": "foreign"}}), flush=True)
    request = {"token": os.environ["SDR_HARNESS_TOKEN"], "operation": "lifecycle",
               "verify": {"action": "run"},
               "argv": ["sdr", "advance", "focal", "--offline", "--no-commit"]}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.connect(os.environ["SDR_HARNESS_SOCKET"])
        client.sendall(json.dumps(request).encode() + b"\n")
        client.makefile("rb").readline()
    time.sleep(30)
elif sys.argv[1:2] == ["export"]:
    if (harness_root / "export-unavailable").exists():
        raise SystemExit(1)
    if (harness_root / "export-delay").exists():
        time.sleep(1.0)
    session = sys.argv[2]
    print(json.dumps({"info": {"id": session}, "messages": [{"info": {
        "role": "assistant", "sessionID": session, "providerID": "fake",
        "modelID": "model", "tokens": {"input": 10, "output": 4}, "cost": 0.01
    }}]}))
else:
    raise SystemExit(2)
"""


SDR_HOST = r"""#!/usr/bin/env python3
import json
import hashlib
import os
import pathlib
import sys
from datetime import date

root = pathlib.Path(os.environ["SDR_ROOT"])
metadata = root / "focal" / "sdr.yaml"
stage = next(line.split(":", 1)[1].strip() for line in metadata.read_text().splitlines() if line.startswith("stage:"))
if sys.argv[1] == "advance":
    stage_hash = hashlib.sha256((root / "focal" / "notes" / "research.md").read_bytes()).hexdigest()
    body = metadata.read_text().replace(f"stage: {stage}", "stage: transfer")
    body = body.replace("validation: {}", f"validation:\n  {stage}: {stage_hash}")
    body = body.replace("updated: '2026-08-10'", f"updated: '{date.today().isoformat()}'")
    metadata.write_text(body)
    print("advanced")
elif sys.argv[1] == "status":
    stage = next(line.split(":", 1)[1].strip() for line in metadata.read_text().splitlines() if line.startswith("stage:"))
    print(json.dumps({"slug": "focal", "stage": stage, "status": "active",
                      "gate_passed": True, "gate_failures": [], "timebox_overdue": False}))
elif sys.argv[1] == "check":
    stage = next(line.split(":", 1)[1].strip() for line in metadata.read_text().splitlines() if line.startswith("stage:"))
    print(json.dumps({"slug": "focal", "stage": stage, "passed": True,
                      "results": [], "consistency_issues": []}))
else:
    print(json.dumps({"ok": True}))
"""


def _prompt():  # type: ignore[no-untyped-def]
    return build_prompt(
        PromptInputs(
            EvaluationQuestion.LIVE_SINGLE_INVESTIGATION,
            "Investigate the focal question.",
            "light",
            None,
            None,
            stop_at_transfer=True,
        ),
        PromptLeakSignals(),
    )


def _prepare(
    tmp_path: Path, space: Runspace, monkeypatch: pytest.MonkeyPatch
) -> tuple[OpenCodeConnector, Any, Path]:
    host = tmp_path / "opencode"
    host.write_text(OPENCODE_HOST, encoding="utf-8")
    host.chmod(0o755)
    calls = space.path / "host-calls.jsonl"
    calls.write_text("", encoding="utf-8")
    sdr = tmp_path / "sdr"
    sdr.write_text(SDR_HOST, encoding="utf-8")
    sdr.chmod(0o755)
    monkeypatch.setattr("bench.harness.live.SDR_EXECUTABLE", sdr)
    focal = space.root / "focal"
    (focal / "notes").mkdir(parents=True)
    (focal / "notes" / "research.md").write_text("research\n", encoding="utf-8")
    (focal / "sdr.yaml").write_text(
        "slug: focal\nstage: explore\nstatus: active\nupdated: '2026-08-10'\nvalidation: {}\n",
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
    (space.path / "live-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    results = tmp_path / "results"
    results.mkdir(exist_ok=True)
    request = create_live_request(
        space,
        _prompt(),
        bounds=LiveBounds(4, 20),
        repetition=0,
        model="fake/model",
        results_root=results,
    )
    connector = OpenCodeConnector(host, model="fake/model")
    return connector, request, calls


def _calls(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_two_keys_are_required_before_boundary_or_host_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exercise(space: Runspace) -> None:
        connector, request, calls = _prepare(tmp_path, space, monkeypatch)
        for opt_in in (LiveOptIn(False, False), LiveOptIn(True, False), LiveOptIn(False, True)):
            with pytest.raises(LiveOptInError):
                execute_live_session(connector, request, opt_in=opt_in)
        assert _calls(calls) == []

    run_isolated(exercise)


def test_shared_deadline_is_fixed_before_every_opencode_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[float, float | None]] = []
    original = OpenCodeConnector.preflight

    def preflight(
        self: OpenCodeConnector,
        boundary: Any,
        environment: Any = None,
        *,
        deadline: float | None = None,
    ) -> Any:
        observed.append((time.monotonic(), deadline))
        if deadline is None:
            return original(self, boundary, environment)
        return original(self, boundary, environment, deadline=deadline)

    monkeypatch.setattr(OpenCodeConnector, "preflight", preflight)

    def exercise(space: Runspace) -> None:
        connector, request, _ = _prepare(tmp_path, space, monkeypatch)
        execute_live_session(connector, request, opt_in=LiveOptIn(True, True))
        assert all(deadline is not None for _, deadline in observed)
        assert len({deadline for _, deadline in observed}) == 1
        first_started, shared_deadline = observed[0]
        assert shared_deadline is not None
        assert 0 < shared_deadline - first_started <= request.bounds.wall_clock_seconds

    run_isolated(exercise)


def test_connector_has_no_effective_config_callback_or_agent_override() -> None:
    parameters = inspect.signature(OpenCodeConnector).parameters

    assert "effective_config_reader" not in parameters
    assert "agent" not in parameters


def test_live_run_uses_exact_preflights_mediator_transfer_and_post_reap_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def exercise(space: Runspace) -> None:
        connector, request, calls = _prepare(tmp_path, space, monkeypatch)
        monkeypatch.setenv("OPENCODE_API_KEY", "synthetic-admitted")
        monkeypatch.setenv("FOREIGN_CREDENTIAL", "forbidden")
        evidence = execute_live_session(
            connector,
            request,
            opt_in=LiveOptIn(True, True),
        )
        plan = PilotPlan(
            scenario_id="scenario-one",
            item_id=None,
            arm="light",
            repetition=0,
            host="opencode",
            host_version="fake-opencode-1",
            model="fake/model",
            model_version=None,
            prompt=request.prompt,
            bounds=request.bounds,
            results_root=request.sealed_request.results_root,
        )
        identity_evidence = IdentityEvidence(request.manifest, request.sealed_request, evidence)
        observed = derive_observed_identity(identity_evidence)
        assert validate_pilot_attribution(plan, observed) == observed
        changed_sealed = replace(request.sealed_request, repetition=1)
        with pytest.raises(BoundaryError, match="typed"):
            derive_observed_identity(replace(identity_evidence, sealed_request=changed_sealed))
        with pytest.raises(PilotAttributionError, match="model"):
            derive_observed_identity(
                replace(
                    identity_evidence,
                    live=replace(evidence, host=replace(evidence.host, model=None)),
                )
            )
        captured["evidence"] = evidence
        captured["calls"] = _calls(calls)

    run_isolated(exercise)
    evidence = captured["evidence"]
    calls = captured["calls"]

    assert evidence.intentional_stop is True
    assert evidence.process_reaped is True
    assert evidence.approval_state == "operator-pending"
    assert evidence.terminal_state == "awaiting-operator-approval"
    assert evidence.session.session_id == "session-1"
    assert evidence.session.attributed is True
    assert evidence.host.model == "fake/model"
    assert evidence.preflight.resolved_agent["name"] == "pilot"
    assert evidence.protected_identities_before == evidence.protected_identities_after
    assert evidence.transcript_persisted is False
    assert calls[-1]["argv"][:2] == ["export", "session-1"]
    assert all(call["foreign"] is None for call in calls)
    assert calls[0]["credential"] is None
    assert any(call["credential"] == "synthetic-admitted" for call in calls)
    assert all(call["config"] != "/caller/config" for call in calls)
    assert all(call["token"] is None for call in calls if call["argv"][:1] != ["run"])
    assert any(call["token"] for call in calls if call["argv"][:1] == ["run"])


def test_execute_pilot_runs_exactly_one_enforced_session_without_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def exercise(space: Runspace) -> None:
        connector, request, calls = _prepare(tmp_path, space, monkeypatch)
        plan = PilotPlan(
            scenario_id="scenario-one",
            item_id=None,
            arm="light",
            repetition=0,
            host="opencode",
            host_version="fake-opencode-1",
            model="fake/model",
            model_version=None,
            prompt=request.prompt,
            bounds=request.bounds,
            results_root=request.sealed_request.results_root,
        )
        captured["report"] = execute_pilot(
            plan,
            request=request,
            connector=connector,
            opt_in=LiveOptIn(True, True),
        )
        captured["calls"] = _calls(calls)

    run_isolated(exercise)

    report = captured["report"]
    assert report.identity.scenario_id == "scenario-one"
    assert report.approval.state.value == "operator-pending"
    assert report.session_id == "session-1"
    assert report.terminal_state == "awaiting-operator-approval"
    assert sum(call["argv"][0] == "run" for call in captured["calls"]) == 1


def test_unavailable_export_has_no_connector_model_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def exercise(space: Runspace) -> None:
        connector, request, _ = _prepare(tmp_path, space, monkeypatch)
        (request.workspace / "export-unavailable").write_text("1", encoding="utf-8")
        captured["evidence"] = execute_live_session(
            connector, request, opt_in=LiveOptIn(True, True)
        )

    run_isolated(exercise)
    evidence = captured["evidence"]
    assert evidence.session.attributed is False
    assert evidence.host.model is None


def test_final_elapsed_includes_exact_export_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def exercise(space: Runspace) -> None:
        connector, request, _ = _prepare(tmp_path, space, monkeypatch)
        (request.workspace / "export-delay").write_text("1", encoding="utf-8")
        captured["evidence"] = execute_live_session(
            connector, request, opt_in=LiveOptIn(True, True)
        )

    run_isolated(exercise)
    assert captured["evidence"].wall_clock_seconds >= 1.0


def test_nested_conflicting_event_identity_is_reconciled_before_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def exercise(space: Runspace) -> None:
        connector, request, _ = _prepare(tmp_path, space, monkeypatch)
        (request.workspace / "conflicting-event").write_text("1", encoding="utf-8")
        with pytest.raises(BoundaryError, match="conflicting event session identity"):
            execute_live_session(connector, request, opt_in=LiveOptIn(True, True))

    run_isolated(exercise)
