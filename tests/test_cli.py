import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from click.testing import CliRunner

from sdr.cli import main
from sdr.cross_investigation import CrossInvestigationLayer, SourceResolution


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))
    runner = CliRunner()

    def _run(*args):
        return runner.invoke(main, list(args), catch_exceptions=False)

    _run.base = tmp_path
    return _run


def _new(run, slug="eval-foo"):
    return run(
        "new",
        slug,
        "--title",
        "Eval Foo",
        "--question",
        "¿Q?",
        "--owner",
        "nacho",
        "--timebox",
        "3",
    )


def _tree_state(root: Path) -> dict[str, tuple[int, bytes | None]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in sorted(root.rglob("*"))
    }


def test_new_creates_structure_and_copies_brief(run):
    result = _new(run)
    assert result.exit_code == 0
    assert (run.base / "eval-foo" / "sdr.yaml").exists()
    assert (run.base / "eval-foo" / "brief.md").exists()


def test_new_rejects_duplicate(run):
    _new(run)
    result = _new(run)
    assert result.exit_code != 0


def test_new_rejects_bad_slug(run):
    result = run("new", "Bad Slug", "--title", "x", "--question", "y")
    assert result.exit_code != 0


@pytest.mark.parametrize(
    "slug", ["/tmp/eval-foo", "../eval-foo", "nested/eval-foo", "nested\\eval-foo"]
)
def test_commands_reject_path_like_slugs_before_loading(run, tmp_path, slug):
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / "sdr.yaml"
    target.write_text(
        "slug: outside\ntitle: Fuera\nquestion: secreta\nstage: intake\nstatus: active\n",
        encoding="utf-8",
    )
    if slug == "../eval-foo":
        sibling = run.base.parent / "eval-foo"
        sibling.mkdir(exist_ok=True)
        (sibling / "sdr.yaml").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    elif slug == "nested/eval-foo":
        nested = run.base / "nested" / "eval-foo"
        nested.mkdir(parents=True)
        (nested / "sdr.yaml").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

    result = run("status", slug)

    assert result.exit_code != 0
    assert "slug inválido" in result.output
    assert "secreta" not in result.output


def _break_brief(run, slug="eval-foo"):
    # Un brief sin la sección de criterios: falla estructura y min criterios.
    (run.base / slug / "brief.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: intake\nowner: nacho\ntimebox: 3\n---\n\n## Pregunta\n¿Q?\n",
        encoding="utf-8",
    )


def test_check_fails_on_incomplete_brief(run):
    _new(run)
    _break_brief(run)
    result = run("check", "eval-foo")
    assert result.exit_code == 1


def test_check_json_reports_failures(run):
    _new(run)
    _break_brief(run)
    result = run("check", "eval-foo", "--json")
    payload = json.loads(result.output)
    assert payload["passed"] is False
    assert any(not r["passed"] for r in payload["results"])


def test_check_json_reports_offline_link_as_skipped_neutral_result(run):
    _new(run)
    note = run.base / "eval-foo" / "notes" / "n1.md"
    note.write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: explore\nsources:\n"
        "  - id: S1\n    url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-07-03\n"
        "  - id: S2\n    url: https://bench.example.org/foo\n    tier: T2\n    date: 2026-07-03\n"
        "---\n\n## Alternativas evaluadas\nFoo existe [S1].\n\n"
        "## Madurez\nFoo es estable [S1].\n\n## Costos\nEl costo es bajo [S2].\n\n"
        "## Riesgos\nLock-in [S2].\n\n## Contra-evidencia\nNinguna.\n",
        encoding="utf-8",
    )

    result = run("check", "eval-foo", "--stage", "explore", "--offline", "--json")
    payload = json.loads(result.output)
    link = next(item for item in payload["results"] if item["check"] == "links_resolve")

    assert result.exit_code == 0
    assert payload["passed"] is True
    assert link["passed"] is False
    assert link["skipped"] is True


