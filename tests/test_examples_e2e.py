import sys
from pathlib import Path

import frontmatter

from examples.runner import evaluate_failing_example, run_example
from sdr import lifecycle

REPOSITORY_ROOT = Path(__file__).parents[1]
EXAMPLES = REPOSITORY_ROOT / "examples"


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _record_offline_reports(monkeypatch):
    reports = []
    check_stage = lifecycle.gates.check_stage

    def record(*args, **kwargs):
        report = check_stage(*args, **kwargs)
        reports.append(report)
        return report

    def network_check_must_not_run(url):
        raise AssertionError(f"offline lifecycle reached network check for {url}")

    monkeypatch.setattr(lifecycle.gates, "check_stage", record)
    monkeypatch.setattr(lifecycle.gates, "_default_url_checker", network_check_must_not_run)
    return reports


def _assert_network_checks_were_skipped(reports):
    link_results = [
        result for report in reports for result in report.results if result.check == "links_resolve"
    ]
    assert link_results
    assert all(result.skipped is True for result in link_results)
    assert all(result.passed is False for result in link_results)


def test_light_example_completes_offline_without_mutating_fixture(tmp_path, monkeypatch):
    fixture = EXAMPLES / "light-complete"
    before = _tree_bytes(fixture)
    reports = _record_offline_reports(monkeypatch)

    research = run_example(fixture, tmp_path / "research")

    assert research.meta.mode == "light"
    assert research.meta.status == "done"
    assert set(research.meta.validation) == {"intake", "explore", "transfer", "reuse"}
    sources = research.root / "notes" / "sources"
    assert not list(sources.rglob("content.md"))
    assert not list(sources.rglob("meta.yaml"))
    assert lifecycle.check_consistency(research) == []
    _assert_network_checks_were_skipped(reports)
    assert _tree_bytes(fixture) == before


def test_full_example_executes_portable_probe_and_completes_offline(tmp_path, monkeypatch):
    fixture = EXAMPLES / "full-complete"
    before = _tree_bytes(fixture)
    reports = _record_offline_reports(monkeypatch)

    research = run_example(fixture, tmp_path / "research")
    results = frontmatter.load(research.root / "probe" / "results.md")

    assert research.meta.mode == "full"
    assert research.meta.status == "done"
    assert set(research.meta.validation) == {
        "intake",
        "explore",
        "probe",
        "transfer",
        "reuse",
    }
    assert results.metadata["verify"] == {
        "action": "run",
        "argv": [sys.executable, "check.py"],
        "expect": "SYNTHETIC_PROBE_OK",
        "environment": "clean",
    }
    assert research.meta.verify_probe["result"] == "pass"
    assert lifecycle.check_consistency(research) == []
    _assert_network_checks_were_skipped(reports)
    assert _tree_bytes(fixture) == before


def test_failing_gate_examples_have_one_documented_defect_each(tmp_path):
    fixtures = sorted((EXAMPLES / "failing-gates").iterdir())

    assert fixtures
    for fixture in fixtures:
        if not fixture.is_dir():
            continue
        assert (fixture / "README.md").is_file()
        report, expected_check = evaluate_failing_example(
            fixture, tmp_path / fixture.name / "research"
        )
        failures = [result for result in report.results if not result.passed and not result.skipped]
        assert [failure.check for failure in failures] == [expected_check], fixture.name


def test_all_example_urls_use_reserved_synthetic_domains():
    allowed_suffixes = (".example", ".test", ".invalid")
    for path in EXAMPLES.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for token in text.split():
            if token.startswith(("http://", "https://")):
                host = token.split("/", 3)[2].rstrip(".,;:)")
                assert host.endswith(allowed_suffixes), (path, host)
