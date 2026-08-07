import json
import os

import pytest

from sdr.context_graph import (
    ContextGraph,
    ContextGraphError,
    GraphEdge,
    GraphNode,
    build_sdr_context_graph,
    canonical_node_id,
    inspect_codegraph_provider,
    inspect_context_graph,
    redact_secret_like_values,
    validate_paths_within_root,
    write_context_graph,
)
from sdr.research import Research


def test_context_graph_validates_node_and_edge_contract():
    graph = ContextGraph(
        nodes=[
            GraphNode(
                id=canonical_node_id("criterion", "C1"),
                type="criterion",
                title="C1 - Latencia aceptable",
                source_files=("brief.md",),
            ),
            GraphNode(
                id=canonical_node_id("decision", "recommendation"),
                type="decision",
                title="Recomendación",
                source_files=("decision-memo.md",),
            ),
        ],
        edges=[
            GraphEdge(
                source="decision:recommendation",
                target="criterion:C1",
                relation="based_on",
                provenance="explicit",
                source_file="decision-memo.md",
            )
        ],
    )

    graph.validate()

    assert graph.nodes[0].id == "criterion:C1"
    assert graph.edges[0].provenance == "explicit"


def test_context_graph_rejects_duplicate_node_ids():
    graph = ContextGraph(
        nodes=[
            GraphNode(id="criterion:C1", type="criterion", title="C1"),
            GraphNode(id="criterion:C1", type="criterion", title="C1 duplicate"),
        ],
        edges=[],
    )

    with pytest.raises(ContextGraphError, match="duplicate node id"):
        graph.validate()


def test_context_graph_rejects_broken_edges():
    graph = ContextGraph(
        nodes=[GraphNode(id="criterion:C1", type="criterion", title="C1")],
        edges=[
            GraphEdge(
                source="decision:missing",
                target="criterion:C1",
                relation="based_on",
                provenance="explicit",
            )
        ],
    )

    with pytest.raises(ContextGraphError, match="unknown source node"):
        graph.validate()


def test_context_graph_serializes_deterministically():
    graph = ContextGraph(
        nodes=[
            GraphNode(id="decision:recommendation", type="decision", title="B"),
            GraphNode(id="criterion:C1", type="criterion", title="A"),
        ],
        edges=[
            GraphEdge(
                source="decision:recommendation",
                target="criterion:C1",
                relation="based_on",
                provenance="explicit",
            )
        ],
        metadata={"slug": "eval-context"},
    )

    first = graph.to_json()
    second = graph.to_json()

    assert first == second
    payload = json.loads(first)
    assert [node["id"] for node in payload["nodes"]] == [
        "criterion:C1",
        "decision:recommendation",
    ]


def test_write_context_graph_creates_context_json(tmp_path):
    graph = ContextGraph(
        nodes=[GraphNode(id="criterion:C1", type="criterion", title="C1")],
        edges=[],
        metadata={"slug": "eval-context"},
    )

    output = write_context_graph(graph, tmp_path / "eval-context")

    assert output == tmp_path / "eval-context" / "context" / "context.json"
    assert json.loads(output.read_text(encoding="utf-8"))["metadata"]["slug"] == "eval-context"


def test_write_context_graph_rejects_context_symlink_escape(tmp_path):
    root = tmp_path / "eval-context"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "context").symlink_to(outside, target_is_directory=True)
    graph = ContextGraph(nodes=[], edges=[])

    with pytest.raises(ValueError, match="fuera de la raíz"):
        write_context_graph(graph, root)

    assert not (outside / "context.json").exists()


