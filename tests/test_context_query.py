import pytest

from sdr.context_graph import ContextGraph, ContextGraphError, GraphEdge, GraphNode
from sdr.context_query import map_query_text_to_intent, query_context_graph


def _query_graph() -> ContextGraph:
    return ContextGraph(
        nodes=[
            GraphNode(id="research:eval", type="research", title="Eval"),
            GraphNode(
                id="criterion:C1", type="criterion", title="C1: métrica", source_files=("brief.md",)
            ),
            GraphNode(
                id="criterion:C2",
                type="criterion",
                title="C2: reproducible",
                source_files=("brief.md",),
            ),
            GraphNode(
                id="result:C1",
                type="result",
                title="C1 no cumple",
                source_files=("probe/results.md",),
                metadata={"status": "no cumple", "evidence": "sin métricas locales"},
            ),
            GraphNode(
                id="result:C2",
                type="result",
                title="C2 parcial",
                source_files=("probe/results.md",),
                metadata={"status": "parcial", "evidence": "solo readiness"},
            ),
            GraphNode(
                id="decision:recommendation",
                type="decision",
                title="Hold",
                source_files=("decision-memo.md",),
                metadata={"ring": "hold", "recommendation": "no demo comercial"},
            ),
            GraphNode(
                id="source:https-docs.example",
                type="source",
                title="Docs",
                metadata={"url": "https://docs.example"},
            ),
            GraphNode(id="openspec_task:add-context-7.3", type="openspec_task", title="Query C1"),
        ],
        edges=[
            GraphEdge(
                source="research:eval",
                target="criterion:C1",
                relation="defines",
                provenance="explicit",
            ),
            GraphEdge(
                source="research:eval",
                target="source:https-docs.example",
                relation="cites",
                provenance="explicit",
            ),
            GraphEdge(
                source="result:C1",
                target="criterion:C1",
                relation="evaluates",
                provenance="explicit",
            ),
            GraphEdge(
                source="result:C2",
                target="criterion:C2",
                relation="evaluates",
                provenance="explicit",
            ),
            GraphEdge(
                source="decision:recommendation",
                target="result:C1",
                relation="based_on",
                provenance="explicit",
            ),
            GraphEdge(
                source="decision:recommendation",
                target="result:C2",
                relation="based_on",
                provenance="explicit",
            ),
            GraphEdge(
                source="openspec_task:add-context-7.3",
                target="criterion:C1",
                relation="satisfies",
                provenance="explicit",
            ),
        ],
    )


def test_query_why_ring_reports_blocking_results_and_decision():
    answer = query_context_graph(_query_graph(), "why-ring")

    assert answer["intent"] == "why-ring"
    assert answer["ring"] == "hold"
    assert "decision:recommendation" in answer["involved_nodes"]
    assert "result:C1" in answer["involved_nodes"]
    assert any("no cumple" in item["text"] for item in answer["evidence_snippets"])
    assert answer["warnings"] == []


def test_query_criterion_lineage_reports_definition_result_decision_and_task():
    answer = query_context_graph(_query_graph(), "criterion-lineage", criterion="C1")

    assert answer["target"] == "criterion:C1"
    assert "research:eval" in answer["involved_nodes"]
    assert "result:C1" in answer["involved_nodes"]
    assert "decision:recommendation" in answer["involved_nodes"]
    assert "openspec_task:add-context-7.3" in answer["involved_nodes"]


def test_query_gaps_and_sources_for_result_warn_on_global_citation_fallback():
    graph = _query_graph()

    gaps = query_context_graph(graph, "gaps")
    sources = query_context_graph(graph, "sources-for-result", criterion="C1")

    assert "unlinked OpenSpec task: openspec_task:add-context-7.3" not in gaps["warnings"]
    assert "source:https-docs.example" in sources["involved_nodes"]
    assert any(
        "global citations rather than direct support" in warning for warning in sources["warnings"]
    )


def test_query_errors_and_natural_language_mapping():
    graph = _query_graph()

    with pytest.raises(ContextGraphError, match="criterion not found: criterion:C99"):
        query_context_graph(graph, "criterion-lineage", criterion="C99")
    with pytest.raises(ContextGraphError, match="unknown query intent"):
        query_context_graph(graph, "bogus")

    assert map_query_text_to_intent("why was it hold")[0] == "why-ring"
    assert map_query_text_to_intent("sources for result C2") == (
        "sources-for-result",
        {"criterion": "C2"},
    )
