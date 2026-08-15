from pathlib import Path

import pytest
import yaml

from sdr.research import Research, ResearchMeta, is_valid_slug

FIXTURES = Path(__file__).parent / "fixtures"


def test_valid_and_invalid_slugs():
    assert is_valid_slug("eval-voice-agents")
    assert is_valid_slug("foo")
    assert not is_valid_slug("Eval_Voice")
    assert not is_valid_slug("has space")
    assert not is_valid_slug("-leading")


def test_create_initializes_structure_and_metadata(tmp_path):
    r = Research.create(
        base=tmp_path,
        slug="eval-foo",
        title="Evaluar Foo",
        question="¿Sirve Foo para X?",
        mode="full",
        owner="nacho",
        timebox=3,
    )
    assert (tmp_path / "eval-foo" / "sdr.yaml").exists()
    assert (tmp_path / "eval-foo" / "notes").is_dir()
    assert (tmp_path / "eval-foo" / "probe").is_dir()
    assert (tmp_path / "eval-foo" / "assets").is_dir()
    assert r.meta.stage == "intake"
    assert r.meta.status == "active"
    assert r.meta.mode == "full"
    assert r.meta.owner == "nacho"
    assert r.meta.schema_version == 2


def test_create_rejects_bad_slug(tmp_path):
    with pytest.raises(ValueError):
        Research.create(base=tmp_path, slug="Bad Slug", title="x", question="y")


@pytest.mark.parametrize(
    "slug", ["/tmp/eval-foo", "../eval-foo", "nested/eval-foo", "nested\\eval-foo"]
)
def test_create_rejects_path_like_slugs(tmp_path, slug):
    with pytest.raises(ValueError, match="invalid slug"):
        Research.create(base=tmp_path, slug=slug, title="x", question="y")


def test_create_rejects_duplicate(tmp_path):
    Research.create(base=tmp_path, slug="eval-foo", title="x", question="y")
    with pytest.raises(FileExistsError):
        Research.create(base=tmp_path, slug="eval-foo", title="x", question="y")


def test_load_round_trip(tmp_path):
    Research.create(
        base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?", tags=["ia"]
    )
    loaded = Research.load(tmp_path / "eval-foo")
    assert loaded.meta.title == "Evaluar Foo"
    assert loaded.meta.tags == ["ia"]


def test_load_rejects_research_symlink_outside_base(tmp_path):
    base = tmp_path / "research"
    outside = tmp_path / "outside"
    base.mkdir()
    Research.create(base=outside, slug="eval-foo", title="Fuera", question="¿Q?")
    (base / "eval-foo").symlink_to(outside / "eval-foo", target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed root"):
        Research.load(base / "eval-foo", within=base)


def test_artifact_path_rejects_symlink_escape(tmp_path):
    research = Research.create(base=tmp_path / "research", slug="eval-foo", title="x", question="y")
    outside = tmp_path / "outside"
    outside.mkdir()
    (research.root / "notes" / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside the allowed root"):
        research.artifact_path("notes/linked/secret.md")


def test_artifact_directory_rejects_nested_symlink_escape_before_glob(tmp_path):
    research = Research.create(base=tmp_path / "research", slug="eval-foo", title="x", question="y")
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (research.root / "notes" / "linked.md").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the allowed root"):
        research.artifact_path("notes")


def test_historical_judge_and_unknown_metadata_round_trip_without_loss(tmp_path):
    root = tmp_path / "eval-foo"
    root.mkdir()
    source = yaml.safe_load((FIXTURES / "legacy_sdr_judge.yaml").read_text(encoding="utf-8"))
    (root / "sdr.yaml").write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    research = Research.load(root)
    research.save()
    saved = yaml.safe_load((root / "sdr.yaml").read_text(encoding="utf-8"))

    assert saved["judge"] == source["judge"]
    assert saved["future_top_level"] == source["future_top_level"]
    assert research.meta.judge == source["judge"]
    assert isinstance(research.meta.judge["explore"], dict)


def test_nested_approval_and_reopen_unknown_fields_round_trip(tmp_path):
    research = Research.create(base=tmp_path, slug="eval-foo", title="t", question="q")
    path = research.root / "sdr.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["approval"] = {
        "by": "Nacho",
        "date": "2026-07-11",
        "signature_provider": {"id": "legacy-approval"},
    }
    raw["reopens"] = [
        {
            "from_stage": "probe",
            "to_stage": "explore",
            "reason": "nueva evidencia",
            "date": "2026-07-11",
            "legacy_audit": {"ticket": 42},
        }
    ]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    Research.load(research.root).save()
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert saved["approval"] == raw["approval"]
    assert saved["reopens"] == raw["reopens"]


def test_load_legacy_metadata_without_schema_version_defaults_to_v1(tmp_path):
    Research.create(base=tmp_path, slug="eval-foo", title="Evaluar Foo", question="¿Q?")
    meta_path = tmp_path / "eval-foo" / "sdr.yaml"
    text = meta_path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if not line.startswith("schema_version:")]
    meta_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    loaded = Research.load(tmp_path / "eval-foo")

    assert loaded.meta.schema_version == 1


def test_following_stage_uses_mode():
    meta = ResearchMeta(slug="x", title="t", question="q", mode="light", stage="explore")
    assert meta.following_stage() == "transfer"


def test_advancing_from_last_stage_marks_done(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="t", question="q", mode="light")
    r.meta.stage = "reuse"
    r.advance_stage()
    assert r.meta.status == "done"


def test_advance_moves_to_next_stage(tmp_path):
    r = Research.create(base=tmp_path, slug="eval-foo", title="t", question="q", mode="full")
    r.advance_stage()
    assert r.meta.stage == "explore"
    assert r.meta.status == "active"
