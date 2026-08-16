"""Model of an investigation and the state it persists in `sdr.yaml`."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from sdr import schema
from sdr.paths import (
    ensure_tree_within,
    ensure_within,
    resolve_child,
    resolve_root,
    resolve_segment,
)

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

META_FILE = "sdr.yaml"
_STAGE_DIRS = ("notes", "probe", "assets")


def is_valid_slug(slug: str) -> bool:
    """True if `slug` is valid kebab-case."""
    return bool(_SLUG_RE.match(slug))


class Approval(BaseModel):
    """Human approval of the decision memo (transfer stage)."""

    model_config = ConfigDict(extra="allow")

    by: str
    date: str


class Reopen(BaseModel):
    """Record of a stage rollback (explicit backtracking)."""

    model_config = ConfigDict(extra="allow")

    from_stage: str
    to_stage: str
    reason: str
    date: str


class ResearchMeta(BaseModel):
    """Metadata of an investigation (the contents of `sdr.yaml`)."""

    model_config = ConfigDict(extra="allow")

    slug: str
    title: str
    question: str
    mode: str = "full"
    stage: str = "intake"
    status: str = "active"  # active | done | dropped
    owner: str = ""
    timebox: int = 0  # estimate in days
    created: str = Field(default_factory=lambda: date.today().isoformat())
    updated: str = Field(default_factory=lambda: date.today().isoformat())
    tags: list[str] = Field(default_factory=list)
    schema_version: int = 1
    dropped_reason: str = ""
    validation: dict[str, str] = Field(default_factory=dict)  # stage -> gate hash
    judge: dict[str, Any] = Field(default_factory=dict)
    verify_probe: dict[str, Any] = Field(default_factory=dict)
    overrides: list[dict[str, Any]] = Field(default_factory=list)
    approval: Approval | None = None
    reopens: list[Reopen] = Field(default_factory=list)
    archived: bool = False

    def following_stage(self) -> str | None:
        """Stage after the current one for this mode, or None if it is the last."""
        return schema.next_stage(self.stage, self.mode)


class Research:
    """Investigation on disk: root plus metadata."""

    def __init__(self, root: Path, meta: ResearchMeta) -> None:
        self.root = resolve_root(root)
        self.meta = meta

    @classmethod
    def create(
        cls,
        base: str | Path,
        slug: str,
        title: str,
        question: str,
        mode: str = "full",
        owner: str = "",
        timebox: int = 0,
        tags: list[str] | None = None,
    ) -> Research:
        """Create the `research/<slug>/` structure with an initialized `sdr.yaml`."""
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug (must be kebab-case): {slug!r}")
        if mode not in schema.MODES:
            raise ValueError(f"unknown mode: {mode!r}")
        base = resolve_root(base)
        root = resolve_segment(base, slug)
        if root.exists():
            raise FileExistsError(f"investigation {slug!r} already exists")
        root.mkdir(parents=True)
        for sub in _STAGE_DIRS:
            (root / sub).mkdir()
        meta = ResearchMeta(
            slug=slug,
            title=title,
            question=question,
            mode=mode,
            owner=owner,
            timebox=timebox,
            tags=list(tags or []),
            schema_version=2,
        )
        research = cls(root, meta)
        research.save()
        return research

    @classmethod
    def load(cls, root: str | Path, *, within: str | Path | None = None) -> Research:
        """Load an investigation from its directory."""
        root = resolve_root(root)
        if within is not None:
            root = ensure_within(within, root)
        meta_path = resolve_child(root, META_FILE)
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        meta = ResearchMeta.model_validate(raw)
        if not is_valid_slug(meta.slug):
            raise ValueError(f"invalid slug in {META_FILE}: {meta.slug!r}")
        return cls(root, meta)

    def save(self) -> None:
        """Persist the metadata to `sdr.yaml`."""
        self.meta.updated = date.today().isoformat()
        data = self.meta.model_dump(mode="json", exclude_none=True)
        self.artifact_path(META_FILE).write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def advance_stage(self) -> None:
        """Advance to the next stage or mark the investigation as done.

        Runs no gates; it only mutates state. Gate verification happens in the
        CLI layer before this method is called.
        """
        nxt = self.meta.following_stage()
        if nxt is None:
            self.meta.status = "done"
        else:
            self.meta.stage = nxt
        self.save()

    def artifact_path(self, relative: str) -> Path:
        """Absolute path to an artifact relative to the investigation root."""
        path = resolve_child(self.root, relative)
        if path.is_dir():
            ensure_tree_within(self.root, path)
        return path

    def to_dict(self) -> dict[str, Any]:
        return self.meta.model_dump(mode="json", exclude_none=True)
