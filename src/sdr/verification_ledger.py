"""Tolerant persistence contract for the verification.yaml schema v2."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

SCHEMA_VERSION = 2
ACTIVE_STATES = frozenset({"verified", "not_anchored", "unverifiable", "human_reviewed", "stale"})
LEGACY_SEMANTIC_STATES = frozenset({"supported", "contradicted", "not_found"})
_COLLECTIONS = ("claims", "resolutions", "degradation_acknowledgements", "legacy")
_CLAIM_FIELDS = {
    "claim_id",
    "note_path",
    "line_start",
    "line_end",
    "claim_text",
    "source_id",
    "claim_hash",
    "snapshot_hash",
    "normalization_version",
    "matcher_version",
}
_RESOLUTION_FIELDS = {
    "claim_id",
    "by",
    "reason",
    "date",
    "claim_hash",
    "snapshot_hash",
    "normalization_version",
    "matcher_version",
}
_ACKNOWLEDGEMENT_FIELDS = {
    "acknowledgement_id",
    "source_investigation",
    "source_id",
    "cause",
    "observation_id",
    "by",
    "reason",
    "date",
}
_DEGRADATION_CAUSES = frozenset({"unreachable", "changed", "expired"})


class LedgerValidationError(ValueError):
    """Signals that verification.yaml does not hold a safe, actionable structure."""


def empty_ledger() -> dict[str, Any]:
    """Create an empty v2 ledger with its collections kept separate."""
    return {
        "schema_version": SCHEMA_VERSION,
        "claims": [],
        "resolutions": [],
        "degradation_acknowledgements": [],
        "legacy": [],
    }


def make_degradation_acknowledgement_id(
    source_investigation: str,
    source_id: str,
    cause: str,
    observation_id: str,
) -> str:
    """Derive the immutable identity of one reviewed degradation observation."""
    values = (source_investigation, source_id, cause, observation_id)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise LedgerValidationError("degradation acknowledgement identity must not be empty")
    if cause not in _DEGRADATION_CAUSES:
        raise LedgerValidationError(f"unknown degradation cause: {cause}")
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return f"degradation-ack-{hashlib.sha256(canonical.encode()).hexdigest()}"


def make_claim_id(
    note_path: str,
    line_start: int,
    line_end: int,
    source_id: str,
    claim_hash: str,
) -> str:
    """Derive the stable identity of a claim from all of its contractual components."""
    canonical_path = _canonical_note_path(note_path)
    if not _valid_line_range(line_start, line_end):
        raise LedgerValidationError("claim locator must be an inclusive line range")
    if not source_id.strip() or not claim_hash.strip():
        raise LedgerValidationError("source_id and claim_hash must not be empty")
    identity = [canonical_path, line_start, line_end, source_id, claim_hash]
    canonical = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return f"claim-{hashlib.sha256(canonical.encode()).hexdigest()}"


def load_ledger(path: Path) -> dict[str, Any]:
    """Load a v2 ledger, conservatively separating out older semantic entries."""
    if not path.exists():
        return empty_ledger()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LedgerValidationError(f"{path.name} must contain a top-level mapping")
    _validate_raw_collections(path, raw)
    if raw.get("schema_version") == SCHEMA_VERSION:
        ledger = dict(raw)
        for collection in _COLLECTIONS:
            ledger.setdefault(collection, [])
        active_claims: list[dict[str, Any]] = []
        for claim in ledger["claims"]:
            if _is_legacy_semantic_claim(claim):
                ledger["legacy"].append(claim)
            else:
                active_claims.append(claim)
        ledger["claims"] = active_claims
        active_resolutions: list[dict[str, Any]] = []
        for resolution in ledger["resolutions"]:
            if _resolution_has_versioned_identity(resolution):
                active_resolutions.append(resolution)
            else:
                ledger["legacy"].append({"kind": "resolution", "data": resolution})
        ledger["resolutions"] = active_resolutions
        return ledger

    ledger = empty_ledger()
    ledger["legacy"] = list(raw.get("legacy") or []) + list(raw.get("claims") or [])
    ledger["legacy"].extend(
        {"kind": "resolution", "data": resolution} for resolution in raw.get("resolutions") or []
    )
    unknown = {key: value for key, value in raw.items() if key not in _COLLECTIONS}
    if unknown:
        ledger["legacy"].append({"kind": "top_level", "data": unknown})
    return ledger


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """Save the ledger with deterministic serialization, keeping unknown fields."""
    issues = validate_ledger(ledger)
    blocking = [issue for issue in issues if " has unknown active state: " not in issue]
    if blocking:
        raise LedgerValidationError("invalid verification ledger: " + "; ".join(blocking))
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(ledger, allow_unicode=True, sort_keys=False)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(serialized)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@contextmanager
def ledger_directory_lock(path: Path) -> Iterator[None]:
    """Serialize ledger read-modify-write operations without creating a lock artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def validate_ledger(ledger: dict[str, Any]) -> list[str]:
    """Report inconsistencies of the active contract without rejecting future or legacy data."""
    issues: list[str] = []
    if ledger.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must be {SCHEMA_VERSION}")
    for collection in _COLLECTIONS:
        if not isinstance(ledger.get(collection), list):
            issues.append(f"{collection} must be a list")

    claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
    seen_claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            issues.append(f"claims[{index}] must be a mapping")
            continue
        claim_id = str(claim.get("claim_id") or "<missing>")
        if claim_id in seen_claim_ids:
            issues.append(f"duplicate claim_id: {claim_id}")
        seen_claim_ids.add(claim_id)
        _validate_required_strings(
            claim,
            _CLAIM_FIELDS - {"line_start", "line_end"},
            "claim",
            claim_id,
            issues,
        )
        valid_range = _valid_line_range(claim.get("line_start"), claim.get("line_end"))
        if not valid_range:
            issues.append(f"claim {claim_id} locator must be an inclusive line range")
        try:
            note_path = str(claim.get("note_path") or "")
            canonical_path = _canonical_note_path(note_path)
            if note_path != canonical_path:
                issues.append(f"claim {claim_id} note_path must be canonical POSIX")
        except LedgerValidationError as exc:
            issues.append(f"claim {claim_id} note_path: {exc}")
            canonical_path = ""
        identity_fields = (
            canonical_path,
            claim.get("source_id"),
            claim.get("claim_hash"),
        )
        identity_complete = all(
            isinstance(value, str) and value.strip() for value in identity_fields
        )
        if valid_range and identity_complete:
            expected_id = make_claim_id(
                canonical_path,
                claim["line_start"],
                claim["line_end"],
                claim["source_id"],
                claim["claim_hash"],
            )
            if claim.get("claim_id") != expected_id:
                issues.append(f"claim {claim_id} claim_id does not match its identity fields")
        state = claim.get("state")
        if state not in ACTIVE_STATES:
            issues.append(f"claim {claim_id} has unknown active state: {state}")
            continue
        if state == "verified":
            locator = claim.get("locator")
            if not claim.get("quote"):
                issues.append(f"claim {claim_id} verified quote must not be empty")
            if not _valid_locator(locator):
                issues.append(f"claim {claim_id} locator must be an inclusive line range")

    resolutions = ledger.get("resolutions") if isinstance(ledger.get("resolutions"), list) else []
    seen_active_resolution_ids: set[str] = set()
    for index, resolution in enumerate(resolutions):
        if not isinstance(resolution, dict):
            issues.append(f"resolutions[{index}] must be a mapping")
            continue
        claim_id = str(resolution.get("claim_id") or "<missing>")
        _validate_required_strings(resolution, _RESOLUTION_FIELDS, "resolution", claim_id, issues)
        state = resolution.get("state", "active")
        if state not in {"active", "stale"}:
            issues.append(f"resolution {claim_id} has unknown state: {state}")
        if state == "active":
            if claim_id in seen_active_resolution_ids:
                issues.append(f"duplicate active resolution for claim_id: {claim_id}")
            seen_active_resolution_ids.add(claim_id)
        claim = next((entry for entry in claims if entry.get("claim_id") == claim_id), None)
        if resolution.get("state") == "active" and (
            claim is None
            or any(
                resolution.get(field) != claim.get(field)
                for field in (
                    "claim_hash",
                    "snapshot_hash",
                    "normalization_version",
                    "matcher_version",
                )
            )
        ):
            issues.append(f"resolution {claim_id} active resolution does not match current claim")

    acknowledgements = (
        ledger.get("degradation_acknowledgements")
        if isinstance(ledger.get("degradation_acknowledgements"), list)
        else []
    )
    seen_acknowledgement_ids: set[str] = set()
    for index, acknowledgement in enumerate(acknowledgements):
        if not isinstance(acknowledgement, dict):
            issues.append(f"degradation_acknowledgements[{index}] must be a mapping")
            continue
        acknowledgement_id = str(acknowledgement.get("acknowledgement_id") or "<missing>")
        _validate_required_strings(
            acknowledgement,
            _ACKNOWLEDGEMENT_FIELDS,
            "degradation acknowledgement",
            acknowledgement_id,
            issues,
        )
        if acknowledgement_id in seen_acknowledgement_ids:
            issues.append(f"duplicate degradation acknowledgement: {acknowledgement_id}")
        seen_acknowledgement_ids.add(acknowledgement_id)
        try:
            expected_id = make_degradation_acknowledgement_id(
                str(acknowledgement.get("source_investigation") or ""),
                str(acknowledgement.get("source_id") or ""),
                str(acknowledgement.get("cause") or ""),
                str(acknowledgement.get("observation_id") or ""),
            )
        except LedgerValidationError as exc:
            issues.append(f"degradation acknowledgement {acknowledgement_id}: {exc}")
        else:
            if acknowledgement_id != expected_id:
                issues.append(
                    f"degradation acknowledgement {acknowledgement_id} acknowledgement_id "
                    "does not match its identity fields"
                )
        acknowledged_on = acknowledgement.get("date")
        if isinstance(acknowledged_on, str):
            try:
                parsed_date = date.fromisoformat(acknowledged_on)
            except ValueError:
                parsed_date = None
            if parsed_date is None or parsed_date.isoformat() != acknowledged_on:
                issues.append(
                    f"degradation acknowledgement {acknowledgement_id} date must be YYYY-MM-DD"
                )
            elif parsed_date > date.today():
                issues.append(
                    f"degradation acknowledgement {acknowledgement_id} date must not be future"
                )

    legacy = ledger.get("legacy") if isinstance(ledger.get("legacy"), list) else []
    for index, entry in enumerate(legacy):
        if not isinstance(entry, dict):
            issues.append(f"legacy[{index}] must be a mapping")
    return issues


