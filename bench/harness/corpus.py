"""Benchmark corpus contract: closed defect vocabulary, typed loading, synthetic-content rules.

On-disk format
--------------

The corpus is a directory, by default ``bench/corpus``:

- ``corpus.yaml`` declares corpus-level metadata. Required key: ``version`` (a string).
- ``items/<item-id>.yaml`` declares exactly one corpus item. The file stem MUST equal the
  item ``id``, which makes item identity visible in the tree and keeps loading order stable
  (items are loaded in sorted filename order).

YAML was chosen because the repository already depends on ``pyyaml`` and stores lifecycle
metadata as YAML, and because block scalars keep multi-line note bodies readable in review.
One file per item was chosen over a single manifest so that authoring or changing one item
produces a self-contained diff.

Item schema
-----------

Required keys:

- ``id``: lowercase identifier matching ``[a-z0-9][a-z0-9-]*`` and unique across the corpus.
- ``mode``: ``light`` or ``full``. It is the lifecycle mode the item is executed under.
- ``title``: short human label.
- ``question``: the invented research question the item investigates.
- ``planted_defects``: list of defect names drawn from ``PLANTED_DEFECT_VOCABULARY``. May be
  empty; an empty list declares a clean item. At least one corpus item must be clean.

Optional keys:

- ``expected_detection``: mapping from a planted defect to ``caught`` or ``uncaught``. It
  records, per defect, whether any current control is expected to report it. Defects absent
  from the mapping default to ``caught``.
- ``sources``: list of source declarations with ``id``, ``url``, ``title``, ``tier``, ``date``
  and optional ``snapshot`` body.
- ``artifacts``: mapping of research-root relative path to file body, replayed by the
  scripted actor.
- ``commands``: list of argv lists, replayed by the scripted actor.
- ``probe``: probe declaration; only valid for ``full`` items, since ``light`` skips probe.
- ``notes``: free-form authoring notes, not interpreted by the harness.

Defect vocabulary
-----------------

The vocabulary derives from the failure taxonomy in ``docs/evidence-model.md`` (the artifact
list, the six validation controls, offline semantics and the confidence boundaries) and from
the README failure statement it mirrors, not from ``src/sdr/gates.py``. Deriving planted
defects from the gate implementations would guarantee a perfect score and measure nothing.
The vocabulary therefore also contains defects no current control is expected to catch; those
entries are deliberate and are scored as ``uncaught`` expectations.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import urljoin

import frontmatter
import yaml

from sdr import claims, schema
from sdr.network_policy import (
    FOLLOWED_REDIRECT_STATUSES,
    is_supported_text_content_type,
    validate_http_url_structure,
)
from sdr.textual_anchoring import match_text

# Defect name -> the failure-taxonomy statement in docs/evidence-model.md that justifies it.
_VOCABULARY: dict[str, str] = {
    "unreachable-source": (
        "Evidence model, Artifacts and Offline semantics: notes declare sources and local "
        "snapshots preserve the text used for matching, and outbound link checks are the "
        "control over sources that disappear or become inaccessible."
    ),
    "unanchored-claim": (
        "Evidence model, Validation controls: textual anchoring extracts factual claims "
        "ending in [S<n>] and matches them against local source snapshots, so a claim its "
        "cited source does not support is a declared failure mode."
    ),
    "unreproducible-result": (
        "Evidence model, Artifacts: probe/results.md maps every criterion to a result and "
        "reproducible evidence, so a result asserted without a reproducible check is a "
        "declared failure mode."
    ),
    "contradictory-sources": (
        "Evidence model, Validation controls: evidential validation checks source "
        "declarations, tiers and triangulation, which is the control over evidence that "
        "contradicts itself across alternatives."
    ),
    "probe-expectation-mismatch": (
        "Evidence model, Validation controls: executable validation runs an explicitly "
        "declared probe action and persists its output and result, so a probe whose "
        "expectation does not match its command is a declared failure mode."
    ),
    "uncovered-criterion": (
        "Evidence model, Validation controls: evidential validation checks criterion "
        "references and artifact paths, so a criterion left without a mapped result is a "
        "declared failure mode."
    ),
    "stale-validation": (
        "Evidence model, Validation controls: hash consistency detects changes after a stage "
        "or probe was validated."
    ),
    "unapproved-decision": (
        "Evidence model, Validation controls: HITL approval records who approved the current "
        "decision memo and when, so a decision presented as approved without it is a "
        "declared failure mode."
    ),
    "inaccurate-source": (
        "Evidence model, Confidence boundaries: a passing gate does not mean a source is "
        "accurate. No current control is expected to catch this."
    ),
    "unrepresentative-benchmark": (
        "Evidence model, Confidence boundaries: a passing gate does not mean a benchmark is "
        "representative. No current control is expected to catch this."
    ),
}

PLANTED_DEFECT_VOCABULARY: Final[Mapping[str, str]] = MappingProxyType(_VOCABULARY)
PLANTED_DEFECTS: Final[frozenset[str]] = frozenset(_VOCABULARY)

DETECTION_EXPECTATIONS: Final[tuple[str, ...]] = ("caught", "uncaught")
MODES: Final[tuple[str, ...]] = ("light", "full")

# RFC 2606 reserved second-level domains and reserved top-level domains.
RESERVED_DOMAINS: Final[tuple[str, ...]] = ("example.com", "example.net", "example.org")
RESERVED_TLDS: Final[tuple[str, ...]] = ("invalid", "test", "example", "localhost")

DEFAULT_CORPUS_ROOT: Final[Path] = Path(__file__).resolve().parent.parent / "corpus"

_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_URL_PATTERN: Final[re.Pattern[str]] = re.compile(r"https?://[^\s<>\"'()\[\]]+")
_TRAILING_PUNCTUATION: Final[str] = ".,;:!?"
_BASELINE_PROVENANCE_KEYS: Final[frozenset[str]] = frozenset(
    {"version", "snapshot_schema_version", "decision_lineage_field", "preserved_baseline"}
)


class CorpusError(Exception):
    """Raised when the corpus violates its declared contract."""


@dataclass(frozen=True)
class Source:
    """A declared source of one corpus item."""

    id: str
    url: str
    title: str
    tier: str
    date: str
    snapshot: str | None = None


@dataclass(frozen=True)
class CorpusItem:
    """One benchmark item with its planted defects and its research inputs."""

    id: str
    mode: str
    title: str
    question: str
    planted_defects: tuple[str, ...]
    expected_detection: Mapping[str, str]
    sources: tuple[Source, ...]
    artifacts: Mapping[str, str]
    commands: tuple[tuple[str, ...], ...]
    probe: Mapping[str, Any] | None
    path: Path

    @property
    def is_clean(self) -> bool:
        """True when the item declares no planted defect."""
        return not self.planted_defects


@dataclass(frozen=True)
class BaselineProvenance:
    """Current corpus migration identity before a replacement baseline is preserved."""

    version: int
    snapshot_schema_version: int
    decision_lineage_field: str
    preserved_baseline: None


@dataclass(frozen=True)
class Corpus:
    """A loaded corpus: its version and its items in stable order."""

    version: str
    baseline_provenance: BaselineProvenance
    items: tuple[CorpusItem, ...]
    root: Path

    @property
    def clean_items(self) -> tuple[CorpusItem, ...]:
        """Items declaring an empty planted-defect list."""
        return tuple(item for item in self.items if item.is_clean)


def is_synthetic_url(url: str) -> bool:
    """True when the URL uses a reserved or non-resolvable domain (RFC 2606)."""
    if not isinstance(url, str):
        return False
    match = re.match(r"^(https?)://([^/?#]+)", url.strip(), flags=re.IGNORECASE)
    if match is None:
        return False
    host = match.group(2).lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = host.split(":", 1)[0].rstrip(".")
    if not host:
        return False
    if host in RESERVED_DOMAINS:
        return True
    if any(host.endswith(f".{domain}") for domain in RESERVED_DOMAINS):
        return True
    return host.rsplit(".", 1)[-1] in RESERVED_TLDS


def extract_urls(text: str) -> tuple[str, ...]:
    """Every http(s) URL appearing in a text body, in order of appearance."""
    found: list[str] = []
    for raw in _URL_PATTERN.findall(text):
        url = raw.rstrip(_TRAILING_PUNCTUATION)
        if url:
            found.append(url)
    return tuple(found)


def synthetic_content_violations(item: CorpusItem) -> tuple[str, ...]:
    """Messages for every URL of the item that is not reserved or non-resolvable."""
    violations: list[str] = []
    for source in item.sources:
        if not is_synthetic_url(source.url):
            violations.append(
                f"item {item.id!r}: source {source.id!r} uses non-reserved URL {source.url!r}"
            )
    bodies: list[tuple[str, str]] = [
        (f"source {source.id!r} snapshot", source.snapshot)
        for source in item.sources
        if source.snapshot
    ]
    bodies.extend((f"artifact {path!r}", body) for path, body in sorted(item.artifacts.items()))
    for label, body in bodies:
        for url in extract_urls(body):
            if not is_synthetic_url(url):
                violations.append(f"item {item.id!r}: {label} embeds non-reserved URL {url!r}")
    return tuple(violations)


def load_corpus(root: Path | str | None = None) -> Corpus:
    """Load and validate the corpus rooted at `root`, defaulting to `bench/corpus`."""
    corpus_root = Path(root) if root is not None else DEFAULT_CORPUS_ROOT
    if not corpus_root.is_dir():
        raise CorpusError(f"corpus root does not exist: {corpus_root}")
    version, baseline_provenance = _load_manifest(corpus_root / "corpus.yaml")

    items_dir = corpus_root / "items"
    if not items_dir.is_dir():
        raise CorpusError(f"corpus items directory does not exist: {items_dir}")
    paths = sorted(items_dir.glob("*.yaml"))
    if not paths:
        raise CorpusError(f"corpus declares no items: {items_dir}")

    items: list[CorpusItem] = []
    seen: dict[str, Path] = {}
    for path in paths:
        item = load_item(path)
        previous = seen.get(item.id)
        if previous is not None:
            raise CorpusError(f"duplicate item id {item.id!r} in {previous.name} and {path.name}")
        seen[item.id] = path
        items.append(item)

    if not any(item.is_clean for item in items):
        raise CorpusError("corpus declares no item with an empty planted-defect list")
    return Corpus(
        version=version,
        baseline_provenance=baseline_provenance,
        items=tuple(items),
        root=corpus_root,
    )


def load_item(path: Path | str) -> CorpusItem:
    """Load and validate a single corpus item file."""
    item_path = Path(path)
    data = _read_yaml_mapping(item_path)
    stem = item_path.stem

    item_id = _require_str(data, "id", stem)
    if not _ID_PATTERN.match(item_id):
        raise CorpusError(f"item {item_id!r} in {item_path.name}: id must match [a-z0-9][a-z0-9-]*")
    if item_id != stem:
        raise CorpusError(f"item {item_id!r} does not match its file stem {stem!r}")

    mode = _require_str(data, "mode", item_id)
    if mode not in MODES:
        raise CorpusError(f"item {item_id!r}: mode {mode!r} is not one of {MODES}")

    title = _require_str(data, "title", item_id)
    question = _require_str(data, "question", item_id)
    planted = _load_planted_defects(data, item_id)
    expected = _load_expected_detection(data, item_id, planted)
    sources = _load_sources(data, item_id)
    artifacts = _load_artifacts(data, item_id)
    commands = _load_commands(data, item_id)
    probe = _load_probe(data, item_id, mode)

    item = CorpusItem(
        id=item_id,
        mode=mode,
        title=title,
        question=question,
        planted_defects=planted,
        expected_detection=MappingProxyType(expected),
        sources=sources,
        artifacts=MappingProxyType(artifacts),
        commands=commands,
        probe=MappingProxyType(probe) if probe is not None else None,
        path=item_path,
    )
    violations = synthetic_content_violations(item)
    if violations:
        raise CorpusError("; ".join(violations))
    _validate_retained_evidence(item)
    return item


def _validate_retained_evidence(item: CorpusItem) -> None:
    validated_snapshots: dict[str, str] = {}
    for source in item.sources:
        if source.snapshot is None:
            continue
        prefix = f"{item.id}/notes/sources/{source.id}"
        content_path = f"{prefix}/content.md"
        metadata_path = f"{prefix}/meta.yaml"
        content = item.artifacts.get(content_path)
        raw_metadata = item.artifacts.get(metadata_path)
        try:
            metadata = yaml.safe_load(raw_metadata) if raw_metadata is not None else None
        except yaml.YAMLError:
            metadata = None
        if content is not None and content != source.snapshot:
            raise CorpusError(
                f"item {item.id!r}: source {source.id!r} declared snapshot and replayed "
                "content.md must have exact bytes"
            )
        if (
            content is None
            or not isinstance(metadata, dict)
            or not _has_current_snapshot_provenance(source, content, metadata)
        ):
            raise CorpusError(
                f"item {item.id!r}: source {source.id!r} lacks current snapshot provenance"
            )
        validated_snapshots[source.id] = content

    current_claims = _extract_retained_claims(item)

    for path, body in item.artifacts.items():
        if not path.endswith("/decision-memo.md"):
            continue
        try:
            metadata = frontmatter.loads(body).metadata
            claim_ids = schema.validate_evidence_claim_ids(metadata.get("evidence_claim_ids"))
        except (ValueError, yaml.YAMLError) as error:
            raise CorpusError(
                f"item {item.id!r}: participating decision {path!r} has invalid "
                f"evidence_claim_ids: {error}"
            ) from error
        if not claim_ids:
            raise CorpusError(
                f"item {item.id!r}: participating decision {path!r} must declare evidence_claim_ids"
            )
        for claim_id in claim_ids:
            claim = current_claims.get(claim_id)
            identity = f"item {item.id!r}: participating decision {path!r}, claim {claim_id!r}"
            if claim is None:
                raise CorpusError(f"{identity} is not a current retained claim (unknown or stale)")
            snapshot = validated_snapshots.get(claim.source_id)
            if snapshot is None:
                raise CorpusError(
                    f"{identity} has no validated retained source snapshot for {claim.source_id!r}"
                )
            if match_text(claim.text, snapshot) is None:
                raise CorpusError(
                    f"{identity} is not anchored in validated retained source snapshot "
                    f"{claim.source_id!r}"
                )


def _extract_retained_claims(item: CorpusItem) -> dict[str, claims.Claim]:
    current: dict[str, claims.Claim] = {}
    prefix = f"{item.id}/"
    for path, body in sorted(item.artifacts.items()):
        if not path.startswith(prefix):
            continue
        note_path = path.removeprefix(prefix)
        parsed = Path(note_path)
        if parsed.parent != Path("notes") or parsed.suffix != ".md":
            continue
        try:
            extracted = claims.extract_claims(body, note_path=note_path)
        except ValueError as error:
            raise CorpusError(f"item {item.id!r}: retained note {path!r}: {error}") from error
        for claim in extracted:
            if claim.id in current:
                raise CorpusError(f"item {item.id!r}: duplicate retained claim id {claim.id!r}")
            current[claim.id] = claim
    return current


def _has_current_snapshot_provenance(
    source: Source, content: str, metadata: Mapping[str, Any]
) -> bool:
    captured_at = metadata.get("captured_at")
    try:
        captured = datetime.fromisoformat(captured_at) if isinstance(captured_at, str) else None
    except ValueError:
        captured = None
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return (
        type(metadata.get("schema_version")) is int
        and metadata["schema_version"] == 2
        and metadata.get("url") == source.url
        and metadata.get("declared_url") == source.url
        and _valid_redirect_provenance(metadata)
        and type(metadata.get("http_status")) is int
        and 200 <= metadata["http_status"] < 300
        and captured is not None
        and captured.tzinfo is not None
        and captured.utcoffset() is not None
        and captured.isoformat() == captured_at
        and isinstance(metadata.get("content_type"), str)
        and is_supported_text_content_type(metadata["content_type"])
        and metadata.get("content_eligible") is True
        and metadata.get("status") == "ok"
        and bool(content.strip())
        and metadata.get("content_hash") == content_hash
    )


def _valid_redirect_provenance(metadata: Mapping[str, Any]) -> bool:
    declared_url = metadata.get("declared_url")
    final_url = metadata.get("final_url")
    redirects = metadata.get("redirects")
    if not isinstance(declared_url, str) or not isinstance(final_url, str):
        return False
    if not isinstance(redirects, list):
        return False
    try:
        validate_http_url_structure(declared_url)
        validate_http_url_structure(final_url)
    except (TypeError, ValueError):
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
            or type(status_code) is not int
            or status_code not in FOLLOWED_REDIRECT_STATUSES
            or not isinstance(location, str)
            or not location
            or not isinstance(target_url, str)
            or target_url != urljoin(expected_url, location)
        ):
            return False
        try:
            validate_http_url_structure(redirect_url)
            validate_http_url_structure(target_url)
        except (TypeError, ValueError):
            return False
        expected_url = target_url
    return expected_url == final_url


def _load_manifest(path: Path) -> tuple[str, BaselineProvenance]:
    data = _read_yaml_mapping(path)
    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        raise CorpusError(f"{path.name}: 'version' must be a non-empty string")
    raw = data.get("baseline_provenance")
    if not isinstance(raw, dict) or set(raw) != _BASELINE_PROVENANCE_KEYS:
        raise CorpusError(
            f"{path.name}: 'baseline_provenance' must contain exactly "
            f"{sorted(_BASELINE_PROVENANCE_KEYS)}"
        )
    if (
        type(raw.get("version")) is not int
        or type(raw.get("snapshot_schema_version")) is not int
        or not isinstance(raw.get("decision_lineage_field"), str)
        or raw.get("preserved_baseline") is not None
    ):
        raise CorpusError(
            f"{path.name}: 'baseline_provenance' is malformed and preserved_baseline must be null"
        )
    provenance = BaselineProvenance(
        version=raw["version"],
        snapshot_schema_version=raw["snapshot_schema_version"],
        decision_lineage_field=raw["decision_lineage_field"],
        preserved_baseline=None,
    )
    expected = BaselineProvenance(
        version=1,
        snapshot_schema_version=2,
        decision_lineage_field="evidence_claim_ids",
        preserved_baseline=None,
    )
    if provenance != expected:
        raise CorpusError(
            f"{path.name}: 'baseline_provenance' does not match current migration metadata"
        )
    return version, provenance


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CorpusError(f"missing corpus file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:  # pragma: no cover - depends on malformed input
        raise CorpusError(f"{path.name}: invalid YAML: {error}") from error
    if not isinstance(data, dict):
        raise CorpusError(f"{path.name}: expected a YAML mapping at the top level")
    return data


def _require_str(data: Mapping[str, Any], key: str, item_id: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"item {item_id!r}: {key!r} must be a non-empty string, got {value!r}")
    return value


def _load_planted_defects(data: Mapping[str, Any], item_id: str) -> tuple[str, ...]:
    raw = data.get("planted_defects", None)
    if raw is None or not isinstance(raw, list):
        raise CorpusError(f"item {item_id!r}: 'planted_defects' must be a list, got {raw!r}")
    defects: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise CorpusError(f"item {item_id!r}: planted defect {entry!r} is not a string")
        if entry not in PLANTED_DEFECTS:
            raise CorpusError(
                f"item {item_id!r}: planted defect {entry!r} is not in the declared vocabulary "
                f"({', '.join(sorted(PLANTED_DEFECTS))})"
            )
        if entry in defects:
            raise CorpusError(f"item {item_id!r}: planted defect {entry!r} is declared twice")
        defects.append(entry)
    return tuple(defects)


def _load_expected_detection(
    data: Mapping[str, Any], item_id: str, planted: Sequence[str]
) -> dict[str, str]:
    raw = data.get("expected_detection", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CorpusError(f"item {item_id!r}: 'expected_detection' must be a mapping, got {raw!r}")
    expected: dict[str, str] = {}
    for defect, value in raw.items():
        if defect not in planted:
            raise CorpusError(
                f"item {item_id!r}: expected_detection names {defect!r}, which is not planted"
            )
        if value not in DETECTION_EXPECTATIONS:
            raise CorpusError(
                f"item {item_id!r}: expected_detection[{defect!r}] is {value!r}, "
                f"expected one of {DETECTION_EXPECTATIONS}"
            )
        expected[defect] = value
    for defect in planted:
        expected.setdefault(defect, "caught")
    return expected


def _load_sources(data: Mapping[str, Any], item_id: str) -> tuple[Source, ...]:
    raw = data.get("sources", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise CorpusError(f"item {item_id!r}: 'sources' must be a list, got {raw!r}")
    sources: list[Source] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise CorpusError(f"item {item_id!r}: source {entry!r} must be a mapping")
        source = Source(
            id=_require_str(entry, "id", item_id),
            url=_require_str(entry, "url", item_id),
            title=_require_str(entry, "title", item_id),
            tier=_require_str(entry, "tier", item_id),
            date=_require_str(entry, "date", item_id),
            snapshot=entry.get("snapshot"),
        )
        if source.snapshot is not None and not isinstance(source.snapshot, str):
            raise CorpusError(f"item {item_id!r}: source {source.id!r} snapshot must be a string")
        if source.id in seen:
            raise CorpusError(f"item {item_id!r}: source id {source.id!r} is declared twice")
        seen.add(source.id)
        sources.append(source)
    return tuple(sources)


def _load_artifacts(data: Mapping[str, Any], item_id: str) -> dict[str, str]:
    raw = data.get("artifacts", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise CorpusError(f"item {item_id!r}: 'artifacts' must be a mapping, got {raw!r}")
    artifacts: dict[str, str] = {}
    for path, body in raw.items():
        if not isinstance(path, str) or not path.strip():
            raise CorpusError(
                f"item {item_id!r}: artifact path {path!r} must be a non-empty string"
            )
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise CorpusError(f"item {item_id!r}: artifact path {path!r} must stay inside the root")
        if not isinstance(body, str):
            raise CorpusError(f"item {item_id!r}: artifact {path!r} body must be a string")
        artifacts[path] = body
    return artifacts


def _load_commands(data: Mapping[str, Any], item_id: str) -> tuple[tuple[str, ...], ...]:
    raw = data.get("commands", [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise CorpusError(f"item {item_id!r}: 'commands' must be a list, got {raw!r}")
    commands: list[tuple[str, ...]] = []
    for entry in raw:
        if not isinstance(entry, list) or not entry:
            raise CorpusError(f"item {item_id!r}: command {entry!r} must be a non-empty argv list")
        if not all(isinstance(token, str) for token in entry):
            raise CorpusError(f"item {item_id!r}: command {entry!r} must contain only strings")
        commands.append(tuple(entry))
    return tuple(commands)


def _load_probe(data: Mapping[str, Any], item_id: str, mode: str) -> dict[str, Any] | None:
    raw = data.get("probe")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CorpusError(f"item {item_id!r}: 'probe' must be a mapping, got {raw!r}")
    if mode != "full":
        raise CorpusError(f"item {item_id!r}: mode {mode!r} skips probe, so 'probe' is not allowed")
    return dict(raw)
