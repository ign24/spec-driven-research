import hashlib
import multiprocessing
import subprocess
import threading
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from sdr import cross_investigation, lifecycle, trail
from sdr import verification as verification_mod
from sdr.context_graph import ContextGraph, GraphEdge, GraphNode
from sdr.cross_investigation import (
    NORMALIZED_URL_RESOLVER,
    CrossInvestigationLayer,
    SourceResolution,
    compare_cross_investigation_layers,
    derive_cross_investigation_layer,
    normalize_url,
    query_source_dependencies,
    resolve_source_identity,
)
from sdr.parser import parse_artifact
from sdr.research import Reopen, Research
from sdr.snapshot import FetchResult
from sdr.verification import verify_explore_claims
from sdr.verification_ledger import LedgerValidationError, load_ledger, save_ledger


def _acknowledge_concurrently(
    base: str,
    slug: str,
    source_id: str,
    observations: tuple[cross_investigation.NetworkSourceObservation, ...],
    start,
) -> None:
    research = Research.load(Path(base) / slug)
    report = cross_investigation.report_degraded_support(base, network_observations=observations)
    item = next(candidate for candidate in report.items if candidate.source_id == source_id)
    start.wait()
    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=observations,
        reason=f"reviewed {source_id}",
        by="reviewer",
    )


def _verify_with_paused_save(base: str, slug: str, ready, proceed) -> None:
    research = Research.load(Path(base) / slug)
    real_save = verification_mod.save_ledger

    def paused_save(path, ledger) -> None:
        ready.set()
        assert proceed.wait(10)
        real_save(path, ledger)

    verification_mod.save_ledger = paused_save
    verification_mod.verify_explore_claims(research)


def _acknowledge_and_signal(
    base: str,
    slug: str,
    degradation: cross_investigation.DegradedSupportItem,
    observations: tuple[cross_investigation.NetworkSourceObservation, ...],
    finished,
) -> None:
    research = Research.load(Path(base) / slug)
    cross_investigation.acknowledge_degradation(
        research,
        degradation,
        network_observations=observations,
        reason="reviewed concurrently",
        by="reviewer",
    )
    finished.set()


def _acknowledge_with_locked_commit(
    base: str,
    slug: str,
    degradation: cross_investigation.DegradedSupportItem,
    observations: tuple[cross_investigation.NetworkSourceObservation, ...],
    ready,
    proceed,
) -> None:
    research = Research.load(Path(base) / slug)

    def commit(path: Path):
        ready.set()
        assert proceed.wait(10)
        result = trail.commit_transition(
            research,
            f"acknowledge degradation {degradation.source_id} {degradation.cause}",
            paths=[path],
        )
        assert result.committed
        return result

    cross_investigation.acknowledge_degradation(
        research,
        degradation,
        network_observations=observations,
        reason="first review",
        by="reviewer",
        after_write=commit,
    )


def _acknowledge_with_paused_save(
    base: str,
    slug: str,
    degradation: cross_investigation.DegradedSupportItem,
    observations: tuple[cross_investigation.NetworkSourceObservation, ...],
    ready,
    proceed,
) -> None:
    research = Research.load(Path(base) / slug)
    real_save = cross_investigation.save_ledger

    def paused_save(path, ledger) -> None:
        ready.set()
        assert proceed.wait(10)
        real_save(path, ledger)

    cross_investigation.save_ledger = paused_save
    cross_investigation.acknowledge_degradation(
        research,
        degradation,
        network_observations=observations,
        reason="reviewed concurrently",
        by="reviewer",
    )


def _resolve_and_signal(base: str, slug: str, claim_id: str, finished) -> None:
    research = Research.load(Path(base) / slug)
    verification_mod.resolve_claim(
        research,
        claim_id,
        reason="resolved concurrently",
        by="reviewer",
    )
    finished.set()


def _tree_state(root: Path) -> dict[str, tuple[int, bytes | None]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    }


def _write_sources(research: Research, sources: str) -> None:
    research.artifact_path("notes/sources.md").write_text(
        "---\n"
        f"research: {research.meta.slug}\n"
        "date: 2026-08-03\n"
        "stage: explore\n"
        "sources:\n"
        f"{sources}"
        "---\n\n"
        "## Sources\n",
        encoding="utf-8",
    )


def _write_verified_anchor(research: Research, source_id: str, claim_text: str) -> str:
    note_path = research.artifact_path("notes/sources.md")
    note = note_path.read_text(encoding="utf-8")
    source_url = next(
        str(source["url"])
        for source in parse_artifact(note_path).frontmatter["sources"]
        if source.get("id") == source_id
    )
    sentence = claim_text.rstrip(".")
    note_path.write_text(note + f"\n{sentence} [{source_id}].\n", encoding="utf-8")
    source_dir = research.artifact_path(f"notes/sources/{source_id}")
    source_dir.mkdir(parents=True, exist_ok=True)
    persisted_content = f"{claim_text}\n"
    source_dir.joinpath("content.md").write_text(persisted_content, encoding="utf-8")
    source_dir.joinpath("meta.yaml").write_text(
        "schema_version: 2\n"
        f"url: {source_url}\n"
        f"declared_url: {source_url}\n"
        f"final_url: {source_url}\n"
        "redirects: []\n"
        "http_status: 200\n"
        "captured_at: '2026-08-03T00:00:00+00:00'\n"
        "content_type: text/plain\n"
        "content_eligible: true\n"
        f"content_hash: {hashlib.sha256(persisted_content.encode()).hexdigest()}\n"
        "status: ok\n",
        encoding="utf-8",
    )
    report = verify_explore_claims(research)
    verified = [item for item in report.items if item.source_id == source_id]
    assert len(verified) == 1 and verified[0].state == "verified"
    return verified[0].claim_id


_OMITTED = object()


def _write_decision(
    research: Research,
    evidence_claim_ids: list[str] | object = _OMITTED,
    *,
    prose: str = "Decision based on explicit evidence.",
) -> None:
    lineage = ""
    if evidence_claim_ids is not _OMITTED:
        lineage = (
            "evidence_claim_ids: []\n"
            if not evidence_claim_ids
            else "evidence_claim_ids:\n"
            + "".join(f"  - {claim_id}\n" for claim_id in evidence_claim_ids)
        )
    research.artifact_path("decision-memo.md").write_text(
        "---\n"
        f"research: {research.meta.slug}\n"
        "date: 2026-08-03\n"
        "stage: transfer\n"
        "ring: assess\n"
        "audience: equipo\n"
        f"{lineage}"
        "---\n\n"
        "## Recomendación\n\n"
        f"{prose}\n",
        encoding="utf-8",
    )
    research.meta.validation["transfer"] = lifecycle.stage_hash(research, "transfer")


def _query_source(layer, identity: str):
    return query_source_dependencies(layer, identity)


def _completed_decision(
    base: Path,
    *,
    slug: str,
    source_id: str = "S1",
    url: str = "https://example.com/doc",
    tier: str = "T1",
    source_date: str = "2026-08-03",
) -> tuple[Research, str]:
    research = Research.create(base=base, slug=slug, title=slug, question="q")
    _write_sources(
        research,
        f"  - id: {source_id}\n    url: {url}\n    tier: {tier}\n    date: {source_date}\n",
    )
    claim_id = _write_verified_anchor(research, source_id, f"Support for {slug}.")
    _write_decision(research, [claim_id])
    research.meta.status = "done"
    research.save()
    return research, claim_id


def _retrieved_observation(
    *,
    investigation: str,
    source_id: str = "S1",
    url: str = "https://example.com/doc",
    text: str,
    status_code: int = 200,
    content_type: str = "text/plain",
    retrieved_at: datetime = datetime(2026, 8, 3, tzinfo=UTC),
) -> cross_investigation.NetworkSourceObservation:
    return cross_investigation.NetworkSourceObservation.from_fetch_result(
        investigation=investigation,
        source_id=source_id,
        declared_url=url,
        retrieved_at=retrieved_at,
        fetched=FetchResult(
            declared_url=url,
            final_url=url,
            redirects=(),
            status_code=status_code,
            content_type=content_type,
            content_eligible=True,
            text=text,
        ),
    )


def _unreachable_observation(
    *,
    investigation: str,
    source_id: str = "S1",
    url: str = "https://example.com/doc",
    retrieved_at: datetime = datetime(2026, 8, 3, tzinfo=UTC),
    status_code: int | None = None,
) -> cross_investigation.NetworkSourceObservation:
    return cross_investigation.NetworkSourceObservation.unreachable(
        investigation=investigation,
        source_id=source_id,
        declared_url=url,
        observed_url=url,
        retrieved_at=retrieved_at,
        status_code=status_code,
    )


def test_layer_derives_from_every_research_under_configured_root(tmp_path: Path) -> None:
    Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    (tmp_path / "not-a-research").mkdir()

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.investigations == ("eval-a", "eval-b")


def test_single_investigation_layer_has_zero_joins(tmp_path: Path) -> None:
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.investigations == ("eval-a",)
    assert layer.join_count == 0


def test_empty_root_produces_an_empty_validated_context_graph(tmp_path: Path) -> None:
    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.investigations == ()
    graph = layer.to_context_graph()
    graph.validate()
    assert graph.nodes == []
    assert graph.edges == []


def test_duplicate_metadata_slugs_are_rejected_with_root_qualified_diagnostics(
    tmp_path: Path,
) -> None:
    first = Research.create(base=tmp_path, slug="first-root", title="First", question="q")
    second = Research.create(base=tmp_path, slug="second-root", title="Second", question="q")
    for research in (first, second):
        metadata = yaml.safe_load(research.artifact_path("sdr.yaml").read_text(encoding="utf-8"))
        metadata["slug"] = "duplicate"
        research.artifact_path("sdr.yaml").write_text(
            yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
        )

    with pytest.raises(ValueError) as exc_info:
        derive_cross_investigation_layer(tmp_path)

    message = str(exc_info.value)
    assert "duplicate investigation slug 'duplicate'" in message
    assert "'first-root'" in message
    assert "'second-root'" in message
    assert str(tmp_path) not in message


