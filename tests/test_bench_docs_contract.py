"""Documentation-contract tests for the harness README.

These assert that `bench/README.md` documents the rescoped evaluation-question
contract from `openspec/changes/add-live-harness-evidence/design.md` (Decisions
1-11): three separate evaluation questions, corpus migration, the credential
boundary, exact reuse outcomes, orthogonal treatments, pilot identity, the HITL
stop, schema version 2, and the standing prohibited-claims block.

This is a documentation contract, not a behavioral test: it only asserts that
specific claims/phrases exist in the harness README text.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH_README = PROJECT_ROOT / "bench" / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    return BENCH_README.read_text(encoding="utf-8")


def test_documents_three_separate_non_aggregated_evaluation_questions(readme_text: str) -> None:
    for question in (
        "lifecycle-control-observability",
        "live-single-investigation",
        "cross-retrieval",
    ):
        assert question in readme_text, f"missing evaluation question: {question}"
    assert "does not compute a combined score" in readme_text or (
        "never aggregate" in readme_text and "evaluation question" in readme_text
    )


def test_documents_corpus_migration_and_baseline_provenance(readme_text: str) -> None:
    assert "snapshot provenance" in readme_text
    assert "evidence_claim_ids" in readme_text
    assert "historical" in readme_text


def test_documents_credential_boundary(readme_text: str) -> None:
    assert "credential-free" in readme_text
    assert "allowlist" in readme_text
    assert "SDR_BENCH_LIVE_ACTOR" in readme_text or "environment opt-in" in readme_text
    assert "--live" in readme_text
    assert "XDG" in readme_text


def test_documents_exact_reuse_cross_check_outcomes(readme_text: str) -> None:
    for outcome in ("correct", "incorrect", "not-exercised", "not-consulted"):
        assert outcome in readme_text, f"missing cross-check outcome: {outcome}"
    assert "negative control" in readme_text
    assert "non-software" in readme_text


def test_documents_orthogonal_treatments(readme_text: str) -> None:
    assert "assisted" in readme_text
    assert "unassisted" in readme_text
    assert "orthogonal" in readme_text
    assert "never aggregated" in readme_text or "not aggregated" in readme_text


def test_documents_pilot_identity_as_exact_scalar(readme_text: str) -> None:
    assert "--live" in readme_text
    assert "exactly one" in readme_text or "exact scalar" in readme_text
    assert "not a matrix" in readme_text or "never a matrix" in readme_text
    for field in ("scenario", "arm", "repetition", "host", "model", "template", "results root"):
        assert field in readme_text, f"missing pilot identity field: {field}"


def test_documents_hitl_stop_at_transfer(readme_text: str) -> None:
    assert "awaiting-operator-approval" in readme_text
    assert "operator-pending" in readme_text
    assert "never" in readme_text and "approve" in readme_text


def test_documents_schema_version_2(readme_text: str) -> None:
    assert "schema version 2" in readme_text.lower() or "schema_version" in readme_text
    assert "version 1" in readme_text.lower()
    assert "reject" in readme_text.lower()


def test_documents_prohibited_claims_block(readme_text: str) -> None:
    for term in (
        "semantic",
        "criterion-level",
        "significance",
        "causal",
    ):
        assert term in readme_text, f"missing prohibited-claim term: {term}"
