"""Cost accounting of the benchmark harness: wall-clock, stage breakdown, token coverage.

What this module reads
----------------------

Everything here derives from :class:`bench.harness.arms.ArmRun` and the
:class:`bench.harness.actor.ActorResult` it carries. Nothing is parsed from prose and
nothing is estimated: the total is the actor's reported run duration, the per-stage
breakdown is the actor's reported :class:`bench.harness.actor.StageBoundary` list, and
token usage is whatever the executing agent host reported, verbatim.

Three properties are load-bearing.

*Unavailable is never zero.* :data:`bench.harness.actor.TokenAccounting` is a union in
which :class:`bench.harness.actor.TokenUsageUnavailable` carries no numeric field at
all, so it cannot be read or summed as zero. This module preserves that property
through aggregation: an arm whose runs all failed to report usage yields
``TokenUsageUnavailable``, never ``TokenUsage(0, 0)``. When coverage is partial, the
aggregate is the sum over the *reporting* runs only, and :class:`TokenCoverage` states
how many runs that was out of how many were costed. A partial total is therefore a
lower bound on the arm, and the report must print the coverage next to it.

*A stage that never ran is not a stage that took zero seconds.* A run that blocks early
produces fewer boundaries than its mode's stage order. The breakdown expresses the
difference by type: :class:`StageElapsed` carries seconds, :class:`StageNotRun` carries
a reason and no numeric field, so an absent stage cannot be summed either.

*Scripted and live costs are never mixed.* Under Decision 1 wall-clock is meaningful for
the live actor; for the scripted actor it measures replay speed, not research effort.
Every aggregate is labelled with the :class:`bench.harness.actor.ActorKind` that
produced it, and :func:`arm_cost` refuses runs of mixed kinds outright.

Determinism (Decision 7)
------------------------

Wall-clock is inherently non-deterministic, so byte-identical report output cannot come
from rounding alone. Rendering therefore takes an explicit :class:`DurationReporting`
mode: ``MEASURED`` prints real seconds at :data:`DURATION_PRECISION` decimals, while
``OMITTED`` replaces every duration field with :data:`OMITTED_DURATION` and leaves token
and coverage fields untouched. The stored record is never modified, so omission hides a
number from one rendering rather than falsifying the measurement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeGuard

from bench.harness.actor import (
    BASELINE_ARM,
    ActorKind,
    ActorResult,
    TokenAccounting,
    TokenUsage,
    TokenUsageUnavailable,
    token_usage_available,
)
from bench.harness.arms import ArmOutcome, ArmRun, runs_for_arm
from sdr.schema import stage_order

#: Decimals used for every duration and share printed by this module.
DURATION_PRECISION: Final[int] = 3

#: Placeholder printed instead of a duration when rendering omits wall-clock.
OMITTED_DURATION: Final[str] = "<omitted>"

#: Printed for a stage the run never entered.
NOT_RUN_LABEL: Final[str] = "not-run"

#: Printed for every metric of a run that produced no actor result.
NOT_COSTED_LABEL: Final[str] = "not-costed"

#: Printed instead of a token count when no usage was reported.
UNAVAILABLE_LABEL: Final[str] = "unavailable"

_STAGE_NOT_ENTERED_REASON: Final[str] = (
    "the run never entered this stage, so it has no duration; this is not a zero-second stage"
)
_ARM_STAGE_NOT_ENTERED_REASON: Final[str] = (
    "no costed run of this arm entered this stage, so it has no duration"
)
_NO_USAGE_REPORTED_REASON: Final[str] = (
    "no costed run of this arm reported token usage, so no total exists; this is not zero tokens"
)


class CostAccountingError(RuntimeError):
    """Raised when cost accounting is asked for something it cannot honestly produce."""


class DurationReporting(StrEnum):
    """How rendering treats wall-clock, which is never reproducible across executions."""

    MEASURED = "measured"
    OMITTED = "omitted"


@dataclass(frozen=True)
class StageElapsed:
    """A stage the run entered, with the wall-clock seconds it remained open."""

    stage: str
    seconds: float


@dataclass(frozen=True)
class StageNotRun:
    """A stage the run never entered. Deliberately not a number."""

    stage: str
    reason: str


#: Either the stage ran and has a duration, or it never ran. Never zero-as-absent.
type StageCost = StageElapsed | StageNotRun


@dataclass(frozen=True)
class StageTotal:
    """One stage summed over the costed runs of an arm, with how many runs entered it."""

    stage: str
    seconds: float
    runs: int


#: Either at least one costed run entered the stage, or none did.
type StageAggregate = StageTotal | StageNotRun


def stage_ran(cost: StageCost | StageAggregate) -> TypeGuard[StageElapsed | StageTotal]:
    """True when the entry carries a duration rather than an absence marker."""
    return isinstance(cost, StageElapsed | StageTotal)


@dataclass(frozen=True)
class TokenCoverage:
    """How many costed runs reported token usage, out of how many were costed."""

    reporting_runs: int
    costed_runs: int

    @property
    def complete(self) -> bool:
        """True when every costed run reported usage. False when nothing was costed."""
        return self.costed_runs > 0 and self.reporting_runs == self.costed_runs

    @property
    def share(self) -> float | None:
        """Reporting share in ``[0, 1]``, or None when no run was costed at all."""
        if self.costed_runs == 0:
            return None
        return self.reporting_runs / self.costed_runs


@dataclass(frozen=True)
class RunCost:
    """The cost of one executed run: total wall-clock, stage breakdown, token accounting."""

    actor: ActorKind
    item_id: str
    arm: str
    repetition: int
    total_seconds: float
    stages: tuple[StageCost, ...]
    tokens: TokenAccounting

    def stage(self, name: str) -> StageCost:
        """The entry for `name`, absent-by-type when the run never entered that stage."""
        for entry in self.stages:
            if entry.stage == name:
                return entry
        return StageNotRun(stage=name, reason=_STAGE_NOT_ENTERED_REASON)

    @property
    def elapsed_stages(self) -> tuple[StageElapsed, ...]:
        """Every stage the run entered, in the mode's declared order."""
        return tuple(entry for entry in self.stages if isinstance(entry, StageElapsed))

    @property
    def skipped_stages(self) -> tuple[StageNotRun, ...]:
        """Every expected stage the run never entered, in the mode's declared order."""
        return tuple(entry for entry in self.stages if isinstance(entry, StageNotRun))

    @property
    def stage_seconds(self) -> dict[str, float]:
        """Seconds per entered stage. Stages that never ran are absent, not zero."""
        return {entry.stage: entry.seconds for entry in self.elapsed_stages}

    @property
    def reports_tokens(self) -> bool:
        """True when the executing agent host reported usage for this run."""
        return token_usage_available(self.tokens)