def test_metadata_slug_must_match_its_root_without_leaking_the_base_path(tmp_path: Path) -> None:
    research = Research.create(base=tmp_path, slug="actual-root", title="Actual", question="q")
    metadata = yaml.safe_load(research.artifact_path("sdr.yaml").read_text(encoding="utf-8"))
    metadata["slug"] = "declared-slug"
    research.artifact_path("sdr.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(ValueError) as exc_info:
        derive_cross_investigation_layer(tmp_path)

    message = str(exc_info.value)
    assert "root 'actual-root' declares slug 'declared-slug'" in message
    assert str(tmp_path) not in message


@pytest.mark.parametrize(
    "source_id",
    ["S/A", "S?A", "S\u00c1", "../private", "/" + "home/alice/key"],
)
def test_noncanonical_source_ids_are_rejected_without_echoing_private_values(
    tmp_path: Path, source_id: str
) -> None:
    research = Research.create(base=tmp_path, slug="source-contract", title="Source", question="q")
    _write_sources(
        research,
        f"  - id: {source_id!r}\n    url: https://example.com/doc\n",
    )

    with pytest.raises(ValueError) as exc_info:
        derive_cross_investigation_layer(tmp_path)

    message = str(exc_info.value)
    assert "source-contract" in message
    assert "expected S<n>" in message
    assert source_id not in message
    assert str(tmp_path) not in message


def test_local_path_source_url_is_rejected_before_work_identifier_resolution(
    tmp_path: Path,
) -> None:
    private_path = "/" + "home/alice/private-paper.pdf"
    research = Research.create(base=tmp_path, slug="source-contract", title="Source", question="q")
    _write_sources(
        research,
        f"  - id: S1\n    url: {private_path}\n    doi: 10.1000/example\n",
    )

    with pytest.raises(ValueError) as exc_info:
        derive_cross_investigation_layer(tmp_path)

    message = str(exc_info.value)
    assert "invalid source URL" in message
    assert "source-contract:S1" in message
    assert private_path not in message
    assert str(tmp_path) not in message


def test_kb_graph_ids_do_not_collide_after_lossy_sanitization() -> None:
    sources = tuple(
        SourceResolution(
            investigation="alpha",
            source_id=source_id,
            url=f"https://example.com/{index}",
            identity=f"identity:{index}",
            resolver="test",
            identifier=f"identity:{index}",
        )
        for index, source_id in enumerate(("S/A", "S?A", "S\u00c1", "S-A"), start=1)
    )
    layer = CrossInvestigationLayer(investigations=("alpha",), sources=sources)

    graph = layer.to_context_graph()

    graph.validate()
    source_nodes = [node for node in graph.nodes if node.type == "source"]
    assert len(source_nodes) == 4
    assert len({node.id for node in source_nodes}) == 4
    assert len([edge for edge in graph.edges if edge.relation == "cites"]) == 4


def test_recomputing_unchanged_layer_is_identical(tmp_path: Path) -> None:
    Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")

    first = derive_cross_investigation_layer(tmp_path).to_dict()
    expected = first.copy()
    del first
    recomputed = derive_cross_investigation_layer(tmp_path).to_dict()

    assert recomputed == expected


def test_derivation_writes_nothing_inside_investigations(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    before = {research.meta.slug: _tree_state(research.root) for research in (first, second)}

    derive_cross_investigation_layer(tmp_path)

    after = {research.meta.slug: _tree_state(research.root) for research in (first, second)}
    assert after == before


def test_accumulated_graph_uses_validated_context_types_and_explicit_qualified_provenance(
    tmp_path: Path,
) -> None:
    first = Research.create(base=tmp_path, slug="alpha", title="Alpha", question="q")
    second = Research.create(base=tmp_path, slug="beta", title="Beta", question="q")
    _write_sources(first, "  - id: S1\n    url: https://example.com/doc\n")
    _write_sources(second, "  - id: S9\n    url: https://example.com/doc\n")
    first_claim = _write_verified_anchor(first, "S1", "Shared explicit sentence.")
    second_claim = _write_verified_anchor(second, "S9", "Shared explicit sentence.")
    _write_decision(first, [first_claim])
    _write_decision(second, [second_claim])
    for research in (first, second):
        research.meta.status = "done"
        research.save()

    graph = derive_cross_investigation_layer(tmp_path).to_context_graph()

    assert isinstance(graph, ContextGraph)
    assert all(isinstance(node, GraphNode) for node in graph.nodes)
    assert all(isinstance(edge, GraphEdge) for edge in graph.edges)
    graph.validate()
    assert all(edge.provenance == "explicit" for edge in graph.edges)
    assert all(edge.metadata.get("origin") for edge in graph.edges)
    assert {node.metadata.get("investigation") for node in graph.nodes} == {"alpha", "beta"}
    assert all(
        node.id.startswith(f"{node.type}:{node.metadata['investigation']}") for node in graph.nodes
    )
    assert str(tmp_path) not in graph.to_json()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.example.com/doc/",
        "https://example.com/doc",
        "https://EXAMPLE.COM/doc/",
        "https://www.example.com/doc?utm_source=newsletter",
        "https://example.com/doc#section",
        "http://example.com/doc/?utm_medium=email#section",
    ],
)
def test_url_normalization_resolves_equivalent_locations_to_one_identity(url: str) -> None:
    assert normalize_url(url) == "https://example.com/doc"


@pytest.mark.parametrize(
    ("url", "metadata", "identifier"),
    [
        ("https://doi.org/10.1000/XYZ", {}, "doi:10.1000/xyz"),
        ("https://arxiv.org/abs/2101.00001v2", {}, "arxiv:2101.00001v2"),
        ("https://pubmed.ncbi.nlm.nih.gov/12345678/", {}, "pmid:12345678"),
        (
            "https://books.example/item",
            {"isbn": "978-0-306-40615-7"},
            "isbn:9780306406157",
        ),
    ],
)
def test_work_identifiers_take_precedence_over_url(
    url: str, metadata: dict[str, object], identifier: str
) -> None:
    resolved = resolve_source_identity(url, metadata)

    assert resolved.identity == identifier
    assert resolved.identifier == identifier
    assert resolved.resolver == "work-identifier"


