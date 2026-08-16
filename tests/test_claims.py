import pytest

from sdr import claims as claims_module

extract_claims = claims_module.extract_claims


def extract_references(markdown):
    return claims_module.extract_references(markdown)


def test_extract_claims_from_paragraphs_and_bullets():
    text = """
## Alternatives evaluated

Foo soporta modo offline [S1]. También ofrece cache local [S2].

- Bar requiere conexión permanente [S3].
- Sin cita no debe aparecer.
"""

    claims = extract_claims(text)

    assert [claim.source_id for claim in claims] == ["S1", "S2", "S3"]
    assert claims[0].text == "Foo soporta modo offline."
    assert claims[1].text == "También ofrece cache local."
    assert claims[2].text == "Bar requiere conexión permanente."
    assert all(claim.id.startswith("claim-") for claim in claims)


def test_extract_claim_ids_are_deterministic():
    text = "Foo soporta modo offline [S1]."

    first = extract_claims(text)
    second = extract_claims(text)

    assert first[0].id == second[0].id


def test_contextual_references_do_not_create_claims():
    claims = extract_claims("Foo se considera maduro [cf. S1].")

    assert claims == []


def test_markdown_headings_extract_factual_claims_without_atx_prefix():
    markdown = "## Foo alcanza madurez [S1]\n\n### Context histórico [cf.S2]\n"

    references = extract_references(markdown)
    claims = extract_claims(markdown, note_path="notes/foo.md")

    assert [(reference.source_id, reference.contextual) for reference in references] == [
        ("S1", False),
        ("S2", True),
    ]
    assert len(claims) == 1
    assert claims[0].text == "Foo alcanza madurez"
    assert claims[0].source_id == "S1"
    assert (claims[0].line_start, claims[0].line_end) == (1, 1)


def test_factual_claim_excludes_contextual_references_from_text_and_identity():
    mixed = extract_claims(
        "Foo reduce latencia [S1] frente al contexto histórico [cf. S2].",
        note_path="notes/foo.md",
    )
    factual_only = extract_claims(
        "Foo reduce latencia [S1] frente al contexto histórico.",
        note_path="notes/foo.md",
    )

    assert len(mixed) == 1
    assert mixed[0].source_id == "S1"
    assert mixed[0].text == "Foo reduce latencia frente al contexto histórico."
    assert mixed[0].id == factual_only[0].id


def test_claim_identity_includes_note_path_and_inclusive_line_range():
    text = "Encabezado sin claim.\n\n- Foo soporta modo offline [S1].\n"

    claim = extract_claims(text, note_path="notes/foo.md")[0]

    assert claim.note_path == "notes/foo.md"
    assert claim.line_start == 3
    assert claim.line_end == 3
    assert claim.id != extract_claims(text, note_path="notes/bar.md")[0].id


def test_multiple_factual_references_in_one_sentence_are_rejected_actionably():
    with pytest.raises(ValueError) as exc_info:
        extract_claims(
            "Foo reduce latencia [S1] y costo [S2].",
            note_path="notes/foo.md",
        )

    message = str(exc_info.value)
    assert "notes/foo.md" in message
    assert "line 1" in message
    assert "[S1]" in message and "[S2]" in message
    assert "split the sentence" in message


def test_multiline_sentence_uses_absolute_inclusive_lines_and_excludes_frontmatter():
    markdown = '---\ntitle: Foo\nexample: "[S9]"\n---\n\nFoo ofrece una API\nestable [S1] (p. 3).\n'

    claim = extract_claims(markdown, note_path="notes/foo.md")[0]

    assert claim.text == "Foo ofrece una API estable (p. 3)."
    assert (claim.line_start, claim.line_end) == (6, 7)
    assert [reference.source_id for reference in extract_references(markdown)] == ["S1"]


