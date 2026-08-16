import hashlib
import textwrap
from pathlib import Path

import pytest
import yaml

from sdr import archive, lifecycle, verification
from sdr.research import Research

FIXTURES = Path(__file__).parent / "fixtures"


def _make(tmp_path, mode="full", stage="probe"):
    r = Research.create(
        base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?", mode=mode
    )
    r.meta.stage = stage
    r.meta.validation = {"intake": "aaa", "explore": "bbb"}
    r.save()
    return r


# --- reopen -----------------------------------------------------------------


def test_reopen_goes_back_and_records_history(tmp_path):
    r = _make(tmp_path, stage="probe")
    lifecycle.reopen(r, to="explore", reason="el probe invalidó los criterios")
    assert r.meta.stage == "explore"
    assert r.meta.reopens[0].from_stage == "probe"
    assert r.meta.reopens[0].to_stage == "explore"
    assert "invalidó" in r.meta.reopens[0].reason


def test_reopen_invalidates_hashes_from_target_onward(tmp_path):
    r = _make(tmp_path, stage="probe")
    lifecycle.reopen(r, to="explore", reason="x")
    # intake queda validado; explore y posteriores se invalidan.
    assert "intake" in r.meta.validation
    assert "explore" not in r.meta.validation


def test_reopen_forward_is_rejected(tmp_path):
    r = _make(tmp_path, stage="explore")
    with pytest.raises(ValueError):
        lifecycle.reopen(r, to="probe", reason="x")


def test_reopen_to_current_stage_is_rejected(tmp_path):
    r = _make(tmp_path, stage="explore")
    with pytest.raises(ValueError):
        lifecycle.reopen(r, to="explore", reason="x")


def test_reopen_requires_reason(tmp_path):
    r = _make(tmp_path, stage="probe")
    with pytest.raises(ValueError):
        lifecycle.reopen(r, to="intake", reason="")


def test_reopen_reactivates_done_research(tmp_path):
    r = _make(tmp_path, stage="reuse")
    r.meta.status = "done"
    r.save()
    lifecycle.reopen(r, to="transfer", reason="la recomendación quedó vieja")
    assert r.meta.status == "active"
    assert r.meta.stage == "transfer"


# --- archive ----------------------------------------------------------------