def test_unrecognized_partial_identifier_falls_back_without_merging(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(
        first,
        "  - id: S1\n"
        "    url: https://example.com/papers/mentions-10.1000/xyz\n"
        "    description: DOI 10.1000/xyz\n",
    )
    _write_sources(
        second,
        "  - id: S1\n"
        "    url: https://other.example/papers/mentions-10.1000/xyz\n"
        "    doi: prefix-10.1000/xyz\n",
    )

    layer = derive_cross_investigation_layer(tmp_path)

    assert {source.resolver for source in layer.sources} == {"normalized-url"}
    assert layer.source_merges == ()


def test_source_merge_names_resolver_and_identifier(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(
        first,
        "  - id: S1\n    url: https://doi.org/10.1000/XYZ\n",
    )
    _write_sources(
        second,
        "  - id: S9\n    url: https://journal.example/article\n    doi: 10.1000/xyz\n",
    )

    layer = derive_cross_investigation_layer(tmp_path)

    assert len(layer.source_merges) == 1
    assert layer.source_merges[0].resolver == "work-identifier"
    assert layer.source_merges[0].identifier == "doi:10.1000/xyz"


def test_one_ordered_chain_handles_a_mixed_domain_root(tmp_path: Path) -> None:
    scholarly = Research.create(base=tmp_path, slug="biology", title="Biology", question="q")
    software = Research.create(base=tmp_path, slug="engineering", title="Engineering", question="q")
    _write_sources(
        scholarly,
        "  - id: S1\n    url: https://pubmed.ncbi.nlm.nih.gov/12345678/\n",
    )
    _write_sources(
        software,
        "  - id: S1\n    url: https://www.github.com/example/project/?utm_source=x\n",
    )

    layer = derive_cross_investigation_layer(tmp_path)

    assert [(source.investigation, source.identity) for source in layer.sources] == [
        ("biology", "pmid:12345678"),
        ("engineering", "https://github.com/example/project"),
    ]


def test_layer_records_resolver_chain_and_versions(tmp_path: Path) -> None:
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.to_dict()["resolver_chain"] == [
        {"name": "work-identifier", "version": "1"},
        {"name": "normalized-url", "version": "1"},
    ]


def test_layers_from_different_resolver_chains_refuse_comparison(tmp_path: Path) -> None:
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    default_layer = derive_cross_investigation_layer(tmp_path)
    url_only_layer = derive_cross_investigation_layer(
        tmp_path, resolver_chain=(NORMALIZED_URL_RESOLVER,)
    )

    with pytest.raises(ValueError, match="resolver chains differ"):
        compare_cross_investigation_layers(default_layer, url_only_layer)


def test_resolved_source_identity_joins_investigations_and_names_resolver(
    tmp_path: Path,
) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(first, "  - id: S1\n    url: http://www.example.com/doc/\n")
    _write_sources(
        second,
        "  - id: S9\n    url: https://example.com/doc?utm_source=test\n",
    )

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.joins == (
        {
            "kind": "source_identity",
            "investigations": ["eval-a", "eval-b"],
            "provenance": "explicit",
            "resolver": "normalized-url",
            "identifier": "https://example.com/doc",
            "origins": ["eval-a:S1", "eval-b:S9"],
        },
    )


def test_shared_topic_without_resolved_source_or_claim_produces_no_join(tmp_path: Path) -> None:
    first = Research.create(
        base=tmp_path,
        slug="eval-a",
        title="Shared topic",
        question="same question",
        tags=["shared-topic"],
    )
    second = Research.create(
        base=tmp_path,
        slug="eval-b",
        title="Shared topic",
        question="same question",
        tags=["shared-topic"],
    )
    _write_sources(first, "  - id: S1\n    url: https://example.com/first\n")
    _write_sources(second, "  - id: S1\n    url: https://example.com/second\n")

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.joins == ()


def test_same_normalized_anchor_joins_distinctly_from_source_identity(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(first, "  - id: S1\n    url: https://example.com/doc\n")
    _write_sources(second, "  - id: S9\n    url: https://example.com/doc\n")
    first_claim = _write_verified_anchor(first, "S1", "The API is stable.")
    second_claim = _write_verified_anchor(second, "S9", "THE API  is stable.")

    layer = derive_cross_investigation_layer(tmp_path)

    assert [join["kind"] for join in layer.joins] == ["anchored_claim", "source_identity"]
    claim_join = layer.joins[0]
    assert claim_join["normalized_sentence"] == "the api is stable."
    assert claim_join["origins"] == [f"eval-a:{first_claim}", f"eval-b:{second_claim}"]
    assert claim_join["provenance"] == "explicit"


def test_same_anchor_in_different_resolved_sources_reports_quote_propagation(
    tmp_path: Path,
) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(first, "  - id: S1\n    url: https://first.example/report\n")
    _write_sources(second, "  - id: S7\n    url: https://second.example/report\n")
    _write_verified_anchor(first, "S1", "The measured reduction was 42 percent.")
    _write_verified_anchor(second, "S7", "The measured reduction was 42 percent.")

    layer = derive_cross_investigation_layer(tmp_path)

    assert len(layer.joins) == 1
    join = layer.joins[0]
    assert join["kind"] == "quote_propagation"
    assert join["source_identities"] == [
        "https://first.example/report",
        "https://second.example/report",
    ]
    assert join["sources"] == ["eval-a:S1", "eval-b:S7"]


def test_every_join_has_explicit_provenance_and_exact_origin(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    _write_sources(first, "  - id: S1\n    url: https://example.com/doc\n")
    _write_sources(second, "  - id: S2\n    url: https://example.com/doc\n")
    first_claim = _write_verified_anchor(first, "S1", "Exact anchored sentence.")
    second_claim = _write_verified_anchor(second, "S2", "Exact anchored sentence.")

    layer = derive_cross_investigation_layer(tmp_path)

    assert all(join["provenance"] == "explicit" for join in layer.joins)
    assert {tuple(join["origins"]) for join in layer.joins} == {
        (f"eval-a:{first_claim}", f"eval-b:{second_claim}"),
        ("eval-a:S1", "eval-b:S2"),
    }


def test_similarity_ranking_and_model_metadata_never_produce_joins(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    second = Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q")
    metadata = "    similarity: 1.0\n    rank: 1\n    model_match: shared-result\n"
    _write_sources(
        first,
        "  - id: S1\n    url: https://first.example/report\n" + metadata,
    )
    _write_sources(
        second,
        "  - id: S1\n    url: https://second.example/report\n" + metadata,
    )
    _write_verified_anchor(first, "S1", "The API is stable for production.")
    _write_verified_anchor(second, "S1", "The API appears stable in production.")

    layer = derive_cross_investigation_layer(tmp_path)

    assert layer.joins == ()


def test_legacy_omission_and_explicit_empty_lineage_remain_distinct(tmp_path: Path) -> None:
    legacy = Research.create(base=tmp_path, slug="legacy", title="Legacy", question="q")
    empty = Research.create(base=tmp_path, slug="empty", title="Empty", question="q")
    _write_sources(legacy, "  - id: S1\n    url: https://example.com/doc\n")
    _write_sources(empty, "  - id: S1\n    url: https://example.com/doc\n")
    _write_decision(legacy)
    _write_decision(empty, [])
    for research in (legacy, empty):
        research.meta.status = "done"
        research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("empty", "no declared claim dependencies"),
        ("legacy", "lineage unavailable"),
    ]


def test_tampered_done_decision_ids_are_invalid_and_emit_no_dependency(tmp_path: Path) -> None:
    research, claim_id = _completed_decision(tmp_path, slug="tampered")
    memo = research.artifact_path("decision-memo.md")
    memo.write_text(
        memo.read_text(encoding="utf-8").replace(claim_id, "claim-" + "f" * 64),
        encoding="utf-8",
    )

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("tampered", "lineage invalid: transfer validation inconsistent")
    ]
    assert result.dependent_decisions == ()


def test_done_decision_without_transfer_hash_is_unavailable_and_does_not_abort_others(
    tmp_path: Path,
) -> None:
    missing, _ = _completed_decision(tmp_path, slug="missing")
    valid, valid_claim = _completed_decision(tmp_path, slug="valid")
    missing.meta.validation.pop("transfer")
    missing.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("missing", "lineage unavailable: transfer validation missing"),
        ("valid", "declared claim dependencies"),
    ]
    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("valid", valid_claim)
    ]
    assert valid.meta.validation["transfer"] == lifecycle.stage_hash(valid, "transfer")


def test_deleted_validated_done_decision_is_invalid_and_does_not_abort_others(
    tmp_path: Path,
) -> None:
    deleted, _ = _completed_decision(tmp_path, slug="deleted")
    valid, valid_claim = _completed_decision(tmp_path, slug="valid")
    deleted.artifact_path("decision-memo.md").unlink()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("deleted", "lineage invalid: transfer validation inconsistent"),
        ("valid", "declared claim dependencies"),
    ]
    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("valid", valid_claim)
    ]


def test_done_investigation_missing_never_validated_decision_has_no_lineage_record(
    tmp_path: Path,
) -> None:
    missing = Research.create(base=tmp_path, slug="missing", title="Missing", question="q")
    _write_sources(missing, "  - id: S1\n    url: https://example.com/doc\n")
    missing.meta.status = "done"
    missing.save()
    valid, valid_claim = _completed_decision(tmp_path, slug="valid")

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("valid", "declared claim dependencies")
    ]
    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("valid", valid_claim)
    ]


@pytest.mark.parametrize("health", ["stale", "not_anchored", "unverifiable"])
def test_degraded_current_health_never_erases_historical_decision_lineage(
    tmp_path: Path, health: str
) -> None:
    research = Research.create(base=tmp_path, slug="completed", title="Done", question="q")
    _write_sources(research, "  - id: S1\n    url: https://example.com/doc\n")
    claim_id = _write_verified_anchor(research, "S1", "The API is stable.")
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    persisted = ledger_path.read_bytes()
    _write_decision(research, [claim_id])
    transfer_hash = research.meta.validation["transfer"]
    research.meta.status = "done"
    research.save()
    if health == "stale":
        note_path = research.artifact_path("notes/sources.md")
        note_path.write_text(
            note_path.read_text(encoding="utf-8").replace("\nThe API is stable [S1].\n", "\n"),
            encoding="utf-8",
        )
    elif health == "not_anchored":
        content = "The API has no stability guarantee."
        research.artifact_path("notes/sources/S1/content.md").write_text(content, encoding="utf-8")
        meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
        metadata = meta_path.read_text(encoding="utf-8")
        old_hash = hashlib.sha256(b"The API is stable.\n").hexdigest()
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        meta_path.write_text(metadata.replace(old_hash, new_hash), encoding="utf-8")
    else:
        research.artifact_path("notes/sources/S1/meta.yaml").write_text(
            "url: https://example.com/doc\nstatus: missing\n", encoding="utf-8"
        )

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("completed", claim_id)
    ]
    assert lifecycle.stage_hash(research, "transfer") == transfer_hash
    assert [(item.claim_id, item.current_evidence_health) for item in result.claims] == [
        (claim_id, health)
    ]
    assert ledger_path.read_bytes() == persisted


def test_decision_dependencies_are_never_inferred_from_prose_or_metadata(tmp_path: Path) -> None:
    research = Research.create(base=tmp_path, slug="completed", title="Done", question="q")
    _write_sources(
        research,
        "  - id: S1\n"
        "    url: https://example.com/doc\n"
        "    similarity: 1.0\n"
        "    model_match: decision-memo.md\n",
    )
    claim_id = _write_verified_anchor(research, "S1", "The API is stable.")
    _write_decision(research, [], prose=f"Depends on {claim_id} and S1.")
    research.meta.status = "done"
    research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert result.dependent_decisions == ()


def test_source_query_excludes_never_anchored_claim_without_historical_lineage(
    tmp_path: Path,
) -> None:
    research = Research.create(base=tmp_path, slug="active", title="Active", question="q")
    _write_sources(research, "  - id: S1\n    url: https://example.com/doc\n")
    claim_id = _write_verified_anchor(research, "S1", "The API might be stable.")
    research.artifact_path("notes/sources/S1/content.md").write_text(
        "No stability statement appears here.", encoding="utf-8"
    )

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert all(item.claim_id != claim_id for item in result.claims)


def test_generic_stale_history_is_not_previously_anchored_without_exact_lineage(
    tmp_path: Path,
) -> None:
    research = Research.create(base=tmp_path, slug="completed", title="Done", question="q")
    _write_sources(research, "  - id: S1\n    url: https://example.com/doc\n")
    claim_id = _write_verified_anchor(research, "S1", "The API is stable.")
    note_path = research.artifact_path("notes/sources.md")
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace("\nThe API is stable [S1].\n", "\n"),
        encoding="utf-8",
    )
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(ledger_path)
    ledger["claims"][0]["state"] = "stale"
    save_ledger(ledger_path, ledger)
    _write_decision(research, [])
    research.meta.status = "done"
    research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert all(item.claim_id != claim_id for item in result.claims)
    assert result.dependent_decisions == ()