@dataclass(frozen=True)
class CostUnavailable:
    """Explicit absence of cost for a run that produced no actor result."""

    item_id: str
    arm: str
    repetition: int
    outcome: ArmOutcome
    reason: str


#: Either the run was costed, or it explicitly was not. Never a zero-cost run.
type RunCostAccounting = RunCost | CostUnavailable


def cost_available(accounting: RunCostAccounting) -> TypeGuard[RunCost]:
    """True when the accounting carries measured cost rather than an absence marker."""
    return isinstance(accounting, RunCost)


@dataclass(frozen=True)
class ArmCost:
    """Cost of one arm under one actor. Never spans actor kinds."""

    arm: str
    actor: ActorKind | None
    costed_runs: int
    uncosted_runs: int
    total_seconds: float
    stages: tuple[StageAggregate, ...]
    tokens: TokenAccounting
    token_coverage: TokenCoverage

    @property
    def mean_seconds(self) -> float | None:
        """Mean wall-clock over the costed runs, or None when nothing was costed."""
        if self.costed_runs == 0:
            return None
        return self.total_seconds / self.costed_runs

    def stage(self, name: str) -> StageAggregate:
        """The total for `name`, absent-by-type when no costed run entered that stage."""
        for entry in self.stages:
            if entry.stage == name:
                return entry
        return StageNotRun(stage=name, reason=_ARM_STAGE_NOT_ENTERED_REASON)


