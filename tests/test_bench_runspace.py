"""Isolation guarantees of the benchmark harness run roots."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

from bench.harness.runspace import (
    REPOSITORY_ROOT,
    Runspace,
    map_isolated,
    run_isolated,
    runspace,
)

NEW_ARGS = (
    "new",
    "bench-item",
    "--title",
    "Bench item",
    "--question",
    "¿Qué mide el harness?",
    "--owner",
    "bench",
    "--timebox",
    "1",
)


def _cli_env(space: Runspace) -> dict[str, str]:
    env = space.env()
    env["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    return env


def _run_cli(space: Runspace, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "from sdr.cli import main; main()", *args],
        cwd=space.path,
        env=_cli_env(space),
        capture_output=True,
        text=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def test_repository_root_points_at_this_checkout() -> None:
    assert (REPOSITORY_ROOT / "pyproject.toml").is_file()
    assert (REPOSITORY_ROOT / "src" / "sdr").is_dir()


def test_run_root_is_materialized_outside_the_repository_tree() -> None:
    with runspace() as space:
        assert space.root.is_dir()
        assert space.knowledge.is_dir()
        assert not space.path.is_relative_to(REPOSITORY_ROOT)
        assert not space.root.is_relative_to(REPOSITORY_ROOT)
        assert not space.knowledge.is_relative_to(REPOSITORY_ROOT)
        assert space.env()["SDR_ROOT"] == str(space.root)
        assert space.env()["SDR_KNOWLEDGE"] == str(space.knowledge)


def test_run_root_is_a_git_repository_of_its_own() -> None:
    with runspace() as space:
        toplevel = _git(space.root, "rev-parse", "--show-toplevel")
        assert toplevel.returncode == 0
        assert Path(toplevel.stdout.strip()).resolve() == space.path
        assert (space.path / ".git").is_dir()


def test_run_root_is_removed_after_the_run() -> None:
    with runspace() as space:
        path = space.path
        (space.root / "leftover.txt").write_text("x", encoding="utf-8")
    assert not path.exists()


def test_run_root_is_removed_when_the_run_raises() -> None:
    captured: list[Path] = []

    def failing(space: Runspace) -> None:
        captured.append(space.path)
        raise RuntimeError("run failed")

    with pytest.raises(RuntimeError):
        run_isolated(failing)

    assert captured and not captured[0].exists()


def test_repository_tree_gains_no_research_or_knowledge_directory() -> None:
    with runspace() as space:
        result = _run_cli(space, *NEW_ARGS)
        assert result.returncode == 0, result.stderr
        assert space.meta_path("bench-item").is_file()
        log = _git(space.path, "log", "--oneline")
        assert log.returncode == 0
        assert "research(bench-item): new" in log.stdout

    assert not (REPOSITORY_ROOT / "research").exists()
    assert not (REPOSITORY_ROOT / "knowledge").exists()
    for name in ("research", "knowledge"):
        assert not list(REPOSITORY_ROOT.glob(f"*/{name}/"))


def test_parallel_runs_never_share_a_research_root_or_metadata_file() -> None:
    lock = threading.Lock()
    observed: list[tuple[Path, Path]] = []

    def work(space: Runspace) -> tuple[Path, Path]:
        slug = "bench-item"
        (space.root / slug).mkdir(parents=True)
        meta = space.meta_path(slug)
        meta.write_text("slug: bench-item\n", encoding="utf-8")
        pair = (space.root, meta)
        with lock:
            observed.append(pair)
        return pair

    results = map_isolated([work] * 6, max_workers=3)

    assert len(results) == 6
    assert sorted(observed) == sorted(results)
    roots = [root for root, _ in results]
    metas = [meta for _, meta in results]
    assert len({str(root) for root in roots}) == len(roots)
    assert len({str(meta) for meta in metas}) == len(metas)
    for index, root in enumerate(roots):
        for other_index, other in enumerate(roots):
            if index != other_index:
                assert not root.is_relative_to(other)


def test_parallel_runs_are_bounded_by_the_worker_limit() -> None:
    lock = threading.Lock()
    live = 0
    peak = 0

    def work(space: Runspace) -> int:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            assert space.root.is_dir()
            return live
        finally:
            with lock:
                live -= 1

    map_isolated([work] * 8, max_workers=2)

    assert peak <= 2


def test_map_isolated_preserves_input_order_and_rejects_bad_limits() -> None:
    def make(index: int):
        def work(space: Runspace) -> int:
            return index

        return work

    assert map_isolated([make(index) for index in range(5)], max_workers=3) == [0, 1, 2, 3, 4]
    assert map_isolated([], max_workers=2) == []
    with pytest.raises(ValueError):
        map_isolated([make(0)], max_workers=0)
