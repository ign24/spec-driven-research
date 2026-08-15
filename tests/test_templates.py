from importlib import resources
from pathlib import Path

import pytest

from sdr import schema
from sdr.parser import parse_artifact

TEMPLATES_DIR = resources.files("sdr").joinpath("templates")
ROOT_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stage", schema.STAGES)
def test_template_exists_for_every_stage(stage):
    assert (TEMPLATES_DIR / schema.template_for(stage)).exists()


@pytest.mark.parametrize("stage", schema.STAGES)
def test_template_declares_required_frontmatter(stage):
    art = parse_artifact(TEMPLATES_DIR / schema.template_for(stage))
    spec = schema.artifact_for(stage)
    for field_name in spec.frontmatter_required:
        assert field_name in art.frontmatter, f"{stage}: falta frontmatter {field_name}"


@pytest.mark.parametrize("stage", schema.STAGES)
def test_template_declares_required_sections(stage):
    art = parse_artifact(TEMPLATES_DIR / schema.template_for(stage))
    spec = schema.artifact_for(stage)
    for section in spec.required_sections:
        assert art.section(section) is not None, f"{stage}: falta sección {section}"


def test_template_stage_matches_frontmatter_stage():
    for stage in schema.STAGES:
        art = parse_artifact(TEMPLATES_DIR / schema.template_for(stage))
        assert art.frontmatter.get("stage") == stage


def test_note_template_includes_source_ids_and_inline_citation_example():
    text = (TEMPLATES_DIR / "note.md").read_text(encoding="utf-8")
    assert "id: S1" in text
    assert "[S1]" in text


def test_templates_document_source_freshness_and_tier_justifications():
    brief = (TEMPLATES_DIR / "brief.md").read_text(encoding="utf-8")
    note = (TEMPLATES_DIR / "note.md").read_text(encoding="utf-8")
    assert "source_max_age" in brief
    assert "tier_justification" in note
    assert "date_justification" in note


def test_note_template_documents_contextual_references_outside_claim_matching():
    text = (TEMPLATES_DIR / "note.md").read_text(encoding="utf-8")
    assert "[cf. S1]" in text
    assert "context, synthesis, or" in text
    assert "stays out of textual matching" in text


def test_decision_template_declares_structured_evidence_claim_ids():
    art = parse_artifact(TEMPLATES_DIR / "decision-memo.md")

    assert art.frontmatter["evidence_claim_ids"] == []


def test_asset_template_and_skill_use_only_public_english_vocabulary():
    template = (TEMPLATES_DIR / "asset.md").read_text(encoding="utf-8")
    skill = (ROOT_DIR / "skills" / "sdr-reuse" / "SKILL.md").read_text(encoding="utf-8")
    expected_types = "playbook|template|post|carousel|script|executive-summary|other"

    assert expected_types in template
    assert "internal|external" in template
    for value in schema.ASSET_TYPES:
        assert f"`{value}`" in skill
    for value in schema.ASSET_AUDIENCES:
        assert f"`{value}`" in skill
    assert "opcional al cierre" not in skill


def test_new_skill_documents_reuse_as_required_in_light_mode():
    skill = (ROOT_DIR / "skills" / "sdr-new" / "SKILL.md").read_text(encoding="utf-8")

    assert "intake -> explore -> transfer -> reuse -> done" in skill


def test_public_documentation_agrees_on_active_validation_controls():
    expected_controls = (
        "Structural",
        "Evidential",
        "Textual anchoring",
        "Executable",
        "Hash consistency",
        "HITL",
    )
    for filename in ("AGENTS.md", "README.md", "docs/validation.md"):
        text = (ROOT_DIR / filename).read_text(encoding="utf-8")
        positions = [text.index(name) for name in expected_controls]
        assert positions == sorted(positions), f"{filename}: controls absent or out of order"
        assert "Context Graph" in text
        assert "non-blocking" in text


def test_documentation_defines_contextual_references_without_active_judge():
    for filename in ("AGENTS.md", "README.md"):
        text = (ROOT_DIR / filename).read_text(encoding="utf-8")
        normalized = text.lower()
        assert "[cf. S" in text
        assert "no crea un claim" in normalized or "does not create a claim" in normalized
        assert "entra al matching" in normalized or "enter textual matching" in normalized
        assert (
            "no usa modelos" in normalized
            or "ninguna validación depende de un llm" in normalized
            or "does not use models" in normalized
        )
        assert "configuración del juez semántico" not in normalized
        assert "sdr_judge_provider=" not in normalized
        assert "--extra judge" not in normalized
        assert "chain of custody" not in normalized
        assert "cadena de custodia" not in normalized


def test_documentation_separates_claim_resolution_from_transfer_approval():
    for filename in ("AGENTS.md", "README.md"):
        text = (ROOT_DIR / filename).read_text(encoding="utf-8")
        normalized = text.lower()
        assert "resolve-claim" in text
        assert "approve" in text
        assert (
            "no sustituye" in normalized
            or "no reemplaza" in normalized
            or "does not replace or substitute" in normalized
        )


def test_readme_offline_example_uses_offline_flag():
    text = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    assert "uv run sdr check example-study --offline" in text


def test_public_documentation_set_includes_both_readmes():
    expected = (
        "README.md",
        "README.es.md",
        "assets/sdr-banner.png",
        "docs/README.md",
        "docs/README.es.md",
        "docs/getting-started.md",
        "docs/getting-started.es.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CHANGELOG.md",
        "LICENSE",
        "docs/workflow.md",
        "docs/cli-reference.md",
        "docs/evidence-model.md",
        "docs/security-model.md",
        "docs/integrations.md",
        "docs/validation.md",
        "docs/releasing.md",
    )
    for filename in expected:
        assert (ROOT_DIR / filename).is_file(), filename


def test_cli_reference_documents_cross_investigation_command_contracts():
    text = (ROOT_DIR / "docs/cli-reference.md").read_text(encoding="utf-8")

    for command in (
        "sdr cross derive",
        "sdr cross source",
        "sdr cross degraded",
        "sdr acknowledge-degradation",
    ):
        assert command in text
    for contract in (
        "read-only",
        "--json",
        "--online",
        "advisory",
        "never blocks",
        "--no-commit",
        "commits by default",
        "preserves pre-existing staging",
        "offline JSON is byte-deterministic",
        "observation timestamp",
        "fixed observation time",
        "staged target ledger",
        "declared URL",
        "final URL provenance",
    ):
        assert contract in text


def test_root_readmes_reserve_shared_replaceable_banner_slot():
    marker = '<img src="assets/sdr-banner.png" alt="Spec-Driven Research">'

    for filename in ("README.md", "README.es.md"):
        text = (ROOT_DIR / filename).read_text(encoding="utf-8")
        assert marker in text
        assert text.index(marker) < text.index("# Spec-Driven Research")


def test_readme_documents_real_modes_commands_and_probe_execution_contract():
    text = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    for required in (
        "intake -> explore -> probe -> transfer -> reuse -> done",
        "intake -> explore -> transfer -> reuse -> done",
        "verify.action: run",
        "argv",
        "without a shell",
        "README.es.md",
        "documented",
    ):
        assert required in text


def test_security_model_names_external_trust_boundaries():
    text = (ROOT_DIR / "docs/security-model.md").read_text(encoding="utf-8")
    for boundary in ("Notes", "Snapshots", "Repositories", "URLs", "Probe commands", "Git"):
        assert boundary in text
