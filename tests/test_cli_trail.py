import hashlib
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from sdr import cross_investigation, lifecycle
from sdr.cli import main
from sdr.research import Research
from sdr.verification import verify_explore_claims


@pytest.fixture
def run(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    monkeypatch.setenv("SDR_ROOT", str(tmp_path / "research"))
    monkeypatch.setenv("SDR_KNOWLEDGE", str(tmp_path / "knowledge"))
    runner = CliRunner()

    def _run(*args):
        return runner.invoke(main, list(args), catch_exceptions=False)

    _run.base = tmp_path
    return _run


def _log(repo):
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, capture_output=True, text=True, check=False
    )
    return out.stdout.strip().splitlines() if out.returncode == 0 else []


def _new(run, slug="eval-foo"):
    return run("new", slug, "--title", "Eval Foo", "--question", "¿Q?")


def _completed_expired_decision(base: Path, slug: str = "completed") -> tuple[Research, str]:
    research = Research.create(base=base, slug=slug, title="Done", question="q")
    source_url = "https://example.com/doc"
    note = research.artifact_path("notes/sources.md")
    note.write_text(
        "---\n"
        f"research: {slug}\n"
        "date: 2026-08-03\n"
        "stage: explore\n"
        "sources:\n"
        f"  - id: S1\n    url: {source_url}\n    tier: T3\n    date: 2025-01-01\n"
        "---\n\nSupport remains reviewable [S1].\n",
        encoding="utf-8",
    )
    content = "Support remains reviewable.\n"
    source_dir = research.artifact_path("notes/sources/S1")
    source_dir.mkdir(parents=True)
    source_dir.joinpath("content.md").write_text(content, encoding="utf-8")
    source_dir.joinpath("meta.yaml").write_text(
        "schema_version: 2\n"
        f"url: {source_url}\n"
        f"declared_url: {source_url}\n"
        f"final_url: {source_url}\n"
        "redirects: []\n"
        "http_status: 200\n"
        "captured_at: '2026-08-03T00:00:00+00:00'\n"
        "content_type: text/plain\n"
        "content_eligible: true\n"
        f"content_hash: {hashlib.sha256(content.encode()).hexdigest()}\n"
        "status: ok\n",
        encoding="utf-8",
    )
    report = verify_explore_claims(research)
    claim_id = report.items[0].claim_id
    research.artifact_path("decision-memo.md").write_text(
        "---\n"
        f"research: {slug}\n"
        "date: 2026-08-03\n"
        "stage: transfer\n"
        "ring: assess\n"
        "audience: team\n"
        f"evidence_claim_ids:\n  - {claim_id}\n"
        "---\n\n## Recomendación\n\nProceed.\n",
        encoding="utf-8",
    )
    research.meta.validation["transfer"] = lifecycle.stage_hash(research, "transfer")
    research.meta.status = "done"
    research.save()
    degradation = cross_investigation.report_degraded_support(
        base, include_expiry=True, as_of=date(2026, 8, 3)
    ).items[0]
    return research, degradation.observation_id


def _completed_expired_fanout_decision(base: Path) -> tuple[Research, str]:
    research, observation_id = _completed_expired_decision(base)
    note = research.artifact_path("notes/sources.md")
    note.write_text(
        note.read_text(encoding="utf-8") + "\nA second dependency remains reviewable [S1].\n",
        encoding="utf-8",
    )
    content = "Support remains reviewable.\nA second dependency remains reviewable.\n"
    source_dir = research.artifact_path("notes/sources/S1")
    source_dir.joinpath("content.md").write_text(content, encoding="utf-8")
    metadata = source_dir.joinpath("meta.yaml")
    lines = metadata.read_text(encoding="utf-8").splitlines()
    metadata.write_text(
        "\n".join(
            f"content_hash: {hashlib.sha256(content.encode()).hexdigest()}"
            if line.startswith("content_hash:")
            else line
            for line in lines
        )
        + "\n",
        encoding="utf-8",
    )
    claims = verify_explore_claims(research).items
    claim_ids = [item.claim_id for item in claims if item.state == "verified"]
    assert len(claim_ids) == 2
    research.artifact_path("decision-memo.md").write_text(
        "---\n"
        "research: completed\n"
        "date: 2026-08-03\n"
        "stage: transfer\n"
        "ring: assess\n"
        "audience: team\n"
        "evidence_claim_ids:\n"
        + "".join(f"  - {claim_id}\n" for claim_id in claim_ids)
        + "---\n\n## Recomendación\n\nProceed.\n",
        encoding="utf-8",
    )
    research.meta.validation["transfer"] = lifecycle.stage_hash(research, "transfer")
    research.save()
    return research, observation_id


