import hashlib
import json
import textwrap
from datetime import date
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from sdr import lifecycle, verification
from sdr.cli import main
from sdr.research import Research
from sdr.textual_anchoring import MATCHER_VERSION, NORMALIZATION_VERSION
from sdr.verification_ledger import SCHEMA_VERSION, load_ledger

FIXTURES = Path(__file__).parent / "fixtures"


def _explore_research(tmp_path):
    research = Research.create(
        base=tmp_path, slug="eval-foo", title="t", question="q", owner="nacho"
    )
    research.meta.stage = "explore"
    research.meta.validation["intake"] = lifecycle.stage_hash(research, "intake")
    research.save()
    research.artifact_path("notes/n1.md").write_text(
        textwrap.dedent(
            """
            ---
            research: eval-foo
            date: 2026-07-03
            stage: explore
            sources:
              - id: S1
                url: https://docs.foo.dev/guide
                tier: T1
                date: 2026-07-03
              - id: S2
                url: https://bench.example.com/foo
                tier: T2
                date: 2026-07-03
            ---

            ## Evidencia
            Foo reduce latencia a 100 ms [S1].
            Foo cuesta menos de 10 USD [S2].
            """
        ).lstrip(),
        encoding="utf-8",
    )
    _write_snapshot(research, "S1", "Foo reduce latencia a 100 ms.")
    _write_snapshot(research, "S2", "Foo cuesta menos de 10 USD.")
    return research


def _write_snapshot(research, source_id, content=None, status="ok", **metadata):
    path = research.artifact_path(f"notes/sources/{source_id}")
    path.mkdir(parents=True, exist_ok=True)
    if content is not None:
        path.joinpath("content.md").write_text(content, encoding="utf-8")
    source_url = {
        "S1": "https://docs.foo.dev/guide",
        "S2": "https://bench.example.com/foo",
    }.get(source_id, f"https://example.com/{source_id}")
    declared_url = metadata.get("declared_url", metadata.get("url", source_url))
    meta = {
        "schema_version": 2,
        "url": declared_url,
        "declared_url": declared_url,
        "final_url": declared_url,
        "redirects": [],
        "status": status,
        "org": "example",
        "captured_at": "2026-07-03T00:00:00+00:00",
        "http_status": 200 if status == "ok" else 404,
        "content_type": "text/plain",
        "content_eligible": True,
        **metadata,
    }
    if content is not None:
        meta["content_hash"] = hashlib.sha256(content.encode()).hexdigest()
    path.joinpath("meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")


def _snapshot_item(report, source_id="S1"):
    return next(item for item in report.items if item.source_id == source_id)


def test_verify_claims_uses_full_markdown_canonical_ids_and_local_matcher(tmp_path):
    research = _explore_research(tmp_path)

    report = verification.verify_explore_claims(research)
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))

    assert report.passed
    assert {item.state for item in report.items} == {"verified"}
    assert all(item.note_path == "notes/n1.md" for item in report.items)
    assert all(item.line_start > 10 for item in report.items)
    assert ledger["schema_version"] == SCHEMA_VERSION
    assert ledger["claims"] == [item.to_dict() for item in report.items]
    assert all(item.quote and item.locator for item in report.items)


def test_verify_claims_reuses_only_current_recoverable_cache(tmp_path):
    research = _explore_research(tmp_path)
    first = verification.verify_explore_claims(research)

    second = verification.verify_explore_claims(research)

    assert first.passed and second.passed
    assert not any(item.cached for item in first.items)
    assert all(item.cached for item in second.items)


def test_nonpersisting_verification_does_not_take_exclusive_ledger_lock(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)

    def forbidden_lock(path):
        raise AssertionError(f"unexpected exclusive lock for {path}")

    monkeypatch.setattr(verification, "ledger_directory_lock", forbidden_lock)

    report = verification.verify_explore_claims(research, persist=False)

    assert report.passed
    assert not research.artifact_path("notes/sources/verification.yaml").exists()


