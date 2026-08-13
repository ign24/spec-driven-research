"""Complete synthetic-but-traceable schema-v2 record fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

SHA = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def projection_hash(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def complete_record(question: str) -> dict[str, Any]:
    """Return a complete valid record for one Design Decision 11 question."""
    cross = question == "cross-retrieval"
    live = question == "live-single-investigation"
    observed = {"source_identity": "https://example.invalid/source", "claims": []}
    expected = copy.deepcopy(observed)
    return {
        "schema_version": 2,
        "run_id": f"run-{question}",
        "evaluation_question": question,
        "actor": "live" if live else "scripted",
        "scenario_id": "reuse-scenario" if cross else None,
        "item_id": None if cross else "lifecycle-item",
        "arm": "light",
        "repetition": 0,
        "started_at": "2026-08-11T10:00:00Z",
        "terminal_state": "awaiting-operator-approval" if live else "completed",
        "results_root": {"identity": "external-results-01", "external": True},
        "corpus": {
            "version": "2026.08",
            "migration_provenance_version": "baseline-provenance-v1",
            "scenario_manifest_sha256": SHA,
            "seed_artifact_sha256": {"seed-a": SHA_B} if cross else {},
            "focal_investigation": "focal-a",
            "history_condition": "history-present" if cross else "not-applicable",
            "seed_immutability": {
                "checked": cross,
                "unchanged": True if cross else None,
                "pre_sha256": {"seed-a": SHA_B} if cross else {},
                "post_sha256": {"seed-a": SHA_B} if cross else {},
            },
        },
        "prompt": {
            "policy": "assisted" if cross else "standard",
            "template_id": f"sdr-bench.{question}.light",
            "template_version": "1",
            "template_sha256": SHA,
            "submitted_prompt_sha256": SHA_B,
            "built_prompt_sha256": SHA_B,
            "canonical_built_prompt": True,
            "leak_validation_passed": True,
        },
        "execution": {
            "environment_policy": "live-opencode" if live else "scripted-allowlist",
            "environment_policy_version": "1",
            "executable": {
                "path": "/usr/bin/opencode" if live else "/usr/bin/python3",
                "sha256": SHA,
                "package_identity": "opencode" if live else "sdr",
                "package_sha256": SHA_B,
            },
            "repository_audit": {"before_sha256": SHA, "after_sha256": SHA, "unchanged": True},
            "process_group": {"identity": 42 if live else None, "reaped": True},
            "bounds": {
                "max_turns": 8 if live else None,
                "wall_clock_seconds": 300.0 if live else None,
            },
            "exceeded_bound": None,
            "live_boundary": (
                {
                    "credential_allowlist_identity": "opencode-fixed-v1",
                    "xdg_roots": {
                        "config": "runspace:xdg-config",
                        "data": "runspace:xdg-data",
                        "cache": "runspace:xdg-cache",
                        "state": "runspace:xdg-state",
                    },
                    "identities": {
                        name: {
                            "path": f"runspace:{name}",
                            "device": 1,
                            "inode": index,
                            "sha256": SHA,
                        }
                        for index, name in enumerate(("executable", "config", "plugin"), start=1)
                    },
                    "revalidation_boundaries": ["startup", "dispatch", "export"],
                    "effective_config_isolated": True,
                    "mediator_token_undisclosed": True,
                    "subprocesses_cancelled": True,
                    "subprocesses_joined": True,
                }
                if live
                else None
            ),
        },
        "live": (
            {
                "host": "opencode",
                "host_version": "1.0.0",
                "model": "provider/model",
                "model_version": None,
                "session_id": "session-01",
                "session_attribution_valid": True,
                "session_attribution_reason": None,
                "transcript_persisted": False,
            }
            if live
            else None
        ),
        "usage": {
            "wall_clock_seconds": 12.5,
            "stage_durations": [{"stage": "intake", "seconds": 2.5}],
            "tokens": {
                "state": "observed" if live else "unavailable",
                "input": 100 if live else None,
                "output": 20 if live else None,
                "reason": None if live else "scripted actor reports no host usage",
            },
            "money": {
                "state": "observed" if live else "unavailable",
                "amount": "0.012" if live else None,
                "currency": "USD" if live else None,
                "reason": None if live else "scripted actor has no monetary usage",
            },
        },
        "friction": [
            {"control": control, "events": [], "evidence_ref_ids": ["ev-cli"]}
            for control in (
                "structural",
                "evidential",
                "textual-anchoring",
                "executable",
                "hash-consistency",
                "human-approval",
            )
        ],
        "lifecycle": (
            {
                "detections": [
                    {
                        "defect": "unreachable-source",
                        "outcome": "caught",
                        "reporting_control": "check:links_resolve",
                        "reason": None,
                        "evidence_ref_ids": ["ev-cli"],
                    }
                ],
                "mutation_identity": "reject-missing-source" if not live else None,
                "baseline_run_id": "baseline-run" if not live else None,
            }
            if not cross
            else None
        ),
        "cross": (
            {
                "consultation": "correct",
                "command_identity": "cross source source-a --json",
                "query_identity": "https://example.invalid/source",
                "observed_projection": observed,
                "observed_projection_sha256": projection_hash(observed),
                "expected_projection": expected,
                "expected_projection_sha256": projection_hash(expected),
                "checks": [
                    {
                        "id": "positive-a",
                        "kind": "positive",
                        "command_identity": "cross source https://example.invalid/source --json",
                        "outcome": "correct",
                        "reason": "exact structured projection matched",
                        "expected_projection": expected,
                        "expected_projection_sha256": projection_hash(expected),
                        "observed_projection": observed,
                        "observed_projection_sha256": projection_hash(observed),
                        "evidence_ref_ids": ["ev-cli"],
                    }
                ],
                "negative_controls": [
                    {"id": "negative-a", "outcome": "correct", "evidence_ref_ids": ["ev-cli"]}
                ],
                "metamorphic_relation": "seed-order-invariance",
                "source_run_ids": ["source-run-a"],
                "resolver_chain_identity": "resolver-v1",
            }
            if cross
            else None
        ),
        "approval": {
            "provenance": "operator" if live else "not-reached",
            "state": "operator-pending" if live else "not-reached",
            "operator_record_ref": None,
            "synthetic_reference": None,
            "synthetic_excluded_from_live": live,
        },
        "mediation": (
            {
                "outcomes": ["tool-policy-enforced"],
                "protected_file_hashes": [
                    {"path": "runspace:focal/sdr.yaml", "before": SHA, "after": SHA}
                ],
                "intentional_transfer_stop": True,
                "metadata_transitions": [
                    {
                        "command": "advance",
                        "expected": {"stage": "transfer"},
                        "observed": {"stage": "transfer"},
                    }
                ],
                "status_check_consistent": True,
                "manifest_sha256": SHA,
                "sealed_request_sha256": SHA_B,
                "observed_identity_evidence_ref_ids": ["ev-manifest", "ev-cli"],
            }
            if live
            else None
        ),
        "evidence": [
            {
                "id": "ev-cli",
                "kind": "structured-cli-field",
                "artifact_path": "evidence/actor.json",
                "command_index": 0,
                "exit_code": 0,
                "structured_field": "results[0]",
            },
            {
                "id": "ev-manifest",
                "kind": "artifact",
                "artifact_path": "evidence/manifest.json",
                "command_index": None,
                "exit_code": None,
                "structured_field": None,
            },
        ],
    }