@dataclass(frozen=True)
class RelativeCost:
    """One arm's cost expressed against the no-SDR baseline of the same actor."""

    arm: str
    baseline_arm: str
    actor: ActorKind | None
    wall_clock_ratio: float | None
    token_ratio: float | None


def expected_stages(arm: str) -> tuple[str, ...]:
    """The stage order an arm is expected to traverse. The baseline traverses none."""
    if arm == BASELINE_ARM:
        return ()
    return stage_order(arm)


def cost_for_run(run: ArmRun) -> RunCostAccounting:
    """Cost of one run record, or an explicit absence when the run produced no result."""
    if run.outcome is not ArmOutcome.EXECUTED or run.result is None:
        return CostUnavailable(
            item_id=run.item_id,
            arm=run.arm,
            repetition=run.repetition,
            outcome=run.outcome,
            reason=_uncosted_reason(run),
        )
    return RunCost(
        actor=run.result.actor,
        item_id=run.item_id,
        arm=run.arm,
        repetition=run.repetition,
        total_seconds=run.result.duration_seconds,
        stages=_stage_breakdown(run.result, run.arm),
        tokens=run.result.tokens,
    )


def cost_for_runs(runs: Sequence[ArmRun]) -> tuple[RunCostAccounting, ...]:
    """Cost of every run record, in the order the records were given."""
    return tuple(cost_for_run(run) for run in runs)


def costed(accountings: Sequence[RunCostAccounting]) -> tuple[RunCost, ...]:
    """Every accounting that carries measured cost, and no other."""
    return tuple(entry for entry in accountings if cost_available(entry))


def arm_cost(runs: Sequence[ArmRun], arm: str) -> ArmCost:
    """Aggregate the cost of one arm, refusing to mix actor kinds.

    Wall-clock sums over the costed runs. Token usage sums over the runs that actually
    reported usage, which :class:`TokenCoverage` states explicitly; when no run reported,
    the aggregate is unavailable rather than zero.
    """
    selected = runs_for_arm(runs, arm)
    accountings = cost_for_runs(selected)
    measured = costed(accountings)
    actor = _single_actor(measured, arm)

    total_seconds = sum(entry.total_seconds for entry in measured)
    reporting = tuple(entry.tokens for entry in measured if token_usage_available(entry.tokens))
    coverage = TokenCoverage(reporting_runs=len(reporting), costed_runs=len(measured))
    return ArmCost(
        arm=arm,
        actor=actor,
        costed_runs=len(measured),
        uncosted_runs=len(accountings) - len(measured),
        total_seconds=total_seconds,
        stages=_stage_aggregate(measured, arm),
        tokens=_sum_usage(reporting),
        token_coverage=coverage,
    )


def relative_to_baseline(cost: ArmCost, baseline: ArmCost) -> RelativeCost:
    """Express one arm against the baseline arm of the same actor.

    A ratio is produced only where both sides carry a comparable number: a baseline
    without costed runs, or either side without reported token usage, yields None
    instead of a fabricated comparison.
    """
    if cost.actor is not baseline.actor:
        raise CostAccountingError(
            "cost is comparable only within one actor kind; refusing to compare "
            f"{cost.arm!r} under {_actor_label(cost.actor)!r} against {baseline.arm!r} under "
            f"{_actor_label(baseline.actor)!r}"
        )
    return RelativeCost(
        arm=cost.arm,
        baseline_arm=baseline.arm,
        actor=cost.actor,
        wall_clock_ratio=_ratio(cost.total_seconds, baseline.total_seconds),
        token_ratio=_token_ratio(cost.tokens, baseline.tokens),
    )


