import pytest

from sdr import schema


def test_stages_are_the_five_stage_pipeline_in_order():
    assert schema.STAGES == ("intake", "explore", "probe", "transfer", "reuse")


def test_full_mode_includes_probe():
    assert schema.stage_order("full") == (
        "intake",
        "explore",
        "probe",
        "transfer",
        "reuse",
    )


def test_light_mode_skips_probe():
    assert schema.stage_order("light") == ("intake", "explore", "transfer", "reuse")


def test_asset_vocabulary_is_public_and_in_english():
    assert schema.ASSET_TYPES == (
        "playbook",
        "template",
        "post",
        "carousel",
        "script",
        "executive-summary",
        "other",
    )
    assert schema.ASSET_AUDIENCES == ("internal", "external")


def test_next_stage_follows_the_pipeline():
    assert schema.next_stage("intake", "full") == "explore"
    assert schema.next_stage("explore", "full") == "probe"
    assert schema.next_stage("transfer", "full") == "reuse"


def test_next_stage_of_last_stage_is_none():
    assert schema.next_stage("reuse", "full") is None


def test_next_stage_in_light_mode_jumps_over_probe():
    assert schema.next_stage("explore", "light") == "transfer"


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        schema.stage_order("turbo")


def test_every_stage_declares_an_artifact_spec():
    for stage in schema.STAGES:
        spec = schema.artifact_for(stage)
        assert spec.stage == stage
        # Cada etapa valida por secciones obligatorias o por frontmatter+directorio.
        assert spec.required_sections or spec.frontmatter_required, (
            f"{stage} must declare a validation contract"
        )


def test_intake_artifact_requires_evaluation_criteria_section():
    spec = schema.artifact_for("intake")
    assert spec.primary_file == "brief.md"
    assert "Criterios de evaluación" in spec.required_sections


def test_explore_artifact_schema_v1_does_not_require_counter_evidence():
    spec = schema.artifact_for("explore", schema_version=1)
    assert "Contra-evidencia" not in spec.required_sections


def test_explore_artifact_schema_v2_requires_counter_evidence():
    spec = schema.artifact_for("explore", schema_version=2)
    assert "Contra-evidencia" in spec.required_sections


def test_transfer_artifact_schema_v1_allows_legacy_lineage_omission():
    spec = schema.artifact_for("transfer", schema_version=1)

    assert "evidence_claim_ids" not in spec.frontmatter_required
    assert "evidence_claim_ids" in spec.checks


def test_transfer_artifact_schema_v2_requires_exact_claim_lineage():
    spec = schema.artifact_for("transfer", schema_version=2)

    assert "evidence_claim_ids" in spec.frontmatter_required
    assert "evidence_claim_ids" in spec.checks


def test_schema_has_no_operational_judge_rubrics():
    assert not hasattr(schema, "Criterion")
    assert not hasattr(schema, "Rubric")
    assert not hasattr(schema, "rubric")


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("claim-" + "a" * 64, "must be a list"),
        ([""], "must not be empty"),
        (["claim-not-a-digest"], "malformed claim ID"),
        (["claim-" + "a" * 64] * 2, "duplicate claim ID"),
    ],
)
def test_evidence_claim_ids_reject_malformed_and_duplicate_values_deterministically(value, message):
    with pytest.raises(ValueError, match=message):
        schema.validate_evidence_claim_ids(value)


def test_evidence_claim_ids_accept_an_explicit_empty_list():
    assert schema.validate_evidence_claim_ids([]) == ()
