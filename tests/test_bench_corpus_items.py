"""Corpus-level properties the authored benchmark items must keep satisfying.

These assertions are about the corpus content in `bench/corpus`, not about the loader.
They fail if an item is deleted, if a defect kind disappears from the corpus, or if a
note body stops using the section headings the artifact contract enforces today.
"""

from __future__ import annotations

import pytest

from bench.harness.corpus import PLANTED_DEFECTS, Corpus, CorpusItem, load_corpus
from sdr import schema

# Defect kinds the corpus is required to exercise (tasks 3.2-3.6).
REQUIRED_DEFECTS: tuple[str, ...] = (
    "unreachable-source",
    "unanchored-claim",
    "contradictory-sources",
    "probe-expectation-mismatch",
)

# Required defects that map onto a control implemented today: outbound link checking,
# textual anchoring, and executable probe verification. `contradictory-sources` is
# deliberately absent: no current control compares source content across alternatives,
# so its expectation is left for detection scoring to establish, not asserted here.
CONTROL_BACKED_DEFECTS: tuple[str, ...] = (
    "unreachable-source",
    "unanchored-claim",
    "probe-expectation-mismatch",
)

# Defect kinds no current control is expected to catch.
UNCATCHABLE_DEFECTS: frozenset[str] = frozenset({"inaccurate-source", "unrepresentative-benchmark"})

NOTE_SECTIONS: tuple[str, ...] = schema.artifact_for("explore", schema_version=2).required_sections
MEMO_SECTIONS: tuple[str, ...] = schema.artifact_for("transfer").required_sections
BRIEF_SECTIONS: tuple[str, ...] = schema.artifact_for("intake").required_sections


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus()


def _artifacts_matching(item: CorpusItem, fragment: str) -> dict[str, str]:
    return {path: body for path, body in item.artifacts.items() if fragment in path}


def _note_bodies(item: CorpusItem) -> dict[str, str]:
    """Explore notes only: captured source snapshots also live under `notes/`."""
    return {
        path: body
        for path, body in item.artifacts.items()
        if "/notes/" in path and path.endswith(".md") and "/notes/sources/" not in path
    }


def _headings(body: str) -> set[str]:
    return {line.lstrip("#").strip() for line in body.splitlines() if line.startswith("#")}


def test_corpus_loads_and_declares_a_version(corpus: Corpus) -> None:
    assert corpus.version
    assert corpus.items


def test_corpus_declares_versioned_provenance_without_a_preserved_current_baseline(
    corpus: Corpus,
) -> None:
    assert corpus.baseline_provenance.version == 1
    assert corpus.baseline_provenance.snapshot_schema_version == 2
    assert corpus.baseline_provenance.decision_lineage_field == "evidence_claim_ids"
    assert corpus.baseline_provenance.preserved_baseline is None


def test_every_item_file_stem_matches_its_id(corpus: Corpus) -> None:
    assert [item.path.stem for item in corpus.items] == [item.id for item in corpus.items]


def test_corpus_has_a_clean_light_item_and_a_clean_full_item(corpus: Corpus) -> None:
    modes = {item.mode for item in corpus.clean_items}
    assert modes == {"light", "full"}


def test_every_required_defect_kind_is_planted_somewhere(corpus: Corpus) -> None:
    planted = {defect for item in corpus.items for defect in item.planted_defects}
    assert planted >= set(REQUIRED_DEFECTS)


@pytest.mark.parametrize("defect", CONTROL_BACKED_DEFECTS)
def test_control_backed_defect_is_expected_to_be_caught(corpus: Corpus, defect: str) -> None:
    expectations = {
        item.expected_detection[defect] for item in corpus.items if defect in item.planted_defects
    }
    assert expectations == {"caught"}


def test_probe_expectation_mismatch_is_planted_on_a_full_item(corpus: Corpus) -> None:
    items = [item for item in corpus.items if "probe-expectation-mismatch" in item.planted_defects]
    assert items
    for item in items:
        assert item.mode == "full"
        assert item.probe is not None


