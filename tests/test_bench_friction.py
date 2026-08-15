"""Lifecycle friction accounting: reopens, gate failures by control, manual claim closures."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from bench.harness.actor import (
    LIGHT_ARM,
    SCRIPTED_TOKENS,
    ActorKind,
    ActorResult,
    CommandResult,
    DetectionBasis,
)
from bench.harness.friction import (
    ADVANCE_BLOCKED_PREFIX,
    CONTROL_VOCABULARY,
    EVIDENTIAL_CHECKS,
    STRUCTURAL_CHECKS,
    ClaimAccounting,
    ControlType,
    FrictionAccounting,
    ReopenTrail,
    ReopenTrailUnavailable,
    attribute_check,
    attribute_prose,
    claim_accounting,
    collect_friction,
    gate_failures,
    reopen_accounting,
    reopen_trail_available,
)
from sdr import lifecycle, probe_verify, schema
from sdr.cli import main
from sdr.research import Research

SLUG = "bench-friction-item"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _trail_repo(path: Path, subjects: list[str]) -> Path:
    """Build a Git root whose history mimics what `sdr.trail` writes."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "--quiet", "--initial-branch", "main")
    _git(path, "config", "user.name", "Bench Friction Test")
    _git(path, "config", "user.email", "bench@localhost")
    _git(path, "config", "commit.gpgsign", "false")
    for index, subject in enumerate(subjects):
        (path / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
        _git(path, "add", "--all")
        _git(path, "commit", "-m", subject)
    return path


def _command(
    *argv: str,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    payload: Any | None = None,
    payload_error: str | None = None,
) -> CommandResult:
    return CommandResult(
        argv=argv,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        payload=payload,
        payload_error=payload_error,
    )


def _blocked_advance(reason: str) -> CommandResult:
    return _command(
        "advance",
        SLUG,
        "--offline",
        exit_code=1,
        stdout=f"{ADVANCE_BLOCKED_PREFIX}{reason}\n[FAIL] {SLUG} (stage explore)\n",
    )


def _result(commands: tuple[CommandResult, ...], workspace: Path) -> ActorResult:
    return ActorResult(
        actor=ActorKind.SCRIPTED,
        item_id=SLUG,
        arm=LIGHT_ARM,
        repetition=0,
        slug=SLUG,
        artifacts_written=(),
        commands=commands,
        stages=(),
        tokens=SCRIPTED_TOKENS,
        detection_basis=DetectionBasis.MEASURED,
        detection_basis_reason="test double",
        duration_seconds=0.0,
        workspace=workspace,
        research_root=workspace / "research",
    )


# ---------------------------------------------------------------------------
# 9.1 / 9.2 Reopen counting from trail commits
# ---------------------------------------------------------------------------


def test_reopen_transitions_are_counted_with_origin_and_target_stage(tmp_path: Path) -> None:
    root = _trail_repo(
        tmp_path / "run",
        [
            f"research({SLUG}): new",
            f"research({SLUG}): intake -> explore",
            f"research({SLUG}): reopen explore -> intake",
            f"research({SLUG}): intake -> explore",
            f"research({SLUG}): reopen explore -> intake",
        ],
    )
    accounting = reopen_accounting(root, slug=SLUG)

    assert reopen_trail_available(accounting)
    assert accounting.count == 2
    assert [(entry.from_stage, entry.to_stage) for entry in accounting.transitions] == [
        ("explore", "intake"),
        ("explore", "intake"),
    ]
    assert accounting.transitions[0].subject == f"research({SLUG}): reopen explore -> intake"


def test_reopen_counting_ignores_non_reopen_and_foreign_transitions(tmp_path: Path) -> None:
    root = _trail_repo(
        tmp_path / "run",
        [
            f"research({SLUG}): new",
            "research(other-item): reopen transfer -> explore",
            f"research({SLUG}): explore -> transfer",
            f"research({SLUG}): reopen transfer -> explore",
            "chore: unrelated commit",
        ],
    )
    accounting = reopen_accounting(root, slug=SLUG)

    assert reopen_trail_available(accounting)
    assert accounting.count == 1
    assert accounting.transitions[0].slug == SLUG
    assert accounting.transitions[0].from_stage == "transfer"
    assert accounting.transitions[0].to_stage == "explore"


def test_reopen_counting_without_a_slug_filter_reports_every_investigation(
    tmp_path: Path,
) -> None:
    root = _trail_repo(
        tmp_path / "run",
        [
            f"research({SLUG}): reopen explore -> intake",
            "research(other-item): reopen transfer -> explore",
        ],
    )
    accounting = reopen_accounting(root)

    assert reopen_trail_available(accounting)
    assert accounting.count == 2


def test_reopen_accounting_is_unavailable_rather_than_zero_after_teardown(tmp_path: Path) -> None:
    missing = tmp_path / "torn-down"
    accounting = reopen_accounting(missing, slug=SLUG)

    assert isinstance(accounting, ReopenTrailUnavailable)
    assert not reopen_trail_available(accounting)
    assert str(missing) in accounting.reason
    assert not hasattr(accounting, "count")


def test_reopen_accounting_is_unavailable_when_the_directory_is_not_a_git_root(
    tmp_path: Path,
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    accounting = reopen_accounting(plain, slug=SLUG)

    assert isinstance(accounting, ReopenTrailUnavailable)
    assert accounting.reason


def test_an_empty_trail_is_available_and_reports_no_reopen(tmp_path: Path) -> None:
    root = _trail_repo(tmp_path / "run", [f"research({SLUG}): new"])
    accounting = reopen_accounting(root, slug=SLUG)

    assert isinstance(accounting, ReopenTrail)
    assert accounting.count == 0


def test_a_git_root_without_commits_is_an_available_empty_trail(tmp_path: Path) -> None:
    root = _trail_repo(tmp_path / "run", [])

    accounting = reopen_accounting(root, slug=SLUG)

    assert isinstance(accounting, ReopenTrail)
    assert accounting.count == 0


def test_a_failed_head_verification_is_not_mistaken_for_an_unborn_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _trail_repo(tmp_path / "run", [f"research({SLUG}): new"])
    real_run = subprocess.run

    def corrupt_head(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1:4] == ["rev-parse", "--verify", "HEAD"]:
            return subprocess.CompletedProcess(argv, 128, "", "fatal: bad object HEAD")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", corrupt_head)

    accounting = reopen_accounting(root, slug=SLUG)

    assert isinstance(accounting, ReopenTrailUnavailable)
    assert "bad object HEAD" in accounting.reason


def test_head_inspection_subprocess_failure_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _trail_repo(tmp_path / "run", [])
    real_run = subprocess.run

    def fail_head(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1:4] == ["rev-parse", "--verify", "HEAD"]:
            raise PermissionError("HEAD denied")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_head)

    accounting = reopen_accounting(root, slug=SLUG)

    assert isinstance(accounting, ReopenTrailUnavailable)
    assert "HEAD denied" in accounting.reason


# ---------------------------------------------------------------------------
# 9.3 / 9.4 Gate failure attribution to the documented control vocabulary
# ---------------------------------------------------------------------------


def test_control_vocabulary_has_exactly_the_six_documented_entries_in_order() -> None:
    assert [control.value for control in CONTROL_VOCABULARY] == [
        "structural",
        "evidential",
        "textual-anchoring",
        "executable",
        "hash-consistency",
        "human-approval",
    ]
    assert set(CONTROL_VOCABULARY) == set(ControlType)


def test_structural_and_evidential_check_names_are_disjoint_and_json_derived() -> None:
    assert STRUCTURAL_CHECKS.isdisjoint(EVIDENTIAL_CHECKS)
    assert attribute_check("structure") is ControlType.STRUCTURAL
    assert attribute_check("frontmatter") is ControlType.STRUCTURAL
    assert attribute_check("section") is ControlType.STRUCTURAL
    assert attribute_check("source_triangulation") is ControlType.EVIDENTIAL
    assert attribute_check("links_resolve") is ControlType.EVIDENTIAL
    assert attribute_check("no-such-check") is None


def test_check_json_failures_are_attributed_from_structured_fields(tmp_path: Path) -> None:
    payload = {
        "stage": "explore",
        "passed": False,
        "slug": SLUG,
        "consistency_issues": [],
        "results": [
            {
                "check": "structure",
                "passed": False,
                "skipped": False,
                "detail": "no artifact covers notes/",
            },
            {
                "check": "links_resolve",
                "passed": False,
                "skipped": False,
                "detail": "S1 is unreachable",
            },
            {"check": "source_tiers", "passed": True, "skipped": False, "detail": "ok"},
            {"check": "links_resolve", "passed": False, "skipped": True, "detail": "offline"},
        ],
    }
    commands = (_command("check", SLUG, "--json", exit_code=1, payload=payload),)
    attributed, unmapped = gate_failures(commands)

    assert not unmapped
    assert [(failure.control, failure.check) for failure in attributed] == [
        (ControlType.STRUCTURAL, "structure"),
        (ControlType.EVIDENTIAL, "links_resolve"),
    ]
    assert all(failure.source == "json" for failure in attributed)


def test_consistency_issues_are_attributed_to_hash_consistency() -> None:
    payload = {
        "stage": "explore",
        "passed": True,
        "slug": SLUG,
        "results": [],
        "consistency_issues": ["stage 'intake': the artifact changed after validation"],
    }
    attributed, unmapped = gate_failures((_command("check", SLUG, "--json", payload=payload),))

    assert not unmapped
    assert [failure.control for failure in attributed] == [ControlType.HASH_CONSISTENCY]
    assert attributed[0].source == "json"


def test_verify_claims_failures_are_attributed_to_textual_anchoring() -> None:
    payload = {
        "schema_version": 2,
        "slug": SLUG,
        "passed": False,
        "failures": ["C1: not_anchored in s1"],
        "items": [
            {"claim_id": "C1", "source_id": "s1", "state": "not_anchored"},
            {"claim_id": "C2", "source_id": "s1", "state": "verified"},
            {"claim_id": "C3", "source_id": "s2", "state": "human_reviewed"},
        ],
    }
    attributed, unmapped = gate_failures(
        (_command("verify-claims", SLUG, "--json", exit_code=1, payload=payload),)
    )

    assert not unmapped
    assert [(failure.control, failure.check) for failure in attributed] == [
        (ControlType.TEXTUAL_ANCHORING, "C1")
    ]
    assert "not_anchored" in attributed[0].detail


def test_verify_probe_failure_is_attributed_to_executable() -> None:
    payload = {
        "result": "fail",
        "command": "python -c 'pass'",
        "expect": "contains: ok",
        "exit_code": 0,
        "error_code": "expect_mismatch",
        "error": None,
        "output": "",
    }
    attributed, unmapped = gate_failures(
        (_command("verify-probe", SLUG, "--json", exit_code=1, payload=payload),)
    )

    assert not unmapped
    assert [failure.control for failure in attributed] == [ControlType.EXECUTABLE]
    assert attributed[0].check == "expect_mismatch"
    assert attributed[0].source == "json"


@pytest.mark.parametrize(
    ("reason", "control"),
    [
        (
            "hash consistency failed: stage 'intake': the artifact changed after validation",
            ControlType.HASH_CONSISTENCY,
        ),
        ("anchored verification failed: C1: not_anchored in s1", ControlType.TEXTUAL_ANCHORING),
        ("missing a stored passing sdr verify-probe", ControlType.EXECUTABLE),
        (
            "probe verification is stale; run sdr verify-probe",
            ControlType.HASH_CONSISTENCY,
        ),
        ("missing human approval (sdr approve)", ControlType.HUMAN_APPROVAL),
    ],
)
def test_blocked_advance_prose_is_attributed_through_the_mapping_table(
    reason: str, control: ControlType
) -> None:
    attributed, unmapped = gate_failures((_blocked_advance(reason),))

    assert not unmapped
    assert [failure.control for failure in attributed] == [control]
    assert attributed[0].source == "prose"
    assert attributed[0].detail == reason


def test_an_unmatched_prose_reason_lands_in_the_unmapped_bucket_verbatim() -> None:
    reason = "a brand new reason the mapping table does not know"
    attributed, unmapped = gate_failures((_blocked_advance(reason),))

    assert not attributed
    assert len(unmapped) == 1
    assert unmapped[0].reason == reason
    assert unmapped[0].pattern is None
    assert unmapped[0].candidates == ()
    assert not unmapped[0].is_ambiguous


def test_a_prose_reason_naming_two_controls_is_unmapped_with_its_candidates() -> None:
    reason = "structural/evidential gates failed"
    attributed, unmapped = gate_failures((_blocked_advance(reason),))

    assert not attributed, "an ambiguous reason must never be guessed into one control"
    assert len(unmapped) == 1
    assert unmapped[0].is_ambiguous
    assert unmapped[0].candidates == (ControlType.STRUCTURAL, ControlType.EVIDENTIAL)
    assert unmapped[0].reason == reason


def test_attribute_prose_reports_candidates_without_choosing_one() -> None:
    single = attribute_prose("missing human approval (sdr approve)")
    assert single.candidates == (ControlType.HUMAN_APPROVAL,)
    assert single.control is ControlType.HUMAN_APPROVAL

    ambiguous = attribute_prose("structural/evidential gates failed")
    assert ambiguous.control is None
    assert len(ambiguous.candidates) == 2

    unknown = attribute_prose("unknown text")
    assert unknown.control is None
    assert unknown.candidates == ()
    assert unknown.pattern is None


def test_a_failed_approve_is_recorded_and_never_guessed_into_one_control() -> None:
    command = _command(
        "approve",
        SLUG,
        "--by",
        "Bench Reviewer",
        exit_code=1,
        stdout=f"[FAIL] {SLUG} (stage transfer)\n",
        stderr="Error: transfer gates must pass before approving\n",
    )
    attributed, unmapped = gate_failures((command,))

    assert not attributed
    assert len(unmapped) == 1
    assert unmapped[0].is_ambiguous
    assert "transfer gates" in unmapped[0].reason


def test_unusable_structured_output_is_unmapped_rather_than_silently_dropped() -> None:
    command = _command(
        "check", SLUG, "--json", exit_code=1, stdout="not json", payload_error="invalid JSON output"
    )
    attributed, unmapped = gate_failures((command,))

    assert not attributed
    assert len(unmapped) == 1
    assert "invalid JSON output" in unmapped[0].reason


def test_every_attributed_control_belongs_to_the_documented_vocabulary(tmp_path: Path) -> None:
    commands = (
        _command(
            "check",
            SLUG,
            "--json",
            exit_code=1,
            payload={
                "stage": "explore",
                "passed": False,
                "results": [
                    {
                        "check": "section",
                        "passed": False,
                        "skipped": False,
                        "detail": "empty section",
                    },
                    {
                        "check": "claim_citation_coverage",
                        "passed": False,
                        "skipped": False,
                        "detail": "no citation",
                    },
                ],
                "consistency_issues": ["stage 'intake': changed"],
            },
        ),
        _blocked_advance("missing human approval (sdr approve)"),
    )
    attributed, _ = gate_failures(commands)

    assert attributed
    assert all(failure.control in CONTROL_VOCABULARY for failure in attributed)


def test_failures_by_control_reports_every_vocabulary_entry_zero_filled(tmp_path: Path) -> None:
    result = _result((_blocked_advance("missing human approval (sdr approve)"),), tmp_path)
    friction = collect_friction(result, git_root=tmp_path)

    counts = friction.failures_by_control()
    assert list(counts) == list(CONTROL_VOCABULARY)
    assert counts[ControlType.HUMAN_APPROVAL] == 1
    assert counts[ControlType.STRUCTURAL] == 0
    assert sum(counts.values()) == len(friction.gate_failures)


def test_unmapped_failures_are_never_counted_toward_a_control(tmp_path: Path) -> None:
    result = _result((_blocked_advance("structural/evidential gates failed"),), tmp_path)
    friction = collect_friction(result, git_root=tmp_path)

    assert friction.unmapped
    assert sum(friction.failures_by_control().values()) == 0
    assert friction.unmapped_count == 1


# ---------------------------------------------------------------------------
# 9.5 Claims closed through resolve-claim versus claims that passed anchoring
# ---------------------------------------------------------------------------


def test_claims_closed_through_resolve_claim_are_counted_separately_from_anchored_ones() -> None:
    payload = {
        "schema_version": 2,
        "slug": SLUG,
        "passed": True,
        "failures": [],
        "items": [
            {"claim_id": "C1", "source_id": "s1", "state": "verified"},
            {"claim_id": "C2", "source_id": "s1", "state": "verified"},
            {"claim_id": "C3", "source_id": "s2", "state": "human_reviewed"},
        ],
    }
    commands = (
        _command("resolve-claim", SLUG, "C3", "--reason", "human review", "--by", "Reviewer"),
        _command("verify-claims", SLUG, "--json", payload=payload),
    )
    accounting = claim_accounting(commands)

    assert accounting.recorded
    assert accounting.passed_anchoring == ("C1", "C2")
    assert accounting.human_reviewed == ("C3",)
    assert accounting.passed_anchoring_count == 2
    assert accounting.human_review_count == 1
    assert accounting.resolved_claim_ids == ("C3",)
    assert len(accounting.resolve_claim_calls) == 1
    assert accounting.resolve_claim_calls[0].succeeded


def test_claim_accounting_exposes_no_merged_verified_total() -> None:
    for forbidden in ("verified", "claims_verified", "passed", "total_passed"):
        assert not hasattr(ClaimAccounting, forbidden), (
            f"{forbidden!r} would merge human overrides with anchored claims"
        )


def test_a_failed_resolve_claim_is_recorded_but_closes_no_claim() -> None:
    commands = (
        _command(
            "resolve-claim",
            SLUG,
            "C9",
            "--reason",
            "x",
            "--by",
            "Reviewer",
            exit_code=1,
            stderr=(
                "Error: active claim C9 does not exist; "
                "run sdr verify-claims and use a current ID\n"
            ),
        ),
    )
    accounting = claim_accounting(commands)

    assert len(accounting.resolve_claim_calls) == 1
    assert not accounting.resolve_claim_calls[0].succeeded
    assert accounting.resolved_claim_ids == ()


def test_claim_accounting_uses_the_last_verify_claims_payload() -> None:
    first = {
        "items": [{"claim_id": "C1", "source_id": "s1", "state": "not_anchored"}],
        "passed": False,
        "failures": ["C1: not_anchored in s1"],
    }
    last = {
        "items": [{"claim_id": "C1", "source_id": "s1", "state": "human_reviewed"}],
        "passed": True,
        "failures": [],
    }
    commands = (
        _command("verify-claims", SLUG, "--json", exit_code=1, payload=first),
        _command("resolve-claim", SLUG, "C1", "--reason", "r", "--by", "Reviewer"),
        _command("verify-claims", SLUG, "--json", payload=last),
    )
    accounting = claim_accounting(commands)

    assert accounting.passed_anchoring == ()
    assert accounting.human_reviewed == ("C1",)
    assert accounting.unresolved == ()


def test_claim_accounting_is_not_recorded_when_no_verify_claims_ran() -> None:
    accounting = claim_accounting((_command("check", SLUG, "--json", payload={"results": []}),))

    assert not accounting.recorded
    assert accounting.passed_anchoring == ()
    assert accounting.human_reviewed == ()


# ---------------------------------------------------------------------------
# collect_friction: the unit group 10 consumes
# ---------------------------------------------------------------------------


def test_collect_friction_joins_reopens_gate_failures_and_claims(tmp_path: Path) -> None:
    root = _trail_repo(
        tmp_path / "run",
        [
            f"research({SLUG}): new",
            f"research({SLUG}): intake -> explore",
            f"research({SLUG}): reopen explore -> intake",
        ],
    )
    commands = (
        _blocked_advance("anchored verification failed: C1: not_anchored in s1"),
        _command(
            "verify-claims",
            SLUG,
            "--json",
            payload={
                "items": [
                    {"claim_id": "C1", "source_id": "s1", "state": "human_reviewed"},
                    {"claim_id": "C2", "source_id": "s1", "state": "verified"},
                ],
                "passed": True,
                "failures": [],
            },
        ),
    )
    friction = collect_friction(_result(commands, root), git_root=root)

    assert isinstance(friction, FrictionAccounting)
    assert friction.item_id == SLUG
    assert friction.arm == LIGHT_ARM
    assert reopen_trail_available(friction.reopens)
    assert friction.reopens.count == 1
    assert friction.failures_by_control()[ControlType.TEXTUAL_ANCHORING] == 1
    assert friction.claims.human_reviewed == ("C1",)
    assert friction.claims.passed_anchoring == ("C2",)


def test_collect_friction_defaults_the_git_root_to_the_run_workspace(tmp_path: Path) -> None:
    root = _trail_repo(tmp_path / "run", [f"research({SLUG}): reopen explore -> intake"])
    friction = collect_friction(_result((), root))

    assert reopen_trail_available(friction.reopens)
    assert friction.reopens.count == 1


def test_collect_friction_after_teardown_reports_reopens_unavailable(tmp_path: Path) -> None:
    friction = collect_friction(_result((), tmp_path / "gone"))

    assert not reopen_trail_available(friction.reopens)
    assert friction.gate_failures == ()


# ---------------------------------------------------------------------------
# The prose mapping is exercised against reasons produced by the real lifecycle
#
# Every reason below is obtained from `sdr.lifecycle.advance`, never written as a
# literal here. A literal would let the product prose drift away from the mapping
# table while these tests kept passing over an unreachable pattern.
# ---------------------------------------------------------------------------


def _artifact(frontmatter: Mapping[str, str], sections: Mapping[str, str]) -> str:
    """Render a stage artifact from frontmatter pairs and section bodies."""
    head = "".join(f"{key}: {value}\n" for key, value in frontmatter.items())
    body = "\n".join(f"## {name}\n{text}\n" for name, text in sections.items())
    return f"---\n{head}---\n\n{body}"


def _today() -> str:
    return date.today().isoformat()


def _intake(tmp_path: Path, *, mode: str = "full") -> Research:
    """An investigation whose intake artifact passes the structural and evidential gates."""
    research = Research.create(
        base=tmp_path,
        slug="bench-friction-lifecycle",
        title="t",
        question="q",
        mode=mode,
        owner="bench",
        timebox=3,
    )
    research.artifact_path("brief.md").write_text(
        _artifact(
            {
                "research": "bench-friction-lifecycle",
                "date": _today(),
                "stage": "intake",
                "owner": "bench",
                "timebox": "3",
            },
            {
                schema.SECTION_QUESTION: "Is Foo usable?",
                schema.SECTION_HYPOTHESIS: "Yes.",
                schema.SECTION_CONTEXT: "X.",
                schema.SECTION_SCOPE: "A, not B.",
                schema.SECTION_EVALUATION_CRITERIA: (
                    "- C1: latency under 200ms\n- C2: cost under 10 USD"
                ),
                schema.SECTION_ADOPTION_RISKS: "Lock-in.",
            },
        ),
        encoding="utf-8",
    )
    research.save()
    return research


def _blocked_by_gates(tmp_path: Path) -> Research:
    """Intake with no usable brief: the structural and evidential layers refuse."""
    return Research.create(base=tmp_path, slug="bench-friction-lifecycle", title="t", question="q")


def _blocked_by_hash_consistency(tmp_path: Path) -> Research:
    """A validated stage whose artifact changed after validation."""
    research = _intake(tmp_path)
    advanced = lifecycle.advance(research, offline=True)
    assert advanced.ok, advanced.blocked_reason
    research.artifact_path("brief.md").write_text("tampered", encoding="utf-8")
    return research


def _blocked_by_anchored_verification(tmp_path: Path) -> Research:
    """Explore notes whose claims cannot be anchored against their sources."""
    research = _intake(tmp_path, mode="light")
    advanced = lifecycle.advance(research, offline=True)
    assert advanced.ok, advanced.blocked_reason
    notes = research.artifact_path("notes")
    notes.mkdir(exist_ok=True)
    today = _today()
    sources = (
        "sources:\n"
        "  - id: S1\n"
        "    url: https://docs.foo.example/guide\n"
        "    tier: T1\n"
        f"    date: {today}\n"
        "    alternative: foo\n"
        "  - id: S2\n"
        "    url: https://bench.example.org/foo\n"
        "    tier: T2\n"
        f"    date: {today}\n"
        "    alternative: foo\n"
    )
    (notes / "scan.md").write_text(
        "---\n"
        "research: bench-friction-lifecycle\n"
        f"date: {today}\n"
        "stage: explore\n"
        f"{sources}"
        "---\n\n"
        f"## {schema.SECTION_ALTERNATIVES}\nFoo exists [S1].\n\n"
        f"## {schema.SECTION_MATURITY}\nFoo has support [S1].\n\n"
        f"## {schema.SECTION_COSTS}\nFoo is cheap [S2].\n\n"
        f"## {schema.SECTION_RISKS}\nFoo carries risks [S2].\n\n"
        f"## {schema.SECTION_COUNTER_EVIDENCE}\nNone found.\n",
        encoding="utf-8",
    )
    research.save()
    return research


def _probe_ready(tmp_path: Path) -> Research:
    """A probe stage whose artifacts pass the gates, with no stored verification."""
    research = _intake(tmp_path)
    research.meta.stage = "probe"
    research.artifact_path("probe/results.md").write_text(
        "---\n"
        "research: bench-friction-lifecycle\n"
        f"date: {_today()}\n"
        "stage: probe\n"
        "verify:\n"
        "  action: run\n"
        "  command: python check.py\n"
        "  expect: OK\n"
        "---\n\n"
        f"## {schema.SECTION_CRITERIA_RESULTS}\n"
        "- C1: meets - output OK\n"
        "- C2: meets - low cost\n\n"
        f"## {schema.SECTION_REPRODUCTION}\n"
        "```bash\npython check.py\n```\n",
        encoding="utf-8",
    )
    research.artifact_path("probe/check.py").write_text("print('OK')\n", encoding="utf-8")
    research.save()
    return research


def _blocked_by_missing_stored_probe(tmp_path: Path) -> Research:
    return _probe_ready(tmp_path)


def _blocked_by_stale_probe(tmp_path: Path) -> Research:
    """A stored passing verification whose hash no longer matches the probe directory."""
    research = _probe_ready(tmp_path)
    research.meta.verify_probe = {"result": "pass", "probe_hash": "stale"}
    research.save()
    assert research.meta.verify_probe["probe_hash"] != probe_verify.hash_probe_dir(research)
    return research


def _blocked_by_missing_approval(tmp_path: Path) -> Research:
    """A transfer memo that passes every gate and has no recorded human approval."""
    research = _intake(tmp_path, mode="light")
    research.meta.stage = "transfer"
    # `gates._check_y_statement` matches the recommendation prose by keyword, so this
    # fixture must carry the decision, context, evidence, and accepted-downside markers.
    recommendation = (
        "We decided to assess Foo for support, because the evidence for C1 and C2 is "
        "partial, accepting the trade-off of not adopting it yet."
    )
    research.artifact_path("decision-memo.md").write_text(
        _artifact(
            {
                "research": "bench-friction-lifecycle",
                "date": _today(),
                "stage": "transfer",
                "ring": "assess",
                "audience": "team",
                "evidence_claim_ids": "[]",
            },
            {
                schema.SECTION_RECOMMENDATION: recommendation,
                schema.SECTION_ALTERNATIVES: "Foo, Bar.",
                schema.SECTION_SELECTION_CRITERIA: "Cost.",
                schema.SECTION_RISKS_AND_LIMITS: "Lock-in.",
                schema.SECTION_NEXT_STEPS: "Pilot.",
                schema.SECTION_AUDIENCE: "Team.",
            },
        ),
        encoding="utf-8",
    )
    research.save()
    return research


_BLOCKED_STATES: tuple[tuple[str, Callable[[Path], Research], tuple[ControlType, ...]], ...] = (
    (
        "hash-consistency",
        _blocked_by_hash_consistency,
        (ControlType.HASH_CONSISTENCY,),
    ),
    (
        "structural-evidential-gates",
        _blocked_by_gates,
        (ControlType.STRUCTURAL, ControlType.EVIDENTIAL),
    ),
    (
        "anchored-verification",
        _blocked_by_anchored_verification,
        (ControlType.TEXTUAL_ANCHORING,),
    ),
    (
        "missing-stored-verify-probe",
        _blocked_by_missing_stored_probe,
        (ControlType.EXECUTABLE,),
    ),
    (
        "stale-probe-verification",
        _blocked_by_stale_probe,
        (ControlType.HASH_CONSISTENCY,),
    ),
    (
        "missing-human-approval",
        _blocked_by_missing_approval,
        (ControlType.HUMAN_APPROVAL,),
    ),
)


def _lifecycle_blocked_reason(build: Callable[[Path], Research], tmp_path: Path) -> str:
    research = build(tmp_path)
    result = lifecycle.advance(research, offline=True)
    assert not result.ok, "the fixture must reach the blocked state under test"
    assert result.blocked_reason, "a blocked advance must state its reason"
    return result.blocked_reason


@pytest.mark.parametrize(
    ("name", "build", "expected"),
    _BLOCKED_STATES,
    ids=[name for name, _, _ in _BLOCKED_STATES],
)
def test_every_lifecycle_blocked_reason_is_classified_by_the_prose_mapping(
    name: str,
    build: Callable[[Path], Research],
    expected: tuple[ControlType, ...],
    tmp_path: Path,
) -> None:
    reason = _lifecycle_blocked_reason(build, tmp_path)

    mapping = attribute_prose(reason)

    assert mapping.candidates == expected, (
        f"lifecycle blocked reason for {name} is unreachable from the mapping table: {reason!r}"
    )


@pytest.mark.parametrize(
    ("name", "build", "expected"),
    _BLOCKED_STATES,
    ids=[name for name, _, _ in _BLOCKED_STATES],
)
def test_a_blocked_advance_command_is_attributed_from_the_lifecycle_reason(
    name: str,
    build: Callable[[Path], Research],
    expected: tuple[ControlType, ...],
    tmp_path: Path,
) -> None:
    reason = _lifecycle_blocked_reason(build, tmp_path)

    attributed, unmapped = gate_failures((_blocked_advance(reason),))

    if len(expected) == 1:
        assert not unmapped
        assert [failure.control for failure in attributed] == list(expected)
        assert attributed[0].source == "prose"
        assert attributed[0].detail == reason
    else:
        assert not attributed, "an ambiguous reason must never be guessed into one control"
        assert len(unmapped) == 1
        assert unmapped[0].is_ambiguous
        assert unmapped[0].candidates == expected
        assert unmapped[0].reason == reason


def test_the_advance_blocked_prefix_matches_the_line_the_cli_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prefix is the mapping's only coupling to the CLI, so the CLI must produce it."""
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))
    research = _blocked_by_missing_approval(tmp_path)

    invocation = CliRunner().invoke(
        main, ["advance", research.meta.slug, "--offline", "--no-commit"], catch_exceptions=False
    )

    lines = [line for line in invocation.output.splitlines() if "blocked" in line]
    assert lines, invocation.output
    assert lines[0].startswith(ADVANCE_BLOCKED_PREFIX)
    reason = lines[0].removeprefix(ADVANCE_BLOCKED_PREFIX).strip()
    assert attribute_prose(reason).control is ControlType.HUMAN_APPROVAL


def test_the_approve_refusal_the_cli_raises_stays_reachable_from_the_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`approve` is the one prose reason that is a CLI refusal rather than lifecycle prose."""
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))
    research = _intake(tmp_path, mode="light")
    research.meta.stage = "transfer"
    research.save()

    invocation = CliRunner().invoke(
        main, ["approve", research.meta.slug, "--offline"], catch_exceptions=False
    )

    assert invocation.exit_code != 0
    errors = [line for line in invocation.output.splitlines() if line.startswith("Error: ")]
    assert errors, invocation.output
    reason = errors[0].removeprefix("Error: ").strip()
    mapping = attribute_prose(reason)
    assert mapping.control is None, "the refusal names no single control and must stay ambiguous"
    assert mapping.candidates == (ControlType.STRUCTURAL, ControlType.EVIDENTIAL), (
        f"the approve refusal is unreachable from the mapping table: {reason!r}"
    )