def test_build_sdr_context_graph_extracts_brief_metadata_criteria_and_risks(tmp_path):
    research = Research.create(
        base=tmp_path,
        slug="eval-context",
        title="Evaluar Context Graph",
        question="¿El grafo mejora la trazabilidad?",
        mode="full",
        owner="nacho",
        timebox=2,
        tags=["speclab"],
    )
    (research.root / "brief.md").write_text(
        """
---
research: eval-context
stage: intake
---

## Pregunta

¿El grafo mejora la trazabilidad?

## Criterios de evaluación

- C1: conecta criterios con decisiones
- C2: detecta tareas sin evidencia

## Riesgos de adopción

- Ruido visual en Obsidian
- Dependencia prematura de CodeGraph
""".lstrip(),
        encoding="utf-8",
    )

    graph = build_sdr_context_graph(research)

    node_ids = {node.id for node in graph.nodes}
    assert "research:eval-context" in node_ids
    assert "question:eval-context" in node_ids
    assert "criterion:C1" in node_ids
    assert "criterion:C2" in node_ids
    assert "risk:R1" in node_ids
    assert graph.metadata["stage"] == "intake"
    assert graph.metadata["mode"] == "full"

    edge_pairs = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert ("research:eval-context", "has_question", "question:eval-context") in edge_pairs
    assert ("research:eval-context", "defines", "criterion:C1") in edge_pairs
    assert ("research:eval-context", "has_risk", "risk:R1") in edge_pairs


def test_build_sdr_context_graph_extracts_sources_probe_results_and_decision(tmp_path):
    research = Research.create(
        base=tmp_path,
        slug="eval-context",
        title="Evaluar Context Graph",
        question="¿El grafo mejora la trazabilidad?",
        mode="full",
    )
    (research.root / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: conecta criterios con decisiones
""".lstrip(),
        encoding="utf-8",
    )
    (research.root / "notes" / "graph.md").write_text(
        """
---
research: eval-context
stage: explore
sources:
  - url: https://docs.example.com/context-graph
    tier: T1
    date: 2026-07-04
    alternative: speclab-context-graph
---

## Alternativas evaluadas

SpecLab Context Graph.
""".lstrip(),
        encoding="utf-8",
    )
    (research.root / "probe" / "results.md").write_text(
        """
## Resultados por criterio

- C1: cumple - El prototipo enlaza criterio y decisión.
""".lstrip(),
        encoding="utf-8",
    )
    (research.root / "decision-memo.md").write_text(
        """
---
research: eval-context
stage: transfer
ring: trial
audience: equipo
---

## Recomendación

En el contexto de SpecLab, ante trazabilidad limitada, decidimos trial del Context Graph para mejorar decisiones, aceptando ruido visual.

## Criterios de selección

C1 fue el criterio principal.
""".lstrip(),
        encoding="utf-8",
    )

    graph = build_sdr_context_graph(research)

    nodes_by_type = {node.type: [] for node in graph.nodes}
    for node in graph.nodes:
        nodes_by_type[node.type].append(node)

    assert nodes_by_type["source"][0].metadata["tier"] == "T1"
    assert nodes_by_type["source"][0].metadata["alternative"] == "speclab-context-graph"
    assert nodes_by_type["result"][0].metadata["status"] == "cumple"
    assert nodes_by_type["decision"][0].metadata["ring"] == "trial"

    edge_pairs = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert ("result:C1", "evaluates", "criterion:C1") in edge_pairs
    assert ("decision:recommendation", "based_on", "result:C1") in edge_pairs


def test_build_sdr_context_graph_links_optional_openspec_change_tasks_and_specs(tmp_path):
    research = Research.create(
        base=tmp_path,
        slug="eval-context",
        title="Evaluar Context Graph",
        question="¿El grafo mejora la trazabilidad?",
        mode="full",
    )
    (research.root / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: conecta criterios con tareas OpenSpec
""".lstrip(),
        encoding="utf-8",
    )
    change_dir = tmp_path / "openspec" / "changes" / "add-context"
    (change_dir / "specs" / "speclab-codegraph-context").mkdir(parents=True)
    (change_dir / "proposal.md").write_text(
        "## Why\n\nImplementar Context Graph para C1.\n", encoding="utf-8"
    )
    (change_dir / "tasks.md").write_text(
        "## 1. Build\n\n- [ ] 1.1 Implement C1 linkage\n", encoding="utf-8"
    )
    (change_dir / "specs" / "speclab-codegraph-context" / "spec.md").write_text(
        "## ADDED Requirements\n\n### Requirement: Link C1\nThe system SHALL link C1.\n",
        encoding="utf-8",
    )

    graph = build_sdr_context_graph(research, openspec_change_path=change_dir)

    node_ids = {node.id for node in graph.nodes}
    assert "openspec_change:add-context" in node_ids
    assert "openspec_task:add-context-1.1" in node_ids
    assert "openspec_spec:speclab-codegraph-context" in node_ids

    edge_pairs = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert (
        "openspec_change:add-context",
        "has_task",
        "openspec_task:add-context-1.1",
    ) in edge_pairs
    assert ("openspec_task:add-context-1.1", "satisfies", "criterion:C1") in edge_pairs
    assert (
        "openspec_change:add-context",
        "modifies_spec",
        "openspec_spec:speclab-codegraph-context",
    ) in edge_pairs


