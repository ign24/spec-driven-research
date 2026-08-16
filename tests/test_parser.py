import textwrap

import pytest

from sdr import parser


def _write(tmp_path, text):
    p = tmp_path / "artifact.md"
    p.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return p


def test_parses_frontmatter_and_sections(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        research: eval-foo
        stage: intake
        ---

        ## Question

        ¿Sirve foo para X?

        ## Hypothesis

        Creemos que sí.
        """,
    )
    art = parser.parse_artifact(path)
    assert art.frontmatter["research"] == "eval-foo"
    assert art.frontmatter["stage"] == "intake"
    assert "¿Sirve foo para X?" in art.section("Question")
    assert "Creemos que sí." in art.section("Hypothesis")


def test_section_missing_returns_none(tmp_path):
    path = _write(tmp_path, "## Question\n\ntexto\n")
    art = parser.parse_artifact(path)
    assert art.section("Scope") is None


def test_empty_section_reports_no_content(tmp_path):
    path = _write(
        tmp_path,
        """
        ## Question

        ## Hypothesis

        algo
        """,
    )
    art = parser.parse_artifact(path)
    assert not art.has_content("Question")
    assert art.has_content("Hypothesis")


def test_section_match_allows_trailing_annotation(tmp_path):
    path = _write(
        tmp_path,
        """
        ## Evaluation criteria (aceptación)

        - C1: latencia < 200ms
        """,
    )
    art = parser.parse_artifact(path)
    assert art.has_content("Evaluation criteria")


def test_section_match_is_whitespace_and_case_insensitive(tmp_path):
    path = _write(tmp_path, "##   adoption RISKS\n\ntexto\n")
    art = parser.parse_artifact(path)
    assert art.has_content("Adoption risks")


def test_duplicate_top_level_evidence_claim_ids_is_rejected_deterministically(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        research: eval-foo
        evidence_claim_ids: []
        evidence_claim_ids:
          - claim-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
        ---
        """,
    )

    with pytest.raises(
        parser.DuplicateFrontmatterKeyError,
        match="duplicate top-level frontmatter key: evidence_claim_ids",
    ):
        parser.parse_artifact(path)


def test_unrelated_duplicate_frontmatter_keys_keep_existing_parser_behavior(tmp_path):
    path = _write(
        tmp_path,
        """
        ---
        research: first
        research: second
        evidence_claim_ids: []
        ---
        """,
    )

    assert parser.parse_artifact(path).frontmatter["research"] == "second"
