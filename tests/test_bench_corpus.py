"""Contract tests for the benchmark corpus loader.

These tests use minimal in-test fixture corpora. The real corpus items live in
`bench/corpus/items/` and are authored separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from bench.harness.corpus import (
    PLANTED_DEFECT_VOCABULARY,
    PLANTED_DEFECTS,
    CorpusError,
    is_synthetic_url,
    load_corpus,
    synthetic_content_violations,
)
from sdr.claims import extract_claims

BASELINE_PROVENANCE = {
    "version": 1,
    "snapshot_schema_version": 2,
    "decision_lineage_field": "evidence_claim_ids",
    "preserved_baseline": None,
}

CLEAN_ITEM: dict = {
    "id": "clean-light",
    "mode": "light",
    "title": "Clean light item",
    "question": "Which synthetic option fits the invented criterion?",
    "planted_defects": [],
    "sources": [
        {
            "id": "S1",
            "url": "https://example.com/synthetic/one",
            "title": "Synthetic source one",
            "tier": "T1",
            "date": "2026-01-05",
            "snapshot": "Invented body text for deterministic matching.",
        }
    ],
    "artifacts": {"notes/exploration.md": "Invented note body.\n"},
    "commands": [["sdr", "check", "clean-light", "--json"]],
}

DEFECTIVE_ITEM: dict = {
    "id": "unreachable-source-full",
    "mode": "full",
    "title": "Full item with an unreachable source",
    "question": "Does the invented component satisfy the invented criterion?",
    "planted_defects": ["unreachable-source"],
    "expected_detection": {"unreachable-source": "caught"},
    "sources": [
        {
            "id": "S1",
            "url": "https://gone.invalid/synthetic/two",
            "title": "Synthetic source two",
            "tier": "T2",
            "date": "2026-01-06",
            "snapshot": "Invented body text that no longer resolves.",
        }
    ],
    "artifacts": {"notes/exploration.md": "Invented note body.\n"},
    "commands": [],
}


def write_corpus(root: Path, items: list[dict], version: str = "1") -> Path:
    """Materialize a fixture corpus and return its root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "corpus.yaml").write_text(
        yaml.safe_dump(
            {"version": version, "baseline_provenance": BASELINE_PROVENANCE}, sort_keys=True
        ),
        encoding="utf-8",
    )
    items_dir = root / "items"
    items_dir.mkdir(exist_ok=True)
    for item in items:
        path = items_dir / f"{item['id']}.yaml"
        path.write_text(yaml.safe_dump(item, sort_keys=True), encoding="utf-8")
    return root


def current_snapshot_artifacts(item: dict, source_id: str = "S1") -> dict[str, str]:
    source = next(source for source in item["sources"] if source["id"] == source_id)
    content = source["snapshot"]
    prefix = f"{item['id']}/notes/sources/{source_id}"
    metadata = {
        "schema_version": 2,
        "url": source["url"],
        "declared_url": source["url"],
        "final_url": source["url"],
        "redirects": [],
        "title": source["title"],
        "captured_at": "2026-07-20T00:00:00+00:00",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "http_status": 200,
        "content_type": "text/plain",
        "content_eligible": True,
        "org": "synthetic",
        "status": "ok",
    }
    return {
        f"{prefix}/content.md": content,
        f"{prefix}/meta.yaml": yaml.safe_dump(metadata, sort_keys=False),
    }


for fixture_item in (CLEAN_ITEM, DEFECTIVE_ITEM):
    fixture_item["artifacts"] = {
        **fixture_item["artifacts"],
        **current_snapshot_artifacts(fixture_item),
    }


def test_load_corpus_returns_typed_items_in_stable_order(tmp_path):
    root = write_corpus(tmp_path / "corpus", [DEFECTIVE_ITEM, CLEAN_ITEM])

    corpus = load_corpus(root)

    assert corpus.version == "1"
    assert corpus.baseline_provenance.version == 1
    assert corpus.baseline_provenance.snapshot_schema_version == 2
    assert corpus.baseline_provenance.decision_lineage_field == "evidence_claim_ids"
    assert corpus.baseline_provenance.preserved_baseline is None
    assert [item.id for item in corpus.items] == ["clean-light", "unreachable-source-full"]
    assert [item.mode for item in corpus.items] == ["light", "full"]
    assert corpus.items[0].planted_defects == ()
    assert corpus.items[1].planted_defects == ("unreachable-source",)
    assert corpus.items[1].sources[0].url == "https://gone.invalid/synthetic/two"
    assert corpus.items[0].artifacts["notes/exploration.md"] == "Invented note body.\n"
    assert corpus.items[0].commands == (("sdr", "check", "clean-light", "--json"),)


def test_retained_snapshot_without_current_provenance_is_rejected(tmp_path):
    artifacts = current_snapshot_artifacts(CLEAN_ITEM)
    metadata_path = "clean-light/notes/sources/S1/meta.yaml"
    metadata = yaml.safe_load(artifacts[metadata_path])
    metadata.pop("schema_version")
    artifacts[metadata_path] = yaml.safe_dump(metadata, sort_keys=False)
    item = {**CLEAN_ITEM, "artifacts": artifacts}
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "S1" in message
    assert "current snapshot provenance" in message


