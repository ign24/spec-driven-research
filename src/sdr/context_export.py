import json
import shutil
from pathlib import Path
from typing import Any

from sdr.context_graph import (
    ContextGraph,
    GraphEdge,
    GraphNode,
    validate_paths_within_root,
    write_context_graph,
)
from sdr.paths import resolve_child, resolve_root
from sdr.public_tree_audit import RedactionContext, redact_sensitive, redact_sensitive_values


def export_context_graph(
    graph: ContextGraph,
    research_path: Path,
    export_format: str,
    *,
    kb_traceability: bool = False,
    _redaction_context: RedactionContext | None = None,
) -> dict[str, Any]:
    """Export an existing context graph into a derived visual format."""
    context = _redaction_context or RedactionContext()
    research_path = resolve_root(research_path)
    graph.validate()
    if export_format == "obsidian":
        summary = _export_obsidian(
            graph, research_path, kb_traceability=kb_traceability, redaction_context=context
        )
        return redact_sensitive_values(summary, context=context)
    if export_format == "mermaid":
        summary = _export_mermaid(
            graph, research_path, kb_traceability=kb_traceability, redaction_context=context
        )
        return redact_sensitive_values(summary, context=context)
    if export_format == "dot":
        summary = _export_dot(
            graph, research_path, kb_traceability=kb_traceability, redaction_context=context
        )
        return redact_sensitive_values(summary, context=context)
    raise ValueError(f"unsupported export format: {export_format}")


def export_knowledge_base_context_graph(base_path: Path, export_format: str) -> dict[str, Any]:
    """Derive and export every investigation through the existing graph exporters."""
    from sdr.cross_investigation import derive_cross_investigation_layer

    layer = derive_cross_investigation_layer(base_path)
    graph = layer.to_context_graph()
    graph_path = write_context_graph(graph, resolve_root(base_path))
    context = RedactionContext()
    summary = export_context_graph(
        graph,
        base_path,
        export_format,
        kb_traceability=True,
        _redaction_context=context,
    )
    return redact_sensitive_values(
        {
            **summary,
            "graph_artifact": str(graph_path),
            "investigations": len(layer.investigations),
        },
        context=context,
    )


def _export_obsidian(
    graph: ContextGraph,
    research_path: Path,
    *,
    kb_traceability: bool,
    redaction_context: RedactionContext,
) -> dict[str, Any]:
    output_dir = resolve_child(research_path, "context/obsidian")
    temp_dir = resolve_child(research_path, "context/obsidian.tmp")
    warnings = _collect_path_warnings(graph, research_path)
    contents = _obsidian_contents(
        graph,
        warnings,
        kb_traceability=kb_traceability,
        redaction_context=redaction_context,
    )
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    try:
        for name, text in contents.items():
            (temp_dir / name).write_text(text, encoding="utf-8")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        temp_dir.rename(output_dir)
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise
    return {
        "files": len(contents),
        "format": "obsidian",
        "index": str(output_dir / "index.md"),
        "notes": len(contents),
        "path": str(output_dir),
        "warnings": warnings,
    }


def _export_mermaid(
    graph: ContextGraph,
    research_path: Path,
    *,
    kb_traceability: bool,
    redaction_context: RedactionContext,
) -> dict[str, Any]:
    path = resolve_child(research_path, "context/context.mmd")
    text = redact_sensitive(
        _render_mermaid(graph, kb_traceability=kb_traceability), context=redaction_context
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"format": "mermaid", "path": str(path), "warnings": []}


def _export_dot(
    graph: ContextGraph,
    research_path: Path,
    *,
    kb_traceability: bool,
    redaction_context: RedactionContext,
) -> dict[str, Any]:
    path = resolve_child(research_path, "context/context.dot")
    text = redact_sensitive(
        _render_dot(graph, kb_traceability=kb_traceability), context=redaction_context
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"format": "dot", "path": str(path), "warnings": []}


def _obsidian_contents(
    graph: ContextGraph,
    warnings: list[str],
    *,
    kb_traceability: bool,
    redaction_context: RedactionContext,
) -> dict[str, str]:
    nodes = sorted(graph.nodes, key=lambda node: node.id)
    edges = _sorted_edges(graph.edges)
    names = {node.id: _note_name(node) for node in nodes}
    contents: dict[str, str] = {
        "index.md": _render_obsidian_index(
            graph, nodes, edges, names, warnings, kb_traceability=kb_traceability
        )
    }
    for node in nodes:
        incoming = [edge for edge in edges if edge.target == node.id]
        outgoing = [edge for edge in edges if edge.source == node.id]
        contents[names[node.id]] = _render_obsidian_node(
            node, incoming, outgoing, names, kb_traceability=kb_traceability
        )
    return redact_sensitive_values(dict(sorted(contents.items())), context=redaction_context)


def _render_obsidian_index(
    graph: ContextGraph,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    names: dict[str, str],
    warnings: list[str],
    *,
    kb_traceability: bool,
) -> str:
    lines = [
        "---",
        "derived: true",
        "graph_artifact: context/context.json",
        f"slug: {_yaml_scalar(str(graph.metadata.get('slug', '')))}",
        "---",
        "",
        "# SpecLab Context Graph",
        "",
        "> Derived from `context.json`. Regenerate this export instead of editing it as evidence.",
        "",
        f"- Nodes: {len(nodes)}",
        f"- Edges: {len(edges)}",
        "",
        "## Nodes",
    ]
    for node in nodes:
        lines.append(f"- [[{Path(names[node.id]).stem}|{_clean(node.id)}]]")
    if kb_traceability:
        lines.extend(["", "## Traceability", "", "```json"])
        lines.append(_traceability_metadata(graph))
        lines.append("```")
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend("- out-of-scope path omitted" for _warning in warnings)
    return "\n".join(lines) + "\n"


