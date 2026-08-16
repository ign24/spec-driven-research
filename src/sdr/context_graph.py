import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sdr import parser
from sdr.paths import resolve_child
from sdr.research import Research
from sdr.schema import (
    SECTION_ADOPTION_RISKS,
    SECTION_CRITERIA_RESULTS,
    SECTION_EVALUATION_CRITERIA,
    SECTION_RECOMMENDATION,
    SECTION_SELECTION_CRITERIA,
)

ALLOWED_PROVENANCE = {"explicit", "inferred", "provider", "judge", "hitl"}
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_CRITERION_RE = re.compile(r"^\s*[-*]\s*(C\d+)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_RESULT_RE = re.compile(
    r"^\s*[-*]\s*(C\d+)\s*:\s*(cumple|no cumple|parcial)\s*-\s*(.+?)\s*$",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


class ContextGraphError(ValueError):
    """Raised when a SpecLab Context Graph is invalid."""


def canonical_node_id(node_type: str, raw_id: str) -> str:
    """Return a stable graph node ID from a node type and raw identifier."""
    safe_type = _sanitize_id_part(node_type)
    safe_raw_id = _sanitize_id_part(raw_id)
    if not safe_type or not safe_raw_id:
        raise ContextGraphError("node type and raw id must be non-empty")
    return f"{safe_type}:{safe_raw_id}"


def _sanitize_id_part(value: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value.strip())
    return normalized.strip("-")


@dataclass(frozen=True)
class GraphNode:
    """A node in the derived SpecLab Context Graph."""

    id: str
    type: str
    title: str
    source_files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the node to a deterministic dictionary."""
        return {
            "id": self.id,
            "metadata": _stable_value(self.metadata),
            "source_files": sorted(self.source_files),
            "title": self.title,
            "type": self.type,
        }


@dataclass(frozen=True)
class GraphEdge:
    """A provenance-aware edge in the derived SpecLab Context Graph."""

    source: str
    target: str
    relation: str
    provenance: str
    source_file: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the edge to a deterministic dictionary."""
        return {
            "metadata": _stable_value(self.metadata),
            "provenance": self.provenance,
            "relation": self.relation,
            "source": self.source,
            "source_file": self.source_file,
            "target": self.target,
        }


@dataclass(frozen=True)
class ContextGraph:
    """A derived, deterministic SpecLab Context Graph."""

    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate node identity, edge references, and provenance values."""
        node_ids: set[str] = set()
        for node in self.nodes:
            if not node.id:
                raise ContextGraphError("node id must be non-empty")
            if not node.type:
                raise ContextGraphError(f"node {node.id!r} type must be non-empty")
            if node.id in node_ids:
                raise ContextGraphError(f"duplicate node id: {node.id}")
            node_ids.add(node.id)

        for edge in self.edges:
            if edge.source not in node_ids:
                raise ContextGraphError(f"unknown source node: {edge.source}")
            if edge.target not in node_ids:
                raise ContextGraphError(f"unknown target node: {edge.target}")
            if edge.provenance not in ALLOWED_PROVENANCE:
                raise ContextGraphError(f"invalid provenance: {edge.provenance}")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph to a deterministic dictionary."""
        self.validate()
        from sdr.public_tree_audit import redact_sensitive_values

        payload = redact_sensitive_values(
            {
                "edges": [edge.to_dict() for edge in _sorted_edges(self.edges)],
                "metadata": _stable_value(self.metadata),
                "nodes": [node.to_dict() for node in sorted(self.nodes, key=lambda node: node.id)],
            }
        )
        payload["nodes"] = sorted(payload["nodes"], key=lambda node: node["id"])
        payload["edges"] = sorted(
            payload["edges"],
            key=lambda edge: (
                edge["source"],
                edge["relation"],
                edge["target"],
                edge["provenance"],
            ),
        )
        return payload

    def to_json(self) -> str:
        """Serialize the graph to stable, pretty-printed JSON."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ContextGraph":
        """Load a context graph from its JSON-compatible dictionary."""
        graph = cls(
            nodes=[
                GraphNode(
                    id=node["id"],
                    type=node["type"],
                    title=node["title"],
                    source_files=tuple(node.get("source_files", ())),
                    metadata=dict(node.get("metadata", {})),
                )
                for node in payload.get("nodes", [])
            ],
            edges=[
                GraphEdge(
                    source=edge["source"],
                    target=edge["target"],
                    relation=edge["relation"],
                    provenance=edge["provenance"],
                    source_file=edge.get("source_file"),
                    metadata=dict(edge.get("metadata", {})),
                )
                for edge in payload.get("edges", [])
            ],
            metadata=dict(payload.get("metadata", {})),
        )
        graph.validate()
        return graph


def write_context_graph(graph: ContextGraph, research_path: Path) -> Path:
    """Write `context/context.json` for a research directory."""
    context_dir = resolve_child(research_path, "context")
    context_dir.mkdir(parents=True, exist_ok=True)
    output_path = resolve_child(research_path, "context/context.json")
    output_path.write_text(graph.to_json(), encoding="utf-8")
    return output_path


def read_context_graph(research_path: Path) -> ContextGraph:
    """Read `context/context.json` for a research directory."""
    path = resolve_child(research_path, "context/context.json")
    if not path.exists():
        raise ContextGraphError(f"context graph not found: {path}")
    return ContextGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))


def inspect_context_graph(graph: ContextGraph) -> dict[str, Any]:
    """Return auxiliary coverage metrics and deterministic relationship warnings."""
    criteria = [node for node in graph.nodes if node.type == "criterion"]
    results = [node for node in graph.nodes if node.type == "result"]
    decisions = [node for node in graph.nodes if node.type == "decision"]
    evaluated_criteria = {
        edge.target
        for edge in graph.edges
        if edge.relation == "evaluates" and edge.source.startswith("result:")
    }
    warnings = [
        f"criterion without result: {node.metadata.get('label', node.id)}"
        for node in sorted(criteria, key=lambda item: item.id)
        if node.id not in evaluated_criteria
    ]
    based_on_sources = {edge.source for edge in graph.edges if edge.relation == "based_on"}
    used_results = {edge.target for edge in graph.edges if edge.relation == "based_on"}
    warnings.extend(
        f"decision without based_on: {node.id}"
        for node in sorted(decisions, key=lambda item: item.id)
        if node.id not in based_on_sources
    )
    warnings.extend(
        f"result not used by decision: {node.id}"
        for node in sorted(results, key=lambda item: item.id)
        if node.id not in used_results
    )
    if graph.metadata.get("decision_memo_present") and not decisions:
        warnings.append("decision memo without decision")
    return {
        "coverage": {
            "criteria": len(criteria),
            "criteria_with_results": len(evaluated_criteria),
            "decisions": len(decisions),
            "sources": sum(1 for node in graph.nodes if node.type == "source"),
        },
        "edges": len(graph.edges),
        "metadata": graph.metadata,
        "nodes": len(graph.nodes),
        "warnings": warnings,
    }


def trace_context_graph(graph: ContextGraph, node_id: str) -> dict[str, Any]:
    """Return incoming and outgoing relationships for a graph node."""
    nodes_by_id = {node.id: node for node in graph.nodes}
    if node_id not in nodes_by_id:
        raise ContextGraphError(f"node not found: {node_id}")
    incoming = [edge.to_dict() for edge in _sorted_edges(graph.edges) if edge.target == node_id]
    outgoing = [edge.to_dict() for edge in _sorted_edges(graph.edges) if edge.source == node_id]
    return {
        "incoming": incoming,
        "outgoing": outgoing,
        "target": nodes_by_id[node_id].to_dict(),
    }


def inspect_codegraph_provider(project_root: Path, executable: str = "codegraph") -> dict[str, Any]:
    """Return non-failing metadata for an optional CodeGraph installation/index."""
    project_root = Path(project_root)
    index_path = project_root / ".codegraph"
    executable_path = shutil.which(executable)
    warnings: list[str] = []

    if not index_path.exists():
        warnings.append("missing .codegraph index")
    if executable_path is None:
        warnings.append("missing codegraph executable")

    version = ""
    if executable_path is not None:
        version = _read_codegraph_version(executable_path)

    return {
        "available": not warnings,
        "executable": executable_path or executable,
        "index_path": str(index_path),
        "provider": "codegraph",
        "version": version,
        "warnings": warnings,
    }


def validate_paths_within_root(root: Path, paths: tuple[Path | str, ...]) -> list[str]:
    """Return warnings for absolute paths outside the allowed root."""
    root = Path(root).resolve(strict=False)
    warnings: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            continue
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(root):
            warnings.append(f"out-of-scope path: {path}")
    return warnings


def redact_secret_like_values(text: str) -> str:
    """Redact common secret-like key assignments in generated summaries."""
    from sdr.public_tree_audit import RedactionContext, redact_sensitive

    context = RedactionContext()
    return "".join(
        redact_sensitive(line.rstrip("\r\n"), context=context) + line[len(line.rstrip("\r\n")) :]
        for line in text.splitlines(keepends=True)
    )


def build_sdr_context_graph(
    research: Research,
    openspec_change_path: Path | None = None,
    static_code_paths: tuple[str, ...] = (),
    static_test_paths: tuple[str, ...] = (),
) -> ContextGraph:
    """Build an auxiliary graph of criteria, results, decision, and source inventory."""
    research_id = canonical_node_id("research", research.meta.slug)
    question_id = canonical_node_id("question", research.meta.slug)
    nodes = [
        GraphNode(
            id=research_id,
            type="research",
            title=research.meta.title,
            source_files=("sdr.yaml",),
            metadata={
                "owner": research.meta.owner,
                "status": research.meta.status,
                "tags": research.meta.tags,
                "timebox": research.meta.timebox,
            },
        ),
        GraphNode(
            id=question_id,
            type="question",
            title=research.meta.question,
            source_files=("sdr.yaml",),
        ),
    ]
    edges = [
        GraphEdge(
            source=research_id,
            target=question_id,
            relation="has_question",
            provenance="explicit",
            source_file="sdr.yaml",
        )
    ]

    brief_path = research.artifact_path("brief.md")
    if brief_path.exists():
        brief = parser.parse_artifact(brief_path)
        for criterion_id, text in _extract_criteria(brief.section(SECTION_EVALUATION_CRITERIA)):
            node_id = canonical_node_id("criterion", criterion_id)
            nodes.append(
                GraphNode(
                    id=node_id,
                    type="criterion",
                    title=f"{criterion_id}: {text}",
                    source_files=("brief.md",),
                    metadata={"label": criterion_id, "text": text},
                )
            )
            edges.append(
                GraphEdge(
                    source=research_id,
                    target=node_id,
                    relation="defines",
                    provenance="explicit",
                    source_file="brief.md",
                )
            )

        risk_bullets = _extract_bullets(brief.section(SECTION_ADOPTION_RISKS))
        for index, text in enumerate(risk_bullets, start=1):
            node_id = canonical_node_id("risk", f"R{index}")
            nodes.append(
                GraphNode(
                    id=node_id,
                    type="risk",
                    title=text,
                    source_files=("brief.md",),
                    metadata={"label": f"R{index}", "text": text},
                )
            )
            edges.append(
                GraphEdge(
                    source=research_id,
                    target=node_id,
                    relation="has_risk",
                    provenance="explicit",
                    source_file="brief.md",
                )
            )

    _add_note_sources(research, research_id, nodes, edges)
    result_ids = _add_probe_results(research, nodes, edges)
    _add_decision(research, result_ids, nodes, edges)
    if openspec_change_path is not None:
        _add_openspec_change(Path(openspec_change_path), nodes, edges)
    _add_static_code_references(research_id, static_code_paths, static_test_paths, nodes, edges)

    graph = ContextGraph(
        nodes=nodes,
        edges=edges,
        metadata={
            "decision_memo_present": research.artifact_path("decision-memo.md").exists(),
            "mode": research.meta.mode,
            "slug": research.meta.slug,
            "stage": research.meta.stage,
            "status": research.meta.status,
        },
    )
    graph.validate()
    return graph


def _add_note_sources(
    research: Research,
    research_id: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    for note_path in sorted(research.artifact_path("notes").glob("*.md")):
        note = parser.parse_artifact(note_path)
        for source in note.frontmatter.get("sources", []) or []:
            if not isinstance(source, dict) or not source.get("url"):
                continue
            source_id = canonical_node_id("source", str(source["url"]))
            _append_node(
                nodes,
                GraphNode(
                    id=source_id,
                    type="source",
                    title=str(source["url"]),
                    source_files=(str(note_path.relative_to(research.root)),),
                    metadata={
                        "alternative": source.get("alternative", ""),
                        "date": source.get("date", ""),
                        "tier": source.get("tier", ""),
                        "url": source["url"],
                    },
                ),
            )
            edges.append(
                GraphEdge(
                    source=research_id,
                    target=source_id,
                    relation="cites",
                    provenance="explicit",
                    source_file=str(note_path.relative_to(research.root)),
                )
            )


def _add_probe_results(
    research: Research,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> set[str]:
    results_path = research.artifact_path("probe/results.md")
    result_ids: set[str] = set()
    if not results_path.exists():
        return result_ids
    artifact = parser.parse_artifact(results_path)
    result_entries = _extract_results(artifact.section(SECTION_CRITERIA_RESULTS))
    for criterion_id, status, evidence in result_entries:
        result_id = canonical_node_id("result", criterion_id)
        criterion_node_id = canonical_node_id("criterion", criterion_id)
        _append_node(
            nodes,
            GraphNode(
                id=result_id,
                type="result",
                title=f"{criterion_id}: {status}",
                source_files=("probe/results.md",),
                metadata={"criterion": criterion_id, "evidence": evidence, "status": status},
            ),
        )
        if any(node.id == criterion_node_id for node in nodes):
            edges.append(
                GraphEdge(
                    source=result_id,
                    target=criterion_node_id,
                    relation="evaluates",
                    provenance="explicit",
                    source_file="probe/results.md",
                )
            )
        result_ids.add(result_id)
    return result_ids


def _add_decision(
    research: Research,
    result_ids: set[str],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    memo_path = research.artifact_path("decision-memo.md")
    if not memo_path.exists():
        return
    memo = parser.parse_artifact(memo_path)
    recommendation = memo.section(SECTION_RECOMMENDATION) or ""
    if not recommendation.strip():
        return
    decision_id = canonical_node_id("decision", "recommendation")
    _append_node(
        nodes,
        GraphNode(
            id=decision_id,
            type="decision",
            title=_first_non_empty_line(recommendation) or SECTION_RECOMMENDATION,
            source_files=("decision-memo.md",),
            metadata={
                "audience": memo.frontmatter.get("audience", ""),
                "recommendation": recommendation.strip(),
                "ring": memo.frontmatter.get("ring", ""),
            },
        ),
    )

    criteria_text = memo.section(SECTION_SELECTION_CRITERIA) or ""
    for result_id in sorted(result_ids):
        criterion_label = result_id.split(":", 1)[1]
        if criterion_label in criteria_text:
            edges.append(
                GraphEdge(
                    source=decision_id,
                    target=result_id,
                    relation="based_on",
                    provenance="explicit",
                    source_file="decision-memo.md",
                )
            )


def _add_openspec_change(
    change_dir: Path,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    change_id = change_dir.name
    change_node_id = canonical_node_id("openspec_change", change_id)
    _append_node(
        nodes,
        GraphNode(
            id=change_node_id,
            type="openspec_change",
            title=change_id,
            source_files=(str(change_dir / "proposal.md"),),
            metadata={"change_id": change_id, "path": str(change_dir)},
        ),
    )

    for task_id, task_text in _extract_openspec_tasks(change_dir / "tasks.md"):
        task_node_id = canonical_node_id("openspec_task", f"{change_id}-{task_id}")
        _append_node(
            nodes,
            GraphNode(
                id=task_node_id,
                type="openspec_task",
                title=f"{task_id} {task_text}",
                source_files=(str(change_dir / "tasks.md"),),
                metadata={"change_id": change_id, "task_id": task_id, "text": task_text},
            ),
        )
        edges.append(
            GraphEdge(
                source=change_node_id,
                target=task_node_id,
                relation="has_task",
                provenance="explicit",
                source_file=str(change_dir / "tasks.md"),
            )
        )
        for node in nodes:
            if node.type == "criterion" and node.metadata.get("label") in task_text:
                edges.append(
                    GraphEdge(
                        source=task_node_id,
                        target=node.id,
                        relation="satisfies",
                        provenance="explicit",
                        source_file=str(change_dir / "tasks.md"),
                    )
                )

    specs_dir = change_dir / "specs"
    if specs_dir.exists():
        for spec_path in sorted(specs_dir.glob("*/spec.md")):
            spec_name = spec_path.parent.name
            spec_node_id = canonical_node_id("openspec_spec", spec_name)
            _append_node(
                nodes,
                GraphNode(
                    id=spec_node_id,
                    type="openspec_spec",
                    title=spec_name,
                    source_files=(str(spec_path),),
                    metadata={"change_id": change_id, "spec": spec_name},
                ),
            )
            edges.append(
                GraphEdge(
                    source=change_node_id,
                    target=spec_node_id,
                    relation="modifies_spec",
                    provenance="explicit",
                    source_file=str(spec_path),
                )
            )


def _add_static_code_references(
    research_id: str,
    code_paths: tuple[str, ...],
    test_paths: tuple[str, ...],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    code_node_ids: list[str] = []
    for code_path in code_paths:
        node_id = canonical_node_id("code_artifact", code_path)
        code_node_ids.append(node_id)
        _append_node(
            nodes,
            GraphNode(
                id=node_id,
                type="code_artifact",
                title=code_path,
                source_files=(code_path,),
                metadata={"path": code_path, "provider": "static"},
            ),
        )
        edges.append(
            GraphEdge(
                source=research_id,
                target=node_id,
                relation="touches",
                provenance="provider",
                source_file="static-provider",
                metadata={"provider": "static"},
            )
        )

    for test_path in test_paths:
        test_node_id = canonical_node_id("test_artifact", test_path)
        _append_node(
            nodes,
            GraphNode(
                id=test_node_id,
                type="test_artifact",
                title=test_path,
                source_files=(test_path,),
                metadata={"path": test_path, "provider": "static"},
            ),
        )
        for code_node_id in code_node_ids:
            edges.append(
                GraphEdge(
                    source=test_node_id,
                    target=code_node_id,
                    relation="validates",
                    provenance="provider",
                    source_file="static-provider",
                    metadata={"provider": "static"},
                )
            )


def _sorted_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    return sorted(
        edges,
        key=lambda edge: (edge.source, edge.relation, edge.target, edge.provenance),
    )


def _read_codegraph_version(executable_path: str) -> str:
    try:
        result = subprocess.run(
            [executable_path, "version"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or result.stderr).strip()


def _extract_criteria(section: str | None) -> list[tuple[str, str]]:
    if not section:
        return []
    criteria: list[tuple[str, str]] = []
    for line in section.splitlines():
        match = _CRITERION_RE.match(line)
        if match:
            criteria.append((match.group(1).upper(), match.group(2).strip()))
    return criteria


def _extract_bullets(section: str | None) -> list[str]:
    if not section:
        return []
    bullets: list[str] = []
    for line in section.splitlines():
        match = _BULLET_RE.match(line)
        if match:
            bullets.append(match.group(1).strip())
    return bullets


def _extract_results(section: str | None) -> list[tuple[str, str, str]]:
    if not section:
        return []
    results: list[tuple[str, str, str]] = []
    for line in section.splitlines():
        match = _RESULT_RE.match(line)
        if match:
            results.append(
                (match.group(1).upper(), match.group(2).casefold(), match.group(3).strip())
            )
    return results


def _extract_openspec_tasks(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    tasks: list[tuple[str, str]] = []
    task_re = re.compile(r"^\s*- \[[ xX]\]\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = task_re.match(line)
        if match:
            tasks.append((match.group(1), match.group(2)))
    return tasks


def _append_node(nodes: list[GraphNode], node: GraphNode) -> None:
    if not any(existing.id == node.id for existing in nodes):
        nodes.append(node)


def _first_non_empty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _stable_value(value[key]) for key in sorted(value)}
    if isinstance(value, list | tuple | set):
        return [_stable_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value
