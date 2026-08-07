"""Deterministic comparison report rendered from durable run records only.

Every fixture here builds run records through the ordinary arm execution path and then
renders them. No test reads a runspace, a corpus file, or repository evidence: the
report contract is that a :class:`bench.harness.record.RunRecordSet` is sufficient.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from bench.harness.actor import (
    ActorKind,
    CommandResult,
    LiveActor,
    LiveSessionOutcome,
    RunRequest,
    ScriptedActor,
    StageBoundary,
    TokenUsage,
)
from bench.harness.arms import ArmRun, execute_arms
from bench.harness.corpus import CorpusItem
from bench.harness.friction import CONTROL_VOCABULARY
from bench.harness.record import (
    SCHEMA_VERSION,
    RunRecord,
    RunRecordSet,
    build_run_record,
)
from bench.harness.report import (
    CONTROL_CONSTANT_LABEL,
    RATE_PRECISION,
    build_sections,
    render_report,
)

ITEM_ID = "bench-report-item"
CLEAN_ITEM_ID = "bench-report-clean-item"
CORPUS_VERSION = "bench-report-corpus-2026.1"


def _item(**overrides: Any) -> CorpusItem:
    data: dict[str, Any] = {
        "id": ITEM_ID,
        "mode": "light",
        "title": "Bench report item",
        "question": "What does the comparison report state per arm?",
        "planted_defects": ("unreachable-source",),
        "expected_detection": {"unreachable-source": "caught"},
        "sources": (),
        "artifacts": {},
        "commands": (),
        "probe": None,
        "path": Path("bench/corpus/items") / f"{ITEM_ID}.yaml",
    }
    data.update(overrides)
    return CorpusItem(**data)


def _clean_item(**overrides: Any) -> CorpusItem:
    return _item(
        id=CLEAN_ITEM_ID,
        title="Bench report clean item",
        planted_defects=(),
        expected_detection={},
        path=Path("bench/corpus/items") / f"{CLEAN_ITEM_ID}.yaml",
        **overrides,
    )


def _entered_item(item_id: str) -> CorpusItem:
    return _item(
        id=item_id,
        commands=(
            (
                "new",
                item_id,
                "--title",
                "Report detection item",
                "--question",
                "What does this run detect?",
                "--mode",
                "light",
                "--owner",
                "bench",
                "--timebox",
                "1",
            ),
        ),
    )


def _failing_check() -> CommandResult:
    return CommandResult(
        argv=("check", ITEM_ID, "--json", "--offline"),
        exit_code=1,
        stdout="",
        stderr="",
        payload={
            "passed": False,
            "results": [
                {
                    "check": "links_resolve",
                    "passed": False,
                    "skipped": False,
                    "detail": "source does not resolve",
                }
            ],
        },
    )


def _unmapped_advance() -> CommandResult:
    return CommandResult(
        argv=("advance", ITEM_ID, "--offline"),
        exit_code=1,
        stdout="avance bloqueado: un motivo sin control documentado\n",
        stderr="",
    )


class _FindingActor(ScriptedActor):
    """Scripted actor that reports one structured finding outside the baseline arm."""

    def execute(self, request):  # type: ignore[no-untyped-def]
        result = super().execute(request)
        if request.arm == "baseline":
            return result
        commands = (*result.commands, _failing_check(), _unmapped_advance())
        return type(result)(**{**vars(result), "commands": commands})


class _SkippedFindingActor(ScriptedActor):
    def execute(self, request):  # type: ignore[no-untyped-def]
        result = super().execute(request)
        if request.arm == "baseline":
            return result
        skipped = CommandResult(
            argv=("check", request.item.id, "--json", "--offline"),
            exit_code=0,
            stdout="",
            stderr="",
            payload={
                "passed": True,
                "results": [
                    {
                        "check": "links_resolve",
                        "passed": True,
                        "skipped": True,
                        "detail": "offline mode skips link resolution",
                    }
                ],
            },
        )
        return type(result)(**{**vars(result), "commands": (*result.commands, skipped)})


class _LiveSession:
    """Deterministic stand-in for a real agent session, with reported token usage."""

    def run(self, request: RunRequest) -> LiveSessionOutcome:
        return LiveSessionOutcome(
            commands=(_failing_check(),),
            stages=(
                StageBoundary(stage="intake", started_at=0.0, ended_at=1.0),
                StageBoundary(stage="explore", started_at=1.0, ended_at=3.0),
            ),
            artifacts_written=(),
            tokens=TokenUsage(input_tokens=1200, output_tokens=300, model="bench-model"),
            slug=None,
        )


def _runs(
    items: tuple[CorpusItem, ...],
    *,
    actor: Any,
    arms: tuple[str, ...] = ("baseline", "light"),
    repetitions: int = 1,
) -> tuple[ArmRun, ...]:
    return execute_arms(items, actor=actor, arms=arms, repetitions=repetitions, max_workers=1)


def _records(
    items: tuple[CorpusItem, ...],
    runs: tuple[ArmRun, ...],
) -> tuple[RunRecord, ...]:
    by_id = {item.id: item for item in items}
    return tuple(build_run_record(by_id[run.item_id], run) for run in runs)


def _record_set(
    items: tuple[CorpusItem, ...],
    *,
    arms: tuple[str, ...] = ("baseline", "light"),
    repetitions: int = 1,
    live: bool = False,
) -> RunRecordSet:
    records = _records(
        items, _runs(items, actor=_FindingActor(), arms=arms, repetitions=repetitions)
    )
    if live:
        live_actor = LiveActor(enabled=True, session=_LiveSession())
        records += _records(
            items, _runs(items, actor=live_actor, arms=arms, repetitions=repetitions)
        )
    return RunRecordSet(
        schema_version=SCHEMA_VERSION,
        corpus_version=CORPUS_VERSION,
        repetitions=repetitions,
        records=records,
    )


def test_two_report_generations_over_unchanged_records_are_byte_identical() -> None:
    records = _record_set((_item(), _clean_item()), live=True)

    first = render_report(records)
    second = render_report(records)

    assert first == second
    assert first == render_report(RunRecordSet.from_json(records.to_json()))
    assert first.encode("utf-8") == second.encode("utf-8")


def test_report_orders_runs_by_item_then_arm_then_repetition() -> None:
    records = _record_set((_item(), _clean_item()), repetitions=2)

    rendered = render_report(records)
    rows = [
        (match["item"], match["arm"], int(match["repetition"]))
        for match in re.finditer(
            r"^\| (?P<item>bench-report-[a-z-]+) +\| (?P<arm>[a-z]+) +\| (?P<repetition>\d+) ",
            rendered,
            flags=re.MULTILINE,
        )
    ]

    assert rows
    assert rows == sorted(
        rows, key=lambda row: (row[0], ("baseline", "light").index(row[1]), row[2])
    )


def test_report_body_carries_no_timestamp_and_fixed_numeric_precision() -> None:
    records = _record_set((_item(),), live=True)

    rendered = render_report(records)

    assert not re.search(r"\d{4}-\d{2}-\d{2}", rendered)
    assert "T00:" not in rendered
    body = "\n".join(
        line for line in rendered.splitlines() if not line.startswith("corpus_version:")
    )
    for number in re.findall(r"\d+\.\d+", body):
        assert len(number.split(".")[1]) == RATE_PRECISION


def test_report_separates_scripted_and_live_sections_without_aggregating() -> None:
    records = _record_set((_item(),), live=True)

    sections = build_sections(records)
    rendered = render_report(records)

    assert tuple(section.actor for section in sections) == (ActorKind.SCRIPTED, ActorKind.LIVE)
    scripted, live = rendered.split("## Actor: live")
    assert "## Actor: scripted" in scripted
    assert "1500" not in scripted
    assert "1500" in live
    for section in sections:
        for summary in section.arms:
            assert summary.cost.actor in {None, section.actor}


def test_report_states_detection_false_positives_relative_cost_and_friction_by_control() -> None:
    records = _record_set((_item(), _clean_item()), live=True)

    rendered = render_report(records)
    sections = {section.actor: section for section in build_sections(records)}
    light = next(summary for summary in sections[ActorKind.SCRIPTED].arms if summary.arm == "light")

    assert light.detection.caught == 1
    assert light.detection.missed == 0
    assert light.detection.rate == pytest.approx(1.0)
    assert light.detection.false_positives == 1
    assert light.relative is not None
    assert light.relative.baseline_arm == "baseline"
    assert light.matched_runs == 2
    assert light.friction.failures_by_control[CONTROL_VOCABULARY[1]] == 2
    assert "detection_rate" in rendered
    assert "false_positives" in rendered
    assert "wall_clock_vs_baseline" in rendered
    for control in CONTROL_VOCABULARY:
        assert control.value in rendered


def test_report_states_corpus_version_repetitions_token_coverage_and_unmapped() -> None:
    records = _record_set((_item(),), repetitions=2, live=True)

    rendered = render_report(records)

    assert f"corpus_version: {CORPUS_VERSION}" in rendered
    assert "repetitions: 2" in rendered
    assert "token_coverage_share" in rendered
    assert "unmapped" in rendered
    assert "un motivo sin control documentado" in rendered


def test_report_marks_the_scripted_baseline_detection_as_a_control_constant() -> None:
    records = _record_set((_item(),))

    rendered = render_report(records)
    sections = {section.actor: section for section in build_sections(records)}
    baseline = next(
        summary for summary in sections[ActorKind.SCRIPTED].arms if summary.arm == "baseline"
    )

    assert baseline.detection.rate is None
    assert baseline.detection.basis == CONTROL_CONSTANT_LABEL
    assert CONTROL_CONSTANT_LABEL in rendered


def test_report_excludes_not_exercised_from_rate_and_reports_it_separately() -> None:
    items = (
        _entered_item("caught-item"),
        _entered_item("missed-item"),
        _entered_item("skipped-item"),
    )
    caught_run = _runs((items[0],), actor=_FindingActor(), arms=("light",))[0]
    missed_run = _runs((items[1],), actor=ScriptedActor(), arms=("light",))[0]
    skipped_run = _runs((items[2],), actor=_SkippedFindingActor(), arms=("light",))[0]
    records = RunRecordSet(
        schema_version=SCHEMA_VERSION,
        corpus_version=CORPUS_VERSION,
        repetitions=1,
        records=_records(items, (caught_run, missed_run, skipped_run)),
    )

    section = build_sections(records)[0]
    detection = section.arms[0].detection
    first = render_report(records)

    assert detection.caught == 1
    assert detection.missed == 1
    assert detection.not_exercised == 1
    assert detection.rate == pytest.approx(0.5)
    assert "not_exercised" in first
    assert re.search(
        r"\| light\s+\| 3\s+\| 3\s+\| 0\s+\| 3\s+\| 1\s+\| 1\s+\| 1\s+\| 0\.500",
        first,
    )
    assert first == render_report(RunRecordSet.from_json(records.to_json()))


def test_report_reads_only_the_record_set_after_every_runspace_is_gone() -> None:
    items = (_item(),)
    runs = _runs(items, actor=_FindingActor())
    for run in runs:
        assert run.research_root is None or not run.research_root.exists()
    records = RunRecordSet(
        schema_version=SCHEMA_VERSION,
        corpus_version=CORPUS_VERSION,
        repetitions=1,
        records=_records(items, runs),
    )

    rendered = render_report(records)

    assert rendered.startswith("# ")
    assert rendered.endswith("\n")
