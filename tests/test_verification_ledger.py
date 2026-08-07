import hashlib
import multiprocessing
import os
from pathlib import Path

import pytest
import yaml

from sdr.verification_ledger import (
    SCHEMA_VERSION,
    LedgerValidationError,
    empty_ledger,
    ledger_directory_lock,
    load_ledger,
    make_claim_id,
    save_ledger,
    validate_claim_references,
    validate_ledger,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _raise_while_locked_then_remain_alive(path: str, released, finish) -> None:
    try:
        with ledger_directory_lock(Path(path)):
            raise RuntimeError("expected")
    except RuntimeError:
        released.set()
        assert finish.wait(10)


def _acquire_directory_lock(path: str, acquired) -> None:
    with ledger_directory_lock(Path(path)):
        acquired.set()


def _verified_claim() -> dict:
    claim = {
        "note_path": "notes/latency.md",
        "line_start": 12,
        "line_end": 14,
        "claim_text": "Foo reduce la latencia.",
        "source_id": "S1",
        "state": "verified",
        "quote": "primera linea\nsegunda linea",
        "locator": {"line_start": 4, "line_end": 5},
        "claim_hash": "claim-hash",
        "snapshot_hash": "snapshot-hash",
        "normalization_version": "norm-v1",
        "matcher_version": "matcher-v1",
    }
    claim["claim_id"] = make_claim_id(
        claim["note_path"],
        claim["line_start"],
        claim["line_end"],
        claim["source_id"],
        claim["claim_hash"],
    )
    return claim


def test_empty_ledger_declares_v2_collections() -> None:
    assert empty_ledger() == {
        "schema_version": SCHEMA_VERSION,
        "claims": [],
        "resolutions": [],
        "degradation_acknowledgements": [],
        "legacy": [],
    }
    assert SCHEMA_VERSION == 2


def test_validates_verified_claim_with_inclusive_multiline_locator() -> None:
    ledger = empty_ledger()
    ledger["claims"].append(_verified_claim())

    assert validate_ledger(ledger) == []

    ledger["claims"][0]["locator"] = {"line_start": 5, "line_end": 4}
    assert "locator must be an inclusive line range" in validate_ledger(ledger)[0]


def test_claim_id_is_stable_and_covers_all_identity_inputs() -> None:
    inputs = ("notes/latency.md", 12, 14, "S2", "canonical-claim-hash")
    claim_id = make_claim_id(*inputs)

    assert claim_id == make_claim_id(*inputs)
    assert claim_id.startswith("claim-")
    for index, value in enumerate(inputs):
        changed = list(inputs)
        changed[index] = value + "-changed" if isinstance(value, str) else value + 1
        assert make_claim_id(*changed) != claim_id


def test_claim_id_canonicalizes_safe_relative_posix_note_path() -> None:
    expected = make_claim_id("notes/latency.md", 12, 14, "S2", "claim-hash")

    assert make_claim_id("notes\\latency.md", 12, 14, "S2", "claim-hash") == expected
    assert make_claim_id("notes/./latency.md", 12, 14, "S2", "claim-hash") == expected
    with pytest.raises(LedgerValidationError, match="relative POSIX path"):
        make_claim_id("../outside.md", 12, 14, "S2", "claim-hash")
    with pytest.raises(LedgerValidationError, match="inclusive line range"):
        make_claim_id("notes/latency.md", 14, 12, "S2", "claim-hash")


@pytest.mark.parametrize(
    "state", ["verified", "not_anchored", "unverifiable", "human_reviewed", "stale"]
)
def test_each_active_state_is_valid_and_persisted(tmp_path, state) -> None:
    path = tmp_path / "verification.yaml"
    claim = _verified_claim() | {"state": state}
    ledger = empty_ledger()
    ledger["claims"].append(claim)

    assert validate_ledger(ledger) == []
    save_ledger(path, ledger)

    assert load_ledger(path)["claims"][0]["state"] == state


def test_mismatched_claim_id_is_inconsistent_and_blocks_save(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    ledger = empty_ledger()
    ledger["claims"].append(_verified_claim() | {"claim_id": "claim-wrong"})

    assert "claim claim-wrong claim_id does not match its identity fields" in validate_ledger(
        ledger
    )
    with pytest.raises(LedgerValidationError, match="claim_id does not match"):
        save_ledger(path, ledger)


def test_bool_is_not_a_valid_line_range() -> None:
    with pytest.raises(LedgerValidationError, match="inclusive line range"):
        make_claim_id("notes/latency.md", True, 14, "S2", "claim-hash")

    claim = _verified_claim() | {"line_start": True}
    ledger = empty_ledger()
    ledger["claims"].append(claim)
    assert any("inclusive line range" in issue for issue in validate_ledger(ledger))


def test_persisted_note_path_must_be_canonical_posix_with_coherent_id(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    claim = _verified_claim() | {"note_path": "notes\\latency.md"}
    ledger = empty_ledger()
    ledger["claims"].append(claim)

    assert any("note_path must be canonical POSIX" in issue for issue in validate_ledger(ledger))
    with pytest.raises(LedgerValidationError, match="canonical POSIX"):
        save_ledger(path, ledger)


def test_v2_round_trip_keeps_sections_and_unknown_legacy_fields(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    ledger = empty_ledger()
    ledger["claims"].append(_verified_claim())
    ledger["resolutions"].append(
        {
            "claim_id": "claim-2",
            "by": "Nacho",
            "reason": "Revisión manual",
            "date": "2026-07-10",
            "claim_hash": "claim-hash",
            "snapshot_hash": "snapshot-hash",
            "normalization_version": "norm-v1",
            "matcher_version": "matcher-v1",
        }
    )
    ledger["legacy"].append(
        {
            "claim_id": "old-1",
            "verdict": "supported",
            "provider_payload": {"score": 0.91, "future_field": [1, 2]},
        }
    )

    save_ledger(path, ledger)

    assert load_ledger(path) == ledger


def test_loading_semantic_v1_separates_claims_into_legacy(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    old_claim = {
        "claim_id": "old-1",
        "verdict": "contradicted",
        "model": "legacy-model",
        "unknown": {"keep": True},
    }
    old_resolution = {"claim_id": "old-1", "reason": "aceptado antes"}
    path.write_text(
        yaml.safe_dump(
            {
                "claims": [old_claim],
                "resolutions": [old_resolution],
                "provider": "old-provider",
                "opaque_metadata": {"keep": [1, 2]},
            }
        ),
        encoding="utf-8",
    )

    loaded = load_ledger(path)

    assert loaded == {
        "schema_version": 2,
        "claims": [],
        "resolutions": [],
        "degradation_acknowledgements": [],
        "legacy": [
            old_claim,
            {"kind": "resolution", "data": old_resolution},
            {
                "kind": "top_level",
                "data": {
                    "provider": "old-provider",
                    "opaque_metadata": {"keep": [1, 2]},
                },
            },
        ],
    }


@pytest.mark.parametrize("verdict", ["supported", "contradicted", "not_found"])
def test_v2_semantic_verdict_is_preserved_as_legacy_never_active(tmp_path, verdict) -> None:
    path = tmp_path / "verification.yaml"
    semantic = {
        "claim_id": "old-semantic-claim",
        "verdict": verdict,
        "provider_payload": {"unknown": [1, 2]},
    }
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 2,
                "claims": [semantic],
                "resolutions": [],
                "legacy": [{"opaque": "keep"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    loaded = load_ledger(path)

    assert loaded["claims"] == []
    assert semantic in loaded["legacy"]
    assert {"opaque": "keep"} in loaded["legacy"]


def test_legacy_v2_fixture_round_trip_preserves_all_metadata(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    path.write_bytes((FIXTURES / "legacy_verification_v2.yaml").read_bytes())
    expected = yaml.safe_load(path.read_text(encoding="utf-8"))

    loaded = load_ledger(path)
    save_ledger(path, loaded)
    reloaded = load_ledger(path)

    assert reloaded["claims"] == []
    assert expected["claims"][0] in reloaded["legacy"]
    assert {"kind": "resolution", "data": expected["resolutions"][0]} in reloaded["legacy"]
    assert expected["legacy"][0] in reloaded["legacy"]


def test_unknown_active_state_survives_round_trip_and_is_inconsistent(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    claim = _verified_claim() | {"state": "future_state", "future": {"data": 1}}
    ledger = empty_ledger()
    ledger["claims"].append(claim)

    save_ledger(path, ledger)
    loaded = load_ledger(path)

    assert loaded["claims"][0] == claim
    assert validate_ledger(loaded) == [
        f"claim {claim['claim_id']} has unknown active state: future_state"
    ]


def test_reference_validation_does_not_weaken_whole_ledger_validation() -> None:
    claim = _verified_claim() | {"state": "future_state", "future": {"data": 1}}
    ledger = empty_ledger()
    ledger["claims"].append(claim)

    expected_issue = f"claim {claim['claim_id']} has unknown active state: future_state"
    assert validate_claim_references(ledger, ()) == []
    assert validate_claim_references(ledger, (claim["claim_id"],)) == [expected_issue]
    assert validate_ledger(ledger) == [expected_issue]


def test_save_is_stable_for_equivalent_ledger(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    ledger = empty_ledger()
    ledger["legacy"].append({"opaque": "value"})

    save_ledger(path, ledger)
    first_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    save_ledger(path, load_ledger(path))

    assert hashlib.sha256(path.read_bytes()).hexdigest() == first_hash


@pytest.mark.parametrize("content", ["- not-a-mapping\n", "claims: {}\n"])
def test_load_rejects_malformed_ledger_with_actionable_error(tmp_path, content) -> None:
    path = tmp_path / "verification.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerValidationError, match="verification.yaml"):
        load_ledger(path)


def test_validation_requires_mapping_entries_nonempty_fields_and_unique_claim_ids() -> None:
    claim = _verified_claim()
    ledger = empty_ledger()
    ledger["claims"] = [claim, claim | {"claim_text": ""}, "not-a-mapping"]

    issues = validate_ledger(ledger)

    assert f"duplicate claim_id: {claim['claim_id']}" in issues
    assert f"claim {claim['claim_id']} claim_text must not be empty" in issues
    assert "claims[2] must be a mapping" in issues


def test_validation_checks_v2_resolutions_and_collections() -> None:
    ledger = empty_ledger()
    ledger["resolutions"] = [{"claim_id": "claim-1", "by": ""}]
    ledger["legacy"] = ["not-a-mapping"]

    issues = validate_ledger(ledger)

    assert "resolution claim-1 by must not be empty" in issues
    assert "resolution claim-1 missing fields:" in "\n".join(issues)
    assert "legacy[0] must be a mapping" in issues


def test_validation_rejects_active_resolution_that_does_not_match_its_claim() -> None:
    claim = _verified_claim() | {"state": "not_anchored"}
    ledger = empty_ledger()
    ledger["claims"] = [claim]
    ledger["resolutions"] = [
        {
            "claim_id": claim["claim_id"],
            "by": "Nacho",
            "reason": "Revisión manual",
            "date": "2026-07-11",
            "claim_hash": claim["claim_hash"],
            "snapshot_hash": "different-snapshot",
            "normalization_version": claim["normalization_version"],
            "matcher_version": claim["matcher_version"],
            "state": "active",
        }
    ]

    assert any(
        "active resolution does not match current claim" in issue
        for issue in validate_ledger(ledger)
    )


def test_validation_rejects_duplicate_active_resolutions_without_last_wins() -> None:
    claim = _verified_claim() | {"state": "not_anchored"}
    ledger = empty_ledger()
    ledger["claims"] = [claim]
    resolution = {
        "claim_id": claim["claim_id"],
        "by": "Nacho",
        "reason": "Revisión manual",
        "date": "2026-07-11",
        "claim_hash": claim["claim_hash"],
        "snapshot_hash": claim["snapshot_hash"],
        "normalization_version": claim["normalization_version"],
        "matcher_version": claim["matcher_version"],
        "state": "active",
    }
    ledger["resolutions"] = [resolution, resolution | {"by": "Otra persona"}]

    assert any("duplicate active resolution" in issue for issue in validate_ledger(ledger))


def test_save_rejects_invalid_v2_ledger(tmp_path) -> None:
    path = tmp_path / "verification.yaml"
    ledger = empty_ledger()
    ledger["claims"] = {}

    with pytest.raises(LedgerValidationError, match="claims must be a list"):
        save_ledger(path, ledger)

    assert not path.exists()


def test_save_replaces_file_atomically(tmp_path, monkeypatch) -> None:
    path = tmp_path / "verification.yaml"
    path.write_text("old: content\n", encoding="utf-8")
    replacements = []
    real_replace = os.replace

    def recording_replace(source, destination):
        replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", recording_replace)
    save_ledger(path, empty_ledger())

    assert len(replacements) == 1
    assert replacements[0][1] == path
    assert replacements[0][0].parent == path.parent
    assert not replacements[0][0].exists()


def test_directory_lock_releases_on_exception_before_process_exit(tmp_path: Path) -> None:
    path = tmp_path / "verification.yaml"
    context = multiprocessing.get_context("fork")
    released = context.Event()
    finish = context.Event()
    acquired = context.Event()
    holder = context.Process(
        target=_raise_while_locked_then_remain_alive,
        args=(str(path), released, finish),
    )
    contender = context.Process(target=_acquire_directory_lock, args=(str(path), acquired))

    holder.start()
    assert released.wait(5)
    contender.start()
    assert acquired.wait(5)
    assert holder.is_alive()
    finish.set()
    holder.join(10)
    contender.join(10)

    assert holder.exitcode == 0
    assert contender.exitcode == 0