def format_seconds(
    seconds: float | None, durations: DurationReporting = DurationReporting.MEASURED
) -> str:
    """Render a duration at fixed precision, or omit it for reproducible output."""
    if durations is DurationReporting.OMITTED:
        return OMITTED_DURATION
    if seconds is None:
        return NOT_COSTED_LABEL
    return f"{seconds:.{DURATION_PRECISION}f}"


def format_run_cost(
    accounting: RunCostAccounting,
    durations: DurationReporting = DurationReporting.MEASURED,
) -> dict[str, str]:
    """Render one run's cost as report fields, in a stable key order.

    Costed and uncosted runs share one key order so a report can print them in one
    table without either becoming a zero row.
    """
    stages = expected_stages(accounting.arm)
    if not cost_available(accounting):
        row = _row(
            item_id=accounting.item_id,
            arm=accounting.arm,
            repetition=str(accounting.repetition),
            outcome=accounting.outcome.value,
            actor=NOT_COSTED_LABEL,
            total_seconds=NOT_COSTED_LABEL,
            tokens=(NOT_COSTED_LABEL, NOT_COSTED_LABEL, NOT_COSTED_LABEL),
            tokens_note=accounting.reason,
        )
        row.update({f"stage.{stage}": NOT_COSTED_LABEL for stage in stages})
        return row

    row = _row(
        item_id=accounting.item_id,
        arm=accounting.arm,
        repetition=str(accounting.repetition),
        outcome=ArmOutcome.EXECUTED.value,
        actor=accounting.actor.value,
        total_seconds=format_seconds(accounting.total_seconds, durations),
        tokens=_token_fields(accounting.tokens),
        tokens_note=_token_note(accounting.tokens),
    )
    row.update(
        {
            f"stage.{entry.stage}": (
                format_seconds(entry.seconds, durations)
                if isinstance(entry, StageElapsed)
                else NOT_RUN_LABEL
            )
            for entry in accounting.stages
        }
    )
    return row


def format_arm_cost(
    cost: ArmCost, durations: DurationReporting = DurationReporting.MEASURED
) -> dict[str, str]:
    """Render one arm's cost as report fields, coverage included, in a stable key order."""
    row = {
        "arm": cost.arm,
        "actor": _actor_label(cost.actor),
        "costed_runs": str(cost.costed_runs),
        "uncosted_runs": str(cost.uncosted_runs),
        "total_seconds": format_seconds(cost.total_seconds, durations),
        "mean_seconds": format_seconds(cost.mean_seconds, durations),
    }
    total, input_tokens, output_tokens = _token_fields(cost.tokens)
    row["tokens_total"] = total
    row["tokens_input"] = input_tokens
    row["tokens_output"] = output_tokens
    row["tokens_note"] = _token_note(cost.tokens)
    coverage = cost.token_coverage
    row["token_coverage"] = f"{coverage.reporting_runs}/{coverage.costed_runs}"
    row["token_coverage_share"] = _format_share(cost.token_coverage.share)
    row.update(
        {
            f"stage.{entry.stage}": (
                format_seconds(entry.seconds, durations)
                if isinstance(entry, StageTotal)
                else NOT_RUN_LABEL
            )
            for entry in cost.stages
        }
    )
    return row


def _row(
    *,
    item_id: str,
    arm: str,
    repetition: str,
    outcome: str,
    actor: str,
    total_seconds: str,
    tokens: tuple[str, str, str],
    tokens_note: str,
) -> dict[str, str]:
    total, input_tokens, output_tokens = tokens
    return {
        "item_id": item_id,
        "arm": arm,
        "repetition": repetition,
        "outcome": outcome,
        "actor": actor,
        "total_seconds": total_seconds,
        "tokens_total": total,
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "tokens_note": tokens_note,
    }


