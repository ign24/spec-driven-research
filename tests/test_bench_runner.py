"""Command-line entry point for the harness: offline defaults and output containment.

The entry point is the only supported way to execute the whole corpus in one command.
Everything asserted here is a property of that boundary: the scripted actor is the
default, no output ever lands inside the repository tree, and the run leaves no
lifecycle residue behind.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from bench.harness.actor import ARMS, ActorKind
from bench.harness.record import RunRecordSet
from bench.harness.runner import EXIT_OK, EXIT_USAGE, build_parser, main, resolve_arms
from bench.harness.runspace import REPOSITORY_ROOT

_LIGHT_ITEM = """
id: runner-light-item
mode: light
title: Runner light item
question: What does the entry point execute for a light item?
planted_defects: []
"""

_FULL_ITEM = """
id: runner-full-item
mode: full
title: Runner full item
question: What does the entry point execute for a full item?
planted_defects:
  - inaccurate-source
expected_detection:
  inaccurate-source: uncaught
"""


@pytest.fixture
def corpus_root(tmp_path: Path) -> Path:
    root = tmp_path / "corpus"
    (root / "items").mkdir(parents=True)
    (root / "corpus.yaml").write_text(
        """version: "runner-test"
baseline_provenance:
  version: 1
  snapshot_schema_version: 2
  decision_lineage_field: evidence_claim_ids
  preserved_baseline: null
