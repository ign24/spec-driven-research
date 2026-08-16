import hashlib
import textwrap

import pytest
import yaml

from sdr import index, verification
from sdr.research import Research


def test_index_lists_research_with_stage_and_status(tmp_path):
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    Research.create(base=tmp_path, slug="eval-b", title="Eval B", question="q", mode="light")
    md = index.build_index(tmp_path)
    assert "eval-a" in md
    assert "Eval B" in md
    assert "intake" in md


def test_index_includes_ring_when_memo_exists(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    r.artifact_path("decision-memo.md").write_text(
        "---\nresearch: eval-a\nstage: transfer\nring: trial\naudience: equipo\n---\n\ntexto\n",
        encoding="utf-8",
    )
    md = index.build_index(tmp_path)
    assert "trial" in md


def test_write_index_creates_file(tmp_path):
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    path = index.write_index(tmp_path)
    assert path.exists()
    assert path.name == "INDEX.md"
    assert "eval-a" in path.read_text(encoding="utf-8")


def test_write_index_rejects_output_symlink_escape(tmp_path):
    outside = tmp_path / "outside.md"
    base = tmp_path / "research"
    base.mkdir()
    outside.write_text("keep", encoding="utf-8")
    (base / "INDEX.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the allowed root"):
        index.write_index(base)

    assert outside.read_text(encoding="utf-8") == "keep"


def test_index_rejects_research_symlink_escape(tmp_path):
    base = tmp_path / "research"
    outside = tmp_path / "outside"
    base.mkdir()
    Research.create(base=outside, slug="eval-a", title="Fuera", question="q")
    (base / "eval-a").symlink_to(outside / "eval-a", target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed root"):
        index.build_index(base)


def test_index_ignores_directories_without_metadata(tmp_path):
    (tmp_path / "not-a-research").mkdir()
    Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    md = index.build_index(tmp_path)
    assert "not-a-research" not in md
    assert "eval-a" in md


def test_index_reports_deterministic_claim_states_without_legacy_judge(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    r.meta.judge = {"explore": {"passed": False, "model": "legacy"}}
    r.save()
    ledger = r.artifact_path("notes/sources/verification.yaml")
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "claims": [{"claim_id": "c1", "state": "human_reviewed"}],
                "resolutions": [],
                "legacy": [],
            }
        ),
        encoding="utf-8",
    )

    md = index.build_index(tmp_path)

    assert "human_reviewed:1" in md
    assert "flagger" not in md
    assert "legacy" not in md


def test_index_recalculates_claims_without_persisting_and_marks_stale_ledger(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-a", title="Eval A", question="q")
    r.artifact_path("notes/n1.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-a
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
                "schema_version": 2,
                "url": "https://docs.example.com/foo",
                "declared_url": "https://docs.example.com/foo",
                "final_url": "https://docs.example.com/foo",
                "redirects": [],
                "http_status": 200,
                "captured_at": "2026-07-11T00:00:00+00:00",
                "content_type": "text/plain",
                "content_eligible": True,
                "status": "ok",
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    assert verification.verify_explore_claims(r).passed
    ledger = r.artifact_path("notes/sources/verification.yaml")
    before = ledger.read_bytes()
    source.joinpath("content.md").write_text("Contenido reemplazado.", encoding="utf-8")

    md = index.build_index(tmp_path)

    assert "unverifiable:1" in md
    assert "stale" in md
    assert ledger.read_bytes() == before
