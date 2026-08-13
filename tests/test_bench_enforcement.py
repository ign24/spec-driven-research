"""Section-8 prompt, approval, session, and pilot contracts."""

from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bench.harness.enforcement import (
    BoundaryError,
    ImmutableSession,
)
from bench.harness.live import LiveBounds
from bench.harness.pilot import (
    ApprovalEvidence,
    ApprovalProvenance,
    ApprovalState,
    IdentityEvidence,
    OperatorDecisionRecord,
    PilotPlan,
    execute_pilot,
    validate_initial_live_approval,
)
from bench.harness.prompts import (
    EvaluationQuestion,
    HistoryCondition,
    PromptInputs,
    PromptLeakSignals,
    PromptPolicy,
    build_prompt,
    validate_live_prompt,
)


def _live_prompt(*, policy: PromptPolicy = PromptPolicy.ASSISTED):  # type: ignore[no-untyped-def]
    return build_prompt(
        PromptInputs(
            evaluation_question=EvaluationQuestion.CROSS_RETRIEVAL,
            research_question="Which declared option satisfies the focal constraints?",
            arm="light",
            policy=policy,
            history_condition=HistoryCondition.PRESENT,
            stop_at_transfer=True,
        ),
        PromptLeakSignals(),
    )


def _plan(results_root: Path, **changes: Any) -> PilotPlan:
    values: dict[str, Any] = {
        "scenario_id": "scenario-one",
        "item_id": None,
        "arm": "light",
        "repetition": 0,
        "host": "opencode",
        "host_version": "1.18.16",
        "model": "fake/model",
        "model_version": None,
        "prompt": _live_prompt(),
        "bounds": LiveBounds(4, 30.0),
        "results_root": results_root,
    }
    values.update(changes)
    return PilotPlan(**values)


def test_immutable_session_rejects_every_event_identity_conflict() -> None:
    session = ImmutableSession()
    session.observe_event("session-1")
    with pytest.raises(BoundaryError, match="conflicting"):
        session.observe_event("session-2")


def test_live_prompt_is_canonical_hashed_and_contains_stop_before_validation() -> None:
    prompt = _live_prompt()
    evidence = validate_live_prompt(prompt, prompt.text.encode())

    assert "stop at transfer" in prompt.text.casefold()
    assert evidence.template_sha256 == hashlib.sha256(evidence.template_bytes).hexdigest()
    assert evidence.submitted_sha256 == hashlib.sha256(prompt.text.encode()).hexdigest()
    assert evidence.submitted_bytes == prompt.text.encode()


def test_live_prompt_refuses_caller_text_stale_hash_and_post_validation_mutation() -> None:
    prompt = _live_prompt()
    with pytest.raises((TypeError, BoundaryError)):
        validate_live_prompt("caller", b"caller")  # type: ignore[arg-type]
    with pytest.raises(BoundaryError, match="submitted"):
        validate_live_prompt(prompt, prompt.text.encode() + b"mutation")
    with pytest.raises(BoundaryError, match="template"):
        validate_live_prompt(
            replace(prompt, template=replace(prompt.template, sha256="0" * 64)),
            prompt.text.encode(),
        )


@pytest.mark.parametrize(
    ("state", "provenance", "operator_decision", "synthetic_reference"),
    (
        (ApprovalState.NOT_REACHED, ApprovalProvenance.OPERATOR, None, None),
        (ApprovalState.OPERATOR_PENDING, ApprovalProvenance.NOT_REACHED, None, None),
        (ApprovalState.OPERATOR_PENDING, ApprovalProvenance.OPERATOR, "unexpected", None),
        (ApprovalState.OPERATOR_APPROVED, ApprovalProvenance.OPERATOR, None, None),
        (ApprovalState.SYNTHETIC_APPROVED, ApprovalProvenance.OPERATOR, None, "record"),
        (ApprovalState.SYNTHETIC_REJECTED, ApprovalProvenance.SYNTHETIC, None, None),
    ),
)
def test_approval_consistency_table_rejects_every_invalid_combination(
    state: ApprovalState,
    provenance: ApprovalProvenance,
    operator_decision: object,
    synthetic_reference: str | None,
) -> None:
    with pytest.raises(ValueError, match="approval"):
        ApprovalEvidence(state, provenance, operator_decision, synthetic_reference)  # type: ignore[arg-type]


def test_approval_states_are_explicit_and_synthetic_is_not_initial_live_evidence() -> None:
    assert ApprovalEvidence.not_reached().state is ApprovalState.NOT_REACHED
    assert ApprovalEvidence.operator_pending().state is ApprovalState.OPERATOR_PENDING
    decision = OperatorDecisionRecord("operator-1", "run-1", "session-1", True)
    assert ApprovalEvidence.operator_decided(decision).state is ApprovalState.OPERATOR_APPROVED
    synthetic = ApprovalEvidence.synthetic_decided(False, "fixture-1")
    with pytest.raises(BoundaryError, match="synthetic"):
        validate_initial_live_approval(synthetic)


@pytest.mark.parametrize(
    "changes",
    (
        {"scenario_id": None, "item_id": None},
        {"scenario_id": "s", "item_id": "i"},
        {"arm": ("light", "full")},
        {"repetition": (0, 1)},
        {"host": ("opencode", "other")},
        {"model": ("fake/model", "other/model")},
        {"bounds": (LiveBounds(1, 1), LiveBounds(2, 2))},
    ),
)
def test_pilot_plan_cannot_expand_beyond_one_session(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _plan(tmp_path, **changes)


def test_initial_reuse_pilot_is_assisted_only(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="assisted"):
        _plan(tmp_path, prompt=_live_prompt(policy=PromptPolicy.UNASSISTED))


def test_pilot_execution_has_no_run_one_or_mapping_identity_escape_hatch() -> None:
    assert "run_one" not in inspect.signature(execute_pilot).parameters
    assert tuple(IdentityEvidence.__dataclass_fields__) == (
        "manifest",
        "sealed_request",
        "live",
    )