def test_check_passes_on_filled_template_structure(run):
    # La plantilla recién copiada tiene estructura completa: el gate estructural
    # pasa; la calidad del contenido es responsabilidad del juez (capa 3).
    _new(run)
    result = run("check", "eval-foo", "--offline")
    assert result.exit_code == 0


def test_snapshot_json_captures_declared_sources(run, monkeypatch):
    _new(run)
    note = run.base / "eval-foo" / "notes" / "n1.md"
    note.write_text(
        "---\n"
        "research: eval-foo\n"
        "date: 2026-07-03\n"
        "stage: explore\n"
        "sources:\n"
        "  - url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "---\n\n"
        "## Alternativas evaluadas\nFoo [S1].\n",
        encoding="utf-8",
    )

    from sdr import snapshot

    def fetcher(url: str) -> snapshot.FetchResult:
        return snapshot.FetchResult(
            declared_url=url,
            final_url=url,
            redirects=(),
            status_code=200,
            content_type="text/html",
            content_eligible=True,
            text="<html><body>Foo tiene documentación oficial.</body></html>",
        )

    monkeypatch.setattr(snapshot, "fetch_url", fetcher)
    result = run("snapshot", "eval-foo", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["slug"] == "eval-foo"
    assert payload["captured"][0]["source_id"] == "S1"
    assert (run.base / "eval-foo" / "notes" / "sources" / "S1" / "content.md").exists()


def test_snapshot_human_output_shows_distinct_locations_and_ordered_redirects(run, monkeypatch):
    _new(run)
    note = run.base / "eval-foo" / "notes" / "n1.md"
    declared_url = "https://declared.example/report"
    intermediate_url = "https://routing.example/step"
    final_url = "https://final.example/mirror"
    note.write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: explore\nsources:\n"
        f"  - url: {declared_url}\n    tier: T1\n    date: 2026-01-01\n"
        "---\n\n## Alternativas evaluadas\nFoo [S1].\n",
        encoding="utf-8",
    )
    from sdr import snapshot
    from sdr.network_policy import RedirectRecord

    monkeypatch.setattr(
        snapshot,
        "fetch_url",
        lambda url: snapshot.FetchResult(
            declared_url=url,
            final_url=final_url,
            redirects=(
                RedirectRecord(url, 302, intermediate_url, intermediate_url),
                RedirectRecord(intermediate_url, 301, final_url, final_url),
            ),
            status_code=200,
            content_type="text/plain",
            content_eligible=True,
            text="Foo docs",
        ),
    )

    result = run("snapshot", "eval-foo")

    assert result.exit_code == 0
    assert f"declarada: {declared_url}" in result.output
    assert f"final: {final_url}" in result.output
    assert f"1. 302 {declared_url} -> {intermediate_url}" in result.output
    assert f"2. 301 {intermediate_url} -> {final_url}" in result.output
    assert "independ" not in result.output.lower()
    assert "autor" not in result.output.lower()


def test_verify_claims_json_declares_ledger_schema_version(run):
    _new(run)
    result = run("verify-claims", "eval-foo", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "schema_version": 2,
        "slug": "eval-foo",
        "passed": True,
        "failures": [],
        "items": [],
    }


def test_status_global_lists_research(run):
    _new(run, "eval-a")
    _new(run, "eval-b")
    result = run("status")
    assert "eval-a" in result.output
    assert "eval-b" in result.output


def test_status_global_json_hides_legacy_judge_but_preserves_file(run):
    _new(run)
    meta = run.base / "eval-foo" / "sdr.yaml"
    original = meta.read_text(encoding="utf-8")
    legacy = "judge:\n  explore:\n    passed: false\n    opaque: keep\n"
    meta.write_text(original + legacy, encoding="utf-8")

    result = run("status", "--json")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert "judge" not in payload[0]
    assert payload[0]["claim_states"] == {}
    assert payload[0]["claims_passed"] is True
    assert legacy in meta.read_text(encoding="utf-8")