def test_source_query_reports_citations_claims_and_explicit_completed_decisions(
    tmp_path: Path,
) -> None:
    first = Research.create(base=tmp_path, slug="alpha", title="Alpha", question="q")
    second = Research.create(base=tmp_path, slug="beta", title="Beta", question="q")
    _write_sources(first, "  - id: S1\n    url: https://example.com/doc\n")
    _write_sources(second, "  - id: S9\n    url: http://www.example.com/doc/\n")
    first_claim = _write_verified_anchor(first, "S1", "The API is stable.")
    second_claim = _write_verified_anchor(second, "S9", "Latency is bounded.")
    _write_decision(first, [first_claim])
    _write_decision(second, [second_claim])
    for research in (first, second):
        research.meta.status = "done"
        research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [(item.investigation, item.source_id) for item in result.citations] == [
        ("alpha", "S1"),
        ("beta", "S9"),
    ]
    assert [(item.investigation, item.claim_id) for item in result.claims] == [
        ("alpha", first_claim),
        ("beta", second_claim),
    ]
    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("alpha", first_claim),
        ("beta", second_claim),
    ]


def test_source_query_lineage_statuses_exclude_unrelated_completed_investigations(
    tmp_path: Path,
) -> None:
    target = Research.create(base=tmp_path, slug="target", title="Target", question="q")
    citation_only = Research.create(
        base=tmp_path, slug="citation-only", title="Citation", question="q"
    )
    unrelated = Research.create(base=tmp_path, slug="unrelated", title="Other", question="q")
    _write_sources(
        target,
        "  - id: S1\n    url: https://example.com/target\n"
        "  - id: S2\n    url: https://example.com/other\n",
    )
    _write_sources(citation_only, "  - id: S1\n    url: https://example.com/target\n")
    _write_sources(unrelated, "  - id: S1\n    url: https://unrelated.example/doc\n")
    target_claim = _write_verified_anchor(target, "S1", "Target support is stable.")
    other_claim = _write_verified_anchor(target, "S2", "Other support is stable.")
    _write_decision(target, [other_claim, target_claim])
    _write_decision(citation_only, [])
    _write_decision(unrelated)
    for research in (target, citation_only, unrelated):
        research.meta.status = "done"
        research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/target")

    assert [(item.investigation, item.status) for item in result.lineage_statuses] == [
        ("citation-only", "no declared claim dependencies"),
        ("target", "declared claim dependencies"),
    ]
    assert [(item.investigation, item.claim_id) for item in result.dependent_decisions] == [
        ("target", target_claim)
    ]


def test_source_query_order_is_stable_when_layer_inputs_are_reversed(tmp_path: Path) -> None:
    first = Research.create(base=tmp_path, slug="alpha", title="Alpha", question="q")
    second = Research.create(base=tmp_path, slug="beta", title="Beta", question="q")
    for research, source_id in ((first, "S1"), (second, "S9")):
        _write_sources(
            research,
            f"  - id: {source_id}\n    url: https://example.com/doc\n",
        )
        claim_id = _write_verified_anchor(research, source_id, f"Claim for {research.meta.slug}.")
        _write_decision(research, [claim_id])
        research.meta.status = "done"
        research.save()
    layer = derive_cross_investigation_layer(tmp_path)
    reversed_layer = replace(
        layer,
        sources=tuple(reversed(layer.sources)),
        evidence_claims=tuple(reversed(layer.evidence_claims)),
        completed_decisions=tuple(reversed(layer.completed_decisions)),
    )

    expected = query_source_dependencies(layer, "https://example.com/doc").to_dict()
    actual = query_source_dependencies(reversed_layer, "https://example.com/doc").to_dict()

    assert actual == expected


def test_every_source_query_item_and_lineage_status_names_its_investigation(
    tmp_path: Path,
) -> None:
    research = Research.create(base=tmp_path, slug="qualified", title="Q", question="q")
    _write_sources(research, "  - id: S1\n    url: https://example.com/doc\n")
    claim_id = _write_verified_anchor(research, "S1", "The API is stable.")
    _write_decision(research, [claim_id])
    research.meta.status = "done"
    research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    for collection in (
        result.citations,
        result.claims,
        result.lineage_statuses,
        result.dependent_decisions,
    ):
        assert collection
        assert all(item.investigation == "qualified" for item in collection)


def test_only_exact_done_status_qualifies_dependent_decisions(tmp_path: Path) -> None:
    cases = (
        ("done", "done", False),
        ("active", "active", False),
        ("reopened", "active", True),
        ("dropped", "dropped", False),
    )
    for slug, status, reopened in cases:
        research = Research.create(base=tmp_path, slug=slug, title=slug, question="q")
        _write_sources(research, "  - id: S1\n    url: https://example.com/doc\n")
        claim_id = _write_verified_anchor(research, "S1", f"Claim for {slug}.")
        _write_decision(research, [claim_id])
        research.meta.status = status
        if reopened:
            research.meta.reopens = [
                Reopen(
                    from_stage="reuse",
                    to_stage="transfer",
                    reason="review",
                    date="2026-08-03",
                )
            ]
        research.save()

    result = _query_source(derive_cross_investigation_layer(tmp_path), "https://example.com/doc")

    assert [item.investigation for item in result.dependent_decisions] == ["done"]


def test_unreachable_source_reports_dependent_completed_decision_with_distinct_cause(
    tmp_path: Path,
) -> None:
    _, claim_id = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )

    assert [item.to_dict() for item in report.items] == [
        {
            "source_investigation": "completed",
            "source_id": "S1",
            "source_identity": "https://example.com/doc",
            "cause": "unreachable",
            "observation_source": "network",
            "mechanical_fact": "retrieval_failed",
            "observation_id": report.items[0].observation_id,
            "decision_investigation": "completed",
            "claim_id": claim_id,
        }
    ]


def test_live_content_hash_mismatch_reports_changed_not_local_snapshot_mismatch(
    tmp_path: Path,
) -> None:
    _, claim_id = _completed_decision(tmp_path, slug="completed")
    observation = _retrieved_observation(
        investigation="completed",
        text="Changed live source.",
    )

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )

    assert [(item.cause, item.observation_source, item.claim_id) for item in report.items] == [
        ("changed", "network", claim_id)
    ]
    assert next(fact for fact in report.facts if fact.cause == "changed").mechanical_fact == (
        "live_content_hash_mismatch"
    )


def test_expired_source_is_reported_only_when_requested_with_explicit_as_of(
    tmp_path: Path,
) -> None:
    _, claim_id = _completed_decision(
        tmp_path,
        slug="completed",
        tier="T3",
        source_date="2025-01-01",
    )

    report = cross_investigation.report_degraded_support(
        tmp_path,
        include_expiry=True,
        as_of=date(2026, 8, 3),
    )

    assert [(item.cause, item.observation_source, item.claim_id) for item in report.items] == [
        ("expired", "tier_policy", claim_id)
    ]
    assert next(fact for fact in report.facts if fact.cause == "expired").mechanical_fact == (
        "tier_expiry_exceeded"
    )


def test_degradation_causes_are_separate_records_never_one_collapsed_flag(tmp_path: Path) -> None:
    _completed_decision(
        tmp_path, slug="changed", url="https://example.com/changed", source_date="2026-08-03"
    )
    _completed_decision(
        tmp_path,
        slug="expired",
        url="https://example.com/expired",
        tier="T3",
        source_date="2025-01-01",
    )
    _completed_decision(
        tmp_path,
        slug="unreachable",
        url="https://example.com/unreachable",
        source_date="2026-08-03",
    )
    observations = (
        _retrieved_observation(
            investigation="changed",
            url="https://example.com/changed",
            text="Changed live source.",
        ),
        _unreachable_observation(
            investigation="unreachable", url="https://example.com/unreachable"
        ),
    )

    report = cross_investigation.report_degraded_support(
        tmp_path,
        network_observations=observations,
        include_expiry=True,
        as_of=date(2026, 8, 3),
    )

    assert [item.cause for item in report.items] == ["changed", "expired", "unreachable"]
    assert all("degraded" not in item.to_dict() for item in report.items)


def test_default_degraded_support_report_includes_events_and_excludes_expiry(
    tmp_path: Path,
) -> None:
    _completed_decision(
        tmp_path, slug="changed", url="https://example.com/changed", source_date="2026-08-03"
    )
    _completed_decision(
        tmp_path,
        slug="expired",
        url="https://example.com/expired",
        tier="T3",
        source_date="2025-01-01",
    )
    _completed_decision(
        tmp_path,
        slug="unreachable",
        url="https://example.com/unreachable",
        source_date="2026-08-03",
    )
    observations = (
        _retrieved_observation(
            investigation="changed",
            url="https://example.com/changed",
            text="Changed live source.",
        ),
        _unreachable_observation(
            investigation="unreachable", url="https://example.com/unreachable"
        ),
    )

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    )

    assert [item.cause for item in report.items] == ["changed", "unreachable"]
    assert "expired" not in {fact.cause for fact in report.facts}


def test_report_keeps_offline_snapshot_mismatch_separate_and_states_only_mechanical_facts(
    tmp_path: Path,
) -> None:
    research, claim_id = _completed_decision(tmp_path, slug="completed")
    research.artifact_path("notes/sources/S1/content.md").write_text(
        "Locally edited snapshot.", encoding="utf-8"
    )

    report = cross_investigation.report_degraded_support(tmp_path)
    rendered = report.to_dict()

    assert report.items == ()
    assert [fact.to_dict() for fact in report.facts] == [
        {
            "source_investigation": "completed",
            "source_id": "S1",
            "source_identity": "https://example.com/doc",
            "cause": None,
            "observation_source": "local_snapshot",
            "reachability": None,
            "mechanical_fact": "local_snapshot_hash_mismatch",
            "observation_id": report.facts[0].observation_id,
            "observation_timestamp": None,
            "observation_state": None,
        },
        {
            "source_investigation": "completed",
            "source_id": "S1",
            "source_identity": "https://example.com/doc",
            "cause": None,
            "observation_source": "network",
            "reachability": "not checked",
            "mechanical_fact": "reachability_not_checked",
            "observation_id": report.facts[1].observation_id,
            "observation_timestamp": None,
            "observation_state": None,
        },
    ]
    assert claim_id in {
        decision.claim_id
        for decision in query_source_dependencies(
            derive_cross_investigation_layer(tmp_path), "https://example.com/doc"
        ).dependent_decisions
    }
    serialized = str(rendered).lower()
    for characterization in ("conclusion", "valid", "invalid", "sound", "unsound"):
        assert characterization not in serialized