def test_invalid_cached_locator_is_preserved_stale_and_recomputed_immediately(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["locator"] = {"line_start": 99, "line_end": 99}
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)
    updated = load_ledger(path)

    assert report.passed
    assert report.items[0].state == "verified"
    assert report.items[0].cached is False
    assert any(
        entry.get("kind") == "stale_claim" and entry["data"]["state"] == "stale"
        for entry in updated["legacy"]
    )


def test_changed_snapshot_invalidates_cache_and_recomputes_not_anchored(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    _write_snapshot(research, "S1", "La latencia es 200 ms.")

    report = verification.verify_explore_claims(research)

    item = next(item for item in report.items if item.source_id == "S1")
    assert not report.passed
    assert item.state == "not_anchored"
    assert item.cached is False


def test_missing_empty_or_non_ok_snapshot_is_unverifiable_with_stable_sentinel(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    _write_snapshot(research, "S2", "", status="ok", http_status=200)

    first = verification.verify_explore_claims(research)
    first_hashes = {item.source_id: item.snapshot_hash for item in first.items}
    second = verification.verify_explore_claims(research)

    assert not first.passed
    assert {item.state for item in first.items} == {"unverifiable"}
    assert first_hashes == {item.source_id: item.snapshot_hash for item in second.items}
    assert all(value.startswith("snapshot-v2-") for value in first_hashes.values())
    assert all(item.normalization_version == NORMALIZATION_VERSION for item in first.items)
    assert all(item.matcher_version == MATCHER_VERSION for item in first.items)


def test_sentinel_changes_when_canonical_metadata_changes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    before = verification.verify_explore_claims(research)
    old_hash = next(item.snapshot_hash for item in before.items if item.source_id == "S1")
    _write_snapshot(research, "S1", None, status="missing", http_status=410)

    after = verification.verify_explore_claims(research)

    assert next(item.snapshot_hash for item in after.items if item.source_id == "S1") != old_hash


def test_sentinel_is_stable_when_metadata_key_order_changes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    before = verification.verify_explore_claims(research)
    old_hash = next(item.snapshot_hash for item in before.items if item.source_id == "S1")
    meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta_path.write_text(
        yaml.safe_dump(dict(reversed(list(metadata.items()))), sort_keys=False),
        encoding="utf-8",
    )

    after = verification.verify_explore_claims(research)

    assert next(item.snapshot_hash for item in after.items if item.source_id == "S1") == old_hash


def test_unknown_active_state_is_preserved_as_history_and_recomputed(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["state"] = "future_state"
    ledger["claims"][0]["opaque"] = {"keep": True}
    ledger["legacy"].append({"verdict": "supported", "opaque": {"keep": True}})
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)
    ledger = load_ledger(path)

    assert report.items[0].state == "verified"
    assert all(entry.get("state") != "future_state" for entry in ledger["claims"])
    assert any(
        entry.get("kind") == "stale_claim"
        and entry["data"].get("state") == "stale"
        and entry["data"].get("opaque") == {"keep": True}
        for entry in ledger["legacy"]
    )
    assert {"verdict": "supported", "opaque": {"keep": True}} in ledger["legacy"]


def test_legacy_semantic_claim_is_recomputed_and_metadata_survives(tmp_path):
    research = _explore_research(tmp_path)
    path = research.artifact_path("notes/sources/verification.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((FIXTURES / "legacy_verification_v2.yaml").read_bytes())
    legacy_raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    report = verification.verify_explore_claims(research)
    ledger = load_ledger(path)

    assert report.passed
    assert {item.state for item in report.items} == {"verified"}
    assert all(item.claim_id != "old-semantic-claim" for item in report.items)
    assert legacy_raw["claims"][0] in ledger["legacy"]
    assert {"kind": "resolution", "data": legacy_raw["resolutions"][0]} in ledger["legacy"]


def test_replacing_active_entry_preserves_its_unknown_fields_in_legacy(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    old_entry = ledger["claims"][0]
    old_entry["opaque_extension"] = {"keep": [1, 2]}
    old_entry["matcher_version"] = "obsolete"
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    verification.verify_explore_claims(research)
    updated = load_ledger(path)

    assert any(
        entry.get("kind") == "stale_claim"
        and entry["data"].get("opaque_extension") == {"keep": [1, 2]}
        for entry in updated["legacy"]
    )


def test_cached_active_entry_preserves_unknown_fields_round_trip(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["opaque_extension"] = {"keep": [1, 2]}
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    verification.verify_explore_claims(research)

    assert load_ledger(path)["claims"][0]["opaque_extension"] == {"keep": [1, 2]}


def test_verify_claims_cli_json_is_v2_and_items_are_v2(tmp_path, monkeypatch):
    _explore_research(tmp_path)
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    result = CliRunner().invoke(
        main, ["verify-claims", "eval-foo", "--json"], catch_exceptions=False
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["schema_version"] == 2
    assert payload["passed"] is True
    assert {item["state"] for item in payload["items"]} == {"verified"}
    assert all("verdict" not in item for item in payload["items"])


def test_deterministic_match_reports_only_local_textual_anchoring(tmp_path, monkeypatch):
    _explore_research(tmp_path)
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    structured = CliRunner().invoke(
        main, ["verify-claims", "eval-foo", "--json"], catch_exceptions=False
    )
    human = CliRunner().invoke(main, ["verify-claims", "eval-foo"], catch_exceptions=False)
    payload = json.loads(structured.output)
    rendered = json.dumps(payload, ensure_ascii=False).lower()

    assert structured.exit_code == 0
    assert human.exit_code == 0
    assert {item["confidence_scope"] for item in payload["items"]} == {"local_textual_anchoring"}
    assert "anclaje textual local" in human.output.lower()
    for overclaim in (
        "publisher_identity",
        "authenticated_publisher",
        "authorship",
        "authenticity",
        "accuracy",
        "truth",
    ):
        assert overclaim not in rendered


@pytest.mark.parametrize(
    ("state", "expected_scope"),
    [
        ("verified", "local_textual_anchoring"),
        ("human_reviewed", "scoped_human_review"),
        ("not_anchored", "not_anchored"),
        ("unverifiable", "unverifiable"),
        ("stale", "stale"),
    ],
)
def test_confidence_scope_describes_the_actual_verification_state(state, expected_scope):
    item = verification.VerificationItem(
        claim_id="claim-1",
        note_path="notes/n1.md",
        line_start=1,
        line_end=1,
        source_id="S1",
        claim_text="claim",
        claim_hash="claim-hash",
        snapshot_hash="snapshot-hash",
        normalization_version=NORMALIZATION_VERSION,
        matcher_version=MATCHER_VERSION,
        state=state,
    )

    assert item.to_dict()["confidence_scope"] == expected_scope


def test_human_reviewed_cli_reports_scoped_review_without_claiming_textual_match(
    tmp_path, monkeypatch
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    _write_snapshot(research, "S2", None, status="missing")
    blocked = verification.verify_explore_claims(research)
    for item in blocked.items:
        verification.resolve_claim(research, item.claim_id, reason="revisado", by="nacho")
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    result = CliRunner().invoke(main, ["verify-claims", "eval-foo"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "[OK] verificación de claims" in result.output
    assert "revisión humana acotada" in result.output
    assert "[OK] anclaje textual local" not in result.output
    assert "anclaje textual local" not in result.output


def test_unverifiable_cli_does_not_label_the_report_as_textual_anchoring(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    result = CliRunner().invoke(main, ["verify-claims", "eval-foo"], catch_exceptions=False)

    assert result.exit_code == 1
    assert "[FALLA] verificación de claims" in result.output
    assert "evidencia no verificable" in result.output
    assert "[OK] anclaje textual local" not in result.output


def test_verify_claims_cli_exits_nonzero_for_unverifiable(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    monkeypatch.setenv("SDR_ROOT", str(tmp_path))

    result = CliRunner().invoke(main, ["verify-claims", "eval-foo", "--json"])
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["passed"] is False
    assert "unverifiable" in {item["state"] for item in payload["items"]}


def test_resolution_only_unlocks_when_all_hashes_and_versions_match(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    blocked = verification.verify_explore_claims(research)
    item = next(item for item in blocked.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["resolutions"] = [
        {
            "claim_id": item.claim_id,
            "by": "nacho",
            "reason": "revisado",
            "date": "2026-07-11",
            "claim_hash": item.claim_hash,
            "snapshot_hash": item.snapshot_hash,
            "normalization_version": item.normalization_version,
            "matcher_version": "obsolete",
        }
    ]
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)

    current = next(item for item in report.items if item.source_id == "S1")
    assert not report.passed
    assert current.state == "unverifiable"


def test_incomplete_legacy_resolution_does_not_unlock(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    blocked = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in blocked.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["resolutions"] = [
        {"claim_id": claim_id, "by": "nacho", "reason": "legacy", "date": "2026-07-11"}
    ]
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)

    assert not report.passed
    assert next(item.state for item in report.items if item.source_id == "S1") == "unverifiable"


def test_cached_human_reviewed_without_current_resolution_does_not_pass(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    entry = next(item for item in ledger["claims"] if item["source_id"] == "S1")
    entry["state"] = "human_reviewed"
    ledger["resolutions"] = []
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)

    item = next(item for item in report.items if item.source_id == "S1")
    assert not report.passed
    assert item.state == "unverifiable"
    assert item.cached is False


def test_stale_active_claim_with_usable_snapshot_is_recomputed(tmp_path):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["state"] = "stale"
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    report = verification.verify_explore_claims(research)

    assert report.passed
    assert report.items[0].state == "verified"
    assert report.items[0].cached is False


def test_stale_remains_effective_only_when_recomputation_raises(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["state"] = "stale"
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    calls = 0

    def fail_match(claim_text, snapshot_text):
        nonlocal calls
        calls += 1
        raise RuntimeError("matcher unavailable")

    monkeypatch.setattr(verification, "match_text", fail_match)
    report = verification.verify_explore_claims(research)

    assert not report.passed
    assert report.items[0].state == "stale"
    assert calls == 1


def test_failed_stale_recomputation_preserves_unknown_fields(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    ledger["claims"][0]["state"] = "stale"
    ledger["claims"][0]["opaque_extension"] = {"keep": [1, 2]}
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    def fail_match(claim_text, snapshot_text):
        raise RuntimeError("matcher unavailable")

    monkeypatch.setattr(verification, "match_text", fail_match)
    verification.verify_explore_claims(research)
    updated = load_ledger(path)

    preserved = updated["claims"] + [entry.get("data", {}) for entry in updated["legacy"]]
    assert any(entry.get("opaque_extension") == {"keep": [1, 2]} for entry in preserved)


def test_snapshot_url_mismatch_is_unverifiable_without_network(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    _write_snapshot(
        research,
        "S1",
        "Foo reduce latencia a 100 ms.",
        url="https://stale.example/old-guide",
    )

    def no_network(*args, **kwargs):
        raise AssertionError("verify-claims must not use network")

    monkeypatch.setattr("urllib.request.urlopen", no_network)
    report = verification.verify_explore_claims(research)

    item = next(item for item in report.items if item.source_id == "S1")
    assert not report.passed
    assert item.state in {"unverifiable", "stale"}


def test_incomplete_legacy_snapshot_metadata_is_never_inferred_as_eligible(tmp_path):
    research = _explore_research(tmp_path)
    source_dir = research.artifact_path("notes/sources/S1")
    content = source_dir.joinpath("content.md").read_bytes()
    source_dir.joinpath("meta.yaml").write_text(
        yaml.safe_dump(
            {
                "url": "https://docs.foo.dev/guide",
                "status": "ok",
                "content_hash": hashlib.sha256(content).hexdigest(),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.snapshot_hash.startswith("snapshot-v2-")


@pytest.mark.parametrize(
    "changes",
    [
        {"declared_url": "https://changed.example/guide", "url": "https://changed.example/guide"},
        {
            "final_url": "https://cdn.foo.dev/guide",
            "redirects": [
                {
                    "url": "https://docs.foo.dev/guide",
                    "status_code": 302,
                    "location": "https://cdn.foo.dev/guide",
                    "target_url": "https://cdn.foo.dev/guide",
                }
            ],
        },
        {"http_status": 201},
        {"content_type": "text/markdown"},
        {"captured_at": "2026-07-04T00:00:00+00:00"},
    ],
)
def test_every_evidence_affecting_provenance_change_invalidates_cached_identity(tmp_path, changes):
    research = _explore_research(tmp_path)
    before = _snapshot_item(verification.verify_explore_claims(research))
    meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    metadata.update(changes)
    meta_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    after = _snapshot_item(verification.verify_explore_claims(research))

    assert after.snapshot_hash != before.snapshot_hash
    assert after.cached is False
    if changes.get("declared_url"):
        assert after.state == "unverifiable"


@pytest.mark.parametrize(
    ("content", "metadata"),
    [
        ("Foo reduce latencia a 100 ms.", {"http_status": 404}),
        ("Foo reduce latencia a 100 ms.", {"content_eligible": False}),
        ("", {}),
    ],
)
def test_noneligible_http_unsupported_and_empty_snapshots_cannot_anchor(
    tmp_path, content, metadata
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", content, status="ok", **metadata)

    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.quote == ""
    assert item.locator is None


@pytest.mark.parametrize(
    "content_type",
    [
        "",
        "application/octet-stream",
        "text/plain, application/octet-stream",
        "text/",
        "text/plain@invalid",
        "text/plain; charset",
    ],
)
def test_persisted_true_eligibility_cannot_override_canonical_content_type_policy(
    tmp_path, content_type
):
    research = _explore_research(tmp_path)
    _write_snapshot(
        research,
        "S1",
        "Foo reduce latencia a 100 ms.",
        content_type=content_type,
        content_eligible=True,
    )

    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.quote == ""


@pytest.mark.parametrize(
    "changes",
    [
        {
            "declared_url": "file:///etc/passwd",
            "url": "file:///etc/passwd",
            "final_url": "file:///etc/passwd",
        },
        {
            "declared_url": "https://bad host.example/guide",
            "url": "https://bad host.example/guide",
            "final_url": "https://bad host.example/guide",
        },
        {
            "declared_url": "http://127.0.0.1/private",
            "url": "http://127.0.0.1/private",
            "final_url": "http://127.0.0.1/private",
        },
        *[
            {
                "final_url": target,
                "redirects": [
                    {
                        "url": "https://docs.foo.dev/guide",
                        "status_code": 302,
                        "location": target,
                        "target_url": target,
                    }
                ],
            }
            for target in (
                "file:///etc/passwd",
                "https://user:secret@docs.foo.dev/guide",
                "https://docs.foo.dev:70000/guide",
                "https://bad host.example/guide",
                "http://127.0.0.1/private",
            )
        ],
    ],
)
def test_structurally_invalid_persisted_provenance_fails_closed_without_dns(
    tmp_path, monkeypatch, changes
):
    research = _explore_research(tmp_path)
    meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    metadata.update(changes)
    meta_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("offline verification used DNS")
        ),
    )
    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.quote == ""


@pytest.mark.parametrize(
    "captured_at",
    [
        "not-a-timestamp",
        "2026-02-30T00:00:00+00:00",
        "2026-07-03T00:00:00",
        "2026-07-03",
        "2026-07-03 00:00:00+00:00",
        "2026-07-03T00:00:00Z",
    ],
)
def test_invalid_or_naive_capture_timestamp_fails_closed(tmp_path, captured_at):
    research = _explore_research(tmp_path)
    _write_snapshot(
        research,
        "S1",
        "Foo reduce latencia a 100 ms.",
        captured_at=captured_at,
    )

    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.quote == ""


def test_anchoring_recomputes_sha256_over_exact_persisted_content_bytes(tmp_path):
    research = _explore_research(tmp_path)
    content_path = research.artifact_path("notes/sources/S1/content.md")
    content_path.write_bytes(b"Foo reduce latencia a 100 ms.\n")
    meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    metadata["content_hash"] = hashlib.sha256(b"Foo reduce latencia a 100 ms.").hexdigest()
    meta_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    item = _snapshot_item(verification.verify_explore_claims(research))

    assert item.state == "unverifiable"
    assert item.quote == ""


def test_provenance_change_stales_scoped_resolution_without_making_snapshot_eligible(
    tmp_path,
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", "Different text.")
    blocked = _snapshot_item(verification.verify_explore_claims(research))
    verification.resolve_claim(research, blocked.claim_id, reason="reviewed", by="nacho")
    reviewed = _snapshot_item(verification.verify_explore_claims(research))
    assert reviewed.state == "human_reviewed"
    assert reviewed.quote == ""

    meta_path = research.artifact_path("notes/sources/S1/meta.yaml")
    metadata = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    metadata["http_status"] = 201
    meta_path.write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")

    current = _snapshot_item(verification.verify_explore_claims(research))
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))

    assert current.state == "not_anchored"
    assert current.snapshot_hash != blocked.snapshot_hash
    assert (
        next(
            resolution
            for resolution in ledger["resolutions"]
            if resolution["claim_id"] == blocked.claim_id
        )["state"]
        == "stale"
    )


def test_legacy_resolution_remains_effective_as_human_reviewed(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    blocked = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in blocked.items if item.source_id == "S1")

    verification.resolve_claim(research, claim_id, reason="revisado", by="nacho")
    report = verification.verify_explore_claims(research)

    item = next(item for item in report.items if item.source_id == "S1")
    assert report.passed
    assert item.state == "human_reviewed"


def test_current_human_review_is_reused_stably_without_stale_duplicates(tmp_path, monkeypatch):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    blocked = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in blocked.items if item.source_id == "S1")
    verification.resolve_claim(research, claim_id, reason="revisado", by="nacho")
    first = verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    legacy_count = len(load_ledger(path)["legacy"])

    def unexpected_match(claim_text, snapshot_text):
        raise AssertionError("current human review must be reusable")

    monkeypatch.setattr(verification, "match_text", unexpected_match)
    second = verification.verify_explore_claims(research)
    third = verification.verify_explore_claims(research)

    assert first.passed and second.passed and third.passed
    assert next(item.state for item in second.items if item.source_id == "S1") == "human_reviewed"
    assert len(load_ledger(path)["legacy"]) == legacy_count


def test_persisted_human_review_is_invalidated_before_reuse_when_snapshot_identity_changes(
    tmp_path,
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    blocked = verification.verify_explore_claims(research)
    item = next(current for current in blocked.items if current.source_id == "S1")
    verification.resolve_claim(research, item.claim_id, reason="revisado", by="nacho")

    reviewed = verification.verify_explore_claims(research)
    persisted = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    assert next(
        current.state for current in reviewed.items if current.claim_id == item.claim_id
    ) == ("human_reviewed")
    assert (
        next(
            current["state"]
            for current in persisted["claims"]
            if current["claim_id"] == item.claim_id
        )
        == "human_reviewed"
    )

    _write_snapshot(research, "S1", None, status="missing", http_status=410)
    recomputed = verification.verify_explore_claims(research)
    updated = load_ledger(research.artifact_path("notes/sources/verification.yaml"))

    current = next(value for value in recomputed.items if value.claim_id == item.claim_id)
    resolution = next(
        value for value in updated["resolutions"] if value["claim_id"] == item.claim_id
    )
    assert current.state == "unverifiable"
    assert current.snapshot_hash != item.snapshot_hash
    assert resolution["state"] == "stale"


@pytest.mark.parametrize("state", ["verified"])
def test_resolve_claim_rejects_ineligible_active_state_without_changing_bytes(tmp_path, state):
    research = _explore_research(tmp_path)
    if state != "verified":
        _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    item = next(item for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    next(entry for entry in ledger["claims"] if entry["claim_id"] == item.claim_id)["state"] = state
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match=state):
        verification.resolve_claim(research, item.claim_id, reason="revisado", by="nacho")

    assert path.read_bytes() == before


def test_resolve_claim_rejects_effective_human_reviewed_without_changing_bytes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in report.items if item.source_id == "S1")
    verification.resolve_claim(research, claim_id, reason="primera", by="nacho")
    path = research.artifact_path("notes/sources/verification.yaml")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="human_reviewed"):
        verification.resolve_claim(research, claim_id, reason="segunda", by="otra")

    assert path.read_bytes() == before


def test_resolve_claim_rejects_unknown_id_and_blank_actor_or_reason_without_changing_bytes(
    tmp_path,
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")

    for candidate_id, reason, actor, message in (
        ("claim-does-not-exist", "revisado", "nacho", "no existe"),
        (claim_id, "   ", "nacho", "motivo"),
        (claim_id, "revisado", "   ", "actor"),
    ):
        before = path.read_bytes()
        with pytest.raises(ValueError, match=message):
            verification.resolve_claim(research, candidate_id, reason=reason, by=actor)
        assert path.read_bytes() == before


def test_resolve_unverifiable_persists_exact_identity_and_becomes_human_reviewed(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    blocked = verification.verify_explore_claims(research)
    item = next(item for item in blocked.items if item.source_id == "S1")

    verification.resolve_claim(
        research, item.claim_id, reason="  evidencia revisada  ", by=" Nacho "
    )

    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    resolution = next(
        entry for entry in ledger["resolutions"] if entry["claim_id"] == item.claim_id
    )
    assert resolution == {
        "claim_id": item.claim_id,
        "by": "Nacho",
        "reason": "evidencia revisada",
        "date": date.today().isoformat(),
        "claim_hash": item.claim_hash,
        "snapshot_hash": item.snapshot_hash,
        "normalization_version": item.normalization_version,
        "matcher_version": item.matcher_version,
        "state": "active",
    }
    assert resolution["snapshot_hash"].startswith("snapshot-v2-")
    assert (
        next(
            current.state
            for current in verification.verify_explore_claims(research).items
            if current.claim_id == item.claim_id
        )
        == "human_reviewed"
    )


def test_changed_snapshot_marks_resolution_stale_and_preserves_audit(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    blocked = verification.verify_explore_claims(research)
    item = next(item for item in blocked.items if item.source_id == "S1")
    verification.resolve_claim(research, item.claim_id, reason="revisado", by="nacho")
    _write_snapshot(research, "S1", None, status="missing", http_status=410)

    report = verification.verify_explore_claims(research)
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))

    assert (
        next(current.state for current in report.items if current.claim_id == item.claim_id)
        == "unverifiable"
    )
    stale = next(entry for entry in ledger["resolutions"] if entry["claim_id"] == item.claim_id)
    assert stale["state"] == "stale"
    assert stale["snapshot_hash"] == item.snapshot_hash


def test_generic_override_metadata_does_not_unlock_verification_ledger(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    verification.verify_explore_claims(research)
    path = research.artifact_path("notes/sources/verification.yaml")
    research.meta.overrides.append(
        {"stage": "explore", "reason": "override genérico", "by": "nacho", "layer": "anchored"}
    )
    research.save()

    report = verification.verify_explore_claims(research)

    assert not report.passed
    assert not any(item.state == "human_reviewed" for item in report.items)
    assert load_ledger(path)["resolutions"] == []


def test_resolve_rejects_claim_removed_since_last_verification_without_changing_bytes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in report.items if item.source_id == "S1")
    note_path = research.artifact_path("notes/n1.md")
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace("Foo reduce latencia a 100 ms [S1].\n", ""),
        encoding="utf-8",
    )
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    before = ledger_path.read_bytes()

    with pytest.raises(ValueError, match="no existe|vigente"):
        verification.resolve_claim(research, claim_id, reason="revisado", by="nacho")

    assert ledger_path.read_bytes() == before


def test_resolve_uses_fresh_changed_sentinel_identity_without_intermediate_write(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing", http_status=404)
    report = verification.verify_explore_claims(research)
    old = next(item for item in report.items if item.source_id == "S1")
    _write_snapshot(research, "S1", None, status="missing", http_status=410)

    verification.resolve_claim(research, old.claim_id, reason="revisado", by="nacho")

    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    resolution = next(item for item in ledger["resolutions"] if item["claim_id"] == old.claim_id)
    assert resolution["snapshot_hash"].startswith("snapshot-v2-")
    assert resolution["snapshot_hash"] != old.snapshot_hash
    assert (
        next(item for item in ledger["claims"] if item["claim_id"] == old.claim_id)["snapshot_hash"]
        == resolution["snapshot_hash"]
    )


def test_resolve_rejects_when_new_snapshot_now_verifies_without_changing_bytes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    claim_id = next(item.claim_id for item in report.items if item.source_id == "S1")
    _write_snapshot(research, "S1", "Foo reduce latencia a 100 ms.", status="ok")
    ledger_path = research.artifact_path("notes/sources/verification.yaml")
    before = ledger_path.read_bytes()

    with pytest.raises(ValueError, match="verified"):
        verification.resolve_claim(research, claim_id, reason="revisado", by="nacho")

    assert ledger_path.read_bytes() == before


def test_resolve_uses_fresh_identity_instead_of_obsolete_cached_claim(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", "Texto que no contiene el claim.")
    report = verification.verify_explore_claims(research)
    item = next(item for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    cached = next(entry for entry in ledger["claims"] if entry["claim_id"] == item.claim_id)
    cached["matcher_version"] = "obsolete"
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    verification.resolve_claim(research, item.claim_id, reason="revisado", by="nacho")

    resolution = next(
        entry for entry in load_ledger(path)["resolutions"] if entry["claim_id"] == item.claim_id
    )
    assert resolution["matcher_version"] == MATCHER_VERSION
    assert cached["matcher_version"] == "obsolete"


def test_resolve_rejects_duplicate_active_resolutions_without_changing_bytes(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    item = next(item for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    base = {
        "claim_id": item.claim_id,
        "by": "uno",
        "reason": "primera",
        "date": "2026-07-11",
        "claim_hash": item.claim_hash,
        "snapshot_hash": item.snapshot_hash,
        "normalization_version": item.normalization_version,
        "matcher_version": item.matcher_version,
        "state": "active",
    }
    ledger["resolutions"] = [base, base | {"by": "dos", "reason": "segunda"}]
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="duplicad"):
        verification.resolve_claim(research, item.claim_id, reason="tercera", by="tres")

    assert path.read_bytes() == before


def test_verify_rejects_duplicate_active_resolutions_without_last_wins_or_write(tmp_path):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    item = next(item for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    resolution = {
        "claim_id": item.claim_id,
        "by": "uno",
        "reason": "primera",
        "date": "2026-07-11",
        "claim_hash": item.claim_hash,
        "snapshot_hash": item.snapshot_hash,
        "normalization_version": item.normalization_version,
        "matcher_version": item.matcher_version,
        "state": "active",
    }
    ledger["resolutions"] = [resolution, resolution | {"by": "dos"}]
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")
    before = path.read_bytes()

    with pytest.raises(ValueError, match="duplicad"):
        verification.verify_explore_claims(research)

    assert path.read_bytes() == before


def test_stale_and_active_resolution_for_same_claim_survive_verify_save_reload_in_order(
    tmp_path,
):
    research = _explore_research(tmp_path)
    _write_snapshot(research, "S1", None, status="missing")
    report = verification.verify_explore_claims(research)
    item = next(item for item in report.items if item.source_id == "S1")
    path = research.artifact_path("notes/sources/verification.yaml")
    ledger = load_ledger(path)
    identity = {
        "claim_id": item.claim_id,
        "date": "2026-07-11",
        "claim_hash": item.claim_hash,
        "snapshot_hash": item.snapshot_hash,
        "normalization_version": item.normalization_version,
        "matcher_version": item.matcher_version,
    }
    stale = identity | {"by": "antes", "reason": "histórica", "state": "stale"}
    active = identity | {"by": "ahora", "reason": "vigente", "state": "active"}
    ledger["resolutions"] = [stale, active]
    path.write_text(yaml.safe_dump(ledger, sort_keys=False), encoding="utf-8")

    verified = verification.verify_explore_claims(research)
    reloaded = load_ledger(path)

    assert verified.passed
    assert next(
        current.state for current in verified.items if current.claim_id == item.claim_id
    ) == ("human_reviewed")
    assert reloaded["resolutions"] == [stale, active]