def validate_claim_references(ledger: dict[str, Any], claim_ids: tuple[str, ...]) -> list[str]:
    """Structurally validate only the claims declared by a decision."""
    referenced = set(claim_ids)
    claims = ledger.get("claims") if isinstance(ledger.get("claims"), list) else []
    scoped = empty_ledger()
    scoped["claims"] = [
        claim for claim in claims if isinstance(claim, dict) and claim.get("claim_id") in referenced
    ]
    return validate_ledger(scoped)


def _valid_locator(locator: object) -> bool:
    if not isinstance(locator, dict):
        return False
    start = locator.get("line_start")
    end = locator.get("line_end")
    return _valid_line_range(start, end)


def _valid_line_range(start: object, end: object) -> bool:
    return type(start) is int and type(end) is int and start > 0 and end >= start


def _canonical_note_path(note_path: str) -> str:
    normalized = note_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = tuple(part for part in path.parts if part != ".")
    if not parts or path.is_absolute() or ".." in parts:
        raise LedgerValidationError("note_path must be a safe relative POSIX path")
    return PurePosixPath(*parts).as_posix()


def _validate_raw_collections(path: Path, raw: dict[str, Any]) -> None:
    for collection in _COLLECTIONS:
        value = raw.get(collection)
        if value is not None and not isinstance(value, list):
            raise LedgerValidationError(f"{path.name}: {collection} must be a list")
        for index, entry in enumerate(value or []):
            if not isinstance(entry, dict):
                raise LedgerValidationError(f"{path.name}: {collection}[{index}] must be a mapping")


def _validate_required_strings(
    entry: dict[str, Any],
    required: set[str],
    kind: str,
    identity: str,
    issues: list[str],
) -> None:
    missing = sorted(required - entry.keys())
    if missing:
        issues.append(f"{kind} {identity} missing fields: {', '.join(missing)}")
    for field in sorted(required & entry.keys()):
        value = entry[field]
        if not isinstance(value, str) or not value.strip():
            issues.append(f"{kind} {identity} {field} must not be empty")


def _is_legacy_semantic_claim(entry: dict[str, Any]) -> bool:
    return (
        entry.get("verdict") in LEGACY_SEMANTIC_STATES
        or entry.get("state") in LEGACY_SEMANTIC_STATES
    )


def _resolution_has_versioned_identity(entry: dict[str, Any]) -> bool:
    return _RESOLUTION_FIELDS <= entry.keys() and all(
        str(entry[field]).strip() for field in _RESOLUTION_FIELDS
    )
