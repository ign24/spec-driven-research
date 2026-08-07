import json

import pytest

from sdr import context_export
from sdr.context_export import export_context_graph
from sdr.context_graph import ContextGraph, GraphEdge, GraphNode, write_context_graph
from sdr.research import Research


def _sample_graph() -> ContextGraph:
    return ContextGraph(
        nodes=[
            GraphNode(
                id="criterion:C1", type="criterion", title="C1: métrica", source_files=("brief.md",)
            ),
            GraphNode(
                id="result:C1",
                type="result",
                title="C1 cumple",
                source_files=("probe/results.md",),
                metadata={"status": "cumple", "evidence": "evidencia reproducible"},
            ),
            GraphNode(
                id="decision:recommendation",
                type="decision",
                title="Recomendación",
                source_files=("decision-memo.md",),
                metadata={"ring": "trial"},
            ),
        ],
        edges=[
            GraphEdge(
                source="result:C1",
                target="criterion:C1",
                relation="evaluates",
                provenance="explicit",
            ),
            GraphEdge(
                source="decision:recommendation",
                target="result:C1",
                relation="based_on",
                provenance="explicit",
            ),
        ],
        metadata={"slug": "eval-context"},
    )


@pytest.mark.parametrize(
    ("export_format", "relative_output"),
    [
        ("obsidian", "context/obsidian/index.md"),
        ("mermaid", "context/context.mmd"),
        ("dot", "context/context.dot"),
    ],
)
def test_export_knowledge_base_covers_every_investigation(tmp_path, export_format, relative_output):
    Research.create(base=tmp_path, slug="beta", title="Beta", question="Second question")
    Research.create(base=tmp_path, slug="alpha", title="Alpha", question="First question")

    summary = context_export.export_knowledge_base_context_graph(tmp_path, export_format)

    assert summary["investigations"] == 2
    rendered = (tmp_path / relative_output).read_text(encoding="utf-8")
    if export_format == "obsidian":
        output = tmp_path / "context" / "obsidian"
        research_notes = sorted(output.glob("research--*.md"))
        assert len(research_notes) == 2
        titles = {
            path.read_text(encoding="utf-8").split("# ", 1)[1].splitlines()[0]
            for path in research_notes
        }
        assert titles == {"alpha", "beta"}
        assert "research:alpha" in rendered
        assert "research:beta" in rendered
    else:
        assert "alpha" in rendered
        assert "beta" in rendered


