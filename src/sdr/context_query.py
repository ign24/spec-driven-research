import re
from typing import Any

from sdr.context_graph import ContextGraph, ContextGraphError, GraphEdge, GraphNode

QUERY_INTENTS = {
    "why-ring",
    "decision-support",
    "criterion-lineage",
    "gaps",
    "sources-for-result",
}


def query_context_graph(graph: ContextGraph, intent: str, *, criterion: str = "") -> dict[str, Any]:
    """Answer a deterministic query over a context graph."""
    graph.validate()
    if intent not in QUERY_INTENTS:
        raise ContextGraphError(f"unknown query intent: {intent}")
    nodes = {node.id: node for node in graph.nodes}
    if intent == "why-ring":
        return _query_why_ring(graph, nodes)
    if intent == "decision-support":
        return _query_decision_support(graph, nodes)
    if intent == "criterion-lineage":
        return _query_criterion_lineage(graph, nodes, criterion)
    if intent == "gaps":
        return _query_gaps(graph)
    return _query_sources_for_result(graph, nodes, criterion)


def map_query_text_to_intent(text: str) -> tuple[str, dict[str, str]]:
    """Map common graph questions in English to deterministic query intents."""
    normalized = text.lower().strip()
    criterion = _extract_criterion(text)
    if "why" in normalized and any(
        ring in normalized for ring in ("hold", "trial", "adopt", "assess")
    ):
        return "why-ring", {}
    if "source" in normalized:
        return "sources-for-result", {"criterion": criterion} if criterion else {}
    if "lineage" in normalized or "trace" in normalized:
        return "criterion-lineage", {"criterion": criterion} if criterion else {}
    if "gap" in normalized or "missing" in normalized:
        return "gaps", {}
    if "decision" in normalized or "support" in normalized:
        return "decision-support", {}
    raise ContextGraphError(f"could not map question: {text}")


def _query_why_ring(graph: ContextGraph, nodes: dict[str, GraphNode]) -> dict[str, Any]:
    decision = _decision(nodes)
    based_on = [
        edge
        for edge in _sorted_edges(graph.edges)
        if edge.source == decision.id and edge.relation == "based_on"
    ]
    warnings = [] if based_on else [f"decision without support: {decision.id}"]
    return _support_payload(
        graph,
        nodes,
        decision,
        based_on,
        "why-ring",
        warnings,
        f"Ring {decision.metadata.get('ring', 'unknown')} based on {len(based_on)} results.",
    )


def _query_decision_support(graph: ContextGraph, nodes: dict[str, GraphNode]) -> dict[str, Any]:
    decision = _decision(nodes)
    based_on = [
        edge
        for edge in _sorted_edges(graph.edges)
        if edge.source == decision.id and edge.relation == "based_on"
    ]
    warnings = [] if based_on else [f"decision without support: {decision.id}"]
    return _support_payload(
        graph,
        nodes,
        decision,
        based_on,
        "decision-support",
        warnings,
        f"The decision uses {len(based_on)} results as support.",
    )


def _query_criterion_lineage(
    graph: ContextGraph, nodes: dict[str, GraphNode], criterion: str
) -> dict[str, Any]:
    target = _criterion_id(criterion)
    if target not in nodes:
        raise ContextGraphError(f"criterion not found: {target}")
    edges: list[GraphEdge] = []
    for edge in _sorted_edges(graph.edges):
        if edge.target == target or edge.source == target:
            edges.append(edge)
    result_ids = {
        edge.source for edge in edges if edge.relation == "evaluates" and edge.target == target
    }
    for edge in _sorted_edges(graph.edges):
        if edge.relation == "based_on" and edge.target in result_ids:
            edges.append(edge)
    involved = {target}
    for edge in edges:
        involved.add(edge.source)
        involved.add(edge.target)
    return _payload(
        "criterion-lineage",
        f"Lineage of {target} with {len(edges)} links.",
        sorted(involved),
        edges,
        nodes,
        target=target,
    )