def _commit_fixture(repo: Path) -> None:
    subprocess.run(["git", "add", "research"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)


def test_new_commits_creation(run):
    _new(run)
    assert _log(run.base)[0] == "research(eval-foo): new"


def test_new_no_commit_flag(run):
    result = run("new", "eval-foo", "--title", "t", "--question", "q", "--no-commit")
    assert result.exit_code == 0
    assert _log(run.base) == []
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=run.base,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == "?? research/\n"


def test_advance_commits_stage_transition(run):
    _new(run)
    result = run("advance", "eval-foo", "--offline")
    assert result.exit_code == 0
    assert _log(run.base)[0] == "research(eval-foo): intake -> explore"


def test_advance_does_not_commit_other_dirty_research_files(run):
    _new(run)
    brief = run.base / "research" / "eval-foo" / "brief.md"
    brief.write_text(brief.read_text(encoding="utf-8") + "\ncambio ajeno\n", encoding="utf-8")

    result = run("advance", "eval-foo", "--offline")

    assert result.exit_code == 0
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format="],
        cwd=run.base,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "research/eval-foo/sdr.yaml" in committed
    assert "research/eval-foo/brief.md" not in committed
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=run.base,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert " M research/eval-foo/brief.md" in status


def test_drop_commits_transition(run):
    _new(run)
    run("drop", "eval-foo", "--reason", "no aplica")
    assert _log(run.base)[0] == "research(eval-foo): drop"


def test_reopen_command_goes_back_with_reason(run):
    _new(run)
    run("advance", "eval-foo", "--offline")
    result = run("reopen", "eval-foo", "--to", "intake", "--reason", "criterios mal definidos")
    assert result.exit_code == 0
    assert _log(run.base)[0] == "research(eval-foo): reopen explore -> intake"
    status = run("status", "eval-foo")
    assert "intake" in status.output


def test_reopen_forward_fails(run):
    _new(run)
    result = run("reopen", "eval-foo", "--to", "probe", "--reason", "x")
    assert result.exit_code != 0


def test_archive_requires_done_or_dropped(run):
    _new(run)
    result = run("archive", "eval-foo")
    assert result.exit_code != 0


def test_archive_dropped_writes_knowledge_and_commits(run):
    _new(run)
    run("drop", "eval-foo", "--reason", "tecnologia inmadura")
    result = run("archive", "eval-foo")
    assert result.exit_code == 0
    knowledge = run.base / "knowledge" / "eval-foo.md"
    assert knowledge.exists()
    assert "tecnologia inmadura" in knowledge.read_text(encoding="utf-8")
    assert _log(run.base)[0] == "research(eval-foo): archive"


def test_acknowledge_degradation_commits_only_ledger_and_preserves_existing_index(run):
    research, observation_id = _completed_expired_decision(run.base / "research")
    _commit_fixture(run.base)
    staged = run.base / "staged.txt"
    staged.write_text("keep staged", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=run.base, check=True)

    result = run(
        "acknowledge-degradation",
        research.meta.slug,
        "S1",
        "--cause",
        "expired",
        "--observation-id",
        observation_id,
        "--include-expiry",
        "--as-of",
        "2026-08-03",
        "--reason",
        "Reviewed against replacement evidence",
        "--by",
        "Reviewer",
        "--json",
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["committed"] is True
    assert payload["source_id"] == "S1"
    assert _log(run.base)[0] == "research(completed): acknowledge degradation S1 expired"
    assert (
        "A  staged.txt"
        in subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=run.base,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format="],
        cwd=run.base,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert committed == ["research/completed/notes/sources/verification.yaml"]


def test_acknowledge_degradation_fanout_records_one_observation_and_suppresses_all_items(run):
    research, observation_id = _completed_expired_fanout_decision(run.base / "research")
    _commit_fixture(run.base)

    result = run(
        "acknowledge-degradation",
        research.meta.slug,
        "S1",
        "--cause",
        "expired",
        "--observation-id",
        observation_id,
        "--include-expiry",
        "--as-of",
        "2026-08-03",
        "--reason",
        "Reviewed both dependent claims",
        "--by",
        "Reviewer",
        "--json",
    )

    assert result.exit_code == 0
    ledger = research.artifact_path("notes/sources/verification.yaml").read_text(encoding="utf-8")
    assert ledger.count("acknowledgement_id:") == 1
    repeated = cross_investigation.report_degraded_support(
        run.base / "research", include_expiry=True, as_of=date(2026, 8, 3)
    )
    assert repeated.items == ()


def test_acknowledge_degradation_rejects_preexisting_target_staging_before_mutation(run):
    research, observation_id = _completed_expired_decision(run.base / "research")
    _commit_fixture(run.base)
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    ledger_path.write_text(ledger_path.read_text(encoding="utf-8") + "# staged owner data\n")
    subprocess.run(
        ["git", "add", "research/completed/notes/sources/verification.yaml"],
        cwd=run.base,
        check=True,
    )
    before_ledger = ledger_path.read_bytes()
    before_index = (run.base / ".git/index").read_bytes()

    result = run(
        "acknowledge-degradation",
        research.meta.slug,
        "S1",
        "--cause",
        "expired",
        "--observation-id",
        observation_id,
        "--include-expiry",
        "--as-of",
        "2026-08-03",
        "--reason",
        "Reviewed",
        "--by",
        "Reviewer",
    )

    assert result.exit_code == 1
    assert "already staged" in result.output
    assert "--no-commit" in result.output
    assert ledger_path.read_bytes() == before_ledger
    assert (run.base / ".git/index").read_bytes() == before_index
    assert _log(run.base) == ["fixture"]


def test_acknowledge_degradation_no_commit_writes_without_creating_commit(run):
    research, observation_id = _completed_expired_decision(run.base / "research")
    _commit_fixture(run.base)
    before = _log(run.base)

    result = run(
        "acknowledge-degradation",
        research.meta.slug,
        "S1",
        "--cause",
        "expired",
        "--observation-id",
        observation_id,
        "--include-expiry",
        "--as-of",
        "2026-08-03",
        "--reason",
        "Reviewed",
        "--by",
        "Reviewer",
        "--no-commit",
        "--json",
    )

    assert result.exit_code == 0
    assert json.loads(result.output)["committed"] is False
    assert _log(run.base) == before
    assert (
        " M research/completed/notes/sources/verification.yaml"
        in subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=run.base,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


def test_acknowledge_degradation_without_git_repo_warns_after_writing(tmp_path, monkeypatch):
    base = tmp_path / "research"
    research, observation_id = _completed_expired_decision(base)
    monkeypatch.setenv("SDR_ROOT", str(base))

    result = CliRunner().invoke(
        main,
        [
            "acknowledge-degradation",
            research.meta.slug,
            "S1",
            "--cause",
            "expired",
            "--observation-id",
            observation_id,
            "--include-expiry",
            "--as-of",
            "2026-08-03",
            "--reason",
            "Reviewed",
            "--by",
            "Reviewer",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "sin repositorio git" in result.output
    assert (
        research.artifact_path("notes/sources/verification.yaml")
        .read_text(encoding="utf-8")
        .count("acknowledgement_id:")
        == 1
    )


def test_acknowledge_degradation_requires_exact_current_observation(run):
    research, _ = _completed_expired_decision(run.base / "research")
    _commit_fixture(run.base)

    result = run(
        "acknowledge-degradation",
        research.meta.slug,
        "S1",
        "--cause",
        "expired",
        "--observation-id",
        "source-observation-" + "0" * 64,
        "--include-expiry",
        "--as-of",
        "2026-08-03",
        "--reason",
        "Reviewed",
        "--by",
        "Reviewer",
    )

    assert result.exit_code == 1
    assert "exact current degradation" in result.output
    assert "traceback" not in result.output.lower()
    assert _log(run.base) == ["fixture"]