def test_drop_marks_dropped(run):
    _new(run)
    result = run("drop", "eval-foo", "--reason", "no aplica")
    assert result.exit_code == 0
    status = run("status", "eval-foo")
    assert "dropped" in status.output


def test_index_writes_file(run):
    _new(run)
    result = run("index")
    assert result.exit_code == 0
    assert (run.base / "INDEX.md").exists()


@pytest.mark.parametrize(
    "args",
    (
        ("cross", "derive", "--json"),
        ("cross", "source", "https://example.com/doc", "--json"),
        ("cross", "degraded", "--json"),
    ),
)
def test_cross_read_only_commands_return_deterministic_json_without_writes(run, args):
    _new(run)
    before = _tree_state(run.base)

    first = run(*args)
    second = run(*args)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert json.loads(first.output) == json.loads(second.output)
    assert first.output == second.output
    assert _tree_state(run.base) == before


def test_cross_degraded_uses_network_only_with_explicit_online_opt_in(run, monkeypatch):
    _new(run)
    calls = []

    def observe(base):
        calls.append(base)
        return ()

    monkeypatch.setattr("sdr.cross_investigation.observe_network_sources", observe)

    offline = run("cross", "degraded", "--json")
    online = run("cross", "degraded", "--online", "--json")

    assert offline.exit_code == 0
    assert online.exit_code == 0
    assert calls == [run.base]


def test_cross_online_json_is_byte_deterministic_only_with_fixed_observation_time(run, monkeypatch):
    _new(run)
    observed_at = datetime(2026, 8, 3, 12, 30, tzinfo=UTC)

    def observe(base, *, observed_at=None):
        assert base == run.base
        return (observed_at,)

    def report(base, *, network_observations, **kwargs):
        assert base == run.base
        timestamp = network_observations[0].isoformat()
        return SimpleNamespace(
            to_dict=lambda: {
                "facts": [{"observation_timestamp": timestamp}],
                "items": [],
                "acknowledgements": [],
            },
            items=(),
        )

    monkeypatch.setattr("sdr.cross_investigation.observe_network_sources", observe)
    monkeypatch.setattr("sdr.cross_investigation.report_degraded_support", report)

    args = (
        "cross",
        "degraded",
        "--online",
        "--observed-at",
        observed_at.isoformat(),
        "--json",
    )
    first = run(*args)
    second = run(*args)

    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output
    assert json.loads(first.output)["facts"][0]["observation_timestamp"] == observed_at.isoformat()


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (yaml.YAMLError("sensitive-ledger-detail"), "invalid YAML"),
        (OSError("sensitive-ledger-detail"), "could not read cross-investigation data"),
    ],
)
def test_cross_cli_expected_data_errors_are_stable_and_redacted(run, monkeypatch, error, message):
    _new(run)

    def fail(base):
        raise error

    monkeypatch.setattr("sdr.cross_investigation.derive_cross_investigation_layer", fail)
    result = CliRunner().invoke(main, ["cross", "derive", "--json"])

    assert result.exit_code == 1
    assert message in result.output
    assert "sensitive-ledger-detail" not in result.output
    assert "traceback" not in result.output.lower()