def _query_gaps(graph: ContextGraph) -> dict[str, Any]:
    warnings: list[str] = []
    criteria = [node for node in graph.nodes if node.type == "criterion"]
    results = [node for node in graph.nodes if node.type == "result"]
    decisions = [node for node in graph.nodes if node.type == "decision"]
    for criterion in criteria:
        has_result = any(
            edge.relation == "evaluates" and edge.target == criterion.id for edge in graph.edges
        )
        if not has_result:
            label = criterion.metadata.get("label", criterion.id.split(":")[-1])
            warnings.append(f"criterion without result: {label}")
    for decision in decisions:
        has_support = any(
            edge.relation == "based_on" and edge.source == decision.id for edge in graph.edges
        )
        if not has_support:
            warnings.append(f"decision without support: {decision.id}")
    for result in results:
        is_used = any(
            edge.relation == "based_on" and edge.target == result.id for edge in graph.edges
        )
        if not is_used:
            warnings.append(f"unused result: {result.id}")
    for task in [node for node in graph.nodes if node.type == "openspec_task"]:
        if not any(edge.source == task.id or edge.target == task.id for edge in graph.edges):
            warnings.append(f"unlinked OpenSpec task: {task.id}")
    return {
        "answer": f"Found {len(warnings)} gaps.",
        "evidence_snippets": [],
        "intent": "gaps",
        "involved_edges": [],
        "involved_nodes": [],
        "warnings": sorted(warnings),
    }


def _query_sources_for_result(
    graph: ContextGraph, nodes: dict[str, GraphNode], criterion: str
) -> dict[str, Any]:
    result_id = f"result:{_extract_criterion(criterion) or criterion}"
    if result_id not in nodes:
        raise ContextGraphError(f"result not found: {result_id}")
    direct_edges = [
        edge
        for edge in _sorted_edges(graph.edges)
        if edge.source == result_id and edge.relation in {"supported_by", "cites", "derived_from"}
    ]
    warnings: list[str] = []
    if direct_edges:
        involved = {result_id, *(edge.target for edge in direct_edges)}
        edges = direct_edges
    else:
        global_edges = [edge for edge in _sorted_edges(graph.edges) if edge.relation == "cites"]
        involved = {result_id, *(edge.target for edge in global_edges)}
        edges = global_edges
        warnings.append(
            "direct source-to-result support is not present; returning global citations "
            f"rather than direct support for {result_id}"
        )
    return _payload(
        "sources-for-result",
        f"Sources for {result_id}.",
        sorted(involved),
        edges,
        nodes,
        target=result_id,
        warnings=warnings,
    )


def _support_payload(
    graph: ContextGraph,
    nodes: dict[str, GraphNode],
    decision: GraphNode,
    based_on: list[GraphEdge],
    intent: str,
    warnings: list[str],
    answer: str,
) -> dict[str, Any]:
    edges = list(based_on)
    involved = {decision.id}
    for edge in based_on:
        involved.add(edge.target)
        for result_edge in _sorted_edges(graph.edges):
            if result_edge.source == edge.target and result_edge.relation == "evaluates":
                edges.append(result_edge)
                involved.add(result_edge.target)
    payload = _payload(
        intent, answer, sorted(involved), edges, nodes, target=decision.id, warnings=warnings
    )
    payload["ring"] = decision.metadata.get("ring", "")
    return payload


def _payload(
    intent: str,
    answer: str,
    involved_nodes: list[str],
    edges: list[GraphEdge],
    nodes: dict[str, GraphNode],
    *,
    target: str = "",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "answer": answer,
        "evidence_snippets": [
            _snippet(nodes[node_id]) for node_id in involved_nodes if node_id in nodes
        ],
        "intent": intent,
        "involved_edges": [edge.to_dict() for edge in _sorted_edges(edges)],
        "involved_nodes": involved_nodes,
        "target": target,
        "warnings": sorted(warnings or []),
    }


def _snippet(node: GraphNode) -> dict[str, Any]:
    text = node.metadata.get("evidence") or node.metadata.get("recommendation") or node.title
    status = node.metadata.get("status")
    if status:
        text = f"{status}: {text}"
    return {"node_id": node.id, "source_files": list(node.source_files), "text": str(text)}


def _decision(nodes: dict[str, GraphNode]) -> GraphNode:
    if "decision:recommendation" in nodes:
        return nodes["decision:recommendation"]
    for node in nodes.values():
        if node.type == "decision":
            return node
    raise ContextGraphError("decision not found")


def _criterion_id(value: str) -> str:
    criterion = _extract_criterion(value)
    if not criterion:
        raise ContextGraphError("specify --criterion <ID>")
    return f"criterion:{criterion}"


def _extract_criterion(value: str) -> str:
    match = re.search(r"\bC\d+\b", value, re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _sorted_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    return sorted(
        edges, key=lambda edge: (edge.source, edge.relation, edge.target, edge.provenance)
    )