def test_multiple_factual_references_across_lines_in_one_sentence_are_rejected():
    markdown = "Foo reduce latencia [S1]\ny también reduce costo [S2].\n"

    with pytest.raises(ValueError) as exc_info:
        extract_claims(markdown, note_path="notes/foo.md")

    message = str(exc_info.value)
    assert "lines 1-2" in message
    assert "[S1]" in message and "[S2]" in message
    assert "split the sentence" in message


@pytest.mark.parametrize(
    "marker",
    ["[cf. S1]", "[cf.S1]", "[cf.   S1]", "[CF. s1]", "[cF   .   S1]"],
)
def test_contextual_marker_variants_are_consistent(marker):
    markdown = f"Foo aporta contexto {marker}."

    references = extract_references(markdown)

    assert extract_claims(markdown) == []
    assert len(references) == 1
    assert references[0].source_id == "S1"
    assert references[0].contextual


def test_versions_and_domains_do_not_truncate_claim_sentence():
    markdown = "La versión v1.2 publicada en docs.foo.com soporta cache [S1]."

    claim = extract_claims(markdown, note_path="notes/foo.md")[0]

    assert claim.text == "La versión v1.2 publicada en docs.foo.com soporta cache."


def test_fenced_and_inline_code_do_not_create_references_or_claims():
    markdown = (
        "Texto sin cita y ejemplo `[S8]`.\n\n"
        "```markdown\n"
        "Ejemplo factual [S9].\n"
        "Contexto [cf.S7].\n"
        "```\n"
    )

    assert extract_references(markdown) == []
    assert extract_claims(markdown, note_path="notes/foo.md") == []


def test_complete_markdown_and_note_path_produce_stable_ids_for_all_consumers():
    markdown = "---\ntitle: Foo\n---\n\nFoo es estable [S1].\n"

    claims_consumer = extract_claims(markdown, note_path="notes/foo.md")
    group_4_consumer = extract_claims(markdown, note_path="notes/foo.md")

    assert claims_consumer[0].id == group_4_consumer[0].id
    assert (claims_consumer[0].line_start, claims_consumer[0].line_end) == (5, 5)


@pytest.mark.parametrize("prefixes", [("- ", "- "), ("* ", "+ "), ("1. ", "2. ")])
def test_unpunctuated_markdown_list_items_are_separate_claim_units(prefixes):
    markdown = f"{prefixes[0]}Foo reduce latencia [S1]\n{prefixes[1]}Bar reduce costo [S2]\n"

    claims = extract_claims(markdown, note_path="notes/foo.md")

    assert [claim.source_id for claim in claims] == ["S1", "S2"]
    assert [claim.text for claim in claims] == ["Foo reduce latencia", "Bar reduce costo"]
    assert [(claim.line_start, claim.line_end) for claim in claims] == [(1, 1), (2, 2)]


def test_indented_code_does_not_create_references_or_claims():
    markdown = "    Ejemplo [S8]\n\tOtro ejemplo [cf. S9]\nTexto real [S1].\n"

    references = extract_references(markdown)
    claims = extract_claims(markdown, note_path="notes/foo.md")

    assert [reference.source_id for reference in references] == ["S1"]
    assert [claim.source_id for claim in claims] == ["S1"]
    assert (claims[0].line_start, claims[0].line_end) == (3, 3)


def test_link_image_destinations_autolinks_and_urls_do_not_create_references():
    markdown = (
        "[Soporte [S1]](https://example.com/docs/[S8]) está disponible.\n"
        "![Contexto [cf. S2]](https://example.com/image-[S7].png)\n"
        "<https://example.com/[S6]>\n"
        "https://example.com/raw/[S5]\n"
    )

    references = extract_references(markdown)
    claims = extract_claims(markdown, note_path="notes/foo.md")

    assert [(reference.source_id, reference.contextual) for reference in references] == [
        ("S1", False),
        ("S2", True),
    ]
    assert [claim.source_id for claim in claims] == ["S1"]
    assert "Soporte" in claims[0].text