def test_corpus_keeps_a_defect_no_control_is_expected_to_catch(corpus: Corpus) -> None:
    uncaught = {
        defect
        for item in corpus.items
        for defect, expectation in item.expected_detection.items()
        if expectation == "uncaught"
    }
    assert uncaught & UNCATCHABLE_DEFECTS


def test_unreachable_source_is_unreachable_by_construction(corpus: Corpus) -> None:
    items = [item for item in corpus.items if "unreachable-source" in item.planted_defects]
    assert items
    for item in items:
        hosts = [source.url.split("/")[2].split(":")[0] for source in item.sources]
        assert any(host.endswith(".invalid") for host in hosts), item.id


def test_unanchored_claim_item_still_ships_a_snapshot(corpus: Corpus) -> None:
    items = [item for item in corpus.items if "unanchored-claim" in item.planted_defects]
    assert items
    for item in items:
        assert any(source.snapshot for source in item.sources), item.id


def test_contradictory_sources_item_declares_more_than_one_source(corpus: Corpus) -> None:
    items = [item for item in corpus.items if "contradictory-sources" in item.planted_defects]
    assert items
    for item in items:
        assert len(item.sources) >= 2, item.id


def test_every_note_body_uses_the_contract_section_headings(corpus: Corpus) -> None:
    seen = 0
    for item in corpus.items:
        for path, body in _note_bodies(item).items():
            headings = _headings(body)
            missing = [section for section in NOTE_SECTIONS if section not in headings]
            assert not missing, f"{item.id}:{path} missing {missing}"
            seen += 1
    assert seen >= len(corpus.items)


def test_every_brief_and_memo_uses_the_contract_section_headings(corpus: Corpus) -> None:
    briefs = 0
    memos = 0
    for item in corpus.items:
        for path, body in _artifacts_matching(item, "brief.md").items():
            headings = _headings(body)
            missing = [section for section in BRIEF_SECTIONS if section not in headings]
            assert not missing, f"{item.id}:{path} missing {missing}"
            briefs += 1
        for path, body in _artifacts_matching(item, "decision-memo.md").items():
            headings = _headings(body)
            missing = [section for section in MEMO_SECTIONS if section not in headings]
            assert not missing, f"{item.id}:{path} missing {missing}"
            memos += 1
    assert briefs >= len(corpus.items)
    assert memos >= len(corpus.items)


def test_declared_snapshots_are_materialized_as_artifacts(corpus: Corpus) -> None:
    """Nothing in the harness writes snapshots, so the items must ship them themselves.

    Without `notes/sources/<id>/content.md` and its `meta.yaml`, every claim resolves as
    `unverifiable` and textual anchoring is never exercised.
    """
    for item in corpus.items:
        for source in item.sources:
            if not source.snapshot:
                continue
            prefix = f"{item.id}/notes/sources/{source.id}"
            content = item.artifacts.get(f"{prefix}/content.md")
            meta = item.artifacts.get(f"{prefix}/meta.yaml")
            assert content is not None, f"{item.id}: {source.id} has no captured content"
            assert meta is not None, f"{item.id}: {source.id} has no capture metadata"
            assert content == source.snapshot, f"{item.id}: {source.id} drifted"
            assert f"url: {source.url}" in meta, f"{item.id}: {source.id} url mismatch"
            assert "status: ok" in meta, f"{item.id}: {source.id} capture is not usable"


def test_every_planted_defect_belongs_to_the_vocabulary(corpus: Corpus) -> None:
    for item in corpus.items:
        assert set(item.planted_defects) <= PLANTED_DEFECTS, item.id


def test_clean_items_declare_no_expected_detection(corpus: Corpus) -> None:
    for item in corpus.clean_items:
        assert dict(item.expected_detection) == {}
