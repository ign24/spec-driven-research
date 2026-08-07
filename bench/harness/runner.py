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
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from bench.harness.actor import ARMS, ScriptedActor
from bench.harness.arms import DEFAULT_REPETITIONS, ArmExecutionError, execute_arms
from bench.harness.corpus import DEFAULT_CORPUS_ROOT, CorpusError, load_corpus
from bench.harness.cost import DurationReporting
from bench.harness.record import RunRecordSet, build_run_record_set
from bench.harness.report import ReportError, render_report
from bench.harness.runspace import DEFAULT_MAX_WORKERS, REPOSITORY_ROOT, RunspaceError

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
        ValueError,
    ) as error:
        print(f"{_PROGRAM}: {error}", file=err)
        return EXIT_USAGE
    return EXIT_OK


def _execute(args: argparse.Namespace, out: TextIO) -> None:
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


def _executed_records(args: argparse.Namespace) -> RunRecordSet:
    """Execute the corpus with the scripted actor and build the durable record set."""
    corpus = load_corpus(args.corpus)
    runs = execute_arms(
        corpus,
        actor=ScriptedActor(),
        arms=resolve_arms(args.arm),
        repetitions=args.repetitions,
        max_workers=args.max_workers,
    )
    return build_run_record_set(
        corpus_version=corpus.version,
        repetitions=args.repetitions,
        items=corpus.items,
        runs=runs,
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
