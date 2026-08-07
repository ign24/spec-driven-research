"""Verificación textual determinista de claims contra snapshots locales."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml

from sdr import claims
from sdr.network_policy import (
    FOLLOWED_REDIRECT_STATUSES,
    is_supported_text_content_type,
    validate_http_url_structure,
)
from sdr.parser import parse_artifact
from sdr.research import Research
from sdr.textual_anchoring import (
    MATCHER_VERSION,
    NORMALIZATION_VERSION,
    match_text,
    normalize_text,
)
from sdr.verification_ledger import ledger_directory_lock, load_ledger, save_ledger

_SNAPSHOT_IDENTITY_VERSION = "2"
_PASSING_STATES = frozenset({"verified", "human_reviewed"})
_CONFIDENCE_SCOPE_BY_STATE = {
    "verified": "local_textual_anchoring",
    "human_reviewed": "scoped_human_review",
    "not_anchored": "not_anchored",
    "unverifiable": "unverifiable",
    "stale": "stale",
}
_SNAPSHOT_IDENTITY_FIELDS = (
    "schema_version",
    "url",
    "declared_url",
    "final_url",
    "redirects",
    "http_status",
    "captured_at",
    "content_type",
    "content_eligible",
    "status",
    "content_hash",
)


@dataclass(frozen=True)
class VerificationItem:
    claim_id: str
    note_path: str
    line_start: int
    line_end: int
    source_id: str
    claim_text: str
    claim_hash: str
    snapshot_hash: str
    normalization_version: str
    matcher_version: str
    state: str
    quote: str = ""
    locator: dict[str, int] | None = None
    reason: str = ""
    cached: bool = False

    @property
    def verdict(self) -> str:
        """Alias transitorio para consumidores anteriores al ledger v2."""
        return self.state

    @property
    def resolved(self) -> bool:
        """Compatibilidad de lectura con reportes anteriores."""
        return self.state == "human_reviewed"

    def to_dict(self) -> dict[str, Any]:
        """Representa exclusivamente los campos persistibles del item v2."""
        result: dict[str, Any] = {
            "claim_id": self.claim_id,
            "note_path": self.note_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "source_id": self.source_id,
            "claim_text": self.claim_text,
            "claim_hash": self.claim_hash,
            "snapshot_hash": self.snapshot_hash,
            "normalization_version": self.normalization_version,
            "matcher_version": self.matcher_version,
            "state": self.state,
            "confidence_scope": _CONFIDENCE_SCOPE_BY_STATE[self.state],
        }
        if self.quote:
            result["quote"] = self.quote
        if self.locator is not None:
            result["locator"] = self.locator
        if self.reason:
            result["reason"] = self.reason
        return result


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    items: list[VerificationItem]
    failures: list[str]


@dataclass(frozen=True)
class _SnapshotEvidence:
    content: str
    identity: str
    eligible: bool


@dataclass(frozen=True)
class SourceSnapshotHealth:
    """Current local snapshot identity and exact persisted-byte hash health."""

    identity: str
    eligible: bool
    persisted_content_hash: str
    recorded_content_hash: str
    content_hash_matches: bool
    metadata_exists: bool
    content_exists: bool


def verify_explore_claims(
    research: Research,
    *,
    complete: object | None = None,
    persist: bool = True,
) -> VerificationReport:
    """Verifica claims solo con snapshots locales y persiste un ledger v2."""
    del complete  # Compatibilidad temporal de firma; nunca se invoca.
    path = _ledger_path(research)
    if persist:
        with ledger_directory_lock(path):
            return _verify_explore_claims(research, path=path, persist=True)
    return _verify_explore_claims(research, path=path, persist=False)


def _verify_explore_claims(research: Research, *, path: Path, persist: bool) -> VerificationReport:
    ledger = load_ledger(path)
    _ensure_no_duplicate_active_resolutions(ledger["resolutions"])
    existing = list(ledger["claims"])
    resolutions, preserved_resolutions, legacy_resolutions = _partition_resolutions(
        ledger["resolutions"]
    )
    ledger["resolutions"] = preserved_resolutions
    ledger["legacy"].extend(legacy_resolutions)
    current_ids: set[str] = set()
    active: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    items: list[VerificationItem] = []

    for claim in _iter_claims(research):
        current_ids.add(claim.id)
        metadata = _source_meta(research, claim.source_id)
        declared_url = _declared_source_url(research, claim)
        content_exists, content_bytes = _source_content_bytes(research, claim.source_id)
        snapshot = _evaluate_snapshot(
            metadata,
            content_exists=content_exists,
            content_bytes=content_bytes,
            declared_url=declared_url,
        )
        content = snapshot.content
        usable = snapshot.eligible
        snapshot_hash = snapshot.identity
        cached, stale = _find_cached(existing, claim, snapshot_hash, content)

        resolution = resolutions.get(claim.id)
        if cached is None and stale is not None and stale.get("state") == "human_reviewed":
            reviewed = _item_from_entry(stale, cached=True)
            current_identity = (
                reviewed.claim_hash == claim.claim_hash
                and reviewed.snapshot_hash == snapshot_hash
                and reviewed.normalization_version == NORMALIZATION_VERSION
                and reviewed.matcher_version == MATCHER_VERSION
            )
            if current_identity and _resolution_is_current(resolution, reviewed):
                items.append(reviewed)
                active.append({**stale, **reviewed.to_dict()})
                continue
        if cached is not None:
            item = _item_from_entry(cached, cached=True)
            if _resolution_is_current(resolution, item) and item.state in {
                "not_anchored",
                "unverifiable",
            }:
                item = replace(
                    item,
                    state="human_reviewed",
                    reason=str(resolution.get("reason") or ""),
                )
        else:
            try:
                match = match_text(claim.text, content) if usable else None
            except Exception:
                if stale is None:
                    raise
                item = replace(_item_from_entry(stale, cached=False), state="stale")
                items.append(item)
                active.append({**stale, **item.to_dict()})
                continue
            if stale is not None:
                historical.append(_stale_history(stale))
            if not usable:
                state = "unverifiable"
            elif match is None:
                state = "not_anchored"
            else:
                state = "verified"
            reason = ""
            item = VerificationItem(
                claim_id=claim.id,
                note_path=claim.note_path,
                line_start=claim.line_start,
                line_end=claim.line_end,
                source_id=claim.source_id,
                claim_text=claim.text,
                claim_hash=claim.claim_hash,
                snapshot_hash=snapshot_hash,
                normalization_version=NORMALIZATION_VERSION,
                matcher_version=MATCHER_VERSION,
                state=state,
                quote=match.quote if match else "",
                locator=(
                    {
                        "line_start": match.locator.line_start,
                        "line_end": match.locator.line_end,
                    }
                    if match
                    else None
                ),
                reason=reason,
            )
            if _resolution_is_current(resolution, item) and state in {
                "not_anchored",
                "unverifiable",
            }:
                item = replace(
                    item,
                    state="human_reviewed",
                    reason=str(resolution.get("reason") or ""),
                )
        items.append(item)
        active.append({**cached, **item.to_dict()} if cached is not None else item.to_dict())

    for entry in existing:
        if not isinstance(entry, dict):
            continue
        claim_id = str(entry.get("claim_id") or "")
        if claim_id not in current_ids:
            if entry.get("state") not in {
                "verified",
                "not_anchored",
                "unverifiable",
                "human_reviewed",
                "stale",
            }:
                active.append(entry)
            else:
                historical.append(_stale_history(entry))

    ledger["claims"] = active
    _invalidate_resolutions(ledger["resolutions"], active)
    ledger["legacy"].extend(historical)
    if persist:
        save_ledger(path, ledger)
    failures = [
        f"{item.claim_id}: {item.state} en {item.source_id}"
        for item in items
        if item.state not in _PASSING_STATES
    ]
    return VerificationReport(passed=not failures, items=items, failures=failures)


def resolve_claim(research: Research, claim_id: str, *, reason: str, by: str = "") -> None:
    """Registra una revisión humana ligada a la identidad vigente del claim."""
    reason = reason.strip()
    by = by.strip()
    if not reason:
        raise ValueError("resolve-claim requiere un motivo no vacío en --reason")
    if not by:
        raise ValueError("resolve-claim requiere un actor no vacío en --by")
    path = _ledger_path(research)
    with ledger_directory_lock(path):
        ledger = load_ledger(path)
        _ensure_no_duplicate_active_resolutions(ledger["resolutions"])

        report = verify_explore_claims(research, persist=False)
        current = next((item for item in report.items if item.claim_id == claim_id), None)
        if current is None:
            raise ValueError(
                f"el claim activo {claim_id} no existe; "
                "ejecute sdr verify-claims y use un ID vigente"
            )
        state = current.state
        if state not in {"not_anchored", "unverifiable"}:
            raise ValueError(
                f"el claim {claim_id} tiene estado {state}; solo not_anchored o unverifiable "
                "admiten revisión humana"
            )
        existing_claim = next(
            (item for item in ledger["claims"] if item.get("claim_id") == claim_id), None
        )
        if existing_claim is not None and any(
            existing_claim.get(field) != getattr(current, field)
            for field in (
                "claim_hash",
                "snapshot_hash",
                "normalization_version",
                "matcher_version",
            )
        ):
            ledger["legacy"].append(_stale_history(existing_claim))
        ledger["claims"] = [
            item for item in ledger["claims"] if item.get("claim_id") != claim_id
        ] + [current.to_dict()]
        _invalidate_resolutions(ledger["resolutions"], ledger["claims"])
        resolution = {
            "claim_id": claim_id,
            "reason": reason,
            "by": by,
            "date": date.today().isoformat(),
            "state": "active",
        }
        resolution.update(
            {
                field: getattr(current, field)
                for field in (
                    "claim_hash",
                    "snapshot_hash",
                    "normalization_version",
                    "matcher_version",
                )
            }
        )
        ledger["resolutions"].append(resolution)
        save_ledger(path, ledger)


def _iter_claims(research: Research) -> list[claims.Claim]:
    found: list[claims.Claim] = []
    notes_dir = research.artifact_path("notes")
    for path in sorted(notes_dir.glob("*.md")) if notes_dir.is_dir() else []:
        note_path = path.relative_to(research.root).as_posix()
        found.extend(claims.extract_claims(path.read_text(encoding="utf-8"), note_path=note_path))
    return found


def _ledger_path(research: Research) -> Path:
    return research.artifact_path("notes/sources/verification.yaml")


def _source_meta(research: Research, source_id: str) -> dict[str, Any]:
    path = research.artifact_path(f"notes/sources/{source_id}/meta.yaml")
    if not path.exists():
        return {"status": "missing"}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {"status": "invalid_metadata"}


def _source_content_bytes(research: Research, source_id: str) -> tuple[bool, bytes]:
    path = research.artifact_path(f"notes/sources/{source_id}/content.md")
    return (True, path.read_bytes()) if path.is_file() else (False, b"")


def evaluate_source_snapshot(
    research: Research, source_id: str, *, declared_url: str
) -> SourceSnapshotHealth:
    """Evaluate one local snapshot through the canonical secure snapshot machinery."""
    metadata_path = research.artifact_path(f"notes/sources/{source_id}/meta.yaml")
    metadata = _source_meta(research, source_id)
    content_exists, content_bytes = _source_content_bytes(research, source_id)
    persisted_hash = hashlib.sha256(content_bytes).hexdigest()
    evidence = _evaluate_snapshot(
        metadata,
        content_exists=content_exists,
        content_bytes=content_bytes,
        declared_url=declared_url,
    )
    recorded_hash = metadata.get("content_hash")
    recorded_content_hash = recorded_hash if isinstance(recorded_hash, str) else ""
    return SourceSnapshotHealth(
        identity=evidence.identity,
        eligible=evidence.eligible,
        persisted_content_hash=persisted_hash,
        recorded_content_hash=recorded_content_hash,
        content_hash_matches=(
            content_exists
            and bool(recorded_content_hash)
            and recorded_content_hash == persisted_hash
        ),
        metadata_exists=metadata_path.is_file(),
        content_exists=content_exists,
    )


def _declared_source_url(research: Research, claim: claims.Claim) -> str:
    note = parse_artifact(research.artifact_path(claim.note_path))
    for source in note.frontmatter.get("sources") or []:
        if isinstance(source, dict) and str(source.get("id") or "") == claim.source_id:
            return str(source.get("url") or "")
    return ""


def _evaluate_snapshot(
    metadata: dict[str, Any],
    *,
    content_exists: bool,
    content_bytes: bytes,
    declared_url: str,
) -> _SnapshotEvidence:
    persisted_hash = hashlib.sha256(content_bytes).hexdigest()
    try:
        content = content_bytes.decode("utf-8")
        decodable = True
    except UnicodeDecodeError:
        content = ""
        decodable = False

    identity = _snapshot_identity(
        metadata,
        declared_url=declared_url,
        content_exists=content_exists,
        persisted_hash=persisted_hash,
        decodable=decodable,
    )
    eligible = (
        type(metadata.get("schema_version")) is int
        and metadata["schema_version"] == 2
        and isinstance(declared_url, str)
        and bool(declared_url)
        and metadata.get("url") == declared_url
        and metadata.get("declared_url") == declared_url
        and _valid_redirect_provenance(metadata)
        and type(metadata.get("http_status")) is int
        and 200 <= metadata["http_status"] < 300
        and _valid_captured_at(metadata.get("captured_at"))
        and isinstance(metadata.get("content_type"), str)
        and is_supported_text_content_type(metadata["content_type"])
        and metadata.get("content_eligible") is True
        and metadata.get("status") == "ok"
        and content_exists
        and decodable
        and bool(content.strip())
        and metadata.get("content_hash") == persisted_hash
    )
    return _SnapshotEvidence(content=content, identity=identity, eligible=eligible)


def _valid_redirect_provenance(metadata: dict[str, Any]) -> bool:
    declared_url = metadata.get("declared_url")
    final_url = metadata.get("final_url")
    redirects = metadata.get("redirects")
    if not isinstance(declared_url, str) or not declared_url:
        return False
    if not isinstance(final_url, str) or not final_url or not isinstance(redirects, list):
        return False
    if not _valid_http_url(declared_url) or not _valid_http_url(final_url):
        return False
    expected_url = declared_url
    for redirect in redirects:
        if not isinstance(redirect, dict):
            return False
        location = redirect.get("location")
        status_code = redirect.get("status_code")
        redirect_url = redirect.get("url")
        target_url = redirect.get("target_url")
        if (
            redirect_url != expected_url
            or not isinstance(redirect_url, str)
            or not _valid_http_url(redirect_url)
            or type(status_code) is not int
            or status_code not in FOLLOWED_REDIRECT_STATUSES
            or not isinstance(location, str)
            or not location
            or not isinstance(target_url, str)
            or not _valid_http_url(target_url)
            or target_url != urljoin(expected_url, location)
        ):
            return False
        expected_url = target_url
    return expected_url == final_url


def _valid_http_url(url: str) -> bool:
    try:
        validate_http_url_structure(url)
    except (TypeError, ValueError):
        return False
    return True


def _valid_captured_at(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        captured_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (
        captured_at.tzinfo is not None
        and captured_at.utcoffset() is not None
        and captured_at.isoformat() == value
    )


def _snapshot_identity(
    metadata: dict[str, Any],
    *,
    declared_url: str,
    content_exists: bool,
    persisted_hash: str,
    decodable: bool,
) -> str:
    provenance = {
        field: {"present": field in metadata, "value": metadata.get(field)}
        for field in _SNAPSHOT_IDENTITY_FIELDS
    }
    identity = {
        "expected_declared_url": declared_url,
        "provenance": provenance,
        "content_exists": content_exists,
        "persisted_sha256": persisted_hash,
        "utf8_decodable": decodable,
    }
    canonical = yaml.safe_dump(
        identity,
        allow_unicode=True,
        canonical=True,
        sort_keys=True,
    ).encode("utf-8")
    domain = f"sdr:snapshot-identity:v{_SNAPSHOT_IDENTITY_VERSION}\n".encode()
    digest = hashlib.sha256(domain + canonical).hexdigest()
    return f"snapshot-v{_SNAPSHOT_IDENTITY_VERSION}-{digest}"


def _find_cached(
    entries: list[dict[str, Any]],
    claim: claims.Claim,
    snapshot_hash: str,
    content: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    candidate = next(
        (entry for entry in entries if entry.get("claim_id") == claim.id),
        None,
    )
    if candidate is None:
        return None, None
    current = (
        candidate.get("state") in {"verified", "not_anchored", "unverifiable"}
        and candidate.get("claim_hash") == claim.claim_hash
        and candidate.get("snapshot_hash") == snapshot_hash
        and candidate.get("normalization_version") == NORMALIZATION_VERSION
        and candidate.get("matcher_version") == MATCHER_VERSION
        and _locator_recovers_quote(candidate, content)
    )
    return (candidate, None) if current else (None, candidate)


def _resolution_is_current(
    resolution: dict[str, Any] | None,
    item: VerificationItem,
) -> bool:
    if resolution is None or resolution.get("state", "active") != "active":
        return False
    return all(
        resolution.get(field) == getattr(item, field)
        for field in (
            "claim_hash",
            "snapshot_hash",
            "normalization_version",
            "matcher_version",
        )
    )


def _ensure_no_duplicate_active_resolutions(resolutions: list[dict[str, Any]]) -> None:
    active_ids = [
        str(item.get("claim_id")) for item in resolutions if item.get("state", "active") == "active"
    ]
    if len(active_ids) != len(set(active_ids)):
        raise ValueError(
            "el ledger contiene resoluciones activas duplicadas; "
            "corríjalo sin aplicar orden last-wins"
        )


def _invalidate_resolutions(
    resolutions: list[dict[str, Any]], active_claims: list[dict[str, Any]]
) -> None:
    claims_by_id = {str(claim.get("claim_id")): claim for claim in active_claims}
    identity_fields = (
        "claim_hash",
        "snapshot_hash",
        "normalization_version",
        "matcher_version",
    )
    for resolution in resolutions:
        if resolution.get("state", "active") != "active":
            continue
        claim = claims_by_id.get(str(resolution.get("claim_id")))
        if claim is None or any(
            resolution.get(field) != claim.get(field) for field in identity_fields
        ):
            resolution["state"] = "stale"


def _partition_resolutions(
    entries: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    required = {
        "claim_id",
        "by",
        "reason",
        "date",
        "claim_hash",
        "snapshot_hash",
        "normalization_version",
        "matcher_version",
    }
    active: dict[str, dict[str, Any]] = {}
    preserved: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    for entry in entries:
        if required <= entry.keys() and all(str(entry[field]).strip() for field in required):
            preserved.append(entry)
            if entry.get("state", "active") == "active":
                active[str(entry["claim_id"])] = entry
        else:
            legacy.append({"kind": "resolution", "data": entry})
    return active, preserved, legacy


def _locator_recovers_quote(entry: dict[str, Any], content: str) -> bool:
    if entry.get("state") != "verified":
        return True
    locator = entry.get("locator")
    quote = entry.get("quote")
    if not isinstance(locator, dict) or not isinstance(quote, str) or not quote:
        return False
    start = locator.get("line_start")
    end = locator.get("line_end")
    lines = content.splitlines(keepends=True)
    valid_range = (
        type(start) is int
        and type(end) is int
        and start >= 1
        and end >= start
        and end <= len(lines)
    )
    if not valid_range:
        return False
    passage = "".join(lines[start - 1 : end])
    return quote in passage and normalize_text(quote) == normalize_text(entry.get("claim_text", ""))


def _stale_history(entry: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "stale_claim", "data": {**entry, "state": "stale"}}


def _item_from_entry(entry: dict[str, Any], *, cached: bool) -> VerificationItem:
    return VerificationItem(
        claim_id=str(entry["claim_id"]),
        note_path=str(entry["note_path"]),
        line_start=int(entry["line_start"]),
        line_end=int(entry["line_end"]),
        source_id=str(entry["source_id"]),
        claim_text=str(entry["claim_text"]),
        claim_hash=str(entry["claim_hash"]),
        snapshot_hash=str(entry["snapshot_hash"]),
        normalization_version=str(entry["normalization_version"]),
        matcher_version=str(entry["matcher_version"]),
        state=str(entry["state"]),
        quote=str(entry.get("quote") or ""),
        locator=entry.get("locator"),
        reason=str(entry.get("reason") or ""),
        cached=cached,
    )