def test_cross_cli_json_recursively_redacts_sensitive_urls_and_private_paths(run, monkeypatch):
    first_url = "https://example.com/doc?api_key=first-cli-secret&section=methods"
    second_url = "https://example.com/doc?password=second-cli-secret&section=methods"
    private_path = "/" + "home/alice/private/source.txt"
    layer = CrossInvestigationLayer(
        investigations=("eval-foo",),
        joins=({"kind": "test", "nested": {"path": private_path}},),
        sources=(
            SourceResolution(
                investigation="eval-foo",
                source_id="S1",
                url=first_url,
                identity=first_url,
                resolver="normalized-url",
                identifier="doi:10.1000/api-key-study",
            ),
            SourceResolution(
                investigation="eval-foo",
                source_id="S2",
                url=second_url,
                identity=second_url,
                resolver="normalized-url",
                identifier="source:S2",
            ),
        ),
    )
    monkeypatch.setattr(
        "sdr.cross_investigation.derive_cross_investigation_layer", lambda base: layer
    )

    result = run("cross", "derive", "--json")
    repeated = run("cross", "derive", "--json")
    payload = json.loads(result.output)

    assert result.exit_code == repeated.exit_code == 0
    assert result.output == repeated.output
    assert "first-cli-secret" not in result.output
    assert "second-cli-secret" not in result.output
    assert private_path not in result.output
    assert payload["joins"][0]["nested"]["path"] == "<redacted-value-1>"
    assert "api_key=<redacted-value-2>" in result.output
    assert "password=<redacted-value-3>" in result.output
    assert payload["sources"][0]["url"] == payload["sources"][0]["identity"]
    assert payload["sources"][1]["url"] == payload["sources"][1]["identity"]
    assert "section=methods" in result.output
    assert "doi:10.1000/api-key-study" in result.output
    assert "source:S2" in result.output
    assert payload["sources"][0]["identity"] != payload["sources"][1]["identity"]
    assert layer.sources[0].identity == first_url
    assert layer.sources[1].identity == second_url


@pytest.mark.parametrize("marker_first", [False, True])
def test_cross_cli_marker_shaped_sensitive_value_does_not_alias_actual_secret(
    run, monkeypatch, marker_first
):
    marker_input = "<redacted-value-1>"
    private_path = "/" + "home/alice/private/source.txt"
    urls = [
        "https://example.com/doc?token=actual-cli-secret",
        f"https://example.com/doc?token={marker_input}",
    ]
    paths = [private_path, marker_input]
    if marker_first:
        urls.reverse()
        paths.reverse()
    layer = CrossInvestigationLayer(
        investigations=("eval-foo",),
        joins=tuple({"path": path} for path in paths),
        sources=tuple(
            SourceResolution(
                investigation="eval-foo",
                source_id=f"S{index}",
                url=url,
                identity=url,
                resolver="normalized-url",
                identifier=f"source:S{index}",
            )
            for index, url in enumerate(urls, start=1)
        ),
    )
    monkeypatch.setattr(
        "sdr.cross_investigation.derive_cross_investigation_layer", lambda base: layer
    )

    result = run("cross", "derive", "--json")
    payload = json.loads(result.output)
    redacted_urls = [source["url"] for source in payload["sources"]]
    redacted_paths = [join["path"] for join in payload["joins"]]

    assert result.exit_code == 0
    assert len(set(redacted_urls)) == 2
    assert len(set(redacted_paths)) == 2
    assert "actual-cli-secret" not in result.output
    assert private_path not in result.output
    assert redacted_paths[paths.index(marker_input)] == marker_input
    marker_source_id = f"source:S{urls.index(f'https://example.com/doc?token={marker_input}') + 1}"
    marker_source = next(
        source for source in payload["sources"] if source["identifier"] == marker_source_id
    )
    assert f"token={marker_input}" in marker_source["url"]
    assert all(source["url"] == source["identity"] for source in payload["sources"])


def test_cross_cli_does_not_swallow_programmer_errors(run, monkeypatch):
    _new(run)

    def fail(base):
        raise RuntimeError("programmer defect")

    monkeypatch.setattr("sdr.cross_investigation.derive_cross_investigation_layer", fail)
    with pytest.raises(RuntimeError, match="programmer defect"):
        run("cross", "derive", "--json")