@pytest.mark.parametrize(
    ("export_format", "relative_output"),
    [
        ("obsidian", "context/obsidian/index.md"),
        ("mermaid", "context/context.mmd"),
        ("dot", "context/context.dot"),
    ],
)
def test_empty_knowledge_base_exports_a_valid_graph(tmp_path, export_format, relative_output):
    summary = context_export.export_knowledge_base_context_graph(tmp_path, export_format)

    assert summary["investigations"] == 0
    assert (tmp_path / relative_output).is_file()
    graph_path = tmp_path / "context" / "context.json"
    assert graph_path.is_file()
    ContextGraph.from_dict(json.loads(graph_path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("export_format", ["obsidian", "mermaid", "dot"])
def test_kb_exports_resolver_chain_and_exact_edge_origins(tmp_path, export_format):
    first = Research.create(base=tmp_path, slug="alpha", title="Alpha", question="q")
    second = Research.create(base=tmp_path, slug="beta", title="Beta", question="q")
    for research, source_id in ((first, "S1"), (second, "S2")):
        research.artifact_path("notes/sources.md").write_text(
            "---\n"
            f"research: {research.meta.slug}\n"
            "date: 2026-08-03\n"
            "stage: explore\n"
            "sources:\n"
            f"  - id: {source_id}\n"
            "    url: https://example.com/shared\n"
            "---\n\n"
            "## Sources\n",
            encoding="utf-8",
        )

    summary = context_export.export_knowledge_base_context_graph(tmp_path, export_format)

    graph_path = tmp_path / "context" / "context.json"
    assert summary["graph_artifact"] == str(graph_path)
    graph = ContextGraph.from_dict(json.loads(graph_path.read_text(encoding="utf-8")))
    graph.validate()
    if export_format == "obsidian":
        rendered = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((tmp_path / "context" / "obsidian").glob("*.md"))
        )
    else:
        rendered = (
            tmp_path / f"context/context.{'mmd' if export_format == 'mermaid' else 'dot'}"
        ).read_text(encoding="utf-8")
    assert "work-identifier" in rendered
    assert "normalized-url" in rendered
    assert "alpha:S1" in rendered
    assert "beta:S2" in rendered


def test_per_investigation_exports_remain_byte_for_byte_unchanged(tmp_path):
    graph = ContextGraph(
        nodes=[GraphNode(id="source:S1", type="source", title="Source", metadata={"tier": "T1"})],
        edges=[],
        metadata={"slug": "legacy"},
    )
    root = tmp_path / "legacy"

    export_context_graph(graph, root, "obsidian")
    export_context_graph(graph, root, "mermaid")
    export_context_graph(graph, root, "dot")

    assert (root / "context/obsidian/index.md").read_bytes() == (
        b"---\nderived: true\ngraph_artifact: context/context.json\nslug: legacy\n---\n\n"
        b"# SpecLab Context Graph\n\n"
        b"> Derived from `context.json`. Regenerate this export instead of editing it as evidence.\n\n"
        b"- Nodes: 1\n- Edges: 0\n\n## Nodes\n- [[source--S1|source:S1]]\n"
    )
    assert (root / "context/obsidian/source--S1.md").read_bytes() == (
        b"---\nderived: true\ngraph_artifact: context/context.json\nnode_id: source:S1\n"
        b"node_type: source\nsource_files:\n---\n\n# Source\n\n## Metadata\n\n"
        b'```json\n{\n  "tier": "T1"\n}\n```\n\n## Outgoing links\n- none\n\n'
        b"## Incoming links\n- none\n"
    )
    assert (root / "context/context.mmd").read_bytes() == b'flowchart TD\n  n1["Source"]\n'
    assert (root / "context/context.dot").read_bytes() == (
        b'digraph context {\n  n1 [label="Source"];\n}\n'
    )


def test_export_obsidian_writes_index_notes_frontmatter_and_wikilinks(tmp_path):
    root = tmp_path / "eval-context"
    graph = _sample_graph()
    write_context_graph(graph, root)

    summary = export_context_graph(graph, root, "obsidian")

    out = root / "context" / "obsidian"
    assert summary["format"] == "obsidian"
    assert summary["notes"] == 4
    assert (out / "index.md").exists()
    assert (out / "criterion--C1.md").exists()
    assert (out / "result--C1.md").exists()
    criterion = (out / "criterion--C1.md").read_text(encoding="utf-8")
    result = (out / "result--C1.md").read_text(encoding="utf-8")
    assert "node_id: criterion:C1" in criterion
    assert "node_type: criterion" in criterion
    assert "# C1: métrica" in criterion
    assert "[[result--C1|result:C1]]" in criterion
    assert "[[criterion--C1|criterion:C1]]" in result


def test_export_obsidian_is_deterministic_and_redacts_secrets_and_paths(tmp_path):
    root = tmp_path / "eval-context"
    outside = tmp_path / "secret.env"
    graph = ContextGraph(
        nodes=[
            GraphNode(
                id="source:https-example.com",
                type="source",
                title="API_KEY=abc123",
                source_files=(str(outside),),
                metadata={
                    "repeated": "TOKEN: abc123",
                    "summary": "TOKEN: super-secret",
                },
            )
        ],
        edges=[],
    )
    write_context_graph(graph, root)

    first = export_context_graph(graph, root, "obsidian")
    contents_first = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((root / "context" / "obsidian").glob("*.md"))
    }
    second = export_context_graph(graph, root, "obsidian")
    contents_second = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((root / "context" / "obsidian").glob("*.md"))
    }

    assert first["warnings"] == second["warnings"] == [f"out-of-scope path: {outside}"]
    assert contents_first == contents_second
    combined = "\n".join(contents_first.values())
    assert "abc123" not in combined
    assert "super-secret" not in combined
    assert combined.count("<redacted-value-1>") == 2
    assert "<redacted-value-2>" in combined
    assert str(outside) not in combined


def test_export_mermaid_and_dot_write_safe_deterministic_files(tmp_path):
    root = tmp_path / "eval-context"
    graph = _sample_graph()
    write_context_graph(graph, root)

    mermaid = export_context_graph(graph, root, "mermaid")
    dot = export_context_graph(graph, root, "dot")

    mmd_path = root / "context" / "context.mmd"
    dot_path = root / "context" / "context.dot"
    assert mermaid["path"] == str(mmd_path)
    assert dot["path"] == str(dot_path)
    assert mmd_path.read_text(encoding="utf-8").startswith("flowchart TD")
    assert "result:C1" not in mmd_path.read_text(encoding="utf-8").splitlines()[1].split("[")[0]
    dot_text = dot_path.read_text(encoding="utf-8")
    assert dot_text.startswith("digraph context")
    assert 'label="based_on / explicit"' in dot_text
    assert export_context_graph(graph, root, "mermaid")["path"] == mermaid["path"]


def test_export_rejects_context_symlink_escape(tmp_path):
    root = tmp_path / "eval-context"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "context").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="fuera de la raíz"):
        export_context_graph(_sample_graph(), root, "mermaid")

    assert not (outside / "context.mmd").exists()


def test_export_unknown_format_fails(tmp_path):
    graph = _sample_graph()

    try:
        export_context_graph(graph, tmp_path / "eval-context", "bogus")
    except ValueError as exc:
        assert "unsupported export format" in str(exc)
    else:
        raise AssertionError("expected unsupported format failure")
