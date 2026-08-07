"""Actor interface of the benchmark harness: scripted replay, live opt-in, token accounting."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bench.harness.actor import (
    BASELINE_ARM,
    LIVE_ACTOR_ENV_FLAG,
    STAGES,
    Actor,
    ActorKind,
    DetectionBasis,
    LiveActor,
    LiveActorNotConfiguredError,
    RunRequest,
    ScriptedActor,
    TokenUsage,
    TokenUsageUnavailable,
    detection_evidence,
    token_usage_available,
)
from bench.harness.corpus import CorpusItem
from bench.harness.runspace import Runspace, run_isolated

ITEM_ID = "bench-actor-item"

NOTE_BODY = """# Nota de fuente

## Fuente
- id: s1
- url: https://research.example/report
- titulo: Informe sintetico
- tier: A
- fecha: 2026-01-01

## Extracto
El informe sintetico declara una cifra inventada. [S1]
"""


def _item(**overrides: Any) -> CorpusItem:
    """Build a corpus item in-test, without depending on files under bench/corpus."""
    data: dict[str, Any] = {
        "id": ITEM_ID,
        "mode": "light",
        "title": "Bench actor item",
        "question": "¿Que replica el actor guionado?",
        "planted_defects": (),
        "expected_detection": {},
        "sources": (),
        "artifacts": {},
        "commands": (),
        "probe": None,
        "path": Path("bench/corpus/items") / f"{ITEM_ID}.yaml",
    }
    data.update(overrides)
    return CorpusItem(**data)


def _scripted_item() -> CorpusItem:
    return _item(
        artifacts={f"{ITEM_ID}/notes/s1.md": NOTE_BODY},
        commands=(
            (
                "new",
                ITEM_ID,
                "--title",
                "Bench actor item",
                "--question",
                "¿Que replica el actor guionado?",
                "--mode",
                "light",
                "--owner",
                "bench",
                "--timebox",
                "1",
            ),
            ("check", ITEM_ID, "--json"),
        ),
    )


def _execute(item: CorpusItem, arm: str = "light", actor: ScriptedActor | None = None):
    executor = ScriptedActor() if actor is None else actor

    def work(space: Runspace):
        return executor.execute(RunRequest(item=item, arm=arm, repetition=0, space=space))

    return run_isolated(work)


def test_stage_vocabulary_matches_the_lifecycle() -> None:
    assert STAGES == ("intake", "explore", "probe", "transfer", "reuse")


def test_both_actors_satisfy_the_actor_protocol() -> None:
    assert isinstance(ScriptedActor(), Actor)
    assert isinstance(LiveActor(), Actor)
    assert ScriptedActor().kind is ActorKind.SCRIPTED
    assert LiveActor().kind is ActorKind.LIVE


def test_token_usage_unavailable_is_distinct_from_zero() -> None:
    unavailable = TokenUsageUnavailable(reason="no agent host reported usage")
    zero = TokenUsage(input_tokens=0, output_tokens=0)

    assert unavailable != zero
    assert not token_usage_available(unavailable)
    assert token_usage_available(zero)
    assert zero.total_tokens == 0
    assert not hasattr(unavailable, "total_tokens")


def test_scripted_run_reports_unavailable_token_usage_never_zero() -> None:
    result = _execute(_scripted_item())

    assert result.actor is ActorKind.SCRIPTED
    assert isinstance(result.tokens, TokenUsageUnavailable)
    assert result.tokens.reason
    assert not token_usage_available(result.tokens)


def test_harness_runs_end_to_end_offline_with_the_scripted_actor() -> None:
    item = _scripted_item()
    result = _execute(item)

    assert result.item_id == ITEM_ID
    assert result.arm == "light"
    assert result.slug == ITEM_ID
    assert result.artifacts_written == (f"{ITEM_ID}/notes/s1.md",)

    verbs = [command.argv[0] for command in result.commands]
    assert verbs == ["new", "check"]

    created = result.commands[0]
    assert created.exit_code == 0, created.stderr

    checked = result.commands[1]
    assert checked.payload_error is None
    assert isinstance(checked.payload, dict)
    assert checked.payload["slug"] == ITEM_ID
    assert "passed" in checked.payload
    assert checked.offline_injected is True
    assert "--offline" in checked.argv

    assert result.stages
    assert result.stages[0].stage == "intake"
    assert result.stages[0].duration_seconds >= 0.0
    assert result.duration_seconds >= 0.0
    assert result.detection_basis is DetectionBasis.MEASURED


def test_two_scripted_runs_over_one_item_produce_identical_detection_scoring() -> None:
    item = _scripted_item()

    first = detection_evidence(_execute(item))
    second = detection_evidence(_execute(item))

    assert first == second
    assert first.commands
    assert all(command.payload_error is None for command in first.commands)
    assert any(command.payload_json is not None for command in first.commands)


def test_scripted_baseline_arm_is_labelled_a_control_constant() -> None:
    result = _execute(_scripted_item(), arm=BASELINE_ARM)

    assert result.commands == ()
    assert result.stages == ()
    assert result.artifacts_written == (f"{ITEM_ID}/notes/s1.md",)
    assert result.detection_basis is DetectionBasis.CONTROL_CONSTANT
    assert result.detection_basis_reason


def test_live_actor_is_constructible_and_disabled_without_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(LIVE_ACTOR_ENV_FLAG, raising=False)

    actor = LiveActor()
    assert actor.enabled is False
    assert LiveActor.from_env({}).enabled is False
    assert LiveActor.from_env({LIVE_ACTOR_ENV_FLAG: "0"}).enabled is False
    assert LiveActor.from_env({LIVE_ACTOR_ENV_FLAG: "1"}).enabled is True


def test_live_actor_refuses_to_run_when_not_opted_in() -> None:
    item = _scripted_item()

    def work(space: Runspace) -> None:
        request = RunRequest(item=item, arm="light", repetition=0, space=space)
        with pytest.raises(LiveActorNotConfiguredError, match=LIVE_ACTOR_ENV_FLAG):
            LiveActor().execute(request)
        with pytest.raises(LiveActorNotConfiguredError, match="session"):
            LiveActor(enabled=True).execute(request)

    run_isolated(work)
