"""Archive: consolidates a closed investigation into the knowledge base.

The archive is the equivalent of the OpenSpec archive: a finished investigation
stops being a loose directory and becomes a queryable asset under
`knowledge/<slug>.md`, carrying the synthesis (question, recommendation, ring,
results) and the link to the full evidence.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sdr.audit import evaluate_claims
from sdr.parser import parse_artifact
from sdr.paths import resolve_child, resolve_root
from sdr.research import Research
from sdr.schema import (
    SECTION_CRITERIA_RESULTS,
    SECTION_QUESTION,
    SECTION_RECOMMENDATION,
    SECTION_RISKS_AND_LIMITS,
)
from sdr.verification_ledger import load_ledger


def archive_research(research: Research, knowledge_dir: str | Path) -> Path:
    """Write the synthesis to `knowledge/<slug>.md` and mark the investigation."""
    if research.meta.status not in ("done", "dropped"):
        raise ValueError(
            f"only done or dropped investigations can be archived; "
            f"{research.meta.slug!r} is {research.meta.status!r}"
        )
    claim_audit = evaluate_claims(research)
    if claim_audit.has_claims and not claim_audit.passed:
        persisted = research.artifact_path("notes/sources/verification.yaml")
        detail = (
            "stale"
            if claim_audit.ledger != "current"
            and persisted.exists()
            and load_ledger(persisted)["claims"]
            else "pending"
        )
        raise ValueError(f"claim ledger {detail}; run sdr verify-claims before archiving")
    knowledge_dir = resolve_root(knowledge_dir)
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    path = resolve_child(knowledge_dir, f"{research.meta.slug}.md")
    path.write_text(_synthesis(research), encoding="utf-8")
    research.meta.archived = True
    research.save()
    return path


def _section_from(research: Research, relative: str, section: str) -> str:
    artifact_path = research.artifact_path(relative)
    if not artifact_path.exists():
        return ""
    return parse_artifact(artifact_path).section(section) or ""


def _ring(research: Research) -> str:
    memo = research.artifact_path("decision-memo.md")
    if memo.exists():
        return str(parse_artifact(memo).frontmatter.get("ring", "-"))
    return "-"


def _synthesis(research: Research) -> str:
    meta = research.meta
    lines = [
        "---",
        f"research: {meta.slug}",
        f"question: {meta.question}",
        f"status: {meta.status}",
        f"ring: {_ring(research)}",
        f"archived: {date.today().isoformat()}",
        "---",
        "",
        f"# {meta.title}",
        "",
        f"**{SECTION_QUESTION}:** {meta.question}",
        "",
    ]
    if meta.status == "dropped":
        lines += ["## Drop reason", "", meta.dropped_reason or "(no reason)", ""]
    recommendation = _section_from(research, "decision-memo.md", SECTION_RECOMMENDATION)
    if recommendation:
        lines += [f"## {SECTION_RECOMMENDATION}", "", recommendation, ""]
    results = _section_from(research, "probe/results.md", SECTION_CRITERIA_RESULTS)
    if results:
        lines += [f"## {SECTION_CRITERIA_RESULTS}", "", results, ""]
    risks = _section_from(research, "decision-memo.md", SECTION_RISKS_AND_LIMITS)
    if risks:
        lines += [f"## {SECTION_RISKS_AND_LIMITS}", "", risks, ""]
    if meta.reopens:
        lines += ["## Reopens", ""]
        lines += [f"- {r.date}: {r.from_stage} -> {r.to_stage} ({r.reason})" for r in meta.reopens]
        lines.append("")
    claim_states = evaluate_claims(research).states
    if claim_states:
        lines += ["## Claim states", ""]
        lines += [f"- {state}: {count}" for state, count in claim_states.items()]
        lines.append("")
    lines += [
        "## Full evidence",
        "",
        f"See `research/{meta.slug}/` (brief, notes, probe, decision memo and assets).",
        "",
    ]
    return "\n".join(lines)