def _stage_breakdown(result: ActorResult, arm: str) -> tuple[StageCost, ...]:
    """Project reported boundaries onto the arm's expected stage order.

    Stages appear in the order the arm declares, so two runs of one arm always render
    the same columns; a stage without a boundary becomes :class:`StageNotRun`.
    """
    order = expected_stages(arm)
    if not order:
        return ()
    durations = result.stage_durations()
    breakdown: list[StageCost] = []
    for stage in order:
        if stage in durations:
            breakdown.append(StageElapsed(stage=stage, seconds=durations[stage]))
        else:
            breakdown.append(StageNotRun(stage=stage, reason=_STAGE_NOT_ENTERED_REASON))
    extra = tuple(stage for stage in durations if stage not in order)
    breakdown.extend(StageElapsed(stage=stage, seconds=durations[stage]) for stage in extra)
    return tuple(breakdown)


def _stage_aggregate(costs: Sequence[RunCost], arm: str) -> tuple[StageAggregate, ...]:
    order = expected_stages(arm)
    if not order:
        return ()
    totals: dict[str, tuple[float, int]] = {}
    for cost in costs:
        for entry in cost.elapsed_stages:
            seconds, runs = totals.get(entry.stage, (0.0, 0))
            totals[entry.stage] = (seconds + entry.seconds, runs + 1)
    aggregate: list[StageAggregate] = []
    for stage in order:
        if stage in totals:
            seconds, runs = totals[stage]
            aggregate.append(StageTotal(stage=stage, seconds=seconds, runs=runs))
        else:
            aggregate.append(StageNotRun(stage=stage, reason=_ARM_STAGE_NOT_ENTERED_REASON))
    return tuple(aggregate)


def _sum_usage(reporting: Sequence[TokenUsage]) -> TokenAccounting:
    """Sum the runs that reported usage. Nothing reported means unavailable, not zero."""
    if not reporting:
        return TokenUsageUnavailable(reason=_NO_USAGE_REPORTED_REASON)
    models = {usage.model for usage in reporting}
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in reporting),
        output_tokens=sum(usage.output_tokens for usage in reporting),
        model=models.pop() if len(models) == 1 else None,
    )


def _single_actor(costs: Sequence[RunCost], arm: str) -> ActorKind | None:
    kinds = {cost.actor for cost in costs}
    if len(kinds) > 1:
        listed = ", ".join(sorted(kind.value for kind in kinds))
        raise CostAccountingError(
            f"arm {arm!r} mixes results from more than one actor kind ({listed}); cost is "
            "meaningful only within one actor and must never be aggregated across kinds"
        )
    return kinds.pop() if kinds else None


def _uncosted_reason(run: ArmRun) -> str:
    if run.outcome is ArmOutcome.NOT_APPLICABLE:
        return (
            "the item does not apply to this arm, so no run took place and no cost exists; "
            "this is not a zero-cost run"
        )
    if run.outcome is ArmOutcome.ERRORED:
        detail = run.error or "the run failed before reporting cost"
        return f"the run errored, so its cost is incomplete and is not counted: {detail}"
    return "the run produced no actor result, so no cost was reported"


def _token_fields(accounting: TokenAccounting) -> tuple[str, str, str]:
    if token_usage_available(accounting):
        return (
            str(accounting.total_tokens),
            str(accounting.input_tokens),
            str(accounting.output_tokens),
        )
    return (UNAVAILABLE_LABEL, UNAVAILABLE_LABEL, UNAVAILABLE_LABEL)


def _token_note(accounting: TokenAccounting) -> str:
    if token_usage_available(accounting):
        return accounting.model or ""
    return accounting.reason


def _format_share(share: float | None) -> str:
    if share is None:
        return NOT_COSTED_LABEL
    return f"{share:.{DURATION_PRECISION}f}"


def _ratio(value: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return value / reference


def _token_ratio(accounting: TokenAccounting, reference: TokenAccounting) -> float | None:
    if not token_usage_available(accounting) or not token_usage_available(reference):
        return None
    return _ratio(float(accounting.total_tokens), float(reference.total_tokens))


def _actor_label(actor: ActorKind | None) -> str:
    return NOT_COSTED_LABEL if actor is None else actor.value