def test_declared_snapshot_must_equal_replayed_content_exactly(tmp_path):
    artifacts = current_snapshot_artifacts(CLEAN_ITEM)
    content_path = "clean-light/notes/sources/S1/content.md"
    metadata_path = "clean-light/notes/sources/S1/meta.yaml"
    artifacts[content_path] += "\n"
    metadata = yaml.safe_load(artifacts[metadata_path])
    metadata["content_hash"] = hashlib.sha256(artifacts[content_path].encode()).hexdigest()
    artifacts[metadata_path] = yaml.safe_dump(metadata, sort_keys=False)
    item = {**CLEAN_ITEM, "artifacts": artifacts}
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "S1" in message
    assert "exact bytes" in message


def test_participating_decision_without_evidence_claim_ids_is_rejected(tmp_path):
    decision_path = "clean-light/decision-memo.md"
    decision = """---
research: clean-light
date: 2026-07-21
stage: transfer
ring: assess
audience: equipo
---

## Recommendation
Conservar el resultado sintético.
"""
    item = {
        **CLEAN_ITEM,
        "artifacts": {
            **current_snapshot_artifacts(CLEAN_ITEM),
            decision_path: decision,
        },
    }
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert decision_path in message
    assert "evidence_claim_ids" in message


def _item_with_decision_claim(claim_id: str, note: str) -> dict:
    decision_path = "clean-light/decision-memo.md"
    decision = f"""---
research: clean-light
date: 2026-07-21
stage: transfer
ring: assess
audience: equipo
evidence_claim_ids:
  - {claim_id}
---

## Recommendation
Conservar el resultado sintético.
"""
    return {
        **CLEAN_ITEM,
        "artifacts": {
            **current_snapshot_artifacts(CLEAN_ITEM),
            "clean-light/notes/landscape.md": note,
            decision_path: decision,
        },
    }


def test_participating_decision_rejects_fabricated_claim_id_with_full_identity(tmp_path):
    fabricated = f"claim-{'0' * 64}"
    note = "El texto coincide con el snapshot. [S1]\n"
    item = _item_with_decision_claim(fabricated, note)
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "clean-light/decision-memo.md" in message
    assert fabricated in message
    assert "current retained claim" in message


def test_participating_decision_rejects_stale_claim_id_with_full_identity(tmp_path):
    old_note = "Invented body text for deterministic matching [S1].\n"
    stale = extract_claims(old_note, note_path="notes/landscape.md")[0].id
    current_note = "Invented body text for deterministic matching changed [S1].\n"
    item = _item_with_decision_claim(stale, current_note)
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "clean-light/decision-memo.md" in message
    assert stale in message
    assert "current retained claim" in message


def test_participating_decision_rejects_current_unanchored_claim(tmp_path):
    note = "This sentence is absent from the retained snapshot. [S1]\n"
    claim_id = extract_claims(note, note_path="notes/landscape.md")[0].id
    item = _item_with_decision_claim(claim_id, note)
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "clean-light/decision-memo.md" in message
    assert claim_id in message
    assert "not anchored" in message


def test_participating_decision_rejects_current_unverified_claim(tmp_path):
    note = "A claim cites a source with no retained snapshot. [S2]\n"
    claim_id = extract_claims(note, note_path="notes/landscape.md")[0].id
    item = _item_with_decision_claim(claim_id, note)
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "clean-light/decision-memo.md" in message
    assert claim_id in message
    assert "validated retained source snapshot" in message


def _write_manifest(root: Path, manifest: dict) -> None:
    (root / "corpus.yaml").write_text(yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8")


def test_baseline_provenance_is_required(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM])
    _write_manifest(root, {"version": "1"})

    with pytest.raises(CorpusError, match="baseline_provenance"):
        load_corpus(root)


@pytest.mark.parametrize(
    "baseline_provenance",
    [
        [],
        {**BASELINE_PROVENANCE, "version": "1"},
        {**BASELINE_PROVENANCE, "preserved_baseline": "legacy-run"},
        {
            key: value
            for key, value in BASELINE_PROVENANCE.items()
            if key != "decision_lineage_field"
        },
    ],
)
def test_baseline_provenance_rejects_malformed_metadata(tmp_path, baseline_provenance):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM])
    _write_manifest(root, {"version": "1", "baseline_provenance": baseline_provenance})

    with pytest.raises(CorpusError, match="baseline_provenance"):
        load_corpus(root)


@pytest.mark.parametrize(
    "drift",
    [
        {"version": 2},
        {"snapshot_schema_version": 1},
        {"decision_lineage_field": "legacy_claims"},
    ],
)
def test_baseline_provenance_rejects_contract_drift(tmp_path, drift):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM])
    _write_manifest(
        root,
        {"version": "1", "baseline_provenance": {**BASELINE_PROVENANCE, **drift}},
    )

    with pytest.raises(CorpusError, match="current migration metadata"):
        load_corpus(root)


