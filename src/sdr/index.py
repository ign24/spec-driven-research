"""Generation of the global investigation index (`research/INDEX.md`).

A reporting view: one table with every investigation ordered by last activity.
It is regenerated, never edited by hand.
"""

from __future__ import annotations

from pathlib import Path

from sdr.audit import audit_markers, evaluate_claims
from sdr.parser import parse_artifact
from sdr.paths import resolve_child, resolve_root, resolve_segment
from sdr.research import META_FILE, Research

INDEX_FILE = "INDEX.md"


def _iter_research(base: Path) -> list[Research]:
    items: list[Research] = []
    for entry in sorted(base.iterdir()):
        child = resolve_segment(base, entry.name)
        if child.is_dir() and resolve_child(child, META_FILE).exists():
            items.append(Research.load(child, within=base))
    return items


def _ring(research: Research) -> str:
    memo = research.artifact_path("decision-memo.md")
    if memo.exists():
        ring = parse_artifact(memo).frontmatter.get("ring")
        if ring:
            return str(ring)
    return "-"


def build_index(base: str | Path) -> str:
    """Build the index markdown for the investigations under `base`."""
    base = resolve_root(base)
    rows = sorted(_iter_research(base), key=lambda r: r.meta.updated, reverse=True)
    lines = [
        "# Investigation index",
        "",
        "| Investigation | Title | Mode | Stage | Status | "
        "Recommendation | Claims | Ledger | Audit | Activity |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        m = r.meta
        claim_audit = evaluate_claims(r)
        claims = ", ".join(f"{state}:{count}" for state, count in claim_audit.states.items())
        lines.append(
            f"| {m.slug} | {m.title} | {m.mode} | {m.stage} | {m.status} "
            f"| {_ring(r)} | "
            f"{claims or '-'} | {claim_audit.ledger} | "
            f"{', '.join(audit_markers(r)) or '-'} | {m.updated} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_index(base: str | Path) -> Path:
    """Regenerate `INDEX.md` under `base` and return its path."""
    base = resolve_root(base)
    path = resolve_child(base, INDEX_FILE)
    path.write_text(build_index(base), encoding="utf-8")
    return path