def test_same_identity_conflicting_observations_affect_only_exact_source_lineage(
    tmp_path: Path,
) -> None:
    _, first_claim = _completed_decision(
        tmp_path, slug="alpha", source_id="S1", url="http://www.example.com/doc/"
    )
    _, second_claim = _completed_decision(
        tmp_path, slug="beta", source_id="S9", url="https://example.com/doc?utm_source=x"
    )
    observations = (
        _unreachable_observation(
            investigation="alpha", source_id="S1", url="http://www.example.com/doc/"
        ),
        _retrieved_observation(
            investigation="beta",
            source_id="S9",
            url="https://example.com/doc?utm_source=x",
            text="Support for beta.",
        ),
    )

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    )

    assert [
        (item.source_investigation, item.decision_investigation, item.claim_id)
        for item in report.items
    ] == [("alpha", "alpha", first_claim)]
    assert all(item.claim_id != second_claim for item in report.items)


def test_shared_identity_expiry_stays_with_old_t3_not_fresh_t1_citation(tmp_path: Path) -> None:
    _, old_claim = _completed_decision(
        tmp_path,
        slug="old",
        source_id="S3",
        url="http://www.example.com/doc/",
        tier="T3",
        source_date="2025-01-01",
    )
    _, fresh_claim = _completed_decision(
        tmp_path,
        slug="fresh",
        source_id="S1",
        url="https://example.com/doc?utm_source=x",
        tier="T1",
        source_date="2026-08-01",
    )

    report = cross_investigation.report_degraded_support(
        tmp_path, include_expiry=True, as_of=date(2026, 8, 3)
    )

    assert [
        (item.source_investigation, item.decision_investigation, item.claim_id)
        for item in report.items
    ] == [("old", "old", old_claim)]
    assert all(item.claim_id != fresh_claim for item in report.items)


def test_one_decision_with_multiple_sources_receives_only_each_sources_exact_causes(
    tmp_path: Path,
) -> None:
    research = Research.create(base=tmp_path, slug="completed", title="Done", question="q")
    _write_sources(
        research,
        "  - id: S1\n    url: https://example.com/one\n    tier: T1\n    date: 2026-08-03\n"
        "  - id: S2\n    url: https://example.com/two\n    tier: T1\n    date: 2026-08-03\n",
    )
    first_claim = _write_verified_anchor(research, "S1", "First support.")
    second_claim = _write_verified_anchor(research, "S2", "Second support.")
    _write_decision(research, [second_claim, first_claim])
    research.meta.status = "done"
    research.save()

    report = cross_investigation.report_degraded_support(
        tmp_path,
        network_observations=(
            _unreachable_observation(
                investigation="completed", source_id="S1", url="https://example.com/one"
            ),
            _retrieved_observation(
                investigation="completed",
                source_id="S2",
                url="https://example.com/two",
                text="Changed second support.",
            ),
        ),
    )

    assert [(item.source_id, item.cause, item.claim_id) for item in report.items] == [
        ("S1", "unreachable", first_claim),
        ("S2", "changed", second_claim),
    ]


def test_same_source_reports_multiple_causes_as_separate_exact_lineage_items(
    tmp_path: Path,
) -> None:
    _, claim_id = _completed_decision(
        tmp_path, slug="completed", tier="T3", source_date="2025-01-01"
    )

    report = cross_investigation.report_degraded_support(
        tmp_path,
        network_observations=(
            _retrieved_observation(investigation="completed", text="Changed live source."),
        ),
        include_expiry=True,
        as_of=date(2026, 8, 3),
    )

    assert [(item.cause, item.claim_id) for item in report.items] == [
        ("changed", claim_id),
        ("expired", claim_id),
    ]


@pytest.mark.parametrize(
    ("snapshot_state", "mechanical_fact"),
    [("missing", "snapshot_missing"), ("incomplete_legacy", "snapshot_incomplete")],
)
def test_missing_or_incomplete_snapshot_is_mechanical_without_fabricated_live_state(
    tmp_path: Path, snapshot_state: str, mechanical_fact: str
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    source_dir = research.artifact_path("notes/sources/S1")
    if snapshot_state == "missing":
        source_dir.joinpath("meta.yaml").unlink()
        source_dir.joinpath("content.md").unlink()
    else:
        source_dir.joinpath("meta.yaml").write_text(
            "url: https://example.com/doc\nstatus: ok\n", encoding="utf-8"
        )

    report = cross_investigation.report_degraded_support(tmp_path)

    assert report.items == ()
    assert mechanical_fact in {fact.mechanical_fact for fact in report.facts}
    assert "changed" not in {fact.cause for fact in report.facts}
    assert "reachable" not in {fact.mechanical_fact for fact in report.facts}


def test_custom_source_max_age_controls_exact_source_expiry(tmp_path: Path) -> None:
    research, claim_id = _completed_decision(
        tmp_path, slug="completed", tier="T1", source_date="2026-07-01"
    )
    brief = research.artifact_path("brief.md")
    brief.write_text(
        "---\n"
        "research: completed\n"
        "date: 2026-08-03\n"
        "stage: intake\n"
        "source_max_age:\n"
        "  T1: 30\n"
        "---\n",
        encoding="utf-8",
    )

    report = cross_investigation.report_degraded_support(
        tmp_path, include_expiry=True, as_of=date(2026, 8, 3)
    )

    assert [(item.cause, item.claim_id) for item in report.items] == [("expired", claim_id)]


def test_retrieved_observation_uses_snapshot_canonicalization_for_comparable_content(
    tmp_path: Path,
) -> None:
    _completed_decision(tmp_path, slug="completed")
    observation = _retrieved_observation(investigation="completed", text="Support for completed.")

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )

    assert observation.content_hash == hashlib.sha256(b"Support for completed.\n").hexdigest()
    assert report.items == ()
    assert observation.declared_url == "https://example.com/doc"
    assert observation.observed_url == "https://example.com/doc"
    assert observation.retrieved_at == datetime(2026, 8, 3, tzinfo=UTC)
    assert observation.status_code == 200
    assert observation.content_eligible is True


@pytest.mark.parametrize("mismatch", ["source", "url", "ineligible"])
def test_report_rejects_mismatched_or_ineligible_network_observation(
    tmp_path: Path, mismatch: str
) -> None:
    _completed_decision(tmp_path, slug="completed")
    if mismatch == "source":
        observation = _unreachable_observation(investigation="completed", source_id="S9")
    elif mismatch == "url":
        observation = _unreachable_observation(
            investigation="completed", url="https://wrong.example/doc"
        )
    else:
        with pytest.raises(ValueError, match="eligible"):
            _retrieved_observation(
                investigation="completed",
                text="binary",
                content_type="application/octet-stream",
            )
        return

    with pytest.raises(ValueError, match="source record|declared URL"):
        cross_investigation.report_degraded_support(tmp_path, network_observations=(observation,))


def test_degraded_support_report_modifies_no_artifact_metadata_or_status(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    before_tree = _tree_state(tmp_path)
    before_metadata = research.meta.model_dump(mode="json")

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )

    assert [item.cause for item in report.items] == ["unreachable"]
    assert _tree_state(tmp_path) == before_tree
    assert research.meta.model_dump(mode="json") == before_metadata
    assert Research.load(research.root).meta.status == "done"


def test_degraded_support_report_does_not_block_lifecycle_completion(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    research.meta.stage = "reuse"
    research.artifact_path("assets/review.md").write_text(
        "---\n"
        "research: completed\n"
        "date: 2026-08-03\n"
        "stage: reuse\n"
        "type: post\n"
        "audience: external\n"
        "---\n\n"
        "Reviewed output.\n",
        encoding="utf-8",
    )
    research.save()
    observation = _unreachable_observation(investigation="completed")

    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )
    result = lifecycle.advance(research, offline=True)

    assert report.items and report.items[0].cause == "unreachable"
    assert result.ok, result.blocked_reason
    assert research.meta.stage == "reuse"
    assert research.meta.status == "done"


@pytest.mark.parametrize(
    ("reason", "author", "message"),
    [("   ", "reviewer", "reason"), ("reviewed", "   ", "author")],
)
def test_acknowledgement_requires_nonempty_reason_and_author_without_writing(
    tmp_path: Path, reason: str, author: str, message: str
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]
    before = _tree_state(research.root)

    with pytest.raises(ValueError, match=message):
        cross_investigation.acknowledge_degradation(research, item, reason=reason, by=author)

    assert _tree_state(research.root) == before


def test_acknowledgement_is_an_additive_event_in_affected_investigation_trail(
    tmp_path: Path,
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]
    decision_before = research.artifact_path("decision-memo.md").read_bytes()
    sources_before = research.artifact_path("notes/sources.md").read_bytes()

    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=(observation,),
        reason="  reviewed with the owner  ",
        by=" Reviewer ",
    )

    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert ledger["degradation_acknowledgements"] == [
        {
            "acknowledgement_id": ledger["degradation_acknowledgements"][0]["acknowledgement_id"],
            "source_investigation": "completed",
            "source_id": "S1",
            "cause": "unreachable",
            "observation_id": item.observation_id,
            "reason": "reviewed with the owner",
            "by": "Reviewer",
            "date": date.today().isoformat(),
        }
    ]
    assert ledger["degradation_acknowledgements"][0]["acknowledgement_id"].startswith(
        "degradation-ack-"
    )
    assert research.artifact_path("decision-memo.md").read_bytes() == decision_before
    assert research.artifact_path("notes/sources.md").read_bytes() == sources_before


