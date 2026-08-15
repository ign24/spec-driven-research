"""Git trail of the investigation: one structured commit per transition.

SDR is to research what SDD is to vibecoding: the git history IS the
traceability. Every state transition (new, stage advance, reopen, drop,
archive) is recorded as `research(<slug>): <transition>` over the files of the
investigation. Without a git repository the trail degrades with a warning; it
never blocks the work.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from sdr.research import Research


@dataclass
class TrailResult:
    committed: bool
    message: str = ""
    warning: str = ""


class TargetPathStagedError(ValueError):
    """A focused commit target already has operator-owned staged content."""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _repo_root(path: Path) -> Path | None:
    result = _git(["rev-parse", "--show-toplevel"], cwd=path)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _warning(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    return result.stderr.strip() or result.stdout.strip() or fallback


def _relative_paths(paths: list[Path], within: Path, label: str) -> tuple[list[str], str]:
    relative: list[str] = []
    for path in paths:
        try:
            relative.append(str(path.resolve().relative_to(within)))
        except ValueError:
            return [], f"{label} outside the repository: {path}"
    return relative, ""


def _index_path(root: Path) -> Path | None:
    result = _git(["rev-parse", "--git-path", "index"], cwd=root)
    if result.returncode != 0:
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else root / path


def _restore_index(path: Path, contents: bytes | None) -> str:
    try:
        if contents is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(contents)
    except OSError as exc:
        return f"could not restore the previous staging: {exc}"
    return ""


def require_unstaged_paths(research: Research, paths: list[Path]) -> None:
    """Refuse a default commit before mutating a target with staged content."""
    root = _repo_root(research.root)
    if root is None:
        return
    relative, warning = _relative_paths(paths, root, "target path")
    if warning:
        raise TargetPathStagedError(warning)
    staged = _git(["diff", "--cached", "--quiet", "--", *relative], cwd=root)
    if staged.returncode == 1:
        raise TargetPathStagedError(
            "target ledger is already staged; use --no-commit or resolve staging before retrying"
        )
    if staged.returncode != 0:
        raise TargetPathStagedError("could not inspect target ledger staging safely")


def commit_transition(
    research: Research,
    transition: str,
    paths: list[Path] | None = None,
    extra_paths: list[Path] | None = None,
) -> TrailResult:
    """Commit the files of the investigation with a structured message.

    `transition` describes the state change: "new", "intake -> explore",
    "reopen probe -> explore", "drop", "archive".
    """
    message = f"research({research.meta.slug}): {transition}"
    root = _repo_root(research.root)
    if root is None:
        return TrailResult(
            committed=False,
            message=message,
            warning="no git repository: the transition was not recorded in the trail",
        )

    research_root = research.root.resolve()
    try:
        research_root.relative_to(root)
    except ValueError:
        return TrailResult(
            committed=False,
            message=message,
            warning=f"the investigation is outside the detected repository: {research.root}",
        )

    selected = paths if paths is not None else [research.root]
    research_paths, warning = _relative_paths(selected, research_root, "investigation path")
    if warning:
        return TrailResult(committed=False, message=message, warning=warning)
    research_prefix = str(research_root.relative_to(root))
    rel_paths = [str(Path(research_prefix) / path) for path in research_paths]
    extras, warning = _relative_paths(extra_paths or [], root, "extra path")
    if warning:
        return TrailResult(committed=False, message=message, warning=warning)
    rel_paths = list(dict.fromkeys([*rel_paths, *extras]))
    if not rel_paths:
        return TrailResult(committed=False, message=message, warning="no paths to record")

    index_path = _index_path(root)
    if index_path is None:
        return TrailResult(
            committed=False,
            message=message,
            warning="could not locate the git index",
        )
    try:
        previous_index = index_path.read_bytes() if index_path.exists() else None
    except OSError as exc:
        return TrailResult(
            committed=False,
            message=message,
            warning=f"could not preserve the previous staging: {exc}",
        )

    add = _git(["add", "--", *rel_paths], cwd=root)
    if add.returncode != 0:
        restore_warning = _restore_index(index_path, previous_index)
        warning = _warning(add, "git add failed")
        if restore_warning:
            warning = f"{warning}; {restore_warning}"
        return TrailResult(committed=False, message=message, warning=warning)

    staged = _git(["diff", "--cached", "--quiet", "--", *rel_paths], cwd=root)
    if staged.returncode == 0:
        restore_warning = _restore_index(index_path, previous_index)
        if restore_warning:
            return TrailResult(committed=False, message=message, warning=restore_warning)
        return TrailResult(committed=False, message=message, warning="no changes to record")
    if staged.returncode != 1:
        restore_warning = _restore_index(index_path, previous_index)
        warning = _warning(staged, "could not inspect the staging")
        if restore_warning:
            warning = f"{warning}; {restore_warning}"
        return TrailResult(committed=False, message=message, warning=warning)

    commit = _git(["commit", "-m", message, "--", *rel_paths], cwd=root)
    if commit.returncode != 0:
        restore_warning = _restore_index(index_path, previous_index)
        warning = _warning(commit, "git commit failed")
        if restore_warning:
            warning = f"{warning}; {restore_warning}"
        return TrailResult(committed=False, message=message, warning=warning)
    return TrailResult(committed=True, message=message)
