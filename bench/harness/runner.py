"""Command-line entry point that executes the corpus and renders the comparison report.

One command runs the whole thing::

    uv run python -m bench.harness --report -

Defaults are the offline ones. The scripted actor is the only actor this entry point
constructs: it replays declared artifact writes and CLI invocations, needs no network
and no API key, and injects ``--offline`` into every offline-capable command. The live
actor is deliberately absent from the command line, because driving a real agent
requires a :class:`bench.harness.actor.LiveSession` that the harness does not implement;
using it means calling :func:`bench.harness.arms.execute_arms` from Python with an
explicit :class:`bench.harness.actor.LiveActor`.

Output containment
------------------

Run records and the report are the only outputs, and they go to a caller-specified path
or to stdout. Any path resolving inside the repository tree is refused before a single
run executes, so a harness invocation can never leave run output, a research root, or
lifecycle metadata in the working tree that the public-tree audit protects. Research
roots themselves are disposable temporary directories created by
:mod:`bench.harness.runspace`, outside the repository by construction.

``--from-records`` renders a stored record set and executes nothing, which is the only
form of report determinism the harness claims: re-rendering unchanged records is
byte-identical, while two independent executions observe different wall-clock.

Errors the operator can act on -- a missing corpus, a bad repetition count, a contained
output path -- are reported as one line on stderr with exit status
:data:`EXIT_USAGE`, never as a traceback.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TextIO

from bench.harness.actor import ARMS, ScriptedActor, TokenUsage, TokenUsageUnavailable
from bench.harness.arms import DEFAULT_REPETITIONS, ArmExecutionError, execute_arms
from bench.harness.corpus import DEFAULT_CORPUS_ROOT, CorpusError, load_corpus
from bench.harness.cost import DurationReporting
from bench.harness.enforcement import BoundaryError
from bench.harness.live import (
    LiveBounds,
    LiveError,
    MonetaryCost,
    MonetaryCostUnavailable,
    OpenCodeConnector,
    audit_repository,
    create_live_request,
    resolve_live_opt_in,
)
from bench.harness.pilot import PilotAttributionError, PilotExitReport, PilotPlan, execute_pilot
from bench.harness.prompts import (
    EvaluationQuestion,
    HistoryCondition,
    PromptInputs,
    PromptLeakSignals,
    PromptPolicy,
    build_prompt,
)
from bench.harness.record import RunRecordSet
from bench.harness.record_builders import build_lifecycle_record_set
from bench.harness.report import ReportError, render_report
from bench.harness.reuse import load_reuse_corpus, prepare_reuse_scenario
from bench.harness.runspace import (
    DEFAULT_MAX_WORKERS,
    REPOSITORY_ROOT,
    Runspace,
    RunspaceError,
    runspace,
)
from sdr.research import Research

#: The run completed and every requested output was written.
EXIT_OK: Final[int] = 0

#: The invocation was refused, or the run could not complete. Nothing was invented.
EXIT_USAGE: Final[int] = 2

#: Passed to ``--records`` or ``--report`` to write that output to stdout.
STDOUT_TARGET: Final[str] = "-"

_PROGRAM: Final[str] = "python -m bench.harness"


class RunnerError(ValueError):
    """Raised when an invocation is refused before or during execution."""


@dataclass(frozen=True)
class Target:
    """Where one output goes: a resolved path outside the repository, or stdout."""

    #: None means stdout. Any other value is an absolute path outside the repository.
    path: Path | None


def resolve_arms(selected: Sequence[str] | None) -> tuple[str, ...]:
    """Arms to execute, in declared order, defaulting to all three."""
    if not selected:
        return ARMS
    chosen = set(selected)
    return tuple(arm for arm in ARMS if arm in chosen)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, with the offline scripted defaults."""
    parser = argparse.ArgumentParser(
        prog=_PROGRAM,
        description=(
            "Execute the SDR evaluation corpus with the offline scripted actor and render "
            "the deterministic comparison report."
        ),
    )
    parser.add_argument(
        "--corpus",
        default=str(DEFAULT_CORPUS_ROOT),
        help="corpus root holding corpus.yaml and items/ (default: %(default)s)",
    )
    parser.add_argument(
        "--arm",
        action="append",
        choices=list(ARMS),
        help=f"arm to execute; repeatable (default: {' '.join(ARMS)})",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=DEFAULT_REPETITIONS,
        help="repetitions per applicable arm (default: %(default)s)",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help="bounded parallelism over disjoint research roots (default: %(default)s)",
    )
    parser.add_argument(
        "--records",
        default=None,
        help=(
            "write the run records as JSON to this path outside the repository, "
            f"or {STDOUT_TARGET!r} for stdout (default: not written)"
        ),
    )
    parser.add_argument(
        "--report",
        default=STDOUT_TARGET,
        help=(
            "write the comparison report to this path outside the repository, "
            f"or {STDOUT_TARGET!r} for stdout (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--from-records",
        default=None,
        help=(
            "render the report from a stored run record set and execute nothing; "
            "re-rendering unchanged records is byte-identical"
        ),
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="skip report rendering and emit only the run records",
    )
    parser.add_argument(
        "--durations",
        choices=[reporting.value for reporting in DurationReporting],
        default=DurationReporting.MEASURED.value,
        help=(
            "print measured wall-clock, or omit the duration columns; omitting does not "
            "make two executions identical, since the relative cost of an arm is still "
            "measured (default: %(default)s)"
        ),
    )
    parser.add_argument("--live", action="store_true", help="execute exactly one scalar live pilot")
    parser.add_argument("--live-item", default=None)
    parser.add_argument("--live-scenario", default=None)
    parser.add_argument("--live-arm", choices=list(ARMS), default=None)
    parser.add_argument("--live-repetition", type=int, default=None)
    parser.add_argument("--live-host", choices=["opencode"], default=None)
    parser.add_argument("--live-host-version", default=None)
    parser.add_argument("--live-model", default=None)
    parser.add_argument(
        "--live-prompt-policy", choices=["standard", "assisted", "unassisted"], default=None
    )
    parser.add_argument("--live-template-version", default=None)
    parser.add_argument("--live-max-turns", type=int, default=None)
    parser.add_argument("--live-wall-clock", type=float, default=None)
    parser.add_argument("--live-results-root", default=None)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the harness and return the process exit status."""
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    args = build_parser().parse_args(argv)
    try:
        _execute(args, out)
    except (
        ArmExecutionError,
        CorpusError,
        ReportError,
        RunnerError,
        RunspaceError,
        BoundaryError,
        LiveError,
        PilotAttributionError,
        ValueError,
    ) as error:
        print(f"{_PROGRAM}: {error}", file=err)
        return EXIT_USAGE
    return EXIT_OK


def _execute(args: argparse.Namespace, out: TextIO) -> None:
    if args.live:
        _validate_live_args(args)
        out.write(_execute_scalar_live(args) + "\n")
        return
    if args.repetitions < 1:
        raise RunnerError(f"repetitions must be at least 1: {args.repetitions}")
    records_target = _target(args.records)
    report_target = None if args.no_report else _target(args.report)

    records = _stored_records(args) if args.from_records is not None else _executed_records(args)

    if records_target is not None:
        _write(records_target, records.to_json(), out)
    if report_target is not None:
        _write(
            report_target, render_report(records, durations=DurationReporting(args.durations)), out
        )


def _validate_live_args(args: argparse.Namespace) -> None:
    if (
        args.arm
        or args.repetitions != DEFAULT_REPETITIONS
        or args.max_workers != DEFAULT_MAX_WORKERS
    ):
        raise RunnerError(
            "scalar --live cannot be combined with matrix arm/repetition/worker flags"
        )
    required = (
        "live_arm",
        "live_repetition",
        "live_host",
        "live_host_version",
        "live_model",
        "live_prompt_policy",
        "live_template_version",
        "live_max_turns",
        "live_wall_clock",
        "live_results_root",
    )
    missing = [name for name in required if getattr(args, name) is None]
    if (args.live_item is None) == (args.live_scenario is None):
        missing.append("exactly one live item or scenario")
    if missing:
        raise RunnerError("scalar --live is missing: " + ", ".join(missing))
    if args.live_repetition < 0 or args.live_max_turns < 1 or args.live_wall_clock <= 0:
        raise RunnerError("scalar --live bounds and repetition are invalid")


def _execute_scalar_live(args: argparse.Namespace) -> str:
    executable = shutil.which("opencode")
    if executable is None:
        raise RunnerError("scalar --live requires the installed OpenCode executable")
    results_root = Path(args.live_results_root).expanduser().resolve(strict=True)
    if args.live_item is not None:
        corpus = load_corpus(args.corpus)
        matches = [item for item in corpus.items if item.id == args.live_item]
        if len(matches) != 1:
            raise RunnerError(f"live item is not an exact corpus identity: {args.live_item}")
        with runspace(prefix=f"sdr-live-{args.live_item}-r{args.live_repetition}-") as space:
            item = matches[0]
            research = Research.create(
                space.root,
                item.id,
                item.title,
                item.question,
                mode=item.mode,
                owner="SDR live harness",
            )
            artifacts: list[str] = []
            for declared, content in sorted(item.artifacts.items()):
                relative = Path(declared)
                if not relative.parts or relative.parts[0] != item.id:
                    raise RunnerError("live item artifact is outside its focal investigation")
                focal_relative = Path(*relative.parts[1:])
                target = research.root / focal_relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                artifacts.append(focal_relative.as_posix())
            _write_live_manifest(
                space,
                identity_kind="item",
                identity=item.id,
                arm=args.live_arm,
                focal_slug=item.id,
                seed_slugs=(),
                stage="intake",
                artifacts=tuple(artifacts),
                cross_argv=(),
                resumed_reuse=False,
            )
            return _run_live_materialization(args, space, item.question, results_root, executable)

    reuse = load_reuse_corpus()
    try:
        scenario = reuse.by_id(args.live_scenario)
    except KeyError as error:
        raise RunnerError(
            f"live scenario is not an exact reuse identity: {args.live_scenario}"
        ) from error
    if args.live_prompt_policy != "assisted":
        raise RunnerError("the initial reuse pilot accepts assisted policy only")
    prepared = prepare_reuse_scenario(scenario, repetition=args.live_repetition)
    with prepared as materialized:
        knowledge = materialized.path / "knowledge"
        knowledge.mkdir()
        space = Runspace(materialized.path, materialized.research_root, knowledge)
        metadata = Research.load(materialized.focal_root, within=materialized.research_root).meta
        artifacts = tuple(
            artifact.path for artifact in scenario.focal.artifacts if artifact.path != "sdr.yaml"
        )
        cross = tuple(
            ("sdr", *expectation.command)
            for expectation in (*scenario.positive_expectations, *scenario.negative_controls)
        )
        _write_live_manifest(
            space,
            identity_kind="scenario",
            identity=scenario.id,
            arm=args.live_arm,
            focal_slug=scenario.focal.id,
            seed_slugs=tuple(materialized.seed_roots),
            stage=metadata.stage,
            artifacts=artifacts,
            cross_argv=cross,
            resumed_reuse=False,
        )
        return _run_live_materialization(
            args, space, f"Investigate reuse scenario {scenario.id}.", results_root, executable
        )


def _write_live_manifest(
    space: Runspace,
    *,
    identity_kind: str,
    identity: str,
    arm: str,
    focal_slug: str,
    seed_slugs: tuple[str, ...],
    stage: str,
    artifacts: tuple[str, ...],
    cross_argv: tuple[tuple[str, ...], ...],
    resumed_reuse: bool,
) -> None:
    payload = {
        "schema_version": 1,
        "identity_kind": identity_kind,
        "identity": identity,
        "arm": arm,
        "focal_slug": focal_slug,
        "seed_slugs": list(seed_slugs),
        "stage": stage,
        "artifacts": list(artifacts),
        "cross_argv": [list(argv) for argv in cross_argv],
        "resumed_reuse": resumed_reuse,
    }
    (space.path / "live-manifest.json").write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )


def _run_live_materialization(
    args: argparse.Namespace,
    space: Runspace,
    question: str,
    results_root: Path,
    executable: str,
) -> str:
    policy = (
        None if args.live_prompt_policy == "standard" else PromptPolicy(args.live_prompt_policy)
    )
    evaluation = (
        EvaluationQuestion.CROSS_RETRIEVAL
        if args.live_scenario is not None
        else EvaluationQuestion.LIVE_SINGLE_INVESTIGATION
    )
    prompt = build_prompt(
        PromptInputs(
            evaluation,
            question,
            args.live_arm,
            policy,
            HistoryCondition.PRESENT if evaluation is EvaluationQuestion.CROSS_RETRIEVAL else None,
            stop_at_transfer=True,
        ),
        PromptLeakSignals(),
    )
    if prompt.template.version != args.live_template_version:
        raise RunnerError("live template version differs from the scalar authorization")
    bounds = LiveBounds(args.live_max_turns, args.live_wall_clock)
    request = create_live_request(
        space,
        prompt,
        bounds=bounds,
        repetition=args.live_repetition,
        model=args.live_model,
        results_root=results_root,
    )
    connector = OpenCodeConnector(Path(executable), args.live_model)
    plan = PilotPlan(
        request.manifest.identity if request.manifest.identity_kind == "scenario" else None,
        request.manifest.identity if request.manifest.identity_kind == "item" else None,
        args.live_arm,
        args.live_repetition,
        args.live_host,
        args.live_host_version,
        args.live_model,
        None,
        prompt,
        bounds,
        results_root,
    )
    report = execute_pilot(
        plan,
        request=request,
        connector=connector,
        opt_in=resolve_live_opt_in(cli_opt_in=True),
    )
    return json.dumps(
        _pilot_report_payload(report),
        sort_keys=True,
        separators=(",", ":"),
    )


def _pilot_report_payload(report: PilotExitReport) -> dict[str, object]:
    if isinstance(report.tokens, TokenUsage):
        usage: dict[str, object] = {
            "available": True,
            "input_tokens": report.tokens.input_tokens,
            "output_tokens": report.tokens.output_tokens,
            "total_tokens": report.tokens.total_tokens,
            "model": report.tokens.model,
        }
    elif isinstance(report.tokens, TokenUsageUnavailable):
        usage = {"available": False, "reason": report.tokens.reason}
    else:
        raise RunnerError("pilot report has invalid token accounting")
    if isinstance(report.cost, MonetaryCost):
        cost: dict[str, object] = {
            "available": True,
            "amount": str(report.cost.amount),
            "currency": report.cost.currency,
        }
    elif isinstance(report.cost, MonetaryCostUnavailable):
        cost = {"available": False, "reason": report.cost.reason}
    else:
        raise RunnerError("pilot report has invalid monetary accounting")
    return {
        "session_id": report.session_id,
        "terminal_state": report.terminal_state,
        "approval_state": report.approval.state.value,
        "attributed": report.attributed,
        "wall_clock_seconds": report.wall_clock_seconds,
        "usage": usage,
        "cost": cost,
    }


def _executed_records(args: argparse.Namespace) -> RunRecordSet:
    """Execute the corpus with the scripted actor and build the durable record set."""
    corpus = load_corpus(args.corpus)
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    repository_before = audit_repository()
    runs = execute_arms(
        corpus,
        actor=ScriptedActor(),
        arms=resolve_arms(args.arm),
        repetitions=args.repetitions,
        max_workers=args.max_workers,
    )
    repository_after = audit_repository()
    return build_lifecycle_record_set(
        corpus=corpus,
        runs=runs,
        started_at=started_at,
        repository_before_sha256=repository_before,
        repository_after_sha256=repository_after,
    )


def _stored_records(args: argparse.Namespace) -> RunRecordSet:
    """Load a stored record set, refusing to combine re-rendering with execution."""
    defaults = build_parser().parse_args([])
    conflicting = tuple(
        name
        for name in ("arm", "corpus", "max_workers", "records", "repetitions")
        if getattr(args, name) != getattr(defaults, name)
    )
    if conflicting:
        flags = ", ".join(f"--{name.replace('_', '-')}" for name in conflicting)
        raise RunnerError(
            f"--from-records re-renders a stored record set and executes nothing, so {flags} "
            "cannot be combined with it"
        )
    path = Path(args.from_records).expanduser()
    if not path.is_file():
        raise RunnerError(f"run record set does not exist: {path}")
    return RunRecordSet.from_json(path.read_text(encoding="utf-8"))


def _target(value: str | None) -> Target | None:
    """Resolve one output request, refusing any path inside the repository tree."""
    if value is None:
        return None
    if value == STDOUT_TARGET:
        return Target(path=None)
    path = Path(value).expanduser().resolve()
    if path == REPOSITORY_ROOT or path.is_relative_to(REPOSITORY_ROOT):
        raise RunnerError(
            f"refusing to write harness output inside the repository tree: {path}; "
            f"choose a path outside {REPOSITORY_ROOT} or {STDOUT_TARGET!r} for stdout"
        )
    return Target(path=path)


def _write(target: Target, text: str, out: TextIO) -> None:
    if target.path is None:
        out.write(text)
        return
    target.path.parent.mkdir(parents=True, exist_ok=True)
    target.path.write_text(text, encoding="utf-8")