""",
        encoding="utf-8",
    )
    (root / "items" / "runner-light-item.yaml").write_text(_LIGHT_ITEM, encoding="utf-8")
    (root / "items" / "runner-full-item.yaml").write_text(_FULL_ITEM, encoding="utf-8")
    return root


def _run(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(argv, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_entry_point_renders_the_report_to_stdout_under_the_scripted_actor(
    corpus_root: Path,
) -> None:
    code, stdout, stderr = _run(["--corpus", str(corpus_root)])

    assert code == EXIT_OK
    assert stderr == ""
    assert stdout.startswith("# SDR evaluation harness evidence report")
    assert f'"actor":"{ActorKind.SCRIPTED.value}"' in stdout
    assert f'"actor":"{ActorKind.LIVE.value}"' not in stdout
    assert '"corpus":"runner-test"' in stdout


def test_entry_point_requires_no_api_key_and_no_network(
    corpus_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for variable in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SDR_BENCH_LIVE_ACTOR"):
        monkeypatch.delenv(variable, raising=False)

    code, stdout, _ = _run(["--corpus", str(corpus_root)])

    assert code == EXIT_OK
    assert f'"actor":"{ActorKind.SCRIPTED.value}"' in stdout


def test_entry_point_writes_run_records_to_a_path_outside_the_repository(
    corpus_root: Path, tmp_path: Path
) -> None:
    records_path = tmp_path / "out" / "records.json"

    code, _, _ = _run(["--corpus", str(corpus_root), "--records", str(records_path)])

    assert code == EXIT_OK
    records = RunRecordSet.from_json(records_path.read_text(encoding="utf-8"))
    assert {record.corpus.version for record in records.records} == {"runner-test"}
    assert {record.arm for record in records.records} == set(ARMS)
    assert all(record.actor is ActorKind.SCRIPTED for record in records.records)


def test_entry_point_writes_the_report_to_a_path_outside_the_repository(
    corpus_root: Path, tmp_path: Path
) -> None:
    report_path = tmp_path / "out" / "report.md"

    code, stdout, _ = _run(["--corpus", str(corpus_root), "--report", str(report_path)])

    assert code == EXIT_OK
    assert stdout == ""
    assert report_path.read_text(encoding="utf-8").startswith(
        "# SDR evaluation harness evidence report"
    )


@pytest.mark.parametrize("flag", ["--records", "--report"])
def test_entry_point_refuses_to_write_inside_the_repository_tree(
    corpus_root: Path, flag: str
) -> None:
    target = REPOSITORY_ROOT / "bench" / "runner-output.json"

    code, stdout, stderr = _run(["--corpus", str(corpus_root), flag, str(target)])

    assert code == EXIT_USAGE
    assert not target.exists()
    assert stdout == ""
    assert "repository tree" in stderr


def test_entry_point_rejects_a_relative_output_path_resolving_into_the_repository(
    corpus_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPOSITORY_ROOT)

    code, _, stderr = _run(["--corpus", str(corpus_root), "--records", "records.json"])

    assert code == EXIT_USAGE
    assert not (REPOSITORY_ROOT / "records.json").exists()
    assert "repository tree" in stderr


def test_entry_point_writes_records_to_stdout_when_asked(corpus_root: Path) -> None:
    code, stdout, _ = _run(["--corpus", str(corpus_root), "--records", "-", "--no-report"])

    assert code == EXIT_OK
    records = RunRecordSet.from_json(stdout)
    assert {record.repetition for record in records.records} == {0}


def test_entry_point_limits_execution_to_the_requested_arms(
    corpus_root: Path, tmp_path: Path
) -> None:
    records_path = tmp_path / "records.json"

    code, _, _ = _run(
        ["--corpus", str(corpus_root), "--arm", "light", "--records", str(records_path)]
    )

    assert code == EXIT_OK
    records = RunRecordSet.from_json(records_path.read_text(encoding="utf-8"))
    assert {record.arm for record in records.records} == {"light"}


def test_entry_point_repeats_every_applicable_arm(corpus_root: Path, tmp_path: Path) -> None:
    records_path = tmp_path / "records.json"

    code, _, _ = _run(
        [
            "--corpus",
            str(corpus_root),
            "--arm",
            "light",
            "--repetitions",
            "2",
            "--records",
            str(records_path),
        ]
    )

    assert code == EXIT_OK
    records = RunRecordSet.from_json(records_path.read_text(encoding="utf-8"))
    assert {record.repetition for record in records.records} == {0, 1}
    repetitions = sorted(
        record.repetition for record in records.records if record.item_id == "runner-light-item"
    )
    assert repetitions == [0, 1]


def test_entry_point_rejects_a_repetition_count_below_one(corpus_root: Path) -> None:
    code, stdout, stderr = _run(["--corpus", str(corpus_root), "--repetitions", "0"])

    assert code == EXIT_USAGE
    assert stdout == ""
    assert "repetitions" in stderr


def test_entry_point_reports_a_missing_corpus_without_a_traceback(tmp_path: Path) -> None:
    code, stdout, stderr = _run(["--corpus", str(tmp_path / "absent")])

    assert code == EXIT_USAGE
    assert stdout == ""
    assert "corpus root does not exist" in stderr


def test_entry_point_leaves_no_lifecycle_residue_in_the_repository_tree(
    corpus_root: Path,
) -> None:
    code, _, _ = _run(["--corpus", str(corpus_root)])

    assert code == EXIT_OK
    assert not (REPOSITORY_ROOT / "research").exists()
    assert not (REPOSITORY_ROOT / "knowledge").exists()
    assert not (REPOSITORY_ROOT / "sdr.yaml").exists()
    assert not (REPOSITORY_ROOT / "bench" / "sdr.yaml").exists()


def test_entry_point_re_renders_a_stored_record_set_byte_identically(
    corpus_root: Path, tmp_path: Path
) -> None:
    records_path = tmp_path / "records.json"
    _run(["--corpus", str(corpus_root), "--records", str(records_path), "--no-report"])

    first = _run(["--from-records", str(records_path)])
    second = _run(["--from-records", str(records_path)])

    assert first[0] == EXIT_OK
    assert first[1] == second[1]
    assert first[1].startswith("# SDR evaluation harness evidence report")


def test_entry_point_refuses_to_re_render_and_execute_in_one_invocation(
    corpus_root: Path, tmp_path: Path
) -> None:
    records_path = tmp_path / "records.json"
    _run(["--corpus", str(corpus_root), "--records", str(records_path), "--no-report"])

    code, stdout, stderr = _run(
        ["--from-records", str(records_path), "--records", str(tmp_path / "again.json")]
    )

    assert code == EXIT_USAGE
    assert stdout == ""
    assert "--from-records" in stderr


def test_default_corpus_is_the_packaged_corpus_and_the_default_report_is_stdout() -> None:
    defaults = build_parser().parse_args([])

    assert Path(defaults.corpus) == REPOSITORY_ROOT / "bench" / "corpus"
    assert defaults.report == "-"
    assert defaults.records is None
    assert defaults.repetitions == 1
    assert resolve_arms(defaults.arm) == ARMS