def test_cross_invalid_source_error_is_clean_and_redacted(run):
    _new(run)
    sensitive_url = "malformed source detail"
    note = run.base / "eval-foo" / "notes" / "source.md"
    note.write_text(
        "---\nresearch: eval-foo\ndate: 2026-08-03\nstage: explore\nsources:\n"
        f"  - id: S1\n    url: {sensitive_url}\n---\n",
        encoding="utf-8",
    )

    result = run("cross", "derive", "--json")

    assert result.exit_code == 1
    assert "invalid source URL for eval-foo:S1" in result.output
    assert sensitive_url not in result.output
    assert "traceback" not in result.output.lower()


def test_acknowledge_online_observation_error_is_clean(run, monkeypatch):
    _new(run)

    def fail_observation(base):
        raise ValueError("network observation failed")

    monkeypatch.setattr("sdr.cross_investigation.observe_network_sources", fail_observation)

    result = run(
        "acknowledge-degradation",
        "eval-foo",
        "S1",
        "--cause",
        "unreachable",
        "--observation-id",
        "source-observation-" + "0" * 64,
        "--reason",
        "Reviewed",
        "--by",
        "Reviewer",
        "--online",
    )

    assert result.exit_code == 1
    assert "network observation failed" in result.output
    assert "traceback" not in result.output.lower()


def test_existing_top_level_and_context_verbs_remain_available(run):
    top_level = run("--help")
    context = run("context", "--help")

    assert top_level.exit_code == 0
    assert context.exit_code == 0
    for command in (
        "new",
        "check",
        "snapshot",
        "verify-claims",
        "resolve-claim",
        "verify-probe",
        "advance",
        "approve",
        "drop",
        "reopen",
        "migrate",
        "archive",
        "status",
        "index",
        "doctor",
        "context",
        "integrations",
    ):
        assert command in top_level.output
    for command in ("build", "inspect", "trace", "check", "export", "query"):
        assert command in context.output


def test_representative_existing_verbs_preserve_output_exit_and_mutation_contracts(run):
    _new(run)
    before_status = _tree_state(run.base)

    status = run("status", "eval-foo", "--json")
    missing = run("status", "missing", "--json")
    after_read_only_verbs = _tree_state(run.base)
    index = run("index")

    assert status.exit_code == 0
    assert json.loads(status.output)["slug"] == "eval-foo"
    assert after_read_only_verbs == before_status
    assert _tree_state(run.base) != after_read_only_verbs
    assert (run.base / "INDEX.md").is_file()
    assert missing.exit_code == 1
    assert "no existe la investigación" in missing.output
    assert index.exit_code == 0
    assert index.output == f"índice regenerado: {run.base / 'INDEX.md'}\n"


def test_status_and_index_show_audit_markers(run):
    _new(run)
    meta = run.base / "eval-foo" / "sdr.yaml"
    text = meta.read_text(encoding="utf-8")
    text += "overrides:\n  - stage: explore\n    reason: claim resuelto\n    by: nacho\n    date: '2026-07-03'\n    layer: anchored\napproval:\n  by: nacho\n  date: '2026-07-03'\n"
    meta.write_text(text, encoding="utf-8")

    status = run("status", "eval-foo")
    index = run("index")

    assert "override" in status.output
    assert "self-approved" in status.output
    assert index.exit_code == 0
    assert "Auditoría" in (run.base / "INDEX.md").read_text(encoding="utf-8")


def test_judge_is_a_non_operational_migration_tombstone(run, monkeypatch):
    monkeypatch.setenv("SDR_JUDGE_PROVIDER", "must-not-be-resolved")
    result = run("judge", "eval-foo")
    assert result.exit_code == 1
    assert "retirado" in result.output.lower()
    assert "verify-claims" in result.output
    assert "resolve-claim" in result.output
    assert "must-not-be-resolved" not in result.output
    assert "traceback" not in result.output.lower()