def test_build_sdr_context_graph_adds_static_code_and_test_references(tmp_path):
    research = Research.create(
        base=tmp_path,
        slug="eval-context",
        title="Evaluar Context Graph",
        question="¿El grafo mejora la trazabilidad?",
        mode="full",
    )

    graph = build_sdr_context_graph(
        research,
        static_code_paths=("sdr/context_graph.py",),
        static_test_paths=("tests/test_context_graph.py",),
    )

    nodes_by_id = {node.id: node for node in graph.nodes}
    assert nodes_by_id["code_artifact:sdr-context_graph.py"].metadata["provider"] == "static"
    assert nodes_by_id["test_artifact:tests-test_context_graph.py"].metadata["provider"] == "static"

    edge_pairs = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
    assert (
        "research:eval-context",
        "touches",
        "code_artifact:sdr-context_graph.py",
    ) in edge_pairs
    assert (
        "test_artifact:tests-test_context_graph.py",
        "validates",
        "code_artifact:sdr-context_graph.py",
    ) in edge_pairs


def test_inspect_codegraph_provider_degrades_when_index_or_command_missing(tmp_path):
    metadata = inspect_codegraph_provider(tmp_path, executable="missing-codegraph")

    assert metadata["provider"] == "codegraph"
    assert metadata["available"] is False
    assert "missing .codegraph index" in metadata["warnings"]
    assert "missing codegraph executable" in metadata["warnings"]


