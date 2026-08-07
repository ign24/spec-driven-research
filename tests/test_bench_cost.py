"""Cost accounting of the benchmark harness: wall-clock, stage breakdown, token coverage.

Every fixture here builds stage boundaries and token usage explicitly. Nothing sleeps
and nothing reads a real clock, so the assertions are exact rather than approximate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.harness.actor import (
    SCRIPTED_TOKENS,
    ActorKind,
    ActorResult,
    DetectionBasis,
    StageBoundary,
    TokenUsage,
    TokenUsageUnavailable,
)
from bench.harness.arms import ArmOutcome, ArmRun, TerminalState
from bench.harness.cost import (
    OMITTED_DURATION,
    CostAccountingError,
    DurationReporting,
    StageElapsed,
    StageNotRun,
    StageTotal,
    arm_cost,
    cost_available,
    cost_for_run,
    cost_for_runs,
    format_arm_cost,
    format_run_cost,
    relative_to_baseline,
    stage_ran,
)

ITEM_ID = "bench-cost-item"


def _result(
    *,
    actor: ActorKind = ActorKind.SCRIPTED,
    arm: str = "light",
    repetition: int = 0,
    stages: tuple[StageBoundary, ...] = (),
    tokens: object = SCRIPTED_TOKENS,
    duration: float = 0.0,
    item_id: str = ITEM_ID,
) -> ActorResult:
    return ActorResult(
        actor=actor,
        item_id=item_id,
        arm=arm,
        repetition=repetition,
        slug="cost-slug",
        artifacts_written=(),
        commands=(),
        stages=stages,
        tokens=tokens,  # type: ignore[arg-type]
        detection_basis=DetectionBasis.MEASURED,
        detection_basis_reason="measured",
        duration_seconds=duration,
        workspace=Path("/nonexistent/workspace"),
        research_root=Path("/nonexistent/workspace/research"),
    )


def _run(
    result: ActorResult | None = None,
    *,
    outcome: ArmOutcome = ArmOutcome.EXECUTED,
    arm: str = "light",
    repetition: int = 0,
    item_id: str = ITEM_ID,
) -> ArmRun:
    return ArmRun(
        item_id=item_id,
        arm=arm,
        repetition=repetition,
        actor=ActorKind.SCRIPTED if result is None else result.actor,
        outcome=outcome,
        terminal_state=TerminalState(recorded=False),
        result=result,
        detection_basis=None if result is None else result.detection_basis,
    )


def _boundaries(*spans: tuple[str, float, float]) -> tuple[StageBoundary, ...]:
    return tuple(
        StageBoundary(stage=stage, started_at=start, ended_at=end) for stage, start, end in spans
    )


# 8.1 / 8.2 -- wall-clock total and per-stage breakdown.


def test_run_cost_carries_total_wall_clock_and_stage_breakdown() -> None:
    result = _result(
        stages=_boundaries(
            ("intake", 10.0, 12.5),
            ("explore", 12.5, 16.0),
            ("transfer", 16.0, 17.0),
            ("reuse", 17.0, 17.25),
        ),
        duration=8.0,
    )
    cost = cost_for_run(_run(result))

    assert cost_available(cost)
    assert cost.total_seconds == 8.0
    assert cost.stage_seconds == {
        "intake": 2.5,
        "explore": 3.5,
        "transfer": 1.0,
        "reuse": 0.25,
    }
    assert tuple(entry.stage for entry in cost.stages) == ("intake", "explore", "transfer", "reuse")
    assert all(stage_ran(entry) for entry in cost.stages)


def test_repeated_visits_to_one_stage_are_summed() -> None:
    result = _result(
        stages=_boundaries(("intake", 0.0, 1.0), ("explore", 1.0, 2.0), ("explore", 5.0, 6.5)),
        duration=6.5,
    )
    cost = cost_for_run(_run(result))

    assert cost_available(cost)
    assert cost.stage_seconds["explore"] == 2.5


def test_stage_never_run_is_distinct_from_a_zero_duration_stage() -> None:
    result = _result(
        stages=_boundaries(("intake", 0.0, 1.0), ("explore", 1.0, 1.0)),
        duration=1.0,
    )
    cost = cost_for_run(_run(result))

    assert cost_available(cost)
    explore = cost.stage("explore")
    assert isinstance(explore, StageElapsed)
    assert explore.seconds == 0.0

    transfer = cost.stage("transfer")
    assert isinstance(transfer, StageNotRun)
    assert not stage_ran(transfer)
    assert not hasattr(transfer, "seconds")
    assert "transfer" not in cost.stage_seconds
    assert tuple(entry.stage for entry in cost.skipped_stages) == ("transfer", "reuse")


def test_full_arm_expects_the_probe_stage_and_light_arm_does_not() -> None:
    full = cost_for_run(_run(_result(arm="full"), arm="full"))
    light = cost_for_run(_run(_result(arm="light"), arm="light"))

    assert cost_available(full)
    assert cost_available(light)
    assert tuple(entry.stage for entry in full.stages) == (
        "intake",
        "explore",
        "probe",
        "transfer",
        "reuse",
    )
    assert "probe" not in {entry.stage for entry in light.stages}


def test_baseline_arm_expects_no_stage_at_all() -> None:
    cost = cost_for_run(_run(_result(arm="baseline"), arm="baseline"))

    assert cost_available(cost)
    assert cost.stages == ()


def test_runs_without_a_result_are_not_costed_as_zero() -> None:
    not_applicable = cost_for_run(_run(None, outcome=ArmOutcome.NOT_APPLICABLE, arm="full"))
    errored = cost_for_run(_run(None, outcome=ArmOutcome.ERRORED))

    for accounting in (not_applicable, errored):
        assert not cost_available(accounting)
        assert not hasattr(accounting, "total_seconds")
        assert accounting.reason


def test_uncosted_runs_are_excluded_from_the_arm_aggregate() -> None:
    runs = (
        _run(_result(stages=_boundaries(("intake", 0.0, 2.0)), duration=2.0)),
        _run(None, outcome=ArmOutcome.ERRORED, repetition=1),
    )
    aggregate = arm_cost(runs, "light")

    assert aggregate.costed_runs == 1
    assert aggregate.uncosted_runs == 1
    assert aggregate.total_seconds == 2.0
    assert aggregate.mean_seconds == 2.0


def test_arm_aggregate_reports_stage_totals_and_stages_no_run_entered() -> None:
    runs = (
        _run(
            _result(stages=_boundaries(("intake", 0.0, 1.0), ("explore", 1.0, 3.0)), duration=3.0)
        ),
        _run(
            _result(stages=_boundaries(("intake", 0.0, 0.5)), duration=0.5, repetition=1),
            repetition=1,
        ),
    )
    aggregate = arm_cost(runs, "light")

    intake = aggregate.stage("intake")
    assert isinstance(intake, StageTotal)
    assert intake.seconds == 1.5
    assert intake.runs == 2

    reuse = aggregate.stage("reuse")
    assert isinstance(reuse, StageNotRun)
    assert not hasattr(reuse, "seconds")


def test_aggregating_across_actor_kinds_is_refused() -> None:
    runs = (
        _run(_result(actor=ActorKind.SCRIPTED)),
        _run(_result(actor=ActorKind.LIVE, repetition=1), repetition=1),
    )
    with pytest.raises(CostAccountingError, match="actor"):
        arm_cost(runs, "light")


# 8.3 / 8.4 -- token accounting, never zero when unavailable.


def test_scripted_run_records_token_usage_as_unavailable_not_zero() -> None:
    cost = cost_for_run(_run(_result()))

    assert cost_available(cost)
    assert isinstance(cost.tokens, TokenUsageUnavailable)
    assert not hasattr(cost.tokens, "input_tokens")
    assert not hasattr(cost.tokens, "total_tokens")
    assert cost.tokens.reason


def test_live_run_records_reported_token_usage() -> None:
    usage = TokenUsage(input_tokens=1200, output_tokens=340, model="fake-model")
    cost = cost_for_run(_run(_result(actor=ActorKind.LIVE, tokens=usage)))

    assert cost_available(cost)
    assert cost.tokens == usage
    assert cost.tokens.total_tokens == 1540


def test_arm_tokens_are_unavailable_when_no_run_reported_usage() -> None:
    runs = (_run(_result()), _run(_result(repetition=1), repetition=1))
    aggregate = arm_cost(runs, "light")

    assert isinstance(aggregate.tokens, TokenUsageUnavailable)
    assert not hasattr(aggregate.tokens, "total_tokens")
    assert aggregate.token_coverage.reporting_runs == 0
    assert aggregate.token_coverage.costed_runs == 2
    assert aggregate.token_coverage.share == 0.0
    assert not aggregate.token_coverage.complete


def test_partial_token_coverage_sums_only_reporting_runs_and_says_so() -> None:
    runs = (
        _run(
            _result(
                actor=ActorKind.LIVE,
                tokens=TokenUsage(input_tokens=100, output_tokens=10, model="fake-model"),
            )
        ),
        _run(
            _result(
                actor=ActorKind.LIVE,
                tokens=TokenUsage(input_tokens=200, output_tokens=20, model="fake-model"),
                repetition=1,
            ),
            repetition=1,
        ),
        _run(
            _result(
                actor=ActorKind.LIVE,
                tokens=TokenUsageUnavailable(reason="the agent host reported no usage"),
                repetition=2,
            ),
            repetition=2,
        ),
    )
    aggregate = arm_cost(runs, "light")

    assert isinstance(aggregate.tokens, TokenUsage)
    assert aggregate.tokens.input_tokens == 300
    assert aggregate.tokens.output_tokens == 30
    assert aggregate.tokens.total_tokens == 330
    assert aggregate.token_coverage.reporting_runs == 2
    assert aggregate.token_coverage.costed_runs == 3
    assert aggregate.token_coverage.share == pytest.approx(2 / 3)
    assert not aggregate.token_coverage.complete


def test_mixed_models_drop_the_model_label_rather_than_guessing() -> None:
    runs = (
        _run(_result(actor=ActorKind.LIVE, tokens=TokenUsage(1, 1, model="fake-a"))),
        _run(
            _result(actor=ActorKind.LIVE, tokens=TokenUsage(1, 1, model="fake-b"), repetition=1),
            repetition=1,
        ),
    )
    aggregate = arm_cost(runs, "light")

    assert isinstance(aggregate.tokens, TokenUsage)
    assert aggregate.tokens.model is None


# Deterministic rendering for the report (Decision 7).


def test_measured_rendering_uses_fixed_precision() -> None:
    result = _result(stages=_boundaries(("intake", 0.0, 1.0 / 3.0)), duration=1.0 / 3.0)
    row = format_run_cost(cost_for_run(_run(result)))

    assert row["total_seconds"] == "0.333"
    assert row["stage.intake"] == "0.333"
    assert row["stage.reuse"] == "not-run"
    assert row["tokens_total"] == "unavailable"


def test_omitted_duration_rendering_is_byte_identical_across_different_clocks() -> None:
    slow = _result(stages=_boundaries(("intake", 0.0, 41.7)), duration=93.2)
    fast = _result(stages=_boundaries(("intake", 0.0, 0.4)), duration=0.9)

    rendered = {
        format_run_cost(cost_for_run(_run(result)), durations=DurationReporting.OMITTED)[key]
        for result in (slow, fast)
        for key in ("total_seconds", "stage.intake")
    }
    assert rendered == {OMITTED_DURATION}

    assert format_run_cost(
        cost_for_run(_run(slow)), durations=DurationReporting.OMITTED
    ) == format_run_cost(cost_for_run(_run(fast)), durations=DurationReporting.OMITTED)


def test_omitting_durations_does_not_alter_the_measured_record() -> None:
    result = _result(stages=_boundaries(("intake", 0.0, 41.7)), duration=93.2)
    cost = cost_for_run(_run(result))

    format_run_cost(cost, durations=DurationReporting.OMITTED)

    assert cost_available(cost)
    assert cost.total_seconds == 93.2
    assert cost.stage_seconds["intake"] == 41.7


def test_arm_rendering_states_actor_and_token_coverage() -> None:
    runs = (
        _run(
            _result(
                actor=ActorKind.LIVE,
                tokens=TokenUsage(input_tokens=10, output_tokens=5, model="fake-model"),
                stages=_boundaries(("intake", 0.0, 1.0)),
                duration=1.0,
            )
        ),
        _run(_result(actor=ActorKind.LIVE, repetition=1), repetition=1),
    )
    row = format_arm_cost(arm_cost(runs, "light"), durations=DurationReporting.OMITTED)

    assert row["actor"] == "live"
    assert row["arm"] == "light"
    assert row["tokens_total"] == "15"
    assert row["token_coverage"] == "1/2"
    assert row["token_coverage_share"] == "0.500"
    assert row["total_seconds"] == OMITTED_DURATION


def test_uncosted_run_rendering_marks_every_metric_as_absent() -> None:
    row = format_run_cost(cost_for_run(_run(None, outcome=ArmOutcome.NOT_APPLICABLE, arm="light")))

    assert row["total_seconds"] == "not-costed"
    assert row["tokens_total"] == "not-costed"
    assert row["outcome"] == "not-applicable"


def test_uncosted_and_costed_rows_share_one_key_order() -> None:
    costed = format_run_cost(cost_for_run(_run(_result())))
    uncosted = format_run_cost(cost_for_run(_run(None, outcome=ArmOutcome.ERRORED)))

    assert tuple(costed) == tuple(uncosted)


def test_relative_to_baseline_refuses_to_compare_across_actors() -> None:
    scripted = arm_cost((_run(_result(arm="light")),), "light")
    live = arm_cost(
        (_run(_result(actor=ActorKind.LIVE, arm="baseline"), arm="baseline"),), "baseline"
    )
    with pytest.raises(CostAccountingError, match="actor"):
        relative_to_baseline(scripted, live)


def test_relative_to_baseline_reports_ratios_only_where_both_sides_exist() -> None:
    baseline = arm_cost(
        (_run(_result(arm="baseline", duration=2.0), arm="baseline"),),
        "baseline",
    )
    light = arm_cost((_run(_result(arm="light", duration=5.0)),), "light")
    relative = relative_to_baseline(light, baseline)

    assert relative.wall_clock_ratio == 2.5
    assert relative.token_ratio is None


def test_cost_for_runs_preserves_run_order() -> None:
    runs = (
        _run(_result(repetition=0)),
        _run(None, outcome=ArmOutcome.ERRORED, repetition=1),
        _run(_result(repetition=2), repetition=2),
    )
    accountings = cost_for_runs(runs)

    assert len(accountings) == 3
    assert [cost_available(entry) for entry in accountings] == [True, False, True]
