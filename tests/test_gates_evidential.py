import ast
import hashlib
import inspect
import textwrap
from pathlib import Path

import pytest

from sdr import gates, lifecycle, probe_verify
from sdr.research import Research
from sdr.textual_anchoring import MATCHER_VERSION, NORMALIZATION_VERSION
from sdr.verification_ledger import empty_ledger, load_ledger, make_claim_id, save_ledger

_DERIVED_LAYER_MODULE = "sdr.cross_investigation"
_DERIVED_LAYER_SYMBOLS = {
    "CrossInvestigationLayer",
    "DegradedSupportReport",
    "derive_cross_investigation_layer",
    "query_source_dependencies",
    "report_degraded_support",
}


def _gate_boundary_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(
                alias.name
                for alias in node.names
                if alias.name == _DERIVED_LAYER_MODULE
                or alias.name.startswith(f"{_DERIVED_LAYER_MODULE}.")
            )
        elif isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            module = node.module or ""
            if module in {_DERIVED_LAYER_MODULE, "cross_investigation"}:
                violations.append(module)
            if (module == "sdr" or node.level > 0) and "cross_investigation" in imported:
                violations.append(_DERIVED_LAYER_MODULE)
        elif isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name in _DERIVED_LAYER_SYMBOLS or name == "cross_investigation":
                violations.append(name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == _DERIVED_LAYER_MODULE or node.value.startswith(
                f"{_DERIVED_LAYER_MODULE}."
            ):
                violations.append(node.value)
    return violations


def _make(tmp_path, mode="full"):
    return Research.create(
        base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?", mode=mode
    )


def _note(sources_yaml: str, body: str = "") -> str:
    default_body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo vs Bar [S1].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        No se encontraron señales contrarias tras buscar benchmarks negativos.
        """
    ).strip()
    return (
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        f"sources:\n{sources_yaml}"
        "---\n\n"
        f"{body or default_body}\n"
    )


# --- explore: tiers and triangulation --------------------------------------


def _ok_sources() -> str:
    return (
        "  - id: S1\n    url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )


def test_explore_passes_with_t1_and_two_distinct_declared_hosts(tmp_path):
    r = _make(tmp_path)
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources()), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert report.passed, report.failures


def test_explore_fails_when_source_date_is_missing(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/guide\n    tier: T1\n"
        "  - url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not report.passed
    assert any(f.check == "source_dates" and "docs.foo.dev" in f.detail for f in report.failures)


def test_explore_v2_fails_without_counter_evidence(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo vs Bar.

        ## Maturity
        Estable.

        ## Costs
        Bajo.

        ## Risks
        Lock-in.
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert any("Counter-evidence" in f.detail for f in report.failures)


def test_explore_v1_does_not_require_counter_evidence(tmp_path):
    r = _make(tmp_path)
    r.meta.schema_version = 1
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo vs Bar.

        ## Maturity
        Estable.

        ## Costs
        Bajo.

        ## Risks
        Lock-in.
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not any("Counter-evidence" in f.detail for f in report.failures)


def test_claim_citation_coverage_fails_unknown_source_marker(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - id: S1\n    url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo tiene soporte estable [S9].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in [S1].

        ## Counter-evidence
        No se encontraron señales contrarias [S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(sources, body=body), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert any(f.check == "claim_citation_coverage" and "S9" in f.detail for f in report.failures)


def test_claim_citation_coverage_fails_unknown_source_marker_in_heading(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated [S9]
        Foo tiene soporte estable [S1].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in [S1].

        ## Counter-evidence
        No se encontraron señales contrarias [cf.S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert any(
        failure.check == "claim_citation_coverage"
        and "[S9]" in failure.detail
        and "S9" in failure.detail
        for failure in report.failures
    )


def test_claim_citation_coverage_fails_unknown_contextual_source_marker(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo tiene soporte estable [cf. S9].

        ## Maturity
        Estable [cf. S1].

        ## Costs
        Bajo [cf. S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        No se encontraron señales contrarias [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert any(
        f.check == "claim_citation_coverage"
        and "n1.md" in f.detail
        and "[cf. S9]" in f.detail
        and "S9" in f.detail
        for f in report.failures
    )


def test_claim_citation_coverage_allows_contextual_only_note(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo se compara con Bar [cf. S1].

        ## Maturity
        El contexto de adopción es estable [cf. S1].

        ## Costs
        El análisis de costos es favorable [cf. S2].

        ## Risks
        Lock-in [cf. S1].

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert all(
        result.passed for result in report.results if result.check == "claim_citation_coverage"
    )


def test_claim_citation_coverage_allows_one_factual_with_contextual_references(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo reduce latencia [S1] en el contexto comparado [cf. S2].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert all(
        result.passed for result in report.results if result.check == "claim_citation_coverage"
    )


def test_claim_citation_coverage_rejects_multiple_factual_markers_actionably(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo reduce latencia [S1] y costo [S2].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    failure = next(
        result
        for result in report.failures
        if result.check == "claim_citation_coverage" and "split the sentence" in result.detail
    )
    assert "n1.md" in failure.detail
    assert "line" in failure.detail
    assert "[S1]" in failure.detail and "[S2]" in failure.detail


def test_claim_citation_coverage_rejects_multiline_sentence_with_two_factual_markers(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo reduce latencia [S1]
        y también reduce costo [S2].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    failure = next(
        result
        for result in report.failures
        if result.check == "claim_citation_coverage" and "split the sentence" in result.detail
    )
    assert "lines" in failure.detail
    assert "[S1]" in failure.detail and "[S2]" in failure.detail


def test_claim_citation_coverage_uses_same_contextual_variants_as_claim_parser(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo se compara con Bar [CF.s1].

        ## Maturity
        El contexto es estable [cf.   S1].

        ## Costs
        El análisis es favorable [cF   .   s2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        Se revisó evidencia contraria [cf.S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert all(
        result.passed for result in report.results if result.check == "claim_citation_coverage"
    )


def test_claim_citation_coverage_ignores_markers_in_frontmatter_and_code(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo se compara con Bar [cf. S1] y muestra `[S8]` como ejemplo.

        ```markdown
        Referencia de ejemplo [S9].
        ```

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        Lock-in.

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    note = _note(_ok_sources(), body=body).replace(
        "stage: explore\n", 'stage: explore\nexample: "[S7]"\n'
    )
    (r.root / "notes" / "n1.md").write_text(note, encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert not any(
        result.check == "claim_citation_coverage"
        and any(marker in result.detail for marker in ("S7", "S8", "S9"))
        for result in report.failures
    )


def test_claim_citation_coverage_ignores_indented_code_and_url_destinations(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        [Foo es estable [S1]](https://example.com/[S9]).

            Ejemplo de código [S8].

        ## Maturity
        Estable [S1].

        ## Costs
        Bajo [S2].

        ## Risks
        <https://example.com/[S7]>

        ## Counter-evidence
        Se revisó evidencia contraria [cf. S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert not any(
        result.check == "claim_citation_coverage"
        and any(marker in result.detail for marker in ("S7", "S8", "S9"))
        for result in report.failures
    )


def test_claim_citation_coverage_requires_citations_in_key_sections(tmp_path):
    r = _make(tmp_path)
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo vs Bar.

        ## Maturity
        Estable.

        ## Costs
        Bajo.

        ## Risks
        Lock-in [S1].

        ## Counter-evidence
        No se encontraron señales contrarias [S2].
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert any(
        f.check == "claim_citation_coverage" and "Alternatives evaluated" in f.detail
        for f in report.failures
    )
    assert any(
        f.check == "claim_citation_coverage" and "Maturity" in f.detail for f in report.failures
    )
    assert any(
        f.check == "claim_citation_coverage" and "Costs" in f.detail for f in report.failures
    )


def test_claim_citation_coverage_is_not_required_in_schema_v1(tmp_path):
    r = _make(tmp_path)
    r.meta.schema_version = 1
    body = textwrap.dedent(
        """
        ## Alternatives evaluated
        Foo vs Bar.

        ## Maturity
        Estable.

        ## Costs
        Bajo.

        ## Risks
        Lock-in.
        """
    ).strip()
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources(), body=body), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not any(result.check == "claim_citation_coverage" for result in report.results)


def test_explore_passes_when_all_source_dates_are_present(tmp_path):
    r = _make(tmp_path)
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources()), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert all(x.passed for x in report.results if x.check == "source_dates")


def test_tier_plausibility_fails_inflated_tier_without_justification(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - id: S1\n    url: https://random-blog.example/post\n    tier: T1\n    date: 2026-01-01\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert any(f.check == "tier_plausibility" and "T3" in f.detail for f in report.failures)


def test_tier_plausibility_allows_justification(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - id: S1\n    url: https://random-blog.example/post\n    tier: T1\n    tier_justification: autor mantiene el benchmark oficial\n    date: 2026-01-01\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert not any(f.check == "tier_plausibility" and not f.passed for f in report.results)


def test_source_dates_fails_when_source_is_stale_without_justification(tmp_path):
    r = _make(tmp_path)
    r.artifact_path("brief.md").write_text("---\ndate: 2026-07-03\n---\n", encoding="utf-8")
    sources = (
        "  - id: S1\n    url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2020-01-01\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert any(f.check == "source_dates" and "is stale" in f.detail for f in report.failures)


def test_source_dates_allows_date_justification(tmp_path):
    r = _make(tmp_path)
    r.artifact_path("brief.md").write_text("---\ndate: 2026-07-03\n---\n", encoding="utf-8")
    sources = (
        "  - id: S1\n    url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2020-01-01\n    date_justification: documento fundacional estable\n"
        "  - id: S2\n    url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")

    report = gates.check_stage(r, stage="explore", offline=True)

    assert not any(f.check == "source_dates" and "is stale" in f.detail for f in report.failures)


def test_explore_fails_without_t1_source(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://blog.a.com/x\n    tier: T3\n    date: 2026-01-01\n"
        "  - url: https://blog.b.com/y\n    tier: T3\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not report.passed
    assert any(f.check == "source_tiers" for f in report.failures)


def test_explore_fails_with_single_declared_host(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://docs.foo.dev/b\n    tier: T2\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not report.passed
    assert any(f.check == "source_triangulation" for f in report.failures)


def test_www_prefix_does_not_count_as_distinct_declared_host(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://www.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://foo.dev/b\n    tier: T2\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert any(f.check == "source_triangulation" for f in report.failures)


def test_redirect_final_host_does_not_count_as_a_distinct_declared_host(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - id: S1\n    url: https://declared.example/report\n    tier: T1\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    snapshot_dir = r.root / "notes" / "sources" / "S1"
    snapshot_dir.mkdir(parents=True)
    snapshot_dir.joinpath("meta.yaml").write_text(
        "declared_url: https://declared.example/report\n"
        "final_url: https://other.example/mirror\n"
        "redirects:\n"
        "  - url: https://declared.example/report\n"
        "    status_code: 302\n"
        "    location: https://other.example/mirror\n"
        "    target_url: https://other.example/mirror\n",
        encoding="utf-8",
    )

    report = gates.check_stage(r, stage="explore", offline=True)
    result = next(item for item in report.results if item.check == "source_triangulation")

    assert not result.passed
    assert "1 distinct declared hosts" in result.detail
    assert "independ" not in result.detail.lower()
    assert "organiz" not in result.detail.lower()


def test_distinct_declared_hosts_count_even_when_redirects_share_a_final_host(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - id: S1\n    url: https://first.example/report\n"
        "    tier: T1\n    date: 2026-01-01\n"
        "  - id: S2\n    url: https://second.example/report\n"
        "    tier: T2\n    date: 2026-01-02\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    for source_id, declared_url in (
        ("S1", "https://first.example/report"),
        ("S2", "https://second.example/report"),
    ):
        snapshot_dir = r.root / "notes" / "sources" / source_id
        snapshot_dir.mkdir(parents=True)
        snapshot_dir.joinpath("meta.yaml").write_text(
            f"declared_url: {declared_url}\n"
            "final_url: https://shared.example/mirror\n"
            "redirects:\n"
            f"  - url: {declared_url}\n"
            "    status_code: 302\n"
            "    location: https://shared.example/mirror\n"
            "    target_url: https://shared.example/mirror\n",
            encoding="utf-8",
        )

    report = gates.check_stage(r, stage="explore", offline=True)
    result = next(item for item in report.results if item.check == "source_triangulation")

    assert result.passed
    assert result.detail == "2 distinct declared hosts"


def test_same_declared_github_host_counts_once(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://github.com/acme/repo-a\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://github.com/acme/repo-b\n    tier: T2\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert any(
        f.check == "source_triangulation" and "declared hosts" in f.detail for f in report.failures
    )


def test_distinct_vendor_hosts_are_counted_mechanically(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.vendor.com/a\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://vendor.com/b\n    tier: T2\n    date: 2026-01-01\n"
        "  - url: https://github.com/vendor/repo\n    tier: T1\n    date: 2026-01-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert all(x.passed for x in report.results if x.check == "source_triangulation")


def test_org_aliases_do_not_change_declared_host_count(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n"
        "  - url: https://bench.example.com/x\n    tier: T2\n    date: 2026-02-01\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    sources_dir = r.root / "notes" / "sources"
    sources_dir.mkdir()
    (sources_dir / "orgs.yaml").write_text(
        "aliases:\n  foo: vendor\n  bench: vendor\n",
        encoding="utf-8",
    )
    report = gates.check_stage(r, stage="explore", offline=True)
    assert all(x.passed for x in report.results if x.check == "source_triangulation")


def test_per_alternative_t1_requirement(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n    alternative: foo\n"
        "  - url: https://blog.bar.io/b\n    tier: T3\n    date: 2026-01-01\n    alternative: bar\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not report.passed
    assert any("bar" in f.detail for f in report.failures)


def test_per_alternative_triangulation_requirement(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n    alternative: foo\n"
        "  - url: https://bench.foo.org/a\n    tier: T2\n    date: 2026-01-02\n    alternative: foo\n"
        "  - url: https://docs.bar.dev/b\n    tier: T1\n    date: 2026-01-01\n    alternative: bar\n"
        "  - url: https://docs.bar.dev/c\n    tier: T2\n    date: 2026-01-02\n    alternative: bar\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert not report.passed
    assert any(f.check == "source_triangulation" and "bar" in f.detail for f in report.failures)


def test_per_alternative_triangulation_passes_with_two_declared_hosts_each(tmp_path):
    r = _make(tmp_path)
    sources = (
        "  - url: https://docs.foo.dev/a\n    tier: T1\n    date: 2026-01-01\n    alternative: foo\n"
        "  - url: https://bench.example.com/a\n    tier: T2\n    date: 2026-01-02\n    alternative: foo\n"
        "  - url: https://docs.bar.dev/b\n    tier: T1\n    date: 2026-01-01\n    alternative: bar\n"
        "  - url: https://bench.example.org/b\n    tier: T2\n    date: 2026-01-02\n    alternative: bar\n"
    )
    (r.root / "notes" / "n1.md").write_text(_note(sources), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    assert all(x.passed for x in report.results if x.check == "source_triangulation")


def test_links_offline_is_skipped_not_passed(tmp_path):
    r = _make(tmp_path)
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources()), encoding="utf-8")
    report = gates.check_stage(r, stage="explore", offline=True)
    link_results = [x for x in report.results if x.check == "links_resolve"]
    assert link_results
    assert link_results[0].passed is False
    assert link_results[0].skipped is True
    assert report.passed is True
    assert link_results[0] not in report.failures


def test_links_broken_fails_when_online(tmp_path):
    r = _make(tmp_path)
    (r.root / "notes" / "n1.md").write_text(_note(_ok_sources()), encoding="utf-8")
    report = gates.check_stage(
        r, stage="explore", offline=False, url_checker=lambda u: "bench" not in u
    )
    assert not report.passed
    assert any(f.check == "links_resolve" and "bench" in f.detail for f in report.failures)


# --- probe: cross-reference and reproducibility ----------------------------


def _brief_with_criteria(r, ids):
    lines = "\n".join(f"- {i}: criterio" for i in ids)
    template = textwrap.dedent(
        """
        ---
        research: eval-foo
        date: 2026-07-03
        stage: intake
        owner: nacho
        timebox: 3
        ---

        ## Evaluation criteria
        __CRITERIA__
        """
    ).lstrip()
    r.artifact_path("brief.md").write_text(
        template.replace("__CRITERIA__", lines), encoding="utf-8"
    )


def test_probe_fails_when_a_criterion_has_no_result(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2", "C3"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\nC1 cumple. C2 parcial.\n\n"
        "## Reproduction\n```bash\npython bench.py\n```\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert not report.passed
    assert any(f.check == "criteria_cross_reference" and "C3" in f.detail for f in report.failures)


def test_probe_passes_with_all_criteria_and_repro(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\nC1 cumple, C2 no cumple.\n\n"
        "## Reproduction\n```bash\npython bench.py\n```\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert report.passed, report.failures


def test_probe_fails_when_referenced_artifact_is_missing(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\nC1 cumple, C2 no cumple. Evidencia: [salida](probe/output.json).\n\n"
        "## Reproduction\n```bash\npython bench.py > probe/output.json\n```\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert not report.passed
    assert any(
        f.check == "probe_artifacts_exist" and "probe/output.json" in f.detail
        for f in report.failures
    )


def test_probe_passes_when_referenced_artifact_exists(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    r.artifact_path("probe/output.json").write_text('{"ok": true}', encoding="utf-8")
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\nC1 cumple, C2 no cumple. Evidencia: `probe/output.json`.\n\n"
        "## Reproduction\n```bash\npython bench.py > probe/output.json\n```\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert all(x.passed for x in report.results if x.check == "probe_artifacts_exist")


def test_probe_fails_without_reproducible_block(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\nC1 cumple, C2 no cumple.\n\n"
        "## Reproduction\nCorrer el script manualmente.\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert not report.passed
    assert any(f.check == "benchmark_reproducible" for f in report.failures)


def test_probe_fails_with_benchmark_table_without_repro_command(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\n"
        "| criterio | resultado |\n|---|---|\n| C1 | 120ms |\n| C2 | 8 USD |\n\n"
        "## Reproduction\nLos resultados salen de una corrida local documentada.\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert not report.passed
    assert any(f.check == "benchmark_reproducible" for f in report.failures)


def test_probe_passes_with_benchmark_table_and_repro_command(tmp_path):
    r = _make(tmp_path)
    _brief_with_criteria(r, ["C1", "C2"])
    results = (
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\nverify:\n  action: run\n  command: python bench.py\n  expect: OK\n---\n\n"
        "## Results by criterion\n"
        "| criterio | resultado |\n|---|---|\n| C1 | 120ms |\n| C2 | 8 USD |\n\n"
        "## Reproduction\n```bash\npython bench.py --json\n```\n"
    )
    r.artifact_path("probe/results.md").write_text(results, encoding="utf-8")
    report = gates.check_stage(r, stage="probe")
    assert all(x.passed for x in report.results if x.check == "benchmark_reproducible")


# --- transfer: y-statement and ring coupled to evidence --------------------


def _memo(ring: str, recommendation: str, evidence_claim_ids: list[str] | None = None) -> str:
    memo = textwrap.dedent(
        f"""
        ---
        research: eval-foo
        date: 2026-07-03
        stage: transfer
        ring: {ring}
        audience: equipo
        __EVIDENCE_CLAIM_IDS__
        ---

        ## Recommendation
        {recommendation}

        ## Alternatives evaluated
        Foo, Bar.

        ## Selection criteria
        Costo y madurez.

        ## Risks and limitations
        Lock-in.

        ## Next steps
        Piloto.

        ## Audience
        Equipo técnico.
        """
    ).lstrip()
    if evidence_claim_ids is None:
        evidence = ""
    elif not evidence_claim_ids:
        evidence = "evidence_claim_ids: []"
    else:
        evidence = (
            "evidence_claim_ids:\n"
            + "".join(f"  - {claim_id}\n" for claim_id in evidence_claim_ids).rstrip()
        )
    return memo.replace("__EVIDENCE_CLAIM_IDS__\n", f"{evidence}\n" if evidence else "")


def _persist_claim(r: Research, state: str = "verified") -> str:
    claim_text = "Foo has stable support."
    claim_hash = hashlib.sha256(claim_text.encode()).hexdigest()
    claim_id = make_claim_id("notes/evidence.md", 1, 1, "S1", claim_hash)
    ledger = empty_ledger()
    ledger["claims"] = [
        {
            "claim_id": claim_id,
            "note_path": "notes/evidence.md",
            "line_start": 1,
            "line_end": 1,
            "source_id": "S1",
            "claim_text": claim_text,
            "claim_hash": claim_hash,
            "snapshot_hash": "snapshot-hash",
            "normalization_version": NORMALIZATION_VERSION,
            "matcher_version": MATCHER_VERSION,
            "state": state,
            "quote": claim_text if state == "verified" else "",
            "locator": {"line_start": 1, "line_end": 1} if state == "verified" else None,
        }
    ]
    save_ledger(r.artifact_path("notes/sources/verification.yaml"), ledger)
    return claim_id


def _append_forward_compatible_claim(r: Research) -> None:
    claim_text = "Future verification state."
    claim_hash = hashlib.sha256(claim_text.encode()).hexdigest()
    claim_id = make_claim_id("notes/future.md", 2, 2, "S2", claim_hash)
    ledger_path = r.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(ledger_path)
    ledger["claims"].append(
        {
            "claim_id": claim_id,
            "note_path": "notes/future.md",
            "line_start": 2,
            "line_end": 2,
            "source_id": "S2",
            "claim_text": claim_text,
            "claim_hash": claim_hash,
            "snapshot_hash": "future-snapshot-hash",
            "normalization_version": NORMALIZATION_VERSION,
            "matcher_version": MATCHER_VERSION,
            "state": "future_state",
        }
    )
    save_ledger(ledger_path, ledger)


def _complete_recommendation() -> str:
    return (
        "We decide to evaluate Foo for technical support, because the C1 and C2 evidence "
        "shows a partial fit, accepting the trade-off of not adopting it yet."
    )


def test_transfer_light_mode_rejects_adopt_ring(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("adopt", _complete_recommendation()), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="transfer")
    assert not report.passed
    assert any(f.check == "ring_backed_by_evidence" for f in report.failures)


def test_transfer_adopt_requires_probe_validated(tmp_path):
    r = _make(tmp_path, mode="full")
    r.artifact_path("decision-memo.md").write_text(
        _memo("adopt", _complete_recommendation()), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="transfer")
    assert any(f.check == "ring_backed_by_evidence" for f in report.failures)
    # With a validated probe and a current verify-probe, the same memo passes the ring check.
    r.artifact_path("probe/results.md").write_text("ok", encoding="utf-8")
    r.meta.validation["probe"] = "deadbeef"
    r.meta.verify_probe = {"result": "pass", "probe_hash": probe_verify.hash_probe_dir(r)}
    r.save()
    report2 = gates.check_stage(r, stage="transfer")
    assert all(x.passed for x in report2.results if x.check == "ring_backed_by_evidence")


def test_transfer_fails_without_y_statement(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", "We recommend using Foo."), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="transfer")
    assert not report.passed
    assert any(f.check == "y_statement" for f in report.failures)


def test_transfer_keyword_only_y_statement_fails(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", "In the context of X we decide Y accepting Z."), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="transfer")
    assert not report.passed
    assert any(f.check == "y_statement" for f in report.failures)


def test_transfer_complete_y_statement_passes(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation()), encoding="utf-8"
    )
    report = gates.check_stage(r, stage="transfer")
    assert all(x.passed for x in report.results if x.check == "y_statement")


def test_transfer_requires_every_declared_claim_to_exist(tmp_path):
    r = _make(tmp_path, mode="light")
    missing_id = "claim-" + "a" * 64
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), [missing_id]), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    failure = next(item for item in report.failures if item.check == "evidence_claim_ids")
    assert failure.detail == f"decision-memo.md: claim ID does not exist: {missing_id}"


def test_transfer_requires_every_declared_claim_to_be_currently_verified(tmp_path):
    r = _make(tmp_path, mode="light")
    claim_id = _persist_claim(r, state="not_anchored")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), [claim_id]), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    failure = next(item for item in report.failures if item.check == "evidence_claim_ids")
    assert failure.detail == (
        f"decision-memo.md: claim ID is not currently verified: {claim_id} (not_anchored)"
    )


def test_transfer_accepts_unique_existing_verified_claim_ids(tmp_path):
    r = _make(tmp_path, mode="light")
    claim_id = _persist_claim(r)
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), [claim_id]), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    assert all(item.passed for item in report.results if item.check == "evidence_claim_ids")


@pytest.mark.parametrize("references_verified_claim", [False, True])
def test_transfer_ignores_unrelated_forward_compatible_claims(tmp_path, references_verified_claim):
    r = _make(tmp_path, mode="light")
    claim_ids = [_persist_claim(r)] if references_verified_claim else []
    _append_forward_compatible_claim(r)
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), claim_ids), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    assert all(item.passed for item in report.results if item.check == "evidence_claim_ids")


def test_transfer_rejects_forged_verified_claim_from_invalid_ledger(tmp_path):
    r = _make(tmp_path, mode="light")
    claim_id = _persist_claim(r)
    ledger_path = r.artifact_path("notes/sources/verification.yaml")
    ledger_path.write_text(
        ledger_path.read_text(encoding="utf-8").replace(
            "source_id: S1",
            "source_id: S2",
        ),
        encoding="utf-8",
    )
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), [claim_id]), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    failure = next(item for item in report.failures if item.check == "evidence_claim_ids")
    assert "invalid verification ledger" in failure.detail
    assert "claim_id does not match its identity fields" in failure.detail


def test_transfer_rejects_duplicate_top_level_evidence_claim_ids_without_last_wins(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation(), []).replace(
            "evidence_claim_ids: []",
            "evidence_claim_ids: []\nevidence_claim_ids: []",
        ),
        encoding="utf-8",
    )

    report = gates.check_stage(r, stage="transfer")

    assert [failure.detail for failure in report.failures] == [
        "decision-memo.md: duplicate top-level frontmatter key: evidence_claim_ids"
    ]


def test_transfer_schema_v1_accepts_legacy_memo_without_lineage(tmp_path):
    r = _make(tmp_path, mode="light")
    r.meta.schema_version = 1
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation()), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    lineage = [item for item in report.results if item.check == "evidence_claim_ids"]
    assert lineage == [
        gates.GateResult(
            "evidence_claim_ids",
            True,
            "legacy schema v1 decision lineage unavailable",
        )
    ]


def test_transfer_schema_v2_rejects_new_memo_without_lineage(tmp_path):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", _complete_recommendation()), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    assert any(
        failure.check == "frontmatter" and "evidence_claim_ids" in failure.detail
        for failure in report.failures
    )
    assert any(
        failure.check == "evidence_claim_ids" and "must be a list" in failure.detail
        for failure in report.failures
    )


def test_gate_boundary_never_imports_or_reads_cross_investigation_derivations() -> None:
    gate_boundary = (
        Path(inspect.getsourcefile(gates) or ""),
        Path(inspect.getsourcefile(lifecycle) or ""),
    )

    violations = {
        str(path): _gate_boundary_violations(path)
        for path in set(gate_boundary)
        if _gate_boundary_violations(path)
    }

    assert violations == {}
    assert "evidence_claim_ids" in gates._CHECKS
    lineage_source = inspect.getsource(gates._check_evidence_claim_ids)
    assert "validate_evidence_claim_ids" in lineage_source
    assert "load_ledger" in lineage_source


# --- reuse: asset metadata ------------------------------------------------


def test_reuse_requires_type_and_audience(tmp_path):
    r = _make(tmp_path)
    (r.root / "assets" / "post.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: reuse\naudience: externa\n---\n\ncontenido\n",
        encoding="utf-8",
    )
    report = gates.check_stage(r, stage="reuse")
    assert not report.passed
    assert any(f.check == "asset_metadata" and "type" in f.detail for f in report.failures)


def test_reuse_passes_with_full_metadata(tmp_path):
    r = _make(tmp_path)
    (r.root / "assets" / "post.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: reuse\ntype: post\naudience: external\n---\n\ncontenido\n",
        encoding="utf-8",
    )
    report = gates.check_stage(r, stage="reuse")
    assert report.passed, report.failures


def test_reuse_rejects_non_public_asset_vocabulary(tmp_path):
    r = _make(tmp_path)
    (r.root / "assets" / "post.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: reuse\n"
        "type: carrusel\naudience: externa\n---\n\ncontenido\n",
        encoding="utf-8",
    )

    report = gates.check_stage(r, stage="reuse")

    assert not report.passed
    assert {failure.check for failure in report.failures} == {"asset_metadata"}
    assert any(
        "type" in failure.detail and "carrusel" in failure.detail for failure in report.failures
    )
    assert any(
        "audience" in failure.detail and "externa" in failure.detail for failure in report.failures
    )


@pytest.mark.parametrize(
    ("missing", "recommendation"),
    (
        (
            "decision",
            "To evaluate Foo for support, because C1 and C2 show a partial fit, "
            "accepting the trade-off of not adopting it yet.",
        ),
        (
            "context",
            "We decide on Foo, because C1 and C2 show a partial fit, "
            "accepting the risk of an incomplete rollout.",
        ),
        (
            "evidence",
            "We decide to evaluate Foo for technical support, "
            "accepting the trade-off of not adopting it yet.",
        ),
        (
            "downside",
            "We decide to evaluate Foo for technical support, because the C1 and C2 "
            "evidence shows a partial fit.",
        ),
    ),
)
def test_transfer_y_statement_rejects_each_missing_clause(tmp_path, missing, recommendation):
    r = _make(tmp_path, mode="light")
    r.artifact_path("decision-memo.md").write_text(
        _memo("assess", recommendation), encoding="utf-8"
    )

    report = gates.check_stage(r, stage="transfer")

    assert any(f.check == "y_statement" for f in report.failures), (
        f"a recommendation missing its {missing} clause must fail the gate"
    )


def test_transfer_y_statement_tolerates_a_line_wrap_inside_a_clause(tmp_path):
    r = _make(tmp_path, mode="light")
    single_line = (
        "In the context of support, we decide to evaluate Foo for the team, "
        "because the C1 and C2 evidence is partial, accepting the trade-off "
        "of not adopting it yet."
    )
    memo = _memo("assess", single_line)
    wrapped = memo.replace("we decide", "we\ndecide").replace("accepting the", "accepting\nthe")
    r.artifact_path("decision-memo.md").write_text(wrapped, encoding="utf-8")

    report = gates.check_stage(r, stage="transfer")

    assert all(x.passed for x in report.results if x.check == "y_statement"), (
        "a clause split across a line wrap is ordinary prose, not a missing clause"
    )