def test_advance_ignores_invalid_judge_config_and_moves_stage(run, monkeypatch):
    _new(run)
    monkeypatch.setenv("SDR_JUDGE_PROVIDER", "bogus")
    result = run("advance", "eval-foo", "--offline")
    assert result.exit_code == 0
    assert "advertencia" not in result.output
    assert "juez" not in result.output.lower()
    assert "traceback" not in result.output.lower()

    status = run("status", "eval-foo")
    assert "etapa: explore" in status.output


def test_advance_help_does_not_advertise_removed_override_flags(run):
    result = run("advance", "--help")
    assert result.exit_code == 0
    assert "--override" not in result.output
    assert "--reason" not in result.output


@pytest.mark.parametrize("flag", ["--override", "--reason"])
def test_advance_removed_override_flags_explain_claim_migration(run, flag):
    args = ["advance", "eval-foo", flag]
    if flag == "--reason":
        args.append("legacy reason")
    result = run(*args)
    assert result.exit_code != 0
    assert "resolve-claim" in result.output
    assert "retir" in result.output.lower()


def test_status_json_hides_legacy_judge_and_reports_claim_states(run):
    _new(run)
    meta = run.base / "eval-foo" / "sdr.yaml"
    text = (
        meta.read_text(encoding="utf-8")
        + "judge:\n  explore:\n    passed: false\n    model: legacy\n"
    )
    meta.write_text(text, encoding="utf-8")
    ledger = run.base / "eval-foo" / "notes" / "sources" / "verification.yaml"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "schema_version: 2\nclaims:\n  - claim_id: c1\n    state: not_anchored\nresolutions: []\nlegacy: []\n",
        encoding="utf-8",
    )

    result = run("status", "eval-foo", "--json")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert "judge" not in payload
    assert payload["claim_states"] == {"not_anchored": 1}
    assert payload["claims_passed"] is False


def test_status_json_does_not_approve_empty_ledger_with_active_claims(run):
    _new(run)
    note = run.base / "eval-foo" / "notes" / "n1.md"
    note.write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-11\nstage: explore\n"
        "sources:\n  - id: S1\n    url: https://example.com\n    tier: T1\n"
        "    date: 2026-07-11\n---\n\n## Evidencia\nFoo funciona [S1].\n",
        encoding="utf-8",
    )
    ledger = run.base / "eval-foo" / "notes" / "sources" / "verification.yaml"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "schema_version: 2\nclaims: []\nresolutions: []\nlegacy: []\n",
        encoding="utf-8",
    )

    result = run("status", "eval-foo", "--json")
    payload = json.loads(result.output)

    assert payload["claims_passed"] is False
    assert payload["claim_ledger"] == "stale"
    assert payload["claim_states"] == {"unverifiable": 1}


def test_doctor_without_judge_config_is_general_and_ready(run, monkeypatch):
    monkeypatch.delenv("SDR_JUDGE_PROVIDER", raising=False)
    monkeypatch.delenv("SDR_JUDGE_CMD", raising=False)
    result = run("doctor", "--json")
    assert result.exit_code == 0
    assert json.loads(result.output) == {"ready": True, "deprecated_environment": []}
    assert "traceback" not in result.output.lower()


def test_approve_fails_outside_transfer(run):
    _new(run)
    result = run("approve", "eval-foo", "--by", "nacho")
    assert result.exit_code != 0


