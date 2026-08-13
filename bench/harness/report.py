"""Deterministic question-specific rendering from schema-v2 records only."""

from __future__ import annotations

import json
from typing import Final

from bench.harness.prompts import EvaluationQuestion
from bench.harness.record import RunRecord, RunRecordSet


class ReportError(ValueError):
    """Raised when durable v2 evidence cannot be rendered without invention."""


_QUESTIONS: Final[tuple[EvaluationQuestion, ...]] = (
    EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
    EvaluationQuestion.LIVE_SINGLE_INVESTIGATION,
    EvaluationQuestion.CROSS_RETRIEVAL,
)
_NON_CLAIMS: Final[tuple[str, ...]] = (
    "semantic applicability",
    "recommendation quality",
    "criterion-level reuse",
    "statistical significance",
    "causal effect",
    "value of the cross CLI",
)


def render_report(records: RunRecordSet, **_: object) -> str:
    """Render fixed question sections without cross-record aggregation."""
    if not isinstance(records, RunRecordSet):
        raise TypeError("reporting accepts only a schema-v2 RunRecordSet")
    lines = [
        "# SDR evaluation harness evidence report",
        "",
        f"schema_version: {records.schema_version}",
        f"run_records: {len(records.records)}",
    ]
    for question in _QUESTIONS:
        lines.extend(("", f"## Question: {question.value}", ""))
        selected = tuple(
            record for record in records.records if record.evaluation_question is question
        )
        if not selected:
            lines.append("- no records")
            continue
        for record in selected:
            lines.extend(_render_record(record))
    lines.extend(("", "## Explicit Non-Claims", ""))
    lines.extend(f"- This evidence does not establish {claim}." for claim in _NON_CLAIMS)
    return "\n".join(lines) + "\n"


def _render_record(record: RunRecord) -> list[str]:
    lines = [
        f"### Run: {record.run_id}",
        "",
        f"group: {_group_identity(record)}",
        f"target: {record.scenario_id or record.item_id}",
        f"arm: {record.arm}",
        f"repetition: {record.repetition}",
        f"terminal_state: {record.terminal_state}",
        f"approval_state: {record.approval.state}",
    ]
    if record.evaluation_question is EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY:
        assert record.lifecycle is not None
        lines.extend(
            f"detection: {entry.defect} | {entry.outcome} | "
            f"control={entry.reporting_control or 'none'} | reason={entry.reason or 'none'}"
            for entry in record.lifecycle.detections
        )
        lines.append(f"mutation: {record.lifecycle.mutation_identity or 'none'}")
        lines.append(f"baseline_reference: {record.lifecycle.baseline_run_id or 'none'}")
    elif record.evaluation_question is EvaluationQuestion.LIVE_SINGLE_INVESTIGATION:
        assert record.live is not None
        lines.extend(
            (
                f"session_id: {record.live.session_id}",
                f"session_attributed: {_boolean(record.live.session_attribution_valid)}",
                f"wall_clock_seconds: {record.usage.wall_clock_seconds:.3f}",
                f"tokens: {_usage(record.usage.tokens.model_dump(mode='json'))}",
                f"money: {_usage(record.usage.money.model_dump(mode='json'))}",
                "friction: " + _friction(record),
            )
        )
    else:
        assert record.cross is not None
        lines.extend(
            (
                f"consultation: {record.cross.consultation}",
                f"command_identity: {record.cross.command_identity or 'none'}",
                f"query_identity: {record.cross.query_identity or 'none'}",
                f"observed_projection_sha256: {record.cross.observed_projection_sha256 or 'none'}",
                f"expected_projection_sha256: {record.cross.expected_projection_sha256 or 'none'}",
                f"metamorphic_relation: {record.cross.metamorphic_relation or 'none'}",
            )
        )
        lines.extend(
            f"cross_check: {entry.id} | {entry.kind} | {entry.outcome} | {entry.reason}"
            for entry in record.cross.checks
        )
        lines.extend(
            f"negative_control: {entry.id} | {entry.outcome}"
            for entry in record.cross.negative_controls
        )
    lines.append("")
    return lines


def _group_identity(record: RunRecord) -> str:
    """Expose every non-aggregatable dimension in canonical key order."""
    values = {
        "actor": record.actor.value,
        "policy": record.prompt.policy,
        "history": record.corpus.history_condition,
        "template_id": record.prompt.template_id,
        "template_version": record.prompt.template_version,
        "template_sha256": record.prompt.template_sha256,
        "corpus": record.corpus.version,
        "migration_provenance": record.corpus.migration_provenance_version,
        "scenario": record.scenario_id or "not-applicable",
        "scenario_manifest_sha256": record.corpus.scenario_manifest_sha256,
        "resolver": "not-applicable"
        if record.cross is None
        else record.cross.resolver_chain_identity,
        "host": "not-applicable" if record.live is None else record.live.host,
        "host_version": "not-applicable" if record.live is None else record.live.host_version,
        "model": "not-applicable" if record.live is None else record.live.model,
        "model_version": (
            "not-reported"
            if record.live is not None and record.live.model_version is None
            else "not-applicable"
            if record.live is None
            else record.live.model_version
        ),
        "environment_policy": record.execution.environment_policy,
        "environment_policy_version": record.execution.environment_policy_version,
        "package": record.execution.executable.package_identity,
        "package_sha256": record.execution.executable.package_sha256,
    }
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _usage(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _friction(record: RunRecord) -> str:
    return ";".join(f"{entry.control}={len(entry.events)}" for entry in record.friction)


def _boolean(value: bool) -> str:
    return "true" if value else "false"
