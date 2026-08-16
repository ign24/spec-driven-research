import subprocess
from pathlib import Path

import pytest

from sdr import trail
from sdr.research import Research


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    return tmp_path


def _log(repo):
    out = subprocess.run(
        ["git", "log", "--format=%s"], cwd=repo, check=False, capture_output=True, text=True
    )
    return out.stdout.strip().splitlines() if out.returncode == 0 else []


def _status(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_commit_transition_creates_structured_commit(git_repo):
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    result = trail.commit_transition(r, "new")
    assert result.committed
    assert _log(git_repo)[0] == "research(eval-foo): new"


def test_commit_transition_stage_change_message(git_repo):
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    trail.commit_transition(r, "new")
    r.artifact_path("brief.md").write_text("brief", encoding="utf-8")
    trail.commit_transition(r, "intake -> explore")
    assert _log(git_repo)[0] == "research(eval-foo): intake -> explore"


def test_commit_outside_git_repo_degrades_gracefully(tmp_path):
    r = Research.create(base=tmp_path / "research", slug="eval-foo", title="t", question="q")
    result = trail.commit_transition(r, "new")
    assert not result.committed
    assert result.warning


def test_commit_only_touches_research_paths(git_repo):
    (git_repo / "unrelated.txt").write_text("fuera del rastro", encoding="utf-8")
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    trail.commit_transition(r, "new")
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=git_repo, check=True, capture_output=True, text=True
    ).stdout
    assert "unrelated.txt" in status  # sigue sin commitear


def test_commit_extra_paths_are_included(git_repo):
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    knowledge = git_repo / "knowledge" / "eval-foo.md"
    knowledge.parent.mkdir()
    knowledge.write_text("sintesis", encoding="utf-8")
    trail.commit_transition(r, "archive", extra_paths=[knowledge])
    files = subprocess.run(
        ["git", "show", "--name-only", "--format="],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "knowledge/eval-foo.md" in files


def test_no_changes_is_not_an_error(git_repo):
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    trail.commit_transition(r, "new")
    result = trail.commit_transition(r, "new")  # nada nuevo que commitear
    assert not result.committed
    assert result.warning


def test_commit_only_stages_explicit_transition_paths(git_repo):
    base = git_repo / "research"
    r = Research.create(base=base, slug="eval-foo", title="t", question="q")
    brief = r.artifact_path("brief.md")
    brief.write_text("cambio ajeno", encoding="utf-8")

    result = trail.commit_transition(r, "new", paths=[r.artifact_path("sdr.yaml")])

    assert result.committed
    assert "?? research/eval-foo/brief.md" in _status(git_repo)
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format="],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "research/eval-foo/sdr.yaml" in committed
    assert "research/eval-foo/brief.md" not in committed


def test_commit_uses_repository_containing_research(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
    inner = outer / "workspace"
    inner.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=inner, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=inner, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=inner, check=True)
    r = Research.create(base=inner / "research", slug="eval-foo", title="t", question="q")

    result = trail.commit_transition(r, "new")

    assert result.committed
    assert _log(inner) == ["research(eval-foo): new"]
    assert _status(outer) == "?? workspace/\n"


def test_commit_preserves_unrelated_staged_and_unstaged_changes(git_repo):
    staged = git_repo / "staged.txt"
    staged.write_text("staged", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=git_repo, check=True)
    unstaged = git_repo / "unstaged.txt"
    unstaged.write_text("unstaged", encoding="utf-8")
    r = Research.create(base=git_repo / "research", slug="eval-foo", title="t", question="q")

    result = trail.commit_transition(r, "new")

    assert result.committed
    assert "A  staged.txt" in _status(git_repo)
    assert "?? unstaged.txt" in _status(git_repo)
    committed = subprocess.run(
        ["git", "show", "--name-only", "--format="],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "staged.txt" not in committed
    assert "unstaged.txt" not in committed


def test_failed_commit_restores_exact_prior_staging(git_repo):
    staged = git_repo / "staged.txt"
    staged.write_text("staged", encoding="utf-8")
    subprocess.run(["git", "add", "staged.txt"], cwd=git_repo, check=True)
    r = Research.create(base=git_repo / "research", slug="eval-foo", title="t", question="q")
    hook = git_repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho hook rechazado >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)

    result = trail.commit_transition(r, "new")

    assert not result.committed
    assert "hook rechazado" in result.warning
    assert _status(git_repo) == "A  staged.txt\n?? research/\n"
    assert _log(git_repo) == []


def test_extra_path_outside_containing_repo_is_rejected_without_staging(git_repo, tmp_path):
    r = Research.create(base=git_repo / "research", slug="eval-foo", title="t", question="q")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    outside.write_text("fuera", encoding="utf-8")

    result = trail.commit_transition(r, "archive", extra_paths=[outside])

    assert not result.committed
    assert "outside the repository" in result.warning
    assert _status(git_repo) == "?? research/\n"