def test_acknowledged_exact_degradation_is_not_reported_again(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _retrieved_observation(investigation="completed", text="Changed live source.")
    first = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )

    cross_investigation.acknowledge_degradation(
        research,
        first.items[0],
        network_observations=(observation,),
        reason="reviewed",
        by="reviewer",
    )

    repeated = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    )
    assert repeated.items == ()
    assert [fact.cause for fact in repeated.facts if fact.cause] == ["changed"]
    assert [(event.status, event.observation_id) for event in repeated.acknowledgements] == [
        ("active", first.items[0].observation_id)
    ]


def test_acknowledgement_scopes_exact_investigation_source_record_and_observation(
    tmp_path: Path,
) -> None:
    alpha, _ = _completed_decision(
        tmp_path, slug="alpha", source_id="S1", url="http://www.example.com/doc/"
    )
    _completed_decision(
        tmp_path, slug="beta", source_id="S9", url="https://example.com/doc?utm_source=x"
    )
    observations = (
        _retrieved_observation(
            investigation="alpha",
            source_id="S1",
            url="http://www.example.com/doc/",
            text="Changed alpha source.",
        ),
        _retrieved_observation(
            investigation="beta",
            source_id="S9",
            url="https://example.com/doc?utm_source=x",
            text="Changed beta source.",
        ),
    )
    first = cross_investigation.report_degraded_support(tmp_path, network_observations=observations)
    alpha_item = next(item for item in first.items if item.source_investigation == "alpha")

    cross_investigation.acknowledge_degradation(
        alpha,
        alpha_item,
        network_observations=observations,
        reason="alpha reviewed",
        by="reviewer",
    )

    repeated = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    )
    assert [(item.source_investigation, item.source_id) for item in repeated.items] == [
        ("beta", "S9")
    ]


def test_new_source_content_or_observation_stales_acknowledgement(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    first_observation = _retrieved_observation(
        investigation="completed", text="First changed live source."
    )
    first_item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(first_observation,)
    ).items[0]
    cross_investigation.acknowledge_degradation(
        research,
        first_item,
        network_observations=(first_observation,),
        reason="first observation reviewed",
        by="reviewer",
    )

    changed_content = _retrieved_observation(
        investigation="completed", text="Second changed live source."
    )
    later_same_content = _retrieved_observation(
        investigation="completed",
        text="First changed live source.",
        retrieved_at=datetime(2026, 8, 4, tzinfo=UTC),
    )

    changed_report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(changed_content,)
    )
    assert len(changed_report.items) == 1
    assert changed_report.items[0].observation_id != first_item.observation_id
    assert [event.status for event in changed_report.acknowledgements] == ["stale"]

    replayed = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(later_same_content,)
    )
    assert replayed.items == ()
    assert [event.status for event in replayed.acknowledgements] == ["active"]


def test_different_cause_reports_despite_existing_acknowledgement(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    changed = _retrieved_observation(investigation="completed", text="Changed live source.")
    changed_item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(changed,)
    ).items[0]
    cross_investigation.acknowledge_degradation(
        research,
        changed_item,
        network_observations=(changed,),
        reason="change reviewed",
        by="reviewer",
    )

    unreachable = _unreachable_observation(investigation="completed")
    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(unreachable,)
    )

    assert [(item.cause, item.source_id) for item in report.items] == [("unreachable", "S1")]


def test_malformed_or_forged_acknowledgement_fails_closed(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]
    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=(observation,),
        reason="reviewed",
        by="reviewer",
    )
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(ledger_path)
    ledger["degradation_acknowledgements"][0]["source_id"] = "S-forged"
    ledger_path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    with pytest.raises(LedgerValidationError, match="acknowledgement_id|source record"):
        cross_investigation.report_degraded_support(tmp_path, network_observations=(observation,))


def test_observation_identity_ignores_check_clock_and_order_but_tracks_state(
    tmp_path: Path,
) -> None:
    _completed_decision(tmp_path, slug="completed")
    checks = (
        _retrieved_observation(
            investigation="completed",
            text="Changed live source.",
            retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        ),
        _retrieved_observation(
            investigation="completed",
            text="Changed live source.",
            retrieved_at=datetime(2026, 8, 1, tzinfo=UTC),
        ),
    )

    ids = [
        cross_investigation.report_degraded_support(tmp_path, network_observations=(observation,))
        .items[0]
        .observation_id
        for observation in checks
    ]

    assert ids[0] == ids[1]
    changed = _retrieved_observation(investigation="completed", text="Another changed state.")
    assert (
        cross_investigation.report_degraded_support(tmp_path, network_observations=(changed,))
        .items[0]
        .observation_id
        != ids[0]
    )


def test_expiry_identity_ignores_as_of_after_threshold_and_tracks_source_replacement(
    tmp_path: Path,
) -> None:
    research, _ = _completed_decision(
        tmp_path, slug="completed", tier="T3", source_date="2025-01-01"
    )
    first = cross_investigation.report_degraded_support(
        tmp_path, include_expiry=True, as_of=date(2026, 8, 1)
    ).items[0]
    later = cross_investigation.report_degraded_support(
        tmp_path, include_expiry=True, as_of=date(2026, 8, 3)
    ).items[0]
    assert later.observation_id == first.observation_id

    _write_sources(
        research,
        "  - id: S1\n    url: https://replacement.example/doc\n"
        "    tier: T3\n    date: 2025-01-01\n",
    )
    replaced = cross_investigation.report_degraded_support(
        tmp_path, include_expiry=True, as_of=date(2026, 8, 3)
    ).items[0]
    assert replaced.observation_id != first.observation_id


def test_acknowledgement_rejects_coherent_forgery_not_in_canonical_current_report(
    tmp_path: Path,
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    actual = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]
    forged = replace(actual, mechanical_fact="invented_fact")

    with pytest.raises(ValueError, match="current degraded-support report"):
        cross_investigation.acknowledge_degradation(
            research,
            forged,
            network_observations=(observation,),
            reason="reviewed",
            by="reviewer",
        )

    assert (
        load_ledger(research.artifact_path("notes/sources/verification.yaml"))[
            "degradation_acknowledgements"
        ]
        == []
    )


def test_source_replacement_makes_old_acknowledgement_stale_and_keeps_audit_event(
    tmp_path: Path,
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    original = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(original,)
    ).items[0]
    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=(original,),
        reason="reviewed original",
        by="reviewer",
    )

    _write_sources(research, "  - id: S1\n    url: https://replacement.example/doc\n")
    replacement = _unreachable_observation(
        investigation="completed", url="https://replacement.example/doc"
    )
    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(replacement,)
    )

    assert len(report.items) == 1
    assert report.items[0].observation_id != item.observation_id
    assert [(event.status, event.observation_id) for event in report.acknowledgements] == [
        ("stale", item.observation_id)
    ]
    assert [fact.cause for fact in report.facts if fact.cause] == ["unreachable"]


def test_acknowledgement_date_is_canonical_nonfuture_and_author_reason_are_trimmed(
    tmp_path: Path,
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]

    with pytest.raises(ValueError, match="future"):
        cross_investigation.acknowledge_degradation(
            research,
            item,
            network_observations=(observation,),
            reason="reviewed",
            by="reviewer",
            acknowledged_on=date.today().replace(year=date.today().year + 1),
        )

    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=(observation,),
        reason="  reviewed  ",
        by="  declared reviewer  ",
        acknowledged_on=date(2026, 1, 2),
    )
    event = load_ledger(research.artifact_path("notes/sources/verification.yaml"))[
        "degradation_acknowledgements"
    ][0]
    assert (event["reason"], event["by"], event["date"]) == (
        "reviewed",
        "declared reviewer",
        "2026-01-02",
    )


@pytest.mark.parametrize("invalid_date", ["2026-8-3", "not-a-date", "2999-01-01"])
def test_malformed_or_future_loaded_acknowledgement_date_fails_closed(
    tmp_path: Path, invalid_date: str
) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    item = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(observation,)
    ).items[0]
    cross_investigation.acknowledge_degradation(
        research,
        item,
        network_observations=(observation,),
        reason="reviewed",
        by="reviewer",
    )
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["degradation_acknowledgements"][0]["date"] = invalid_date
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    with pytest.raises(LedgerValidationError, match="date"):
        cross_investigation.report_degraded_support(tmp_path, network_observations=(observation,))