def test_inspect_codegraph_provider_records_version_when_available(tmp_path, monkeypatch):
    (tmp_path / ".codegraph").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "codegraph"
    executable.write_text("#!/usr/bin/env sh\necho 'codegraph 1.2.3'\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    metadata = inspect_codegraph_provider(tmp_path)

    assert metadata["available"] is True
    assert metadata["index_path"] == str(tmp_path / ".codegraph")
    assert metadata["version"] == "codegraph 1.2.3"
    assert metadata["warnings"] == []


def test_validate_paths_within_root_rejects_out_of_scope_paths(tmp_path):
    allowed = tmp_path / "repo"
    allowed.mkdir()
    inside = allowed / "src" / "app.py"
    outside = tmp_path / "secrets.env"

    assert validate_paths_within_root(allowed, (inside, "tests/test_app.py")) == []

    warnings = validate_paths_within_root(allowed, (outside,))

    assert warnings == [f"out-of-scope path: {outside}"]


def test_redact_secret_like_values_masks_sensitive_assignments():
    text = "API_KEY=abc123\nPASSWORD: super-secret\nnormal=value"

    redacted = redact_secret_like_values(text)

    assert "abc123" not in redacted
    assert "super-secret" not in redacted
    assert "API_KEY=<redacted-value-1>" in redacted
    assert "PASSWORD: <redacted-value-2>" in redacted
    assert "normal=value" in redacted


def test_context_json_recursively_redacts_sensitive_urls_and_private_paths(tmp_path):
    sensitive_url = "https://example.com/doc?token=top-secret&page=2"
    private_path = "/" + "home/alice/private/source.txt"
    graph = ContextGraph(
        nodes=[
            GraphNode(
                id="source:S1",
                type="source",
                title=sensitive_url,
                source_files=(private_path,),
                metadata={
                    "nested": {"password_url": sensitive_url, "path": private_path},
                    "doi": "doi:10.1000/private-paths-are-not-secrets",
                },
            )
        ],
        edges=[],
    )

    output = write_context_graph(graph, tmp_path).read_text(encoding="utf-8")

    assert "top-secret" not in output
    assert private_path not in output
    assert "token=<redacted-value-1>" in output
    assert "page=2" in output
    assert "doi:10.1000/private-paths-are-not-secrets" in output
    assert graph.nodes[0].title == sensitive_url
    assert graph.nodes[0].source_files == (private_path,)


def test_safe_serialization_preserves_sensitive_graph_identity_and_round_trips():
    first_url = "https://example.com/doc?token=alpha-secret&view=full"
    second_url = "https://example.com/doc?token=beta-secret&view=full"
    first_path = "/" + "home/alice/private/alpha.txt"
    second_path = "/" + "home/alice/private/beta.txt"
    graph = ContextGraph(
        nodes=[
            GraphNode(
                id=f"source:{first_url}",
                type="source",
                title=first_url,
                source_files=(first_path,),
                metadata={"url": first_url, "path": first_path, "ordinary": "source:S1"},
            ),
            GraphNode(
                id=f"source:{second_url}",
                type="source",
                title=second_url,
                source_files=(second_path,),
                metadata={"url": second_url, "path": second_path, "ordinary": "source:S2"},
            ),
        ],
        edges=[
            GraphEdge(
                source=f"source:{first_url}",
                target=f"source:{second_url}",
                relation="related",
                provenance="explicit",
                metadata={"password": "PASSWORD=alpha-secret", "ordinary": "edge:E1"},
            )
        ],
        metadata={
            "password": "PASSWORD=beta-secret",
            "provenance_map": {"TOKEN": "provenance-secret"},
            "ordinary": "graph:G1",
        },
    )

    first = graph.to_dict()
    second = graph.to_dict()
    encoded = json.dumps(first, sort_keys=True)
    round_tripped = ContextGraph.from_dict(first)

    assert first == second
    assert len({node["id"] for node in first["nodes"]}) == 2
    node_ids_by_ordinary = {node["metadata"]["ordinary"]: node["id"] for node in first["nodes"]}
    assert first["edges"][0]["source"] == node_ids_by_ordinary["source:S1"]
    assert first["edges"][0]["target"] == node_ids_by_ordinary["source:S2"]
    round_trip_payload = round_tripped.to_dict()
    third_cycle_payload = ContextGraph.from_dict(round_trip_payload).to_dict()
    assert round_trip_payload == first
    assert third_cycle_payload == first
    round_trip_ids = {node["id"] for node in round_trip_payload["nodes"]}
    assert len(round_trip_ids) == 2
    assert round_trip_payload["edges"][0]["source"] in round_trip_ids
    assert round_trip_payload["edges"][0]["target"] in round_trip_ids
    assert "alpha-secret" not in encoded
    assert "beta-secret" not in encoded
    assert "provenance-secret" not in encoded
    assert first_path not in encoded
    assert second_path not in encoded
    assert encoded.count("redacted-value-1") >= 3
    assert encoded.count("redacted-value-2") >= 3
    assert "redacted-value-3" in encoded
    assert "redacted-value-4" in encoded
    assert set(node_ids_by_ordinary) == {"source:S1", "source:S2"}
    assert first["edges"][0]["metadata"]["ordinary"] == "edge:E1"
    assert first["metadata"]["ordinary"] == "graph:G1"


@pytest.mark.parametrize("marker_first", [False, True])
def test_context_graph_reserved_marker_identity_cannot_collide_on_round_trip(marker_first):
    marker_input = "<redacted-value-1>"
    private_path = "/" + "home/alice/private/source.txt"
    actual_secret = "actual-secret"
    actual_id = f"source:https://example.com/doc?token={actual_secret}"
    marker_id = f"source:https://example.com/doc?token={marker_input}"
    nodes = [
        GraphNode(
            id=actual_id,
            type="source",
            title=actual_id,
            source_files=(private_path,),
            metadata={"kind": "actual", "map": {"TOKEN": actual_secret}},
        ),
        GraphNode(
            id=marker_id,
            type="source",
            title=marker_id,
            source_files=(marker_input,),
            metadata={"kind": "marker-shaped", "provenance_detail": marker_input},
        ),
    ]
    if marker_first:
        nodes.reverse()
    graph = ContextGraph(
        nodes=nodes,
        edges=[
            GraphEdge(
                source=actual_id,
                target=marker_id,
                relation="related",
                provenance="explicit",
            )
        ],
    )

    payload = graph.to_dict()
    node_ids = {node["id"] for node in payload["nodes"]}
    round_tripped = ContextGraph.from_dict(payload)
    round_trip_payload = round_tripped.to_dict()
    third_cycle_payload = ContextGraph.from_dict(round_trip_payload).to_dict()
    round_trip_ids = {node["id"] for node in round_trip_payload["nodes"]}

    assert len(node_ids) == len(round_trip_ids) == 2
    assert payload["edges"][0]["source"] in node_ids
    assert payload["edges"][0]["target"] in node_ids
    assert round_trip_payload["edges"][0]["source"] in round_trip_ids
    assert round_trip_payload["edges"][0]["target"] in round_trip_ids
    assert round_trip_payload == payload
    assert third_cycle_payload == payload
    assert actual_secret not in json.dumps(payload)
    marker_node = next(
        node for node in payload["nodes"] if node["metadata"]["kind"] == "marker-shaped"
    )
    source_files = {node["metadata"]["kind"]: node["source_files"][0] for node in payload["nodes"]}
    assert f"token={marker_input}" in marker_node["id"]
    assert len(set(source_files.values())) == 2
    assert source_files["marker-shaped"] == marker_input
    assert private_path not in source_files.values()


def test_context_graph_warns_for_decision_without_basis_and_unused_results():
    graph = ContextGraph(
        nodes=[
            GraphNode(id="criterion:C1", type="criterion", title="C1"),
            GraphNode(id="result:C1", type="result", title="C1: cumple"),
            GraphNode(id="decision:recommendation", type="decision", title="Adoptar"),
        ],
        edges=[
            GraphEdge(
                source="result:C1",
                target="criterion:C1",
                relation="evaluates",
                provenance="explicit",
            )
        ],
    )

    assert inspect_context_graph(graph)["warnings"] == [
        "decision without based_on: decision:recommendation",
        "result not used by decision: result:C1",
    ]


def test_context_graph_warns_when_memo_has_no_decision():
    graph = ContextGraph(nodes=[], edges=[], metadata={"decision_memo_present": True})

    assert inspect_context_graph(graph)["warnings"] == ["decision memo without decision"]


def test_built_context_graph_records_memo_without_recommendation_as_gap(tmp_path):
    research = Research.create(
        base=tmp_path,
        slug="eval-context",
        title="Evaluar Context Graph",
        question="¿El grafo ayuda?",
    )
    research.artifact_path("decision-memo.md").write_text(
        "---\nring: assess\n---\n\n## Recomendación\n\n",
        encoding="utf-8",
    )

    graph = build_sdr_context_graph(research)

    assert graph.metadata["decision_memo_present"] is True
    assert not any(node.type == "decision" for node in graph.nodes)
    assert "decision memo without decision" in inspect_context_graph(graph)["warnings"]
