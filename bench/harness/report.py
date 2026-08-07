"""Deterministic comparison report over durable run records.

What this module reads
----------------------

A :class:`bench.harness.record.RunRecordSet` and nothing else. The runspace is gone by
the time a record exists, and reporting must never reopen a corpus file, a research
root, or repository evidence: whatever the report states was already captured as a
metric with its own evidence reference. That is the whole point of the record envelope,
and it is what makes a report reproducible from a stored artifact.

Separation by actor (Decision 1)
--------------------------------

Detection is measured under the scripted actor; cost and friction are meaningful under
the live actor. Mixing them in one number would be misleading, so the report renders one
section per :class:`bench.harness.actor.ActorKind` present in the record set and never
aggregates across kinds. :func:`bench.harness.cost.relative_to_baseline` enforces the
same rule for cost, and a record's actor identity is retained even when the run errored
or did not apply, so section membership never depends on a successful execution.

The scripted no-SDR baseline is degenerate (Decision 2): no gate runs, so every planted
defect is missed by construction. Records carry that as
:data:`bench.harness.actor.DetectionBasis.CONTROL_CONSTANT`, and this module prints
:data:`CONTROL_CONSTANT_LABEL` instead of a detection rate rather than reporting a
measured 0%.

Determinism (Decision 7)
------------------------

Rows are ordered by item, then arm, then repetition; arms follow their declared order and
control counts follow the documented control vocabulary. Every number is rendered at
:data:`RATE_PRECISION` decimals through :func:`bench.harness.cost.format_seconds` and the
share formatter, and no timestamp appears in the body. Two renderings of one unchanged
record set are therefore byte-identical. That is a claim about rendering, not about
independent executions: wall-clock varies between runs, and
:class:`bench.harness.cost.DurationReporting` exists for callers who need duration
omitted from the output entirely.

Relative cost is computed only over matched baseline records with the same actor, item,
and repetition. Unmatched runs are still counted and printed, but they never enter the
ratio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from bench.harness.actor import (
    ARMS,
    BASELINE_ARM,
    ActorKind,
    TokenAccounting,
    TokenUsage,
    TokenUsageUnavailable,
    token_usage_available,
)
from bench.harness.arms import ArmOutcome
from bench.harness.cost import (
    DURATION_PRECISION,
    NOT_COSTED_LABEL,
    NOT_RUN_LABEL,
    UNAVAILABLE_LABEL,
    ArmCost,
    DurationReporting,
    RelativeCost,
    RunCost,
    StageAggregate,
    StageCost,
    StageElapsed,
    StageNotRun,
    StageTotal,
    TokenCoverage,
    expected_stages,
    format_arm_cost,
    format_seconds,
    relative_to_baseline,
)
from bench.harness.detection import DetectionOutcome
from bench.harness.friction import CONTROL_VOCABULARY, ControlType
from bench.harness.record import MetricState, RunRecord, RunRecordSet

#: Decimals used for every rate and ratio printed by this module.
RATE_PRECISION: Final[int] = DURATION_PRECISION

#: Printed instead of a detection rate that is true by construction.
CONTROL_CONSTANT_LABEL: Final[str] = "control-constant"

#: Printed when no record of an arm carries a measured detection outcome.
NOT_MEASURED_LABEL: Final[str] = "not-measured"

#: Printed when an arm has measured runs but no planted defect to score.
NO_PLANTED_DEFECTS_LABEL: Final[str] = "no-planted-defects"

#: Printed in the relative-cost columns of the baseline arm itself.
BASELINE_LABEL: Final[str] = "baseline"

#: Printed when no matched baseline record supports a ratio.
NO_COMPARISON_LABEL: Final[str] = "no-comparison"

#: Actor sections are rendered in this order, and only when records exist for them.
ACTOR_ORDER: Final[tuple[ActorKind, ...]] = (ActorKind.SCRIPTED, ActorKind.LIVE)

_TITLE: Final[str] = "# SDR evaluation harness comparison report"
_NO_STAGE_LABEL: Final[str] = "no lifecycle stage"
_NONE_LABEL: Final[str] = "none"


class ReportError(ValueError):
    """Raised when a run record cannot be reported without inventing a value."""


@dataclass(frozen=True)
class DetectionSummary:
    """Planted-defect detection for one arm under one actor."""

    arm: str
    executed_runs: int
    measured_runs: int
    control_constant_runs: int
    planted: int
    caught: int
    missed: int
    not_exercised: int
    false_positives: int
    basis: str

    @property
    def rate(self) -> float | None:
        """Caught share over measured runs, or None when no rate may be claimed."""
        denominator = self.caught + self.missed
        if self.measured_runs == 0 or denominator == 0:
            return None
        return self.caught / denominator


@dataclass(frozen=True)
class FrictionSummary:
    """Lifecycle friction for one arm under one actor, in vocabulary order."""

    arm: str
    reopens: int
    reopens_unavailable: int
    failures_by_control: Mapping[ControlType, int]
    unmapped: int
    resolve_claim_closures: int
    passed_anchoring: int


@dataclass(frozen=True)
class UnmappedEntry:
    """One gate failure the harness refused to attribute, kept visible in the report."""

    item_id: str
    arm: str
    repetition: int
    reason: str
    exit_code: int


@dataclass(frozen=True)
class ArmSummary:
    """Everything the report states about one arm under one actor."""

    arm: str
    actor: ActorKind
    detection: DetectionSummary
    cost: ArmCost
    relative: RelativeCost | None
    matched_runs: int
    friction: FrictionSummary


@dataclass(frozen=True)
class ActorSection:
    """One report section. Sections are never aggregated with one another."""

    actor: ActorKind
    arms: tuple[ArmSummary, ...]
    records: tuple[RunRecord, ...]
    unmapped: tuple[UnmappedEntry, ...]


def build_sections(records: RunRecordSet) -> tuple[ActorSection, ...]:
    """Summarize the record set into one section per actor, in declared order."""
    sections: list[ActorSection] = []
    for actor in ACTOR_ORDER:
        selected = tuple(record for record in records.records if record.actor is actor)
        if not selected:
            continue
        sections.append(
            ActorSection(
                actor=actor,
                arms=tuple(_arm_summary(selected, arm, actor) for arm in _arms_present(selected)),
                records=tuple(sorted(selected, key=_run_order)),
                unmapped=_unmapped_entries(selected),
            )
        )
    return tuple(sections)


def render_report(
    records: RunRecordSet, *, durations: DurationReporting = DurationReporting.MEASURED
) -> str:
    """Render the comparison report as deterministic UTF-8 text with a trailing newline."""
    sections = build_sections(records)
    lines: list[str] = [
        _TITLE,
        "",
        f"corpus_version: {records.corpus_version}",
        f"repetitions: {records.repetitions}",
        f"run_records: {len(records.records)}",
        f"actors: {', '.join(section.actor.value for section in sections) or _NONE_LABEL}",
    ]
    for section in sections:
        lines.extend(_render_section(section, durations))
    return "\n".join(lines) + "\n"


def _arms_present(records: Sequence[RunRecord]) -> tuple[str, ...]:
    present = {record.arm for record in records}
    return tuple(arm for arm in ARMS if arm in present)


def _arm_summary(records: Sequence[RunRecord], arm: str, actor: ActorKind) -> ArmSummary:
    selected = tuple(record for record in records if record.arm == arm)
    cost = _arm_cost(selected, arm)
    matched = _matched_keys(records, arm)
    relative: RelativeCost | None = None
    if arm != BASELINE_ARM and matched:
        arm_matched = _arm_cost(_with_keys(selected, matched), arm)
        baseline_matched = _arm_cost(
            _with_keys([record for record in records if record.arm == BASELINE_ARM], matched),
            BASELINE_ARM,
        )
        relative = relative_to_baseline(arm_matched, baseline_matched)
    return ArmSummary(
        arm=arm,
        actor=actor,
        detection=_detection_summary(selected, arm),
        cost=cost,
        relative=relative,
        matched_runs=len(matched),
        friction=_friction_summary(selected, arm),
    )


def _run_order(record: RunRecord) -> tuple[str, int, int]:
    return record.item_id, _arm_index(record.arm), record.repetition


def _arm_index(arm: str) -> int:
    return ARMS.index(arm) if arm in ARMS else len(ARMS)


def _matched_keys(records: Sequence[RunRecord], arm: str) -> tuple[tuple[str, int], ...]:
    """Identities costed in both `arm` and the baseline, for the same actor."""
    costed = {
        (record.item_id, record.repetition)
        for record in records
        if record.arm == arm and _run_cost(record) is not None
    }
    baseline = {
        (record.item_id, record.repetition)
        for record in records
        if record.arm == BASELINE_ARM and _run_cost(record) is not None
    }
    return tuple(sorted(costed & baseline))


def _with_keys(
    records: Sequence[RunRecord], keys: Sequence[tuple[str, int]]
) -> tuple[RunRecord, ...]:
    allowed = set(keys)
    return tuple(record for record in records if (record.item_id, record.repetition) in allowed)


def _detection_summary(records: Sequence[RunRecord], arm: str) -> DetectionSummary:
    executed = tuple(record for record in records if record.outcome is ArmOutcome.EXECUTED)
    measured = 0
    control_constant = 0
    planted = 0
    caught = 0
    missed = 0
    not_exercised = 0
    false_positives = 0
    for record in executed:
        value = _detection_value(record)
        basis = _string(value.get("basis"), "detection basis")
        defects = _sequence(value.get("defects"), "detection defects")
        if basis == CONTROL_CONSTANT_LABEL:
            control_constant += 1
            continue
        measured += 1
        for entry in defects:
            outcome = _string(_mapping(entry, "defect score").get("outcome"), "defect outcome")
            planted += 1
            if outcome == DetectionOutcome.CAUGHT.value:
                caught += 1
            elif outcome == DetectionOutcome.MISSED.value:
                missed += 1
            elif outcome == DetectionOutcome.NOT_EXERCISED.value:
                not_exercised += 1
            else:
                raise ReportError(f"unknown detection outcome {outcome!r}")
        false_positives += len(_sequence(value.get("false_positives"), "false positives"))
    return DetectionSummary(
        arm=arm,
        executed_runs=len(executed),
        measured_runs=measured,
        control_constant_runs=control_constant,
        planted=planted,
        caught=caught,
        missed=missed,
        not_exercised=not_exercised,
        false_positives=false_positives,
        basis=_detection_basis(measured, control_constant),
    )


def _detection_basis(measured: int, control_constant: int) -> str:
    if measured and control_constant:
        return f"mixed ({measured} measured, {control_constant} {CONTROL_CONSTANT_LABEL})"
    if control_constant:
        return CONTROL_CONSTANT_LABEL
    if measured:
        return "measured"
    return NOT_MEASURED_LABEL


def _friction_summary(records: Sequence[RunRecord], arm: str) -> FrictionSummary:
    counts = {control: 0 for control in CONTROL_VOCABULARY}
    reopens = 0
    reopens_unavailable = 0
    unmapped = 0
    closures = 0
    passed_anchoring = 0
    for record in records:
        if record.reopens.state is MetricState.OBSERVED:
            reopens += len(_sequence(record.reopens.value, "reopen transitions"))
        elif record.outcome is ArmOutcome.EXECUTED and record.arm != BASELINE_ARM:
            reopens_unavailable += 1
        if record.gate_failures.state is MetricState.OBSERVED:
            value = _mapping(record.gate_failures.value, "gate failures")
            for entry in _sequence(value.get("attributed"), "attributed gate failures"):
                control = _string(_mapping(entry, "gate failure").get("control"), "control")
                counts[ControlType(control)] += 1
            unmapped += len(_sequence(value.get("unmapped"), "unmapped gate failures"))
        if record.claims.state is MetricState.OBSERVED:
            claims = _mapping(record.claims.value, "claims")
            passed_anchoring += len(_sequence(claims.get("passed_anchoring"), "passed anchoring"))
            closures += sum(
                1
                for call in _sequence(claims.get("resolve_claim_calls"), "resolve-claim calls")
                if _mapping(call, "resolve-claim call").get("succeeded") is True
            )
    return FrictionSummary(
        arm=arm,
        reopens=reopens,
        reopens_unavailable=reopens_unavailable,
        failures_by_control=counts,
        unmapped=unmapped,
        resolve_claim_closures=closures,
        passed_anchoring=passed_anchoring,
    )


def _unmapped_entries(records: Sequence[RunRecord]) -> tuple[UnmappedEntry, ...]:
    entries: list[UnmappedEntry] = []
    for record in sorted(records, key=_run_order):
        if record.gate_failures.state is not MetricState.OBSERVED:
            continue
        value = _mapping(record.gate_failures.value, "gate failures")
        for raw in _sequence(value.get("unmapped"), "unmapped gate failures"):
            failure = _mapping(raw, "unmapped gate failure")
            entries.append(
                UnmappedEntry(
                    item_id=record.item_id,
                    arm=record.arm,
                    repetition=record.repetition,
                    reason=_string(failure.get("reason"), "unmapped reason"),
                    exit_code=_integer(failure.get("exit_code"), "unmapped exit code"),
                )
            )
    return tuple(entries)


def _arm_cost(records: Sequence[RunRecord], arm: str) -> ArmCost:
    """Aggregate arm cost from records alone, preserving unavailable-is-not-zero."""
    costs = tuple(cost for cost in (_run_cost(record) for record in records) if cost is not None)
    actors = {cost.actor for cost in costs}
    if len(actors) > 1:
        raise ReportError(f"arm {arm!r} mixes actor kinds and must never be aggregated")
    reporting = tuple(cost.tokens for cost in costs if token_usage_available(cost.tokens))
    return ArmCost(
        arm=arm,
        actor=actors.pop() if actors else None,
        costed_runs=len(costs),
        uncosted_runs=len(records) - len(costs),
        total_seconds=sum(cost.total_seconds for cost in costs),
        stages=_stage_aggregate(costs, arm),
        tokens=_sum_tokens(reporting),
        token_coverage=TokenCoverage(reporting_runs=len(reporting), costed_runs=len(costs)),
    )


def _run_cost(record: RunRecord) -> RunCost | None:
    """Project one record onto cost, or None when the record carries no cost at all."""
    if record.outcome is not ArmOutcome.EXECUTED:
        return None
    if record.total_wall_clock.state is not MetricState.OBSERVED:
        return None
    return RunCost(
        actor=record.actor,
        item_id=record.item_id,
        arm=record.arm,
        repetition=record.repetition,
        total_seconds=_number(record.total_wall_clock.value, "total wall-clock"),
        stages=_run_stages(record),
        tokens=_run_tokens(record),
    )


def _run_stages(record: RunRecord) -> tuple[StageCost, ...]:
    if record.stage_costs.state is not MetricState.OBSERVED:
        return ()
    stages: list[StageCost] = []
    for raw in _sequence(record.stage_costs.value, "stage costs"):
        entry = _mapping(raw, "stage cost")
        stage = _string(entry.get("stage"), "stage name")
        if entry.get("state") == "observed":
            stages.append(StageElapsed(stage=stage, seconds=_number(entry.get("seconds"), stage)))
        else:
            stages.append(
                StageNotRun(stage=stage, reason=_string(entry.get("reason"), "stage reason"))
            )
    return tuple(stages)


def _run_tokens(record: RunRecord) -> TokenAccounting:
    if record.tokens.state is not MetricState.OBSERVED:
        return TokenUsageUnavailable(
            reason=record.tokens.reason or "the record reports no token usage"
        )
    value = _mapping(record.tokens.value, "token usage")
    model = value.get("model")
    return TokenUsage(
        input_tokens=_integer(value.get("input_tokens"), "input tokens"),
        output_tokens=_integer(value.get("output_tokens"), "output tokens"),
        model=model if isinstance(model, str) else None,
    )


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
            aggregate.append(
                StageNotRun(stage=stage, reason="no costed run of this arm entered this stage")
            )
    return tuple(aggregate)


def _sum_tokens(reporting: Sequence[TokenUsage]) -> TokenAccounting:
    if not reporting:
        return TokenUsageUnavailable(
            reason="no costed run of this arm reported token usage; this is not zero tokens"
        )
    models = {usage.model for usage in reporting}
    return TokenUsage(
        input_tokens=sum(usage.input_tokens for usage in reporting),
        output_tokens=sum(usage.output_tokens for usage in reporting),
        model=models.pop() if len(models) == 1 else None,
    )


def _render_section(section: ActorSection, durations: DurationReporting) -> list[str]:
    lines = ["", f"## Actor: {section.actor.value}", "", "### Detection", ""]
    lines.extend(_detection_table(section))
    lines.extend(["", "### Cost", ""])
    lines.extend(_cost_table(section, durations))
    lines.extend(["", "stage cost (seconds):"])
    lines.extend(_stage_lines(section, durations))
    lines.extend(["", "### Friction", ""])
    lines.extend(_friction_table(section))
    lines.extend(["", "### Unmapped gate failures", ""])
    lines.extend(_unmapped_lines(section))
    lines.extend(["", "### Runs", ""])
    lines.extend(_run_table(section, durations))
    return lines


def _detection_table(section: ActorSection) -> list[str]:
    headers = (
        "arm",
        "executed_runs",
        "measured_runs",
        "control_constant_runs",
        "planted_defects",
        "caught",
        "missed",
        "not_exercised",
        "detection_rate",
        "false_positives",
        "basis",
    )
    rows = []
    for summary in section.arms:
        detection = summary.detection
        rows.append(
            (
                summary.arm,
                str(detection.executed_runs),
                str(detection.measured_runs),
                str(detection.control_constant_runs),
                str(detection.planted),
                str(detection.caught),
                str(detection.missed),
                str(detection.not_exercised),
                _rate_label(detection),
                str(detection.false_positives),
                detection.basis,
            )
        )
    return _table(headers, tuple(rows))


def _rate_label(detection: DetectionSummary) -> str:
    if detection.measured_runs == 0:
        return CONTROL_CONSTANT_LABEL if detection.control_constant_runs else NOT_MEASURED_LABEL
    if detection.caught + detection.missed == 0:
        return NO_PLANTED_DEFECTS_LABEL
    return _format_ratio(detection.rate)


def _cost_table(section: ActorSection, durations: DurationReporting) -> list[str]:
    headers = (
        "arm",
        "costed_runs",
        "uncosted_runs",
        "total_seconds",
        "mean_seconds",
        "tokens_total",
        "token_coverage",
        "token_coverage_share",
        "matched_runs",
        "wall_clock_vs_baseline",
        "tokens_vs_baseline",
    )
    rows = []
    for summary in section.arms:
        row = format_arm_cost(summary.cost, durations)
        wall_clock, tokens = _relative_labels(summary)
        rows.append(
            (
                summary.arm,
                row["costed_runs"],
                row["uncosted_runs"],
                row["total_seconds"],
                row["mean_seconds"],
                row["tokens_total"],
                row["token_coverage"],
                row["token_coverage_share"],
                str(summary.matched_runs),
                wall_clock,
                tokens,
            )
        )
    return _table(headers, tuple(rows))


def _relative_labels(summary: ArmSummary) -> tuple[str, str]:
    if summary.arm == BASELINE_ARM:
        return BASELINE_LABEL, BASELINE_LABEL
    if summary.relative is None:
        return NO_COMPARISON_LABEL, NO_COMPARISON_LABEL
    return (
        _format_ratio(summary.relative.wall_clock_ratio),
        _format_ratio(summary.relative.token_ratio),
    )


def _stage_lines(section: ActorSection, durations: DurationReporting) -> list[str]:
    lines: list[str] = []
    for summary in section.arms:
        row = format_arm_cost(summary.cost, durations)
        stages = [
            f"{key.removeprefix('stage.')}={value}"
            for key, value in row.items()
            if key.startswith("stage.")
        ]
        lines.append(f"- {summary.arm}: {' '.join(stages) if stages else _NO_STAGE_LABEL}")
    return lines


def _friction_table(section: ActorSection) -> list[str]:
    headers = (
        "arm",
        "reopens",
        "reopens_unavailable",
        *(control.value for control in CONTROL_VOCABULARY),
        "unmapped",
        "resolve_claim_closures",
        "passed_anchoring",
    )
    rows = []
    for summary in section.arms:
        friction = summary.friction
        rows.append(
            (
                summary.arm,
                str(friction.reopens),
                str(friction.reopens_unavailable),
                *(str(friction.failures_by_control[control]) for control in CONTROL_VOCABULARY),
                str(friction.unmapped),
                str(friction.resolve_claim_closures),
                str(friction.passed_anchoring),
            )
        )
    return _table(headers, tuple(rows))


def _unmapped_lines(section: ActorSection) -> list[str]:
    if not section.unmapped:
        return [f"- {_NONE_LABEL}"]
    return [
        f"- {entry.item_id} / {entry.arm} / {entry.repetition}: {entry.reason} "
        f"(exit_code={entry.exit_code})"
        for entry in section.unmapped
    ]


def _run_table(section: ActorSection, durations: DurationReporting) -> list[str]:
    headers = (
        "item",
        "arm",
        "repetition",
        "outcome",
        "terminal_stage",
        "caught",
        "missed",
        "not_exercised",
        "false_positives",
        "total_seconds",
        "tokens_total",
        "reopens",
        "gate_failures",
        "unmapped",
    )
    rows = []
    for record in section.records:
        caught, missed, not_exercised, false_positives = _record_detection_counts(record)
        gate_failures, unmapped = _record_gate_counts(record)
        rows.append(
            (
                record.item_id,
                record.arm,
                str(record.repetition),
                record.outcome.value,
                _terminal_stage(record),
                caught,
                missed,
                not_exercised,
                false_positives,
                _record_seconds(record, durations),
                _record_tokens(record),
                _record_reopens(record),
                gate_failures,
                unmapped,
            )
        )
    return _table(headers, tuple(rows))


def _record_detection_counts(record: RunRecord) -> tuple[str, str, str, str]:
    if record.detection.state is not MetricState.OBSERVED:
        label = _absence_label(record.detection.state)
        return label, label, label, label
    value = _detection_value(record)
    defects = [_mapping(entry, "defect score") for entry in _sequence(value.get("defects"), "d")]
    caught = sum(1 for entry in defects if entry.get("outcome") == DetectionOutcome.CAUGHT.value)
    missed = sum(1 for entry in defects if entry.get("outcome") == DetectionOutcome.MISSED.value)
    not_exercised = sum(
        1 for entry in defects if entry.get("outcome") == DetectionOutcome.NOT_EXERCISED.value
    )
    false_positives = len(_sequence(value.get("false_positives"), "false positives"))
    return str(caught), str(missed), str(not_exercised), str(false_positives)


def _record_gate_counts(record: RunRecord) -> tuple[str, str]:
    if record.gate_failures.state is not MetricState.OBSERVED:
        label = _absence_label(record.gate_failures.state)
        return label, label
    value = _mapping(record.gate_failures.value, "gate failures")
    attributed = len(_sequence(value.get("attributed"), "attributed gate failures"))
    unmapped = len(_sequence(value.get("unmapped"), "unmapped gate failures"))
    return str(attributed), str(unmapped)


def _record_reopens(record: RunRecord) -> str:
    if record.reopens.state is not MetricState.OBSERVED:
        return _absence_label(record.reopens.state)
    return str(len(_sequence(record.reopens.value, "reopen transitions")))


def _record_seconds(record: RunRecord, durations: DurationReporting) -> str:
    if record.total_wall_clock.state is not MetricState.OBSERVED:
        return _absence_label(record.total_wall_clock.state)
    return format_seconds(_number(record.total_wall_clock.value, "total wall-clock"), durations)


def _record_tokens(record: RunRecord) -> str:
    tokens = _run_tokens(record)
    if token_usage_available(tokens):
        return str(tokens.total_tokens)
    return UNAVAILABLE_LABEL


def _terminal_stage(record: RunRecord) -> str:
    if record.terminal_state.state is not MetricState.OBSERVED:
        return _absence_label(record.terminal_state.state)
    stage = _mapping(record.terminal_state.value, "terminal state").get("stage")
    return stage if isinstance(stage, str) and stage else UNAVAILABLE_LABEL


def _absence_label(state: MetricState) -> str:
    if state is MetricState.NOT_RUN:
        return NOT_RUN_LABEL
    if state is MetricState.UNAVAILABLE:
        return UNAVAILABLE_LABEL
    return NOT_COSTED_LABEL


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [_table_row(headers, widths), _table_row(["-" * width for width in widths], widths)]
    lines.extend(_table_row(row, widths) for row in rows)
    return lines


def _table_row(cells: Sequence[str], widths: Sequence[int]) -> str:
    padded = " | ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))
    return f"| {padded} |"


def _format_ratio(ratio: float | None) -> str:
    if ratio is None:
        return NO_COMPARISON_LABEL
    return f"{ratio:.{RATE_PRECISION}f}"


def _detection_value(record: RunRecord) -> Mapping[str, Any]:
    return _mapping(record.detection.value, "detection")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReportError(f"{label} must be an object in the run record")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise ReportError(f"{label} must be a list in the run record")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReportError(f"{label} must be a non-empty string in the run record")
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReportError(f"{label} must be an integer in the run record")
    return value


def _number(value: Any, label: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ReportError(f"{label} must be a number in the run record")
    return float(value)