def test_concurrent_acknowledgements_are_additive_without_lost_updates(tmp_path: Path) -> None:
    research = Research.create(base=tmp_path, slug="completed", title="Done", question="q")
    source_count = 8
    _write_sources(
        research,
        "".join(
            f"  - id: S{index}\n    url: https://example.com/{index}\n"
            for index in range(source_count)
        ),
    )
    claim_ids = [
        _write_verified_anchor(research, f"S{index}", f"Support {index}.")
        for index in range(source_count)
    ]
    _write_decision(research, claim_ids)
    research.meta.status = "done"
    research.save()
    observations = tuple(
        _unreachable_observation(
            investigation="completed",
            source_id=f"S{index}",
            url=f"https://example.com/{index}",
        )
        for index in range(source_count)
    )
    context = multiprocessing.get_context("fork")
    start = context.Event()
    processes = [
        context.Process(
            target=_acknowledge_concurrently,
            args=(str(tmp_path), "completed", f"S{index}", observations, start),
        )
        for index in range(source_count)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert {event["source_id"] for event in ledger["degradation_acknowledgements"]} == {
        f"S{index}" for index in range(source_count)
    }


def test_acknowledgement_write_and_default_commit_share_the_ledger_lock(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=tmp_path, check=True)
    research = Research.create(
        base=tmp_path / "research", slug="completed", title="Done", question="q"
    )
    _write_sources(
        research,
        "  - id: S1\n    url: https://example.com/one\n"
        "  - id: S2\n    url: https://example.com/two\n",
    )
    first_claim = _write_verified_anchor(research, "S1", "First support.")
    second_claim = _write_verified_anchor(research, "S2", "Second support.")
    _write_decision(research, [first_claim, second_claim])
    research.meta.status = "done"
    research.save()
    subprocess.run(["git", "add", "research"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    observations = (
        _unreachable_observation(
            investigation="completed", source_id="S1", url="https://example.com/one"
        ),
        _unreachable_observation(
            investigation="completed", source_id="S2", url="https://example.com/two"
        ),
    )
    report = cross_investigation.report_degraded_support(
        tmp_path / "research", network_observations=observations
    )
    first = next(item for item in report.items if item.source_id == "S1")
    second = next(item for item in report.items if item.source_id == "S2")
    context = multiprocessing.get_context("fork")
    commit_ready = context.Event()
    allow_commit = context.Event()
    second_finished = context.Event()
    committer = context.Process(
        target=_acknowledge_with_locked_commit,
        args=(
            str(tmp_path / "research"),
            "completed",
            first,
            observations,
            commit_ready,
            allow_commit,
        ),
    )
    contender = context.Process(
        target=_acknowledge_and_signal,
        args=(str(tmp_path / "research"), "completed", second, observations, second_finished),
    )

    committer.start()
    assert commit_ready.wait(5)
    contender.start()
    assert not second_finished.wait(0.5)
    allow_commit.set()
    committer.join(10)
    contender.join(10)

    assert committer.exitcode == contender.exitcode == 0
    committed_ledger = yaml.safe_load(
        subprocess.run(
            ["git", "show", "HEAD:research/completed/notes/sources/verification.yaml"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert [event["source_id"] for event in committed_ledger["degradation_acknowledgements"]] == [
        "S1"
    ]
    current = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert {event["source_id"] for event in current["degradation_acknowledgements"]} == {
        "S1",
        "S2",
    }


def test_concurrent_verification_cannot_erase_acknowledgement(tmp_path: Path) -> None:
    research, _ = _completed_decision(tmp_path, slug="completed")
    observation = _unreachable_observation(investigation="completed")
    observations = (observation,)
    degradation = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    ).items[0]
    context = multiprocessing.get_context("fork")
    verify_ready = context.Event()
    allow_verify_save = context.Event()
    acknowledgement_finished = context.Event()
    verifier = context.Process(
        target=_verify_with_paused_save,
        args=(str(tmp_path), "completed", verify_ready, allow_verify_save),
    )
    acknowledgement = context.Process(
        target=_acknowledge_and_signal,
        args=(
            str(tmp_path),
            "completed",
            degradation,
            observations,
            acknowledgement_finished,
        ),
    )

    verifier.start()
    assert verify_ready.wait(5)
    acknowledgement.start()
    acknowledgement_finished.wait(2)
    allow_verify_save.set()
    verifier.join(10)
    acknowledgement.join(10)

    assert verifier.exitcode == 0
    assert acknowledgement.exitcode == 0
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert [entry["observation_id"] for entry in ledger["degradation_acknowledgements"]] == [
        degradation.observation_id
    ]


def test_concurrent_acknowledgement_cannot_erase_claim_resolution_updates(
    tmp_path: Path,
) -> None:
    research, claim_id = _completed_decision(tmp_path, slug="completed")
    research.artifact_path("notes/sources/S1/content.md").write_text(
        "Different persisted support.\n", encoding="utf-8"
    )
    current = verify_explore_claims(research)
    assert current.items[0].state == "unverifiable"
    observation = _unreachable_observation(investigation="completed")
    observations = (observation,)
    degradation = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    ).items[0]
    context = multiprocessing.get_context("fork")
    acknowledgement_ready = context.Event()
    allow_acknowledgement_save = context.Event()
    resolution_finished = context.Event()
    acknowledgement = context.Process(
        target=_acknowledge_with_paused_save,
        args=(
            str(tmp_path),
            "completed",
            degradation,
            observations,
            acknowledgement_ready,
            allow_acknowledgement_save,
        ),
    )
    resolution = context.Process(
        target=_resolve_and_signal,
        args=(str(tmp_path), "completed", claim_id, resolution_finished),
    )

    acknowledgement.start()
    assert acknowledgement_ready.wait(5)
    resolution.start()
    resolution_finished.wait(2)
    allow_acknowledgement_save.set()
    acknowledgement.join(10)
    resolution.join(10)

    assert acknowledgement.exitcode == 0
    assert resolution.exitcode == 0
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert [entry["observation_id"] for entry in ledger["degradation_acknowledgements"]] == [
        degradation.observation_id
    ]
    assert [entry["claim_id"] for entry in ledger["claims"]] == [claim_id]
    assert [entry["claim_id"] for entry in ledger["resolutions"]] == [claim_id]


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("offline cross-investigation path used the network")

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    monkeypatch.setattr("sdr.network_policy.fetch_http", forbidden)
    monkeypatch.setattr("sdr.snapshot.fetch_url", forbidden)


def test_layer_and_report_derive_fully_offline_with_reachability_not_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _completed_decision(tmp_path, slug="completed")
    _forbid_network(monkeypatch)

    layer = derive_cross_investigation_layer(tmp_path)
    report = cross_investigation.report_degraded_support(tmp_path)

    assert [(source.investigation, source.source_id) for source in layer.sources] == [
        ("completed", "S1")
    ]
    network_fact = next(fact for fact in report.facts if fact.observation_source == "network")
    assert network_fact.reachability == "not checked"
    assert network_fact.cause is None


def test_local_snapshot_identity_mismatch_and_tier_expiry_remain_available_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research, claim_id = _completed_decision(
        tmp_path,
        slug="completed",
        tier="T3",
        source_date="2025-01-01",
    )
    research.artifact_path("notes/sources/S1/content.md").write_text(
        "Locally modified snapshot.\n", encoding="utf-8"
    )
    _forbid_network(monkeypatch)

    report = cross_investigation.report_degraded_support(
        tmp_path,
        include_expiry=True,
        as_of=date(2026, 8, 3),
    )

    assert {(fact.observation_source, fact.mechanical_fact) for fact in report.facts} >= {
        ("local_snapshot", "local_snapshot_hash_mismatch"),
        ("tier_policy", "tier_expiry_exceeded"),
    }
    assert [(item.cause, item.claim_id) for item in report.items] == [("expired", claim_id)]


def test_unchecked_reachability_is_never_reported_as_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _completed_decision(tmp_path, slug="completed")
    _forbid_network(monkeypatch)

    report = cross_investigation.report_degraded_support(tmp_path)
    network_facts = [fact for fact in report.facts if fact.observation_source == "network"]

    assert [fact.reachability for fact in network_facts] == ["not checked"]
    assert all(fact.mechanical_fact != "retrieval_succeeded" for fact in network_facts)


def test_opt_in_network_action_returns_exact_records_with_redirect_provenance_without_writes(
    tmp_path: Path,
) -> None:
    declared_url = "https://public.example/declared"
    final_url = "https://final.example/evidence"
    research, _ = _completed_decision(
        tmp_path,
        slug="completed",
        url=declared_url,
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers["host"] == "public.example":
            return httpx.Response(302, headers={"location": final_url})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><main>Observed canonical evidence.</main></html>",
        )

    before = _tree_state(tmp_path)
    observed_at = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    observations = cross_investigation.observe_network_sources(
        tmp_path,
        mock_transport=httpx.MockTransport(handler),
        resolver=lambda host: ["93.184.216.34"],
        observed_at=observed_at,
    )

    assert len(observations) == 1
    observation = observations[0]
    assert observation == cross_investigation.NetworkSourceObservation.from_fetch_result(
        investigation="completed",
        source_id="S1",
        declared_url=declared_url,
        retrieved_at=observed_at,
        fetched=FetchResult(
            declared_url=declared_url,
            final_url=final_url,
            redirects=observation.redirects,
            status_code=200,
            content_type="text/html",
            content_eligible=True,
            text="<html><main>Observed canonical evidence.</main></html>",
        ),
    )
    assert [(redirect.url, redirect.target_url) for redirect in observation.redirects] == [
        (declared_url, final_url)
    ]
    assert observation.declared_url == declared_url
    assert observation.observed_url == final_url
    assert derive_cross_investigation_layer(tmp_path).sources[0].url == declared_url
    assert [request.headers["host"] for request in requests] == [
        "public.example",
        "final.example",
    ]
    assert _tree_state(tmp_path) == before


def test_opt_in_network_action_returns_redacted_mechanical_failure_observation(
    tmp_path: Path,
) -> None:
    declared_url = "https://public.example/evidence"
    _completed_decision(tmp_path, slug="completed", url=declared_url)

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "transport failed with bearer super-secret-token",
            request=request,
        )

    observations = cross_investigation.observe_network_sources(
        tmp_path,
        mock_transport=httpx.MockTransport(fail),
        resolver=lambda host: ["93.184.216.34"],
        observed_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )

    assert [
        (
            observation.investigation,
            observation.source_id,
            observation.state,
            observation.declared_url,
            observation.observed_url,
            observation.status_code,
            observation.content_hash,
        )
        for observation in observations
    ] == [
        (
            "completed",
            "S1",
            "unreachable",
            declared_url,
            declared_url,
            None,
            None,
        )
    ]
    assert "super-secret-token" not in repr(observations)


def test_duplicate_source_declarations_fail_before_any_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research = Research.create(base=tmp_path, slug="duplicate", title="Duplicate", question="q")
    _write_sources(
        research,
        "  - id: S1\n    url: https://example.com/first\n"
        "  - id: S1\n    url: https://example.com/second\n",
    )
    calls: list[str] = []

    def forbidden_fetch(url: str, **kwargs) -> FetchResult:
        calls.append(url)
        raise AssertionError("duplicate preflight reached the network boundary")

    monkeypatch.setattr(cross_investigation.snapshot_mod, "fetch_url", forbidden_fetch)

    with pytest.raises(ValueError, match=r"duplicate source id duplicate:S1"):
        cross_investigation.observe_network_sources(tmp_path)

    assert calls == []


def test_structurally_invalid_source_is_redacted_without_network_and_valid_results_stay_ordered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research = Research.create(base=tmp_path, slug="mixed", title="Mixed", question="q")
    invalid_url = "https://user:super-secret@example.com/private"
    valid_urls = (
        "https://example.com/slow",
        "https://example.com/fast",
    )
    _write_sources(
        research,
        f"  - id: S1\n    url: {valid_urls[0]}\n"
        f"  - id: S2\n    url: {invalid_url}\n"
        f"  - id: S3\n    url: {valid_urls[1]}\n",
    )
    expected_preflight = {invalid_url, *valid_urls}
    preflighted: set[str] = set()
    fetched: list[str] = []
    real_validator = cross_investigation.validate_http_url_structure

    def record_preflight(url: str) -> None:
        preflighted.add(url)
        real_validator(url)

    def fetch(url: str, **kwargs) -> FetchResult:
        assert preflighted >= expected_preflight
        fetched.append(url)
        if url.endswith("slow"):
            time.sleep(0.01)
        return FetchResult(
            declared_url=url,
            final_url=url,
            redirects=(),
            status_code=200,
            content_type="text/plain",
            content_eligible=True,
            text=url.rsplit("/", 1)[1],
        )

    monkeypatch.setattr(cross_investigation, "validate_http_url_structure", record_preflight)
    monkeypatch.setattr(cross_investigation.snapshot_mod, "fetch_url", fetch)

    observations = cross_investigation.observe_network_sources(
        tmp_path, observed_at=datetime(2026, 8, 3, 12, tzinfo=UTC)
    )

    assert [(item.source_id, item.state) for item in observations] == [
        ("S1", "retrieved"),
        ("S2", "invalid"),
        ("S3", "retrieved"),
    ]
    assert set(fetched) == set(valid_urls)
    assert invalid_url not in fetched
    invalid = observations[1]
    assert invalid.failure_kind == "invalid_declaration"
    assert invalid.observed_url is None
    assert invalid.redirects == ()
    assert invalid.status_code is None
    assert invalid.content_hash is None
    assert "super-secret" not in repr(invalid)


def test_observer_keeps_success_when_another_source_response_is_malformed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research = Research.create(base=tmp_path, slug="mixed", title="Mixed", question="q")
    _write_sources(
        research,
        "  - id: S1\n    url: https://example.com/malformed\n"
        "  - id: S2\n    url: https://example.com/valid\n",
    )

    def fetch(url: str, **kwargs) -> FetchResult:
        if url.endswith("malformed"):
            return FetchResult(
                declared_url=url,
                final_url="not-an-http-url",
                redirects=(),
                status_code=200,
                content_type="text/plain",
                content_eligible=True,
                text="must not become evidence",
            )
        return FetchResult(
            declared_url=url,
            final_url=url,
            redirects=(),
            status_code=200,
            content_type="text/plain",
            content_eligible=True,
            text="valid evidence",
        )

    monkeypatch.setattr(cross_investigation.snapshot_mod, "fetch_url", fetch)

    observations = cross_investigation.observe_network_sources(
        tmp_path, observed_at=datetime(2026, 8, 3, 12, tzinfo=UTC)
    )

    assert [(item.source_id, item.state) for item in observations] == [
        ("S1", "invalid"),
        ("S2", "retrieved"),
    ]
    assert observations[0].failure_kind == "malformed_response"
    assert observations[0].content_hash is None
    assert "not-an-http-url" not in repr(observations[0])


def test_observer_isolates_unexpected_per_source_exception_without_fabricating_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research = Research.create(base=tmp_path, slug="partial", title="Partial", question="q")
    _write_sources(
        research,
        "  - id: S1\n    url: https://example.com/error\n"
        "  - id: S2\n    url: https://example.com/valid\n",
    )

    def fetch(url: str, **kwargs) -> FetchResult:
        if url.endswith("error"):
            raise RuntimeError("unexpected bearer private-token")
        return FetchResult(
            declared_url=url,
            final_url=url,
            redirects=(),
            status_code=200,
            content_type="text/plain",
            content_eligible=True,
            text="valid evidence",
        )

    monkeypatch.setattr(cross_investigation.snapshot_mod, "fetch_url", fetch)
    observations = cross_investigation.observe_network_sources(
        tmp_path, observed_at=datetime(2026, 8, 3, 12, tzinfo=UTC)
    )
    report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=observations
    )

    assert [(item.source_id, item.state) for item in observations] == [
        ("S1", "invalid"),
        ("S2", "retrieved"),
    ]
    invalid_fact = next(
        fact
        for fact in report.facts
        if fact.source_id == "S1" and fact.observation_source == "network"
    )
    assert invalid_fact.observation_state == "invalid"
    assert invalid_fact.reachability is None
    assert invalid_fact.cause is None
    assert invalid_fact.mechanical_fact == "network_observation_invalid"
    assert "private-token" not in repr(observations)


def test_observer_preserves_declaration_order_under_mixed_latency_and_caps_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    research = Research.create(base=tmp_path, slug="ordered", title="Ordered", question="q")
    source_count = cross_investigation.MAX_BLOCKING_WORKERS + 3
    _write_sources(
        research,
        "".join(
            f"  - id: S{index}\n    url: https://example.com/{index}\n"
            for index in range(1, source_count + 1)
        ),
    )
    active = 0
    maximum_active = 0
    lock = threading.Lock()

    def fetch(url: str, **kwargs) -> FetchResult:
        nonlocal active, maximum_active
        source_number = int(url.rsplit("/", 1)[1])
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep((source_count - source_number) * 0.005)
            return FetchResult(
                declared_url=url,
                final_url=url,
                redirects=(),
                status_code=200,
                content_type="text/plain",
                content_eligible=True,
                text=f"source {source_number}",
            )
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(cross_investigation.snapshot_mod, "fetch_url", fetch)

    observations = cross_investigation.observe_network_sources(
        tmp_path, observed_at=datetime(2026, 8, 3, 12, tzinfo=UTC)
    )

    assert [item.source_id for item in observations] == [
        f"S{index}" for index in range(1, source_count + 1)
    ]
    assert 1 < maximum_active <= cross_investigation.MAX_BLOCKING_WORKERS


def test_repeated_unreachable_checks_in_one_outage_keep_identity_and_show_latest_check(
    tmp_path: Path,
) -> None:
    _completed_decision(tmp_path, slug="completed")
    first = _unreachable_observation(
        investigation="completed", retrieved_at=datetime(2026, 8, 3, 10, tzinfo=UTC)
    )
    later = _unreachable_observation(
        investigation="completed",
        retrieved_at=datetime(2026, 8, 3, 11, tzinfo=UTC),
        status_code=503,
    )
    first_report = cross_investigation.report_degraded_support(
        tmp_path, network_observations=(first,)
    )
    history = cross_investigation.merge_network_observation_history((), (first, later))

    repeated = cross_investigation.report_degraded_support(tmp_path, network_observations=history)

    assert repeated.items[0].observation_id == first_report.items[0].observation_id
    current_fact = next(fact for fact in repeated.facts if fact.cause == "unreachable")
    assert current_fact.observation_state == "unreachable"
    assert current_fact.observation_timestamp == "2026-08-03T11:00:00+00:00"


def test_recovery_boundary_makes_a_later_unreachable_state_a_new_outage(
    tmp_path: Path,
) -> None:
    _completed_decision(tmp_path, slug="completed")
    first = _unreachable_observation(
        investigation="completed", retrieved_at=datetime(2026, 8, 3, 10, tzinfo=UTC)
    )
    recovered = _retrieved_observation(
        investigation="completed",
        text="Support for completed.",
        retrieved_at=datetime(2026, 8, 3, 11, tzinfo=UTC),
    )
    second = _unreachable_observation(
        investigation="completed", retrieved_at=datetime(2026, 8, 3, 12, tzinfo=UTC)
    )
    first_id = (
        cross_investigation.report_degraded_support(tmp_path, network_observations=(first,))
        .items[0]
        .observation_id
    )
    history = cross_investigation.merge_network_observation_history((), (first, recovered))
    history = cross_investigation.merge_network_observation_history(history, (second,))

    second_id = (
        cross_investigation.report_degraded_support(tmp_path, network_observations=history)
        .items[0]
        .observation_id
    )

    assert second_id != first_id


@pytest.mark.parametrize("case", ["replay", "backdated", "out_of_order_history"])
def test_observation_history_rejects_replay_backdating_and_out_of_order(
    case: str,
) -> None:
    earlier = _unreachable_observation(
        investigation="completed", retrieved_at=datetime(2026, 8, 3, 10, tzinfo=UTC)
    )
    later = _unreachable_observation(
        investigation="completed", retrieved_at=datetime(2026, 8, 3, 11, tzinfo=UTC)
    )

    with pytest.raises(ValueError, match="timestamp|order|replay"):
        if case == "replay":
            history = cross_investigation.merge_network_observation_history((), (earlier,))
            cross_investigation.merge_network_observation_history(history, (earlier,))
        elif case == "backdated":
            history = cross_investigation.merge_network_observation_history((), (later,))
            cross_investigation.merge_network_observation_history(history, (earlier,))
        else:
            cross_investigation.merge_network_observation_history((), (later, earlier))