def test_two_files_cannot_declare_the_same_item_id(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM])
    duplicate = root / "items" / "duplicate.yaml"
    duplicate.write_text(
        yaml.safe_dump({**CLEAN_ITEM, "id": "clean-light"}, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "duplicate" in message


def test_loaded_item_ids_are_unique(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, DEFECTIVE_ITEM])

    corpus = load_corpus(root)

    ids = [item.id for item in corpus.items]
    assert len(set(ids)) == len(ids)


def test_item_id_must_match_its_file_stem(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM])
    (root / "items" / "clean-light.yaml").rename(root / "items" / "renamed.yaml")

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "renamed" in str(excinfo.value)


@pytest.mark.parametrize("mode", ["Light", "explore", "", "probe"])
def test_mode_must_be_light_or_full(tmp_path, mode):
    root = write_corpus(tmp_path / "corpus", [{**CLEAN_ITEM, "mode": mode}])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "mode" in message


def test_undeclared_defect_kind_fails_with_item_id_and_defect_name(tmp_path):
    item = {**CLEAN_ITEM, "planted_defects": ["hallucinated-defect"]}
    root = write_corpus(tmp_path / "corpus", [item, DEFECTIVE_ITEM])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "clean-light" in message
    assert "hallucinated-defect" in message


def test_every_planted_defect_belongs_to_the_closed_vocabulary(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, DEFECTIVE_ITEM])

    corpus = load_corpus(root)

    for item in corpus.items:
        for defect in item.planted_defects:
            assert defect in PLANTED_DEFECTS


def test_vocabulary_is_closed_and_documents_each_entry():
    assert PLANTED_DEFECTS == frozenset(PLANTED_DEFECT_VOCABULARY)
    assert PLANTED_DEFECTS
    for name, rationale in PLANTED_DEFECT_VOCABULARY.items():
        assert name == name.lower()
        assert " " not in name
        assert rationale.strip()


def test_vocabulary_retains_defects_no_current_control_is_expected_to_catch():
    assert "inaccurate-source" in PLANTED_DEFECTS
    assert "unrepresentative-benchmark" in PLANTED_DEFECTS


def test_duplicate_planted_defects_are_rejected(tmp_path):
    item = {**DEFECTIVE_ITEM, "planted_defects": ["unreachable-source", "unreachable-source"]}
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "unreachable-source" in str(excinfo.value)


def test_corpus_requires_at_least_one_item_with_an_empty_defect_list(tmp_path):
    root = write_corpus(tmp_path / "corpus", [DEFECTIVE_ITEM])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "empty planted-defect list" in str(excinfo.value)


def test_expected_detection_keys_must_be_planted_defects(tmp_path):
    item = {**DEFECTIVE_ITEM, "expected_detection": {"unanchored-claim": "caught"}}
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "unanchored-claim" in str(excinfo.value)


def test_expected_detection_values_are_caught_or_uncaught(tmp_path):
    item = {**DEFECTIVE_ITEM, "expected_detection": {"unreachable-source": "maybe"}}
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "maybe" in str(excinfo.value)


def test_light_item_cannot_declare_a_probe(tmp_path):
    item = {**CLEAN_ITEM, "probe": {"argv": ["true"], "expectation": "exit 0"}}
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "probe" in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/page",
        "https://docs.example.org/a/b",
        "http://example.net",
        "https://vendor.invalid/spec",
        "https://host.test/spec",
        "https://anything.example/spec",
    ],
)
def test_reserved_and_non_resolvable_urls_are_accepted(url):
    assert is_synthetic_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://notexample.com/page",
        "https://example.com.evil.io/page",
        "https://example.company/page",
        "ftp://example.com/file",
        "not-a-url",
        "",
    ],
)
def test_non_reserved_urls_are_rejected(url):
    assert is_synthetic_url(url) is False


def test_every_corpus_source_url_uses_a_reserved_domain(tmp_path):
    item = {
        **DEFECTIVE_ITEM,
        "sources": [{**DEFECTIVE_ITEM["sources"][0], "url": "https://real-vendor.io/post"}],
    }
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    message = str(excinfo.value)
    assert "unreachable-source-full" in message
    assert "https://real-vendor.io/post" in message


def test_urls_embedded_in_artifact_bodies_must_also_be_reserved(tmp_path):
    item = {
        **CLEAN_ITEM,
        "artifacts": {"notes/exploration.md": "See https://real-vendor.io/post for details.\n"},
    }
    root = write_corpus(tmp_path / "corpus", [item])

    with pytest.raises(CorpusError) as excinfo:
        load_corpus(root)

    assert "https://real-vendor.io/post" in str(excinfo.value)


def test_synthetic_content_violations_are_reported_without_raising(tmp_path):
    root = write_corpus(tmp_path / "corpus", [CLEAN_ITEM, DEFECTIVE_ITEM])
    corpus = load_corpus(root)

    for item in corpus.items:
        assert synthetic_content_violations(item) == ()


def test_corpus_root_must_exist(tmp_path):
    with pytest.raises(CorpusError):
        load_corpus(tmp_path / "missing")
