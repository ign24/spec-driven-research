import inspect
import textwrap

from sdr import lifecycle, probe_verify
from sdr.context_export import export_context_graph
from sdr.context_graph import (
    build_sdr_context_graph,
    inspect_context_graph,
    trace_context_graph,
    write_context_graph,
)
from sdr.context_query import query_context_graph
from sdr.research import Approval, Research


def _valid_intake(tmp_path, mode="full"):
    r = Research.create(
        base=tmp_path, slug="eval-foo", title="t", question="q", mode=mode, owner="nacho", timebox=3
    )
    r.artifact_path("brief.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-03
            stage: intake
            owner: nacho
            timebox: 3
            ---

            ## Pregunta
            ¿Sirve Foo?

            ## Hipótesis
            Sí.

            ## Contexto
            X.

            ## Alcance
            A, no B.

            ## Criterios de evaluación
            - C1: latencia < 200ms
            - C2: costo < 10 USD

            ## Riesgos de adopción
            Lock-in.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return r


def test_stage_hash_changes_with_content(tmp_path):
    r = _valid_intake(tmp_path)
    h1 = lifecycle.stage_hash(r, "intake")
    r.artifact_path("brief.md").write_text("cambiado", encoding="utf-8")
    assert lifecycle.stage_hash(r, "intake") != h1


def test_advance_blocked_when_gate_fails(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="t", question="q")
    result = lifecycle.advance(r)
    assert not result.ok
    assert r.meta.stage == "intake"
    assert result.gate_report.failures


def test_advance_preserves_legacy_judge_metadata_without_operational_judge(tmp_path):
    r = _valid_intake(tmp_path)
    legacy = {"intake": {"passed": False, "opaque": {"keep": True}}}
    r.meta.judge = legacy
    result = lifecycle.advance(r)
    assert result.ok
    assert r.meta.stage == "explore"
    assert r.meta.judge == legacy
    assert not hasattr(result, "judge_verdict")
    assert not hasattr(result, "warnings")


def test_advance_success_records_hash_and_moves_stage(tmp_path):
    r = _valid_intake(tmp_path)
    result = lifecycle.advance(r)
    assert result.ok
    assert r.meta.stage == "explore"
    assert "intake" in r.meta.validation


def test_advance_has_no_semantic_judge_or_override_callbacks():
    assert tuple(inspect.signature(lifecycle.advance).parameters) == ("research", "offline")


def test_advance_explore_runs_local_verification_offline_and_blocks_pending(tmp_path):
    r = _valid_intake(tmp_path, mode="light")
    first = lifecycle.advance(r)
    assert first.ok and r.meta.stage == "explore"
    notes = r.artifact_path("notes")
    notes.mkdir(exist_ok=True)
    (notes / "scan.md").write_text(
        """
---
research: eval-foo
date: 2026-07-03
stage: explore
sources:
  - id: S1
    url: https://docs.foo.com/guide
    tier: T1
    date: 2026-07-03
    alternative: foo
  - id: S2
    url: https://bench.example.org/foo
    tier: T2
    date: 2026-07-03
    alternative: foo
---

## Alternativas evaluadas
Foo existe [S1].

## Madurez
Foo tiene soporte [S1].

## Costos
Foo es barato [S2].

## Riesgos
Foo tiene riesgos [S2].

## Contra-evidencia
No se encontró contra-evidencia.
""".lstrip(),
        encoding="utf-8",
    )
    result = lifecycle.advance(r, offline=True)
    assert not result.ok
    assert r.meta.stage == "explore"
    assert "unverifiable" in result.blocked_reason
    assert r.meta.overrides == []


def test_check_consistency_detects_tampering(tmp_path):
    r = _valid_intake(tmp_path)
    lifecycle.advance(r)
    # Manipula el artefacto ya validado sin re-validar.
    r.artifact_path("brief.md").write_text("hackeado", encoding="utf-8")
    issues = lifecycle.check_consistency(r)
    assert any("intake" in msg for msg in issues)


def test_advance_blocks_when_validated_artifact_changed(tmp_path):
    r = _valid_intake(tmp_path)
    first = lifecycle.advance(r)
    assert first.ok
    r.artifact_path("brief.md").write_text("hackeado", encoding="utf-8")

    result = lifecycle.advance(r)

    assert not result.ok
    assert r.meta.stage == "explore"
    assert "intake" in result.blocked_reason
    assert "cambió" in result.blocked_reason


def test_context_graph_artifacts_do_not_change_stage_hash_or_consistency(tmp_path):
    r = _valid_intake(tmp_path)
    lifecycle.advance(r)
    stored_hash = r.meta.validation["intake"]

    graph = build_sdr_context_graph(r)
    write_context_graph(graph, r.root)

    assert lifecycle.stage_hash(r, "intake") == stored_hash
    assert lifecycle.check_consistency(r) == []


def test_context_exports_and_queries_do_not_change_stage_hash_or_consistency(tmp_path):
    r = _valid_intake(tmp_path)
    lifecycle.advance(r)
    stored_hash = r.meta.validation["intake"]
    graph = build_sdr_context_graph(r)
    write_context_graph(graph, r.root)

    export_context_graph(graph, r.root, "obsidian")
    export_context_graph(graph, r.root, "mermaid")
    query_context_graph(graph, "gaps")

    assert lifecycle.stage_hash(r, "intake") == stored_hash
    assert lifecycle.check_consistency(r) == []


def test_context_graph_build_inspect_trace_then_normal_lifecycle(tmp_path):
    r = _valid_intake(tmp_path)
    r.artifact_path("probe/results.md").write_text(
        "## Resultados por criterio\n\n- C1: cumple - evidencia\n- C2: cumple - evidencia\n",
        encoding="utf-8",
    )

    graph = build_sdr_context_graph(r)
    path = write_context_graph(graph, r.root)

    inspected = inspect_context_graph(graph)
    traced = trace_context_graph(graph, "criterion:C1")
    advanced = lifecycle.advance(r)

    assert path.exists()
    assert inspected["coverage"]["criteria"] == 2
    assert inspected["coverage"]["criteria_with_results"] == 2
    assert traced["target"]["id"] == "criterion:C1"
    assert advanced.ok and r.meta.stage == "explore"


def test_transfer_requires_human_approval(tmp_path):
    r = _valid_intake(tmp_path, mode="light")
    r.meta.stage = "transfer"
    r.artifact_path("decision-memo.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-03
            stage: transfer
            ring: assess
            audience: equipo
            evidence_claim_ids: []
            ---

            ## Recomendación
            Decidimos evaluar Foo para soporte, porque la evidencia de C1 y C2 es parcial, aceptando el trade-off de no adoptarlo todavía.

            ## Alternativas evaluadas
            Foo, Bar.

            ## Criterios de selección
            Costo.

            ## Riesgos y limitaciones
            Lock-in.

            ## Próximos pasos
            Piloto.

            ## Audiencia
            Equipo.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    r.save()
    blocked = lifecycle.advance(r)
    assert not blocked.ok
    assert "aprob" in (blocked.blocked_reason or "").lower()

    r.meta.approval = Approval(by="nacho", date="2026-07-03")
    r.save()
    ok = lifecycle.advance(r)
    assert ok.ok
    assert r.meta.stage == "reuse"


def test_reopened_schema_v1_transfer_memo_without_lineage_advances_deterministically(tmp_path):
    r = _valid_intake(tmp_path, mode="light")
    r.meta.schema_version = 1
    r.meta.stage = "transfer"
    r.meta.approval = Approval(by="nacho", date="2026-07-03")
    r.artifact_path("decision-memo.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-03
            stage: transfer
            ring: assess
            audience: equipo
            ---

            ## Recomendación
            Decidimos evaluar Foo para soporte, porque la evidencia es parcial, aceptando el trade-off de no adoptarlo todavía.

            ## Alternativas evaluadas
            Foo, Bar.

            ## Criterios de selección
            Costo.

            ## Riesgos y limitaciones
            Lock-in.

            ## Próximos pasos
            Piloto.

            ## Audiencia
            Equipo.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    r.save()

    result = lifecycle.advance(r)

    assert result.ok
    assert r.meta.stage == "reuse"


def _valid_probe(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-probe", title="t", question="q")
    r.meta.stage = "probe"
    r.artifact_path("brief.md").write_text(
        "## Criterios de evaluación\n\n- C1: salida OK\n- C2: costo bajo\n",
        encoding="utf-8",
    )
    r.artifact_path("probe/results.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-probe
            date: 2026-07-03
            stage: probe
            verify:
              action: run
              command: python check.py
              expect: OK
            ---

            ## Resultados por criterio
            - C1: cumple - salida OK
            - C2: cumple - costo bajo

            ## Reproducción
            ```bash
            python check.py
            ```
            """
        ).lstrip(),
        encoding="utf-8",
    )
    r.artifact_path("probe/check.py").write_text("print('OK')\n", encoding="utf-8")
    r.save()
    return r


def test_probe_check_covers_structure_without_requiring_execution(tmp_path):
    r = _valid_probe(tmp_path)

    report = lifecycle.gates.check_stage(r, stage="probe", offline=True)

    assert report.passed, report.failures
    assert not r.meta.verify_probe


def test_advance_probe_requires_persisted_green_current_verification(tmp_path, monkeypatch):
    r = _valid_probe(tmp_path)
    monkeypatch.setattr(
        probe_verify,
        "verify_probe",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute probe")),
    )

    missing = lifecycle.advance(r, offline=True)
    assert not missing.ok
    assert "verify-probe" in missing.blocked_reason

    r.meta.verify_probe = {"result": "pass", "probe_hash": "stale"}
    r.save()
    stale = lifecycle.advance(r, offline=True)
    assert not stale.ok
    assert "vigente" in stale.blocked_reason

    r.meta.verify_probe = {
        "result": "pass",
        "probe_hash": probe_verify.hash_probe_dir(r),
    }
    r.save()
    green = lifecycle.advance(r, offline=True)
    assert green.ok
    assert r.meta.stage == "transfer"