def test_context_build_writes_context_json_and_reports_json(run):
    _new(run)

    result = run("context", "build", "eval-foo", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["slug"] == "eval-foo"
    assert payload["path"].endswith("context/context.json")
    assert payload["nodes"] >= 2
    assert (run.base / "eval-foo" / "context" / "context.json").exists()


def test_context_help_describes_an_auxiliary_derived_graph(run):
    result = run("context", "--help")

    assert result.exit_code == 0
    normalized = result.output.lower()
    assert "auxiliar" in normalized
    assert "no bloqueante" in normalized
    assert "criterios, resultados y decisión" in normalized
    assert "inventario global de fuentes" in normalized


def test_context_build_fails_for_missing_slug(run):
    result = run("context", "build", "missing", "--json")

    assert result.exit_code != 0
    assert "missing" in result.output
    assert not (run.base / "missing" / "context").exists()


def test_context_inspect_reports_json_coverage_and_warnings(run):
    _new(run)
    (run.base / "eval-foo" / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: tiene resultado
- C2: queda pendiente
""".lstrip(),
        encoding="utf-8",
    )
    (run.base / "eval-foo" / "probe" / "results.md").write_text(
        """
## Resultados por criterio

- C1: cumple - evidencia reproducible
""".lstrip(),
        encoding="utf-8",
    )
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "inspect", "eval-foo", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["slug"] == "eval-foo"
    assert payload["nodes"] >= 4
    assert payload["edges"] >= 3
    assert payload["coverage"]["criteria"] == 2
    assert payload["coverage"]["criteria_with_results"] == 1
    assert "criterion without result: C2" in payload["warnings"]


def test_context_inspect_human_output(run):
    _new(run)
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "inspect", "eval-foo")

    assert result.exit_code == 0
    assert "Context Graph: eval-foo" in result.output
    assert "Nodes:" in result.output
    assert "Edges:" in result.output


def test_context_trace_criterion_reports_lineage_json(run):
    _new(run)
    (run.base / "eval-foo" / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: tiene resultado
""".lstrip(),
        encoding="utf-8",
    )
    (run.base / "eval-foo" / "probe" / "results.md").write_text(
        """
## Resultados por criterio

- C1: cumple - evidencia reproducible
""".lstrip(),
        encoding="utf-8",
    )
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "trace", "eval-foo", "--criterion", "C1", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["target"]["id"] == "criterion:C1"
    incoming = {(edge["source"], edge["relation"]) for edge in payload["incoming"]}
    assert ("result:C1", "evaluates") in incoming


def test_context_trace_unknown_criterion_fails_cleanly(run):
    _new(run)
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "trace", "eval-foo", "--criterion", "C99")

    assert result.exit_code != 0
    assert "criterion:C99" in result.output
    assert "traceback" not in result.output.lower()


