"""Centralized resolution of paths confined to an explicit root."""

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


def resolve_root(root: str | Path) -> Path:
    """Normalize a configurable root, absolute or relative."""
    return Path(root).expanduser().resolve(strict=False)


def resolve_child(root: str | Path, relative: str | Path) -> Path:
    """Resolve a relative path and require it to stay inside ``root``."""
    root_path = resolve_root(root)
    raw = os.fspath(relative)
    relative_path = Path(raw)
    if (
        relative_path.is_absolute()
        or PureWindowsPath(raw).is_absolute()
        or "\\" in raw
        or ".." in relative_path.parts
    ):
        raise ValueError(f"invalid relative path: {raw!r}")

    return ensure_within(root_path, root_path / relative_path)


def ensure_within(root: str | Path, path: str | Path) -> Path:
    """Normalize ``path`` and require it to stay confined to ``root``."""
    root_path = resolve_root(root)
    candidate = Path(path).resolve(strict=False)
    if not candidate.is_relative_to(root_path):
        raise ValueError(f"path outside the allowed root {root_path}: {path!r}")
    return candidate


def ensure_tree_within(root: str | Path, directory: str | Path) -> Path:
    """Validate the symlinks of a tree without following linked directories."""
    root_path = resolve_root(root)
    directory_path = ensure_within(root_path, directory)
    for current, directories, files in directory_path.walk(follow_symlinks=False):
        for name in (*directories, *files):
            ensure_within(root_path, current / name)
    return directory_path


def resolve_segment(root: str | Path, segment: str) -> Path:
    """Resolve a single name, rejecting any path separator."""
    if not segment or "/" in segment or "\\" in segment:
        raise ValueError(f"invalid segment: {segment!r}")
    return resolve_child(root, segment)