def _finished(tmp_path):
    r = _make(tmp_path, stage="reuse")
    r.meta.status = "done"
    r.save()
    r.artifact_path("decision-memo.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-03
            stage: transfer
            ring: trial
            audience: equipo
            ---

            ## Recommendation
            In the context of X we decide to pilot Foo to achieve Y, because the evidence supports it, accepting Z.

            ## Alternatives evaluated
            Foo, Bar.

            ## Selection criteria
            Costo.

            ## Risks and limitations
            Lock-in.

            ## Next steps
            Piloto.

            ## Audience
            Equipo.
            """
        ).lstrip(),
        encoding="utf-8",
    )
    r.artifact_path("probe/results.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-03\nstage: probe\n---\n\n"
        "## Results by criterion\n- C1: cumple\n\n## Reproduction\n```bash\nx\n```\n",
        encoding="utf-8",
    )
    return r


def test_archive_writes_knowledge_synthesis(tmp_path):
    r = _finished(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    path = archive.archive_research(r, knowledge_dir)
    assert path == knowledge_dir / "eval-foo.md"
    text = path.read_text(encoding="utf-8")
    assert "we decide to pilot Foo" in text  # recommendation
    assert "trial" in text  # ring
    assert "C1" in text  # resultados por criterio
    assert "research/eval-foo" in text  # enlace a evidencia


def test_archive_marks_archived_in_meta(tmp_path):
    r = _finished(tmp_path)
    archive.archive_research(r, tmp_path / "knowledge")
    reloaded = Research.load(r.root)
    assert reloaded.meta.archived


def test_archive_rejects_output_symlink_escape(tmp_path):
    research = _finished(tmp_path)
    knowledge_dir = tmp_path / "knowledge"
    outside = tmp_path / "outside.md"
    knowledge_dir.mkdir()
    outside.write_text("keep", encoding="utf-8")
    (knowledge_dir / "eval-foo.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the allowed root"):
        archive.archive_research(research, knowledge_dir)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_archive_preserves_historical_judge_and_unknown_metadata(tmp_path):
    r = _finished(tmp_path)
    source = yaml.safe_load((FIXTURES / "legacy_sdr_judge.yaml").read_text(encoding="utf-8"))
    meta_path = r.root / "sdr.yaml"
    current = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    current["judge"] = source["judge"]
    current["future_top_level"] = source["future_top_level"]
    meta_path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
    r = Research.load(r.root)

    archive.archive_research(r, tmp_path / "knowledge")
    saved = yaml.safe_load(meta_path.read_text(encoding="utf-8"))

    assert saved["archived"] is True
    assert saved["judge"] == source["judge"]
    assert saved["future_top_level"] == source["future_top_level"]
    synthesis = (tmp_path / "knowledge" / "eval-foo.md").read_text(encoding="utf-8")
    assert "Judge" not in synthesis
    assert "legacy-model" not in synthesis


def test_archive_preserves_nested_metadata_and_verification_ledger_bytes(tmp_path):
    r = _finished(tmp_path)
    meta_path = r.root / "sdr.yaml"
    raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    raw["approval"] = {
        "by": "Nacho",
        "date": "2026-07-11",
        "unknown": {"signature": "keep"},
    }
    raw["reopens"] = [
        {
            "from_stage": "reuse",
            "to_stage": "transfer",
            "reason": "auditoría",
            "date": "2026-07-11",
            "unknown": {"ticket": 7},
        }
    ]
    meta_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    ledger_path = r.artifact_path("notes/sources/verification.yaml")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(
        "schema_version: 2\nclaims: []\nresolutions: []\nlegacy:\n"
        "  - verdict: supported\n    opaque: {keep: true}\nfuture_top: preserve\n",
        encoding="utf-8",
    )
    before = ledger_path.read_bytes()

    archive.archive_research(Research.load(r.root), tmp_path / "knowledge")
    saved = yaml.safe_load(meta_path.read_text(encoding="utf-8"))

    assert saved["approval"] == raw["approval"]
    assert saved["reopens"] == raw["reopens"]
    assert ledger_path.read_bytes() == before


def test_archive_reports_deterministic_claim_states(tmp_path):
    r = _finished(tmp_path)
    ledger = r.artifact_path("notes/sources/verification.yaml")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "schema_version: 2\nclaims:\n"
        "  - claim_id: c1\n    state: verified\n"
        "  - claim_id: c2\n    state: human_reviewed\n"
        "resolutions: []\nlegacy: []\n",
        encoding="utf-8",
    )

    path = archive.archive_research(r, tmp_path / "knowledge")
    text = path.read_text(encoding="utf-8")

    assert "## Claim states" in text
    assert "verified: 1" in text
    assert "human_reviewed: 1" in text


def test_archive_rejects_stale_claim_ledger_after_snapshot_changes(tmp_path):
    r = _finished(tmp_path)
    r.artifact_path("notes/n1.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-11
            stage: explore
            sources:
              - id: S1
                url: https://docs.example.com/foo
                tier: T1
                date: 2026-07-11
            ---

            ## Evidencia
            Foo reduce latencia [S1].
            """
        ).lstrip(),
        encoding="utf-8",
    )
    source = r.artifact_path("notes/sources/S1")
    source.mkdir(parents=True)
    content = "Foo reduce latencia."
    source.joinpath("content.md").write_text(content, encoding="utf-8")
    source.joinpath("meta.yaml").write_text(
        yaml.safe_dump(
            {
                "url": "https://docs.example.com/foo",
                "status": "ok",
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    verification.verify_explore_claims(r)
    source.joinpath("content.md").write_text("Contenido reemplazado.", encoding="utf-8")

    with pytest.raises(ValueError, match="ledger.*stale"):
        archive.archive_research(r, tmp_path / "knowledge")


def test_archive_rejects_empty_ledger_when_active_claims_exist(tmp_path):
    r = _finished(tmp_path)
    r.artifact_path("notes/n1.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-11\nstage: explore\n"
        "sources:\n  - id: S1\n    url: https://example.com\n    tier: T1\n"
        "    date: 2026-07-11\n---\n\n## Evidencia\nFoo funciona [S1].\n",
        encoding="utf-8",
    )
    ledger = r.artifact_path("notes/sources/verification.yaml")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        "schema_version: 2\nclaims: []\nresolutions: []\nlegacy: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="ledger.*pending"):
        archive.archive_research(r, tmp_path / "knowledge")


def test_archive_rejects_current_ledger_with_pending_claim(tmp_path):
    r = _finished(tmp_path)
    r.artifact_path("notes/n1.md").write_text(
        "---\nresearch: eval-foo\ndate: 2026-07-11\nstage: explore\n"
        "sources:\n  - id: S1\n    url: https://example.com\n    tier: T1\n"
        "    date: 2026-07-11\n---\n\n## Evidencia\nFoo funciona [S1].\n",
        encoding="utf-8",
    )
    verification.verify_explore_claims(r)

    with pytest.raises(ValueError, match="ledger.*pending"):
        archive.archive_research(r, tmp_path / "knowledge")


def test_archive_rejects_active_research(tmp_path):
    r = _make(tmp_path, stage="explore")
    with pytest.raises(ValueError):
        archive.archive_research(r, tmp_path / "knowledge")


def test_archive_of_dropped_records_reason(tmp_path):
    r = _make(tmp_path, stage="explore")
    r.meta.status = "dropped"
    r.meta.dropped_reason = "tecnologia inmadura"
    r.save()
    path = archive.archive_research(r, tmp_path / "knowledge")
    assert "tecnologia inmadura" in path.read_text(encoding="utf-8")