def test_context_check_warns_by_default_but_exits_zero(run):
    _new(run)
    (run.base / "eval-foo" / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: sin resultado todavía
""".lstrip(),
        encoding="utf-8",
    )
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "check", "eval-foo", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["warnings"] == ["criterion without result: C1"]


def test_context_check_strict_fails_on_warnings(run):
    _new(run)
    (run.base / "eval-foo" / "brief.md").write_text(
        """
## Criterios de evaluación

- C1: sin resultado todavía
""".lstrip(),
        encoding="utf-8",
    )
    assert run("context", "build", "eval-foo").exit_code == 0

    result = run("context", "check", "eval-foo", "--strict")

    assert result.exit_code == 1
    assert "criterion without result: C1" in result.output


def test_context_export_obsidian_mermaid_and_dot_report_json(run):
    _new(run)
    assert run("context", "build", "eval-foo").exit_code == 0

    obsidian = run("context", "export", "eval-foo", "--format", "obsidian", "--json")
    mermaid = run("context", "export", "eval-foo", "--format", "mermaid", "--json")
    dot = run("context", "export", "eval-foo", "--format", "dot", "--json")

    assert obsidian.exit_code == 0
    obsidian_payload = json.loads(obsidian.output)
    assert obsidian_payload["format"] == "obsidian"
    assert obsidian_payload["path"].endswith("context/obsidian")
    assert (run.base / "eval-foo" / "context" / "obsidian" / "index.md").exists()
    assert json.loads(mermaid.output)["path"].endswith("context/context.mmd")
    assert json.loads(dot.output)["path"].endswith("context/context.dot")


def test_context_export_fails_without_context_json(run):
    _new(run)

    result = run("context", "export", "eval-foo", "--format", "obsidian")

    assert result.exit_code != 0
    assert "context graph not found" in result.output
    assert not (run.base / "eval-foo" / "context" / "obsidian").exists()


def test_context_query_why_ring_and_lineage_report_json(run):
    _new(run)
    (run.base / "eval-foo" / "brief.md").write_text(
        "## Criterios de evaluación\n\n- C1: métrica local\n", encoding="utf-8"
    )
    (run.base / "eval-foo" / "probe" / "results.md").write_text(
        "## Resultados por criterio\n\n- C1: no cumple - sin métricas locales\n", encoding="utf-8"
    )
    (run.base / "eval-foo" / "decision-memo.md").write_text(
        "---\nring: hold\n---\n\n## Recomendación\n\nNo avanzar.\n\n## Criterios\n\nC1 bloquea.\n",
        encoding="utf-8",
    )
    assert run("context", "build", "eval-foo").exit_code == 0

    why = run("context", "query", "eval-foo", "why-ring", "--json")
    lineage = run(
        "context", "query", "eval-foo", "criterion-lineage", "--criterion", "C1", "--json"
    )

    assert why.exit_code == 0
    why_payload = json.loads(why.output)
    assert why_payload["ring"] == "hold"
    assert "decision:recommendation" in why_payload["involved_nodes"]
    assert lineage.exit_code == 0
    assert json.loads(lineage.output)["target"] == "criterion:C1"


def test_context_query_errors_are_clean(run):
    _new(run)
    assert run("context", "build", "eval-foo").exit_code == 0

    unknown = run("context", "query", "eval-foo", "bogus")
    missing = run("context", "query", "eval-foo", "criterion-lineage", "--criterion", "C99")

    assert unknown.exit_code != 0
    assert "unknown query intent" in unknown.output
    assert "traceback" not in unknown.output.lower()
    assert missing.exit_code != 0
    assert "criterion:C99" in missing.output


def test_migrate_sets_schema_v2_and_reports_gaps(run, monkeypatch):
    _new(run)
    note = run.base / "eval-foo" / "notes" / "n1.md"
    note.write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: explore\nsources:\n"
        "  - url: https://docs.foo.dev/guide\n    tier: T1\n    date: 2026-01-01\n"
        "---\n\n## Alternativas evaluadas\nFoo.\n",
        encoding="utf-8",
    )
    meta = run.base / "eval-foo" / "sdr.yaml"
    meta.write_text(
        meta.read_text(encoding="utf-8").replace("schema_version: 2", "schema_version: 1"),
        encoding="utf-8",
    )
    from sdr import snapshot
    from sdr.network_policy import RedirectRecord

    declared_url = "https://docs.foo.dev/guide"
    final_url = "https://mirror.example/guide"

    monkeypatch.setattr(
        snapshot,
        "fetch_url",
        lambda url: snapshot.FetchResult(
            declared_url=url,
            final_url=final_url,
            redirects=(RedirectRecord(url, 302, final_url, final_url),),
            status_code=200,
            content_type="text/plain",
            content_eligible=True,
            text="Foo docs",
        ),
    )

    result = run("migrate", "eval-foo")

    assert result.exit_code == 0
    assert "schema_version: 2" in meta.read_text(encoding="utf-8")
    assert "Contra-evidencia" in result.output
    assert f"declarada: {declared_url}" in result.output
    assert f"final: {final_url}" in result.output
    assert f"1. 302 {declared_url} -> {final_url}" in result.output
    assert "independ" not in result.output.lower()
    assert "autor" not in result.output.lower()


def test_resolve_claim_cli_reports_actionable_domain_error(run):
    _new(run)

    result = run(
        "resolve-claim",
        "eval-foo",
        "claim-does-not-exist",
        "--reason",
        "revisado",
        "--by",
        "nacho",
    )

    assert result.exit_code != 0
    assert "claim-does-not-exist" in result.output
    assert "no existe" in result.output
    assert "traceback" not in result.output.lower()
