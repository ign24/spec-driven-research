"""Captura de snapshots textuales de fuentes declaradas en explore."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin

import frontmatter
import httpx
import yaml

from sdr.gates import _org
from sdr.network_policy import (
    FOLLOWED_REDIRECT_STATUSES,
    MAX_RESPONSE_BYTES,
    RedirectRecord,
    Resolver,
    fetch_http,
    is_supported_text_content_type,
    validate_http_url_structure,
)
from sdr.parser import parse_artifact
from sdr.research import Research


@dataclass(frozen=True)
class FetchResult:
    """Respuesta mínima necesaria para capturar una fuente."""

    declared_url: str
    final_url: str
    redirects: tuple[RedirectRecord, ...]
    status_code: int
    content_type: str
    content_eligible: bool
    text: str


@dataclass(frozen=True)
class SnapshotResult:
    """Resultado persistido de una captura de fuente."""

    source_id: str
    url: str
    status: str
    content_hash: str
    path: str
    declared_url: str
    final_url: str
    redirects: tuple[RedirectRecord, ...]
    status_code: int
    content_eligible: bool

    def to_dict(self) -> dict[str, object]:
        """Representa el resultado con provenance serializable en orden de recuperación."""
        return {
            "source_id": self.source_id,
            "url": self.url,
            "status": self.status,
            "content_hash": self.content_hash,
            "path": self.path,
            "declared_url": self.declared_url,
            "final_url": self.final_url,
            "redirects": [
                {
                    "url": redirect.url,
                    "status_code": redirect.status_code,
                    "location": redirect.location,
                    "target_url": redirect.target_url,
                }
                for redirect in self.redirects
            ],
            "status_code": self.status_code,
            "content_eligible": self.content_eligible,
        }


@dataclass(frozen=True)
class CanonicalFetchedContent:
    """A fetched response normalized exactly as persisted snapshot evidence."""

    status: str
    content_eligible: bool
    persisted_content: bytes
    content_hash: str


Fetcher = Callable[[str], FetchResult]

_SOURCE_ID_RE = re.compile(r"^S(\d+)$")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MAIN_RE = re.compile(r"<(main|article)[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def fetch_url(
    url: str,
    *,
    mock_transport: httpx.MockTransport | None = None,
    resolver: Resolver | None = None,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> FetchResult:
    """Descarga texto HTTP mediante la política de red segura compartida."""
    response = fetch_http(
        url,
        mock_transport=mock_transport,
        resolver=resolver,
        max_response_bytes=max_response_bytes,
        require_text_content=True,
    )
    usable_body = 200 <= response.status_code < 300 and response.content_eligible
    return FetchResult(
        declared_url=response.declared_url,
        final_url=response.final_url,
        redirects=response.redirects,
        status_code=response.status_code,
        content_type=response.content_type,
        content_eligible=response.content_eligible,
        text=response.text if usable_body else "",
    )


def capture_source_snapshot(
    research: Research,
    *,
    source_id: str,
    url: str,
    fetcher: Fetcher = fetch_url,
    archive_url: str = "",
) -> SnapshotResult:
    """Captura una fuente en `notes/sources/<source_id>/`."""
    fetched = fetcher(url)
    canonical = canonicalize_fetched_content(url, fetched)
    title = _extract_title(fetched.text) if canonical.status == "ok" else ""

    source_dir = research.artifact_path(f"notes/sources/{source_id}")
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "content.md").write_bytes(canonical.persisted_content)

    meta = {
        "schema_version": 2,
        "url": url,
        "declared_url": fetched.declared_url,
        "final_url": fetched.final_url,
        "redirects": [
            {
                "url": redirect.url,
                "status_code": redirect.status_code,
                "location": redirect.location,
                "target_url": redirect.target_url,
            }
            for redirect in fetched.redirects
        ],
        "title": title,
        "captured_at": datetime.now(UTC).isoformat(),
        "content_hash": canonical.content_hash,
        "http_status": fetched.status_code,
        "content_type": fetched.content_type,
        "content_eligible": canonical.content_eligible,
        "org": _org(url),
        "status": canonical.status,
    }
    if archive_url:
        meta["archive_url"] = archive_url
    (source_dir / "meta.yaml").write_text(
        yaml.safe_dump(meta, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return SnapshotResult(
        source_id=source_id,
        url=url,
        status=canonical.status,
        content_hash=canonical.content_hash,
        path=str(source_dir),
        declared_url=fetched.declared_url,
        final_url=fetched.final_url,
        redirects=fetched.redirects,
        status_code=fetched.status_code,
        content_eligible=canonical.content_eligible,
    )


def canonicalize_fetched_content(url: str, fetched: FetchResult) -> CanonicalFetchedContent:
    """Apply the snapshot eligibility, extraction, and persisted-byte hash boundary."""
    content_eligible = is_supported_text_content_type(fetched.content_type)
    eligible_response = (
        200 <= fetched.status_code < 300 and content_eligible and _provenance_complete(url, fetched)
    )
    content = _extract_text(fetched.text).strip() if eligible_response else ""
    persisted_content = ((content + "\n") if content else "").encode("utf-8")
    return CanonicalFetchedContent(
        status="ok" if content else "unverifiable",
        content_eligible=content_eligible,
        persisted_content=persisted_content,
        content_hash=hashlib.sha256(persisted_content).hexdigest(),
    )


def _provenance_complete(url: str, fetched: FetchResult) -> bool:
    try:
        validate_http_url_structure(url)
        validate_http_url_structure(fetched.declared_url)
        validate_http_url_structure(fetched.final_url)
        for redirect in fetched.redirects:
            validate_http_url_structure(redirect.url)
            validate_http_url_structure(redirect.target_url)
    except (TypeError, ValueError):
        return False
    if (
        fetched.declared_url != url
        or not fetched.final_url
        or not 100 <= fetched.status_code <= 599
    ):
        return False
    expected_url = fetched.declared_url
    for redirect in fetched.redirects:
        if (
            redirect.url != expected_url
            or redirect.status_code not in FOLLOWED_REDIRECT_STATUSES
            or not redirect.location
            or redirect.target_url != urljoin(redirect.url, redirect.location)
        ):
            return False
        expected_url = redirect.target_url
    return expected_url == fetched.final_url


def assign_source_ids(research: Research) -> int:
    """Asigna IDs S<n> a fuentes de notas que no los tengan, preservando el orden."""
    directory = research.artifact_path("notes")
    if not directory.is_dir():
        return 0
    changed = 0
    next_id = _next_source_number(directory)
    for path in sorted(directory.glob("*.md")):
        post = frontmatter.load(str(path))
        sources = post.metadata.get("sources") or []
        if not isinstance(sources, list):
            continue
        note_changed = False
        normalized_sources = []
        for source in sources:
            if isinstance(source, dict):
                current = dict(source)
                if not current.get("id"):
                    current["id"] = f"S{next_id}"
                    next_id += 1
                    changed += 1
                    note_changed = True
                normalized_sources.append(current)
            else:
                normalized_sources.append(source)
        if note_changed:
            post.metadata["sources"] = normalized_sources
            path.write_text(frontmatter.dumps(post), encoding="utf-8")
    return changed


def capture_declared_sources(
    research: Research,
    *,
    fetcher: Fetcher | None = None,
) -> list[SnapshotResult]:
    """Asigna IDs y captura snapshots de todas las fuentes declaradas en notas."""
    fetcher = fetcher or fetch_url
    assign_source_ids(research)
    results: list[SnapshotResult] = []
    for source in _iter_note_sources(research):
        source_id = str(source.get("id") or "")
        url = str(source.get("url") or "")
        if not source_id or not url:
            continue
        if _snapshot_meta_exists(research, source_id):
            continue
        results.append(
            capture_source_snapshot(
                research,
                source_id=source_id,
                url=url,
                fetcher=fetcher,
                archive_url=str(source.get("archive_url") or ""),
            )
        )
    write_orgs_yaml(research)
    return results


def write_orgs_yaml(research: Research) -> None:
    """Genera `notes/sources/orgs.yaml` con organizaciones derivadas y aliases editables."""
    sources = _iter_note_sources(research)
    source_orgs = {
        str(source.get("id")): _org(str(source.get("url") or ""))
        for source in sources
        if source.get("id") and source.get("url")
    }
    sources_dir = research.artifact_path("notes/sources")
    sources_dir.mkdir(parents=True, exist_ok=True)
    path = sources_dir / "orgs.yaml"
    existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    aliases = existing.get("aliases", {}) if isinstance(existing, dict) else {}
    if not isinstance(aliases, dict):
        aliases = {}
    for org in source_orgs.values():
        aliases.setdefault(org, org)
    payload = {"aliases": aliases, "sources": source_orgs}
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def ensure_explore_snapshots(research: Research, *, offline: bool = False) -> list[SnapshotResult]:
    """Captura snapshots requeridos para explore v2; offline omite la captura."""
    if offline or research.meta.schema_version < 2 or research.meta.stage != "explore":
        return []
    return capture_declared_sources(research)


def _iter_note_sources(research: Research) -> list[dict]:
    directory = research.artifact_path("notes")
    if not directory.is_dir():
        return []
    sources: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        for source in parse_artifact(path).frontmatter.get("sources") or []:
            if isinstance(source, dict):
                sources.append(dict(source))
    return sources


def _snapshot_meta_exists(research: Research, source_id: str) -> bool:
    return research.artifact_path(f"notes/sources/{source_id}/meta.yaml").exists()


def _next_source_number(directory) -> int:
    highest = 0
    for path in sorted(directory.glob("*.md")):
        post = frontmatter.load(str(path))
        for source in post.metadata.get("sources") or []:
            if isinstance(source, dict):
                match = _SOURCE_ID_RE.match(str(source.get("id", "")))
                if match:
                    highest = max(highest, int(match.group(1)))
    return highest + 1


def _extract_title(raw: str) -> str:
    match = _TITLE_RE.search(raw or "")
    if not match:
        return ""
    return _clean_text(match.group(1)).strip()


def _extract_text(raw: str) -> str:
    extracted = _extract_with_trafilatura(raw)
    if extracted:
        return extracted
    match = _MAIN_RE.search(raw or "")
    if match:
        return _clean_text(match.group(2))
    match = _BODY_RE.search(raw or "")
    if match:
        return _clean_text(match.group(1))
    if "<html" in (raw or "").lower():
        return ""
    return _clean_text(raw)


def _extract_with_trafilatura(raw: str) -> str:
    try:
        import trafilatura  # type: ignore[import-not-found]
    except ImportError:
        return ""
    extracted = trafilatura.extract(raw) or ""
    return extracted.strip()


def _clean_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = unescape(text)
    text = _SPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