def _render_obsidian_node(
    node: GraphNode,
    incoming: list[GraphEdge],
    outgoing: list[GraphEdge],
    names: dict[str, str],
    *,
    kb_traceability: bool,
) -> str:
    safe_sources = [source for source in node.source_files if not Path(source).is_absolute()]
    lines = [
        "---",
        "derived: true",
        "graph_artifact: context/context.json",
        f"node_id: {_clean(node.id)}",
        f"node_type: {_yaml_scalar(_clean(node.type))}",
        "source_files:",
    ]
    lines.extend(f"  - {_yaml_scalar(_clean(source))}" for source in safe_sources)
    lines.extend(
        [
            "---",
            "",
            f"# {_clean(node.title)}",
            "",
            "## Metadata",
            "",
            "```json",
            _clean(json.dumps(node.metadata, ensure_ascii=False, indent=2, sort_keys=True)),
            "```",
            "",
            "## Outgoing links",
        ]
    )
    lines.extend(
        _edge_line(edge, edge.target, names, kb_traceability=kb_traceability) for edge in outgoing
    )
    if not outgoing:
        lines.append("- none")
    lines.append("")
    lines.append("## Incoming links")
    lines.extend(
        _edge_line(edge, edge.source, names, kb_traceability=kb_traceability) for edge in incoming
    )
    if not incoming:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _render_mermaid(graph: ContextGraph, *, kb_traceability: bool) -> str:
    aliases = _aliases(graph.nodes)
    lines = []
    if kb_traceability:
        lines.append(f"%% sdr-kb-traceability: {_traceability_metadata(graph)}")
    lines.append("flowchart TD")
    for node in sorted(graph.nodes, key=lambda item: item.id):
        lines.append(f'  {aliases[node.id]}["{_mermaid_label(node.title)}"]')
    for edge in _sorted_edges(graph.edges):
        label = _mermaid_label(_edge_label(edge, kb_traceability=kb_traceability))
        lines.append(f"  {aliases[edge.source]} -->|{label}| {aliases[edge.target]}")
    return "\n".join(lines) + "\n"


def _render_dot(graph: ContextGraph, *, kb_traceability: bool) -> str:
    aliases = _aliases(graph.nodes)
    lines = ["digraph context {"]
    if kb_traceability:
        lines.append(f"  // sdr-kb-traceability: {_traceability_metadata(graph)}")
    for node in sorted(graph.nodes, key=lambda item: item.id):
        lines.append(f'  {aliases[node.id]} [label="{_dot_label(node.title)}"];')
    for edge in _sorted_edges(graph.edges):
        label = _dot_label(_edge_label(edge, kb_traceability=kb_traceability))
        lines.append(f'  {aliases[edge.source]} -> {aliases[edge.target]} [label="{label}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def _edge_line(
    edge: GraphEdge,
    linked_node: str,
    names: dict[str, str],
    *,
    kb_traceability: bool,
) -> str:
    stem = Path(names[linked_node]).stem
    label = _edge_label(edge, kb_traceability=kb_traceability)
    return f"- {label}: [[{stem}|{_clean(linked_node)}]]"


def _edge_label(edge: GraphEdge, *, kb_traceability: bool) -> str:
    label = f"{edge.relation} / {edge.provenance}"
    if kb_traceability:
        origin = json.dumps(edge.metadata.get("origin"), ensure_ascii=True, sort_keys=True)
        label = f"{label} / origin={origin}"
    return label


def _traceability_metadata(graph: ContextGraph) -> str:
    return json.dumps(
        {"resolver_chain": graph.metadata.get("resolver_chain", [])},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _note_name(node: GraphNode) -> str:
    raw = node.id.split(":", 1)[1] if ":" in node.id else node.id
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in raw).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    return f"{node.type}--{safe or 'node'}.md"


def _aliases(nodes: list[GraphNode]) -> dict[str, str]:
    return {
        node.id: f"n{index}"
        for index, node in enumerate(sorted(nodes, key=lambda item: item.id), 1)
    }


def _sorted_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    return sorted(
        edges, key=lambda edge: (edge.source, edge.relation, edge.target, edge.provenance)
    )


def _collect_path_warnings(graph: ContextGraph, research_path: Path) -> list[str]:
    paths: list[str] = []
    for node in graph.nodes:
        paths.extend(node.source_files)
    return validate_paths_within_root(research_path, tuple(paths))


def _clean(value: str) -> str:
    return value


def _yaml_scalar(value: str) -> str:
    if not value:
        return "''"
    if any(char in value for char in ':#[]{}\n"'):
        return json.dumps(value, ensure_ascii=False)
    return value


def _mermaid_label(value: str) -> str:
    return _clean(value).replace('"', "'").replace("|", "/")


def _dot_label(value: str) -> str:
    return _clean(value).replace("\\", "\\\\").replace('"', '\\"')
