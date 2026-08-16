"""Deterministic, derived knowledge across SDR investigations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx
import yaml

from sdr import index, lifecycle
from sdr import snapshot as snapshot_mod
from sdr.context_graph import (
    ContextGraph,
    ContextGraphError,
    GraphEdge,
    GraphNode,
    canonical_node_id,
)
from sdr.gates import source_expiry
from sdr.network_policy import (
    MAX_BLOCKING_WORKERS,
    MAX_RESPONSE_BYTES,
    NetworkPolicyError,
    RedirectRecord,
    Resolver,
    validate_http_url_structure,
)
from sdr.parser import parse_artifact
from sdr.paths import resolve_root
from sdr.research import Research
from sdr.textual_anchoring import normalize_text
from sdr.verification import (
    VerificationItem,
    evaluate_source_snapshot,
    verify_explore_claims,
)
from sdr.verification_ledger import (
    LedgerValidationError,
    ledger_directory_lock,
    load_ledger,
    make_degradation_acknowledgement_id,
    save_ledger,
    validate_ledger,
)

_TRACKING_PARAMETERS = {
    "dclid",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
}
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_ARXIV_RE = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)
_PMID_RE = re.compile(r"[1-9]\d*")
_ISBN_RE = re.compile(r"(?:\d{9}[\dXx]|97[89]\d{10})")


@dataclass(frozen=True)
class ResolvedSourceIdentity:
    """A source identity and the exact resolver output that established it."""

    identity: str
    resolver: str
    identifier: str


IdentityResolver = Callable[[str, Mapping[str, object]], ResolvedSourceIdentity | None]


@dataclass(frozen=True)
class ResolverDefinition:
    """A named, versioned source identity resolver."""

    name: str
    version: str
    resolve: IdentityResolver


def normalize_url(url: str) -> str:
    """Return the deterministic identity form of a declared HTTP(S) URL."""
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"expected an absolute HTTP(S) URL: {url!r}")

    host = parsed.hostname.lower()
    if host.startswith("www."):
        host = host[4:]
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"

    path = parsed.path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMETERS
        ),
        doseq=True,
    )
    return urlunsplit(("https", host, path, query, ""))


def resolve_work_identifier(
    url: str, metadata: Mapping[str, object]
) -> ResolvedSourceIdentity | None:
    """Resolve an exact declared DOI, arXiv id, PMID, or valid ISBN."""
    values = {str(key).lower(): str(value).strip() for key, value in metadata.items()}
    candidates = (
        ("doi", values.get("doi"), _DOI_RE),
        ("arxiv", values.get("arxiv"), _ARXIV_RE),
        ("pmid", values.get("pmid"), _PMID_RE),
        ("isbn", values.get("isbn"), _ISBN_RE),
    )
    for kind, value, pattern in candidates:
        if value is None:
            continue
        normalized = _normalize_identifier(kind, value)
        if pattern.fullmatch(normalized) and (kind != "isbn" or _valid_isbn(normalized)):
            return _work_identity(kind, normalized)

    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = unquote(parsed.path).strip("/")
    if host in {"doi.org", "dx.doi.org"} and _DOI_RE.fullmatch(path):
        return _work_identity("doi", path)
    if host == "arxiv.org":
        arxiv = path.removeprefix("abs/").removeprefix("pdf/").removesuffix(".pdf")
        if path != arxiv and _ARXIV_RE.fullmatch(arxiv):
            return _work_identity("arxiv", arxiv)
    if host == "pubmed.ncbi.nlm.nih.gov" and _PMID_RE.fullmatch(path):
        return _work_identity("pmid", path)
    if host == "openlibrary.org" and path.startswith("isbn/"):
        isbn = _normalize_identifier("isbn", path.removeprefix("isbn/"))
        if _ISBN_RE.fullmatch(isbn) and _valid_isbn(isbn):
            return _work_identity("isbn", isbn)
    return None


def _resolve_normalized_url(url: str, metadata: Mapping[str, object]) -> ResolvedSourceIdentity:
    del metadata
    identity = normalize_url(url)
    return ResolvedSourceIdentity(
        identity=identity,
        resolver="normalized-url",
        identifier=identity,
    )


WORK_IDENTIFIER_RESOLVER = ResolverDefinition(
    name="work-identifier", version="1", resolve=resolve_work_identifier
)
NORMALIZED_URL_RESOLVER = ResolverDefinition(
    name="normalized-url", version="1", resolve=_resolve_normalized_url
)
DEFAULT_RESOLVER_CHAIN = (WORK_IDENTIFIER_RESOLVER, NORMALIZED_URL_RESOLVER)


def resolve_source_identity(
    url: str,
    metadata: Mapping[str, object] | None = None,
    resolver_chain: Sequence[ResolverDefinition] = DEFAULT_RESOLVER_CHAIN,
) -> ResolvedSourceIdentity:
    """Resolve stored source data through the supplied ordered resolver chain."""
    stored_metadata = metadata or {}
    for resolver in resolver_chain:
        resolved = resolver.resolve(url, stored_metadata)
        if resolved is not None:
            return resolved
    raise ValueError("resolver chain produced no source identity")


def _normalize_identifier(kind: str, value: str) -> str:
    if kind == "isbn":
        return value.replace("-", "").replace(" ", "")
    return value.lower()


def _work_identity(kind: str, value: str) -> ResolvedSourceIdentity:
    identifier = f"{kind}:{value.lower()}"
    return ResolvedSourceIdentity(
        identity=identifier,
        resolver="work-identifier",
        identifier=identifier,
    )


def _valid_isbn(value: str) -> bool:
    if len(value) == 10:
        total = sum(
            (10 - index) * (10 if digit.lower() == "x" else int(digit))
            for index, digit in enumerate(value)
        )
        return total % 11 == 0
    total = sum((1 if index % 2 == 0 else 3) * int(digit) for index, digit in enumerate(value))
    return total % 10 == 0


@dataclass(frozen=True)
class ResolverVersion:
    """Serializable identity of one resolver in a derivation chain."""

    name: str
    version: str


@dataclass(frozen=True)
class SourceResolution:
    """Resolved identity for one source declared by one investigation."""

    investigation: str
    source_id: str
    url: str
    identity: str
    resolver: str
    identifier: str

    def to_dict(self) -> dict[str, str]:
        return {
            "investigation": self.investigation,
            "source_id": self.source_id,
            "url": self.url,
            "identity": self.identity,
            "resolver": self.resolver,
            "identifier": self.identifier,
        }


@dataclass(frozen=True)
class SourceMerge:
    """Exact source-identity collision and the identifier that caused it."""

    identity: str
    resolver: str
    identifier: str
    sources: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "identity": self.identity,
            "resolver": self.resolver,
            "identifier": self.identifier,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class AnchoredClaim:
    """One persisted verified textual anchor with its resolved source."""

    investigation: str
    claim_id: str
    source_id: str
    source_identity: str
    normalized_sentence: str

    def to_dict(self) -> dict[str, str]:
        return {
            "investigation": self.investigation,
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "source_identity": self.source_identity,
            "normalized_sentence": self.normalized_sentence,
        }


@dataclass(frozen=True)
class EvidenceClaim:
    """One persisted source-linked claim and its current evidence health."""

    investigation: str
    claim_id: str
    source_id: str
    source_identity: str
    current_evidence_health: str

    def to_dict(self) -> dict[str, str]:
        return {
            "investigation": self.investigation,
            "claim_id": self.claim_id,
            "source_id": self.source_id,
            "source_identity": self.source_identity,
            "current_evidence_health": self.current_evidence_health,
        }


@dataclass(frozen=True)
class DecisionLineage:
    """Persisted decision lineage, with omission distinct from an empty list."""

    investigation: str
    evidence_claim_ids: tuple[str, ...] | None
    lineage_status: str | None = None


@dataclass(frozen=True)
class DecisionLineageStatus:
    """Investigation-qualified interpretation of a persisted lineage declaration."""

    investigation: str
    status: str

    def to_dict(self) -> dict[str, str]:
        return {"investigation": self.investigation, "status": self.status}


@dataclass(frozen=True)
class DependentDecision:
    """One explicit historical decision-to-claim dependency."""

    investigation: str
    claim_id: str

    def to_dict(self) -> dict[str, str]:
        return {"investigation": self.investigation, "claim_id": self.claim_id}


@dataclass(frozen=True)
class SourceDependencyQuery:
    """Deterministic source citations, claims, lineage states, and dependencies."""

    source_identity: str
    citations: tuple[SourceResolution, ...]
    claims: tuple[EvidenceClaim, ...]
    lineage_statuses: tuple[DecisionLineageStatus, ...]
    dependent_decisions: tuple[DependentDecision, ...]

    def to_dict(self) -> dict[str, object]:
        from sdr.public_tree_audit import redact_sensitive_values

        return redact_sensitive_values(
            {
                "source_identity": self.source_identity,
                "citations": [item.to_dict() for item in self.citations],
                "claims": [item.to_dict() for item in self.claims],
                "lineage_statuses": [item.to_dict() for item in self.lineage_statuses],
                "dependent_decisions": [item.to_dict() for item in self.dependent_decisions],
            }
        )


@dataclass(frozen=True, init=False)
class NetworkSourceObservation:
    """Canonical, provenance-bearing result of an explicit source observation."""

    investigation: str
    source_id: str
    state: Literal["retrieved", "unreachable", "invalid"]
    declared_url: str
    observed_url: str | None
    redirects: tuple[RedirectRecord, ...]
    retrieved_at: datetime
    status_code: int | None
    content_eligible: bool | None
    content_hash: str | None = None
    failure_kind: Literal["invalid_declaration", "malformed_response", "observer_error"] | None = (
        None
    )

    @classmethod
    def invalid_declaration(
        cls,
        *,
        investigation: str,
        source_id: str,
        retrieved_at: datetime,
    ) -> NetworkSourceObservation:
        """Build an invalid result without retaining a structurally unsafe URL."""
        return cls._create(
            investigation=investigation,
            source_id=source_id,
            state="invalid",
            declared_url="<redacted>",
            observed_url=None,
            redirects=(),
            retrieved_at=retrieved_at,
            status_code=None,
            content_eligible=None,
            content_hash=None,
            failure_kind="invalid_declaration",
        )

    @classmethod
    def from_fetch_result(
        cls,
        *,
        investigation: str,
        source_id: str,
        declared_url: str,
        retrieved_at: datetime,
        fetched: snapshot_mod.FetchResult,
    ) -> NetworkSourceObservation:
        """Build a comparable live observation through snapshot canonicalization."""
        canonical = snapshot_mod.canonicalize_fetched_content(declared_url, fetched)
        if canonical.status != "ok":
            raise ValueError("retrieved network observation must contain eligible content")
        return cls._create(
            investigation=investigation,
            source_id=source_id,
            state="retrieved",
            declared_url=declared_url,
            observed_url=fetched.final_url,
            redirects=fetched.redirects,
            retrieved_at=retrieved_at,
            status_code=fetched.status_code,
            content_eligible=canonical.content_eligible,
            content_hash=canonical.content_hash,
        )

    @classmethod
    def unreachable(
        cls,
        *,
        investigation: str,
        source_id: str,
        declared_url: str,
        observed_url: str,
        retrieved_at: datetime,
        redirects: tuple[RedirectRecord, ...] = (),
        status_code: int | None = None,
        content_eligible: bool | None = None,
    ) -> NetworkSourceObservation:
        """Build an explicit failed-retrieval observation without inventing response data."""
        return cls._create(
            investigation=investigation,
            source_id=source_id,
            state="unreachable",
            declared_url=declared_url,
            observed_url=observed_url,
            redirects=redirects,
            retrieved_at=retrieved_at,
            status_code=status_code,
            content_eligible=content_eligible,
            content_hash=None,
            failure_kind=None,
        )

    @classmethod
    def invalid(
        cls,
        *,
        investigation: str,
        source_id: str,
        declared_url: str,
        retrieved_at: datetime,
        failure_kind: Literal["malformed_response", "observer_error"],
    ) -> NetworkSourceObservation:
        """Build a redacted invalid result without retaining exception or response data."""
        return cls._create(
            investigation=investigation,
            source_id=source_id,
            state="invalid",
            declared_url=declared_url,
            observed_url=None,
            redirects=(),
            retrieved_at=retrieved_at,
            status_code=None,
            content_eligible=None,
            content_hash=None,
            failure_kind=failure_kind,
        )

    @classmethod
    def _create(
        cls,
        *,
        investigation: str,
        source_id: str,
        state: Literal["retrieved", "unreachable", "invalid"],
        declared_url: str,
        observed_url: str | None,
        redirects: tuple[RedirectRecord, ...],
        retrieved_at: datetime,
        status_code: int | None,
        content_eligible: bool | None,
        content_hash: str | None,
        failure_kind: Literal["invalid_declaration", "malformed_response", "observer_error"]
        | None = None,
    ) -> NetworkSourceObservation:
        if not investigation or not source_id:
            raise ValueError("network observation requires an exact source record")
        if state == "invalid":
            if observed_url is not None or failure_kind is None:
                raise ValueError("invalid network observation must contain only a failure kind")
            if failure_kind == "invalid_declaration":
                if declared_url != "<redacted>":
                    raise ValueError("invalid source declaration URL must be redacted")
            else:
                validate_http_url_structure(declared_url)
        elif observed_url is None or failure_kind is not None:
            raise ValueError("network observation state has inconsistent provenance")
        else:
            validate_http_url_structure(declared_url)
            validate_http_url_structure(observed_url)
        expected_url = declared_url
        for redirect in redirects:
            if redirect.url != expected_url:
                raise ValueError("network observation redirects must form an ordered chain")
            validate_http_url_structure(redirect.url)
            validate_http_url_structure(redirect.target_url)
            expected_url = redirect.target_url
        if state != "invalid" and expected_url != observed_url:
            raise ValueError("network observation final URL must match redirect provenance")
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("network observation retrieved_at must be timezone-aware")
        instance = object.__new__(cls)
        for name, value in (
            ("investigation", investigation),
            ("source_id", source_id),
            ("state", state),
            ("declared_url", declared_url),
            ("observed_url", observed_url),
            ("redirects", redirects),
            ("retrieved_at", retrieved_at),
            ("status_code", status_code),
            ("content_eligible", content_eligible),
            ("content_hash", content_hash),
            ("failure_kind", failure_kind),
        ):
            object.__setattr__(instance, name, value)
        return instance


@dataclass(frozen=True)
class NetworkObservationHistory:
    """Caller-held, read-only observations ordered within each exact source record."""

    observations: tuple[NetworkSourceObservation, ...] = ()

    def __iter__(self) -> Iterator[NetworkSourceObservation]:
        return iter(self.observations)

    def __len__(self) -> int:
        return len(self.observations)


def merge_network_observation_history(
    prior_history: NetworkObservationHistory | Sequence[NetworkSourceObservation],
    current_observations: Sequence[NetworkSourceObservation],
) -> NetworkObservationHistory:
    """Append observations after validating strict per-source timestamp order."""
    prior = _network_observation_sequence(prior_history)
    current = tuple(current_observations)
    _validate_network_observation_order(prior)
    merged = prior + current
    _validate_network_observation_order(merged)
    return NetworkObservationHistory(merged)


@dataclass(frozen=True)
class SourceHealthFact:
    """One mechanical source-health fact with its observation boundary."""

    source_investigation: str
    source_id: str
    source_identity: str
    cause: Literal["unreachable", "changed", "expired"] | None
    observation_source: Literal["network", "local_snapshot", "tier_policy"]
    reachability: Literal["not checked", "reachable", "unreachable"] | None
    mechanical_fact: str
    observation_id: str
    observation_timestamp: str | None = None
    observation_state: Literal["retrieved", "unreachable", "invalid"] | None = None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "source_investigation": self.source_investigation,
            "source_id": self.source_id,
            "source_identity": self.source_identity,
            "cause": self.cause,
            "observation_source": self.observation_source,
            "reachability": self.reachability,
            "mechanical_fact": self.mechanical_fact,
            "observation_id": self.observation_id,
            "observation_timestamp": self.observation_timestamp,
            "observation_state": self.observation_state,
        }


@dataclass(frozen=True)
class DegradedSupportItem:
    """One mechanical degradation joined to one historical decision dependency."""

    source_investigation: str
    source_id: str
    source_identity: str
    cause: Literal["unreachable", "changed", "expired"]
    observation_source: Literal["network", "tier_policy"]
    mechanical_fact: str
    observation_id: str
    decision_investigation: str
    claim_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_investigation": self.source_investigation,
            "source_id": self.source_id,
            "source_identity": self.source_identity,
            "cause": self.cause,
            "observation_source": self.observation_source,
            "mechanical_fact": self.mechanical_fact,
            "observation_id": self.observation_id,
            "decision_investigation": self.decision_investigation,
            "claim_id": self.claim_id,
        }


@dataclass(frozen=True)
class DegradationAcknowledgementStatus:
    """One immutable review event with status derived from current source state."""

    acknowledgement_id: str
    source_investigation: str
    source_id: str
    cause: Literal["unreachable", "changed", "expired"]
    observation_id: str
    reason: str
    by: str
    date: str
    status: Literal["active", "stale"]

    def to_dict(self) -> dict[str, str]:
        return {
            "acknowledgement_id": self.acknowledgement_id,
            "source_investigation": self.source_investigation,
            "source_id": self.source_id,
            "cause": self.cause,
            "observation_id": self.observation_id,
            "reason": self.reason,
            "by": self.by,
            "date": self.date,
            "status": self.status,
        }


@dataclass(frozen=True)
class DegradedSupportReport:
    """Stable, read-only source facts and affected historical dependencies."""

    facts: tuple[SourceHealthFact, ...]
    items: tuple[DegradedSupportItem, ...]
    acknowledgements: tuple[DegradationAcknowledgementStatus, ...] = ()

    def to_dict(self) -> dict[str, object]:
        from sdr.public_tree_audit import redact_sensitive_values

        return redact_sensitive_values(
            {
                "facts": [fact.to_dict() for fact in self.facts],
                "items": [item.to_dict() for item in self.items],
                "acknowledgements": [event.to_dict() for event in self.acknowledgements],
            }
        )


@dataclass(frozen=True)
class CrossInvestigationLayer:
    """A disposable view derived from investigations under one research root."""

    investigations: tuple[str, ...]
    joins: tuple[dict[str, object], ...] = ()
    sources: tuple[SourceResolution, ...] = ()
    source_merges: tuple[SourceMerge, ...] = ()
    resolver_chain: tuple[ResolverVersion, ...] = ()
    anchored_claims: tuple[AnchoredClaim, ...] = ()
    evidence_claims: tuple[EvidenceClaim, ...] = ()
    completed_decisions: tuple[DecisionLineage, ...] = ()

    @property
    def join_count(self) -> int:
        """Return the number of cross-investigation joins."""
        return len(self.joins)

    def to_dict(self) -> dict[str, object]:
        """Serialize the layer to a deterministic dictionary."""
        from sdr.public_tree_audit import redact_sensitive_values

        return redact_sensitive_values(
            {
                "investigations": list(self.investigations),
                "join_count": self.join_count,
                "joins": list(self.joins),
                "sources": [source.to_dict() for source in self.sources],
                "source_merges": [merge.to_dict() for merge in self.source_merges],
                "anchored_claims": [claim.to_dict() for claim in self.anchored_claims],
                "evidence_claims": [claim.to_dict() for claim in self.evidence_claims],
                "completed_decisions": [
                    {
                        "investigation": decision.investigation,
                        "lineage_status": decision.lineage_status,
                        "evidence_claim_ids": (
                            list(decision.evidence_claim_ids)
                            if decision.evidence_claim_ids is not None
                            else None
                        ),
                    }
                    for decision in self.completed_decisions
                ],
                "resolver_chain": [
                    {"name": resolver.name, "version": resolver.version}
                    for resolver in self.resolver_chain
                ],
            }
        )

    def to_context_graph(self) -> ContextGraph:
        """Express the accumulated layer through the validated Context Graph contract."""
        nodes: dict[str, GraphNode] = {}
        edges: list[GraphEdge] = []

        for investigation in self.investigations:
            node_id = _qualified_graph_id("research", investigation)
            _add_unique_graph_node(
                nodes,
                GraphNode(
                    id=node_id,
                    type="research",
                    title=investigation,
                    metadata={"investigation": investigation},
                ),
            )

        source_nodes: dict[tuple[str, str], str] = {}
        for source in self.sources:
            source_id = _qualified_graph_id("source", source.investigation, source.source_id)
            source_nodes[(source.investigation, source.source_id)] = source_id
            _add_unique_graph_node(
                nodes,
                GraphNode(
                    id=source_id,
                    type="source",
                    title=source.url,
                    metadata={
                        "identifier": source.identifier,
                        "identity": source.identity,
                        "investigation": source.investigation,
                        "resolver": source.resolver,
                        "source_id": source.source_id,
                        "url": source.url,
                    },
                ),
            )
            edges.append(
                _explicit_graph_edge(
                    _qualified_graph_id("research", source.investigation),
                    source_id,
                    "cites",
                    f"{source.investigation}:{source.source_id}",
                )
            )

        for claim in self.evidence_claims:
            claim_id = _qualified_graph_id("claim", claim.investigation, claim.claim_id)
            origin = f"{claim.investigation}:{claim.claim_id}"
            _add_unique_graph_node(
                nodes,
                GraphNode(
                    id=claim_id,
                    type="claim",
                    title=origin,
                    metadata={
                        "claim_id": claim.claim_id,
                        "current_evidence_health": claim.current_evidence_health,
                        "investigation": claim.investigation,
                        "source_id": claim.source_id,
                        "source_identity": claim.source_identity,
                    },
                ),
            )
            edges.append(
                _explicit_graph_edge(
                    _qualified_graph_id("research", claim.investigation),
                    claim_id,
                    "has_claim",
                    origin,
                )
            )
            source_node = source_nodes.get((claim.investigation, claim.source_id))
            if source_node is not None:
                edges.append(
                    _explicit_graph_edge(claim_id, source_node, "references_source", origin)
                )

        for decision in self.completed_decisions:
            decision_id = _qualified_graph_id("decision", decision.investigation, "recommendation")
            _add_unique_graph_node(
                nodes,
                GraphNode(
                    id=decision_id,
                    type="decision",
                    title=f"{decision.investigation}: recommendation",
                    metadata={
                        "evidence_claim_ids": decision.evidence_claim_ids,
                        "investigation": decision.investigation,
                        "lineage_status": decision.lineage_status,
                    },
                ),
            )
            for local_claim_id in decision.evidence_claim_ids or ():
                claim_id = _qualified_graph_id("claim", decision.investigation, local_claim_id)
                origin = f"{decision.investigation}:{local_claim_id}"
                existing_claim = nodes.get(claim_id)
                if existing_claim is None:
                    nodes[claim_id] = GraphNode(
                        id=claim_id,
                        type="claim",
                        title=origin,
                        metadata={
                            "claim_id": local_claim_id,
                            "investigation": decision.investigation,
                        },
                    )
                elif (
                    existing_claim.type != "claim"
                    or existing_claim.metadata.get("claim_id") != local_claim_id
                    or existing_claim.metadata.get("investigation") != decision.investigation
                ):
                    raise ContextGraphError(f"knowledge-base node id collision: {claim_id}")
                edges.append(_explicit_graph_edge(decision_id, claim_id, "based_on", origin))

        for join in self.joins:
            left, right = (str(item) for item in join["investigations"])
            metadata = {
                str(key): value
                for key, value in join.items()
                if key not in {"investigations", "kind", "provenance", "origins"}
            }
            metadata.update(
                investigations=(left, right),
                origin=join["origins"],
            )
            edges.append(
                GraphEdge(
                    source=_qualified_graph_id("research", left),
                    target=_qualified_graph_id("research", right),
                    relation=str(join["kind"]),
                    provenance="explicit",
                    metadata=metadata,
                )
            )

        graph = ContextGraph(
            nodes=sorted(nodes.values(), key=lambda node: node.id),
            edges=sorted(
                edges,
                key=lambda edge: (edge.source, edge.relation, edge.target, edge.provenance),
            ),
            metadata={
                "investigations": list(self.investigations),
                "resolver_chain": [
                    {"name": resolver.name, "version": resolver.version}
                    for resolver in self.resolver_chain
                ],
                "scope": "knowledge-base",
            },
        )
        graph.validate()
        return graph


def derive_cross_investigation_layer(
    base: str | Path,
    resolver_chain: Sequence[ResolverDefinition] = DEFAULT_RESOLVER_CHAIN,
) -> CrossInvestigationLayer:
    """Derive a read-only layer from every investigation under ``base``."""
    research = index._iter_research(resolve_root(base))
    _preflight_research_identities(research)
    declarations = {item.meta.slug: _validated_source_declarations(item) for item in research}
    sources: list[SourceResolution] = []
    anchors: list[AnchoredClaim] = []
    evidence_claims: list[EvidenceClaim] = []
    completed_decisions: list[DecisionLineage] = []
    for item in research:
        item_sources: list[SourceResolution] = []
        for source in declarations[item.meta.slug]:
            source_id = str(source.get("id") or "")
            url = str(source.get("url") or "")
            if not source_id or not url:
                continue
            try:
                validate_http_url_structure(url)
            except NetworkPolicyError as exc:
                raise ValueError(f"invalid source URL for {item.meta.slug}:{source_id}") from exc
            metadata = _stored_source_metadata(item.artifact_path("notes"), source_id)
            metadata.update(source)
            resolved = resolve_source_identity(url, metadata, resolver_chain)
            resolution = SourceResolution(
                investigation=item.meta.slug,
                source_id=source_id,
                url=url,
                identity=resolved.identity,
                resolver=resolved.resolver,
                identifier=resolved.identifier,
            )
            sources.append(resolution)
            item_sources.append(resolution)
        current_claims = verify_explore_claims(item, persist=False).items
        item_anchors = _verified_anchors(item, item_sources, current_claims)
        anchors.extend(item_anchors)
        evidence_claims.extend(_evidence_claims(item, item_sources, current_claims))
        if item.meta.status == "done":
            completed_decisions.extend(_decision_lineage(item))
    return CrossInvestigationLayer(
        investigations=tuple(item.meta.slug for item in research),
        joins=_claim_joins(anchors) + _source_joins(sources),
        sources=tuple(sources),
        source_merges=_source_merges(sources),
        anchored_claims=tuple(sorted(anchors, key=_anchor_sort_key)),
        evidence_claims=tuple(sorted(evidence_claims, key=_evidence_claim_sort_key)),
        completed_decisions=tuple(
            sorted(completed_decisions, key=lambda decision: decision.investigation)
        ),
        resolver_chain=tuple(
            ResolverVersion(name=resolver.name, version=resolver.version)
            for resolver in resolver_chain
        ),
    )


def _qualified_graph_id(node_type: str, investigation: str, local_id: str = "") -> str:
    raw_id = investigation if not local_id else f"{investigation}.{local_id}"
    readable = canonical_node_id(node_type, raw_id)
    digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:12]
    return f"{readable}--{digest}"


def _add_unique_graph_node(nodes: dict[str, GraphNode], node: GraphNode) -> None:
    if node.id in nodes:
        raise ContextGraphError(f"duplicate knowledge-base node id: {node.id}")
    nodes[node.id] = node


def _preflight_research_identities(research: Sequence[Research]) -> None:
    roots_by_slug: dict[str, list[str]] = {}
    for item in research:
        roots_by_slug.setdefault(item.meta.slug, []).append(item.root.name)

    duplicate_diagnostics = [
        f"duplicate investigation slug {slug!r} declared by roots "
        + ", ".join(repr(root) for root in sorted(roots))
        for slug, roots in sorted(roots_by_slug.items())
        if len(roots) > 1
    ]
    mismatch_diagnostics = [
        f"root {item.root.name!r} declares slug {item.meta.slug!r}"
        for item in research
        if item.root.name != item.meta.slug
    ]
    diagnostics = duplicate_diagnostics + sorted(mismatch_diagnostics)
    if diagnostics:
        raise ValueError("invalid investigation identities: " + "; ".join(diagnostics))


def _explicit_graph_edge(source: str, target: str, relation: str, origin: str) -> GraphEdge:
    return GraphEdge(
        source=source,
        target=target,
        relation=relation,
        provenance="explicit",
        metadata={"origin": origin},
    )


def query_source_dependencies(
    layer: CrossInvestigationLayer, source_identity: str
) -> SourceDependencyQuery:
    """Report explicit persisted dependencies for one resolved source identity."""
    citations = tuple(
        sorted(
            (source for source in layer.sources if source.identity == source_identity),
            key=_source_sort_key,
        )
    )
    citation_investigations = {item.investigation for item in citations}
    declared_claim_keys = {
        (decision.investigation, claim_id)
        for decision in layer.completed_decisions
        for claim_id in decision.evidence_claim_ids or ()
    }
    source_claims = tuple(
        claim for claim in layer.evidence_claims if claim.source_identity == source_identity
    )
    source_claim_keys = {(claim.investigation, claim.claim_id) for claim in source_claims}
    claims = tuple(
        sorted(
            (
                claim
                for claim in source_claims
                if claim.current_evidence_health == "verified"
                or (claim.investigation, claim.claim_id) in declared_claim_keys
            ),
            key=_evidence_claim_sort_key,
        )
    )
    claim_keys = {(claim.investigation, claim.claim_id) for claim in claims}
    lineage_statuses = tuple(
        sorted(
            (
                DecisionLineageStatus(
                    investigation=decision.investigation,
                    status=(
                        decision.lineage_status
                        if decision.lineage_status is not None
                        else (
                            "lineage unavailable"
                            if decision.evidence_claim_ids is None
                            else (
                                "no declared claim dependencies"
                                if not decision.evidence_claim_ids
                                else "declared claim dependencies"
                            )
                        )
                    ),
                )
                for decision in layer.completed_decisions
                if decision.investigation in citation_investigations
                or any(
                    (decision.investigation, claim_id) in source_claim_keys
                    for claim_id in decision.evidence_claim_ids or ()
                )
            ),
            key=lambda item: item.investigation,
        )
    )
    dependent_decisions = tuple(
        sorted(
            (
                DependentDecision(investigation=decision.investigation, claim_id=claim_id)
                for decision in layer.completed_decisions
                for claim_id in decision.evidence_claim_ids or ()
                if (decision.investigation, claim_id) in claim_keys
            ),
            key=lambda item: (item.investigation, item.claim_id),
        )
    )
    return SourceDependencyQuery(
        source_identity=source_identity,
        citations=citations,
        claims=claims,
        lineage_statuses=lineage_statuses,
        dependent_decisions=dependent_decisions,
    )


def observe_network_sources(
    base: str | Path,
    *,
    mock_transport: httpx.MockTransport | None = None,
    resolver: Resolver | None = None,
    observed_at: datetime | None = None,
) -> tuple[NetworkSourceObservation, ...]:
    """Explicitly observe every declared source without persisting the results."""
    timestamp = observed_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    research = index._iter_research(resolve_root(base))
    declarations = {item.meta.slug: _validated_source_declarations(item) for item in research}
    records = tuple(
        (item.meta.slug, str(source.get("id") or ""), str(source.get("url") or ""))
        for item in research
        for source in declarations[item.meta.slug]
        if source.get("id") and source.get("url")
    )
    if not records:
        return ()

    structurally_invalid_urls: set[str] = set()
    for declared_url in dict.fromkeys(record[2] for record in records):
        try:
            validate_http_url_structure(declared_url)
        except NetworkPolicyError:
            structurally_invalid_urls.add(declared_url)

    def observe(record: tuple[str, str, str]) -> NetworkSourceObservation:
        investigation, source_id, declared_url = record
        try:
            fetched = snapshot_mod.fetch_url(
                declared_url,
                mock_transport=mock_transport,
                resolver=resolver,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except NetworkPolicyError:
            return NetworkSourceObservation.unreachable(
                investigation=investigation,
                source_id=source_id,
                declared_url=declared_url,
                observed_url=declared_url,
                retrieved_at=timestamp,
            )
        except Exception:
            return NetworkSourceObservation.invalid(
                investigation=investigation,
                source_id=source_id,
                declared_url=declared_url,
                retrieved_at=timestamp,
                failure_kind="observer_error",
            )
        try:
            canonical = snapshot_mod.canonicalize_fetched_content(declared_url, fetched)
            if canonical.status != "ok":
                raise ValueError("response is not canonical evidence")
            return NetworkSourceObservation.from_fetch_result(
                investigation=investigation,
                source_id=source_id,
                declared_url=declared_url,
                retrieved_at=timestamp,
                fetched=fetched,
            )
        except Exception:
            return NetworkSourceObservation.invalid(
                investigation=investigation,
                source_id=source_id,
                declared_url=declared_url,
                retrieved_at=timestamp,
                failure_kind="malformed_response",
            )

    observations: list[NetworkSourceObservation | None] = [None] * len(records)
    valid_records: list[tuple[int, tuple[str, str, str]]] = []
    for index_, record in enumerate(records):
        investigation, source_id, declared_url = record
        if declared_url in structurally_invalid_urls:
            observations[index_] = NetworkSourceObservation.invalid_declaration(
                investigation=investigation,
                source_id=source_id,
                retrieved_at=timestamp,
            )
        else:
            valid_records.append((index_, record))

    with ThreadPoolExecutor(max_workers=MAX_BLOCKING_WORKERS) as executor:
        for (index_, _), observation in zip(
            valid_records,
            executor.map(observe, (record for _, record in valid_records)),
            strict=True,
        ):
            observations[index_] = observation
    return tuple(cast(NetworkSourceObservation, item) for item in observations)


def report_degraded_support(
    base: str | Path,
    *,
    network_observations: NetworkObservationHistory | Sequence[NetworkSourceObservation] = (),
    include_expiry: bool = False,
    as_of: date | None = None,
    resolver_chain: Sequence[ResolverDefinition] = DEFAULT_RESOLVER_CHAIN,
) -> DegradedSupportReport:
    """Report mechanical source degradation against historical decision lineage."""
    if include_expiry and as_of is None:
        raise ValueError("include_expiry requires an explicit as_of date")
    observation_sequence = _network_observation_sequence(network_observations)
    _validate_network_observation_order(observation_sequence)
    observation_histories: dict[tuple[str, str], list[NetworkSourceObservation]] = {}
    for observation in observation_sequence:
        key = (observation.investigation, observation.source_id)
        observation_histories.setdefault(key, []).append(observation)

    root = resolve_root(base)
    research = {item.meta.slug: item for item in index._iter_research(root)}
    source_declarations = {
        slug: _validated_source_declarations(item) for slug, item in research.items()
    }
    layer = derive_cross_investigation_layer(root, resolver_chain=resolver_chain)
    declarations = {
        (item.meta.slug, str(source.get("id") or "")): source
        for item in research.values()
        for source in source_declarations[item.meta.slug]
        if source.get("id")
    }
    acknowledgement_events = _degradation_acknowledgements(research, declarations)
    for key, history in sorted(observation_histories.items()):
        declaration = declarations.get(key)
        if declaration is None:
            raise ValueError(
                f"network observation does not match a declared source record: {key[0]}:{key[1]}"
            )
        for observation in history:
            if observation.declared_url != str(declaration.get("url") or ""):
                raise ValueError(
                    "network observation declared URL does not match source record: "
                    f"{key[0]}:{key[1]}"
                )
    facts: list[SourceHealthFact] = []
    for source in sorted(layer.sources, key=_source_sort_key):
        item = research[source.investigation]
        health = evaluate_source_snapshot(item, source.source_id, declared_url=source.url)
        local_observation_id = _observation_id(
            source,
            "local_snapshot",
            health.identity,
            health.eligible,
            health.persisted_content_hash,
            health.recorded_content_hash,
            health.content_hash_matches,
            health.metadata_exists,
            health.content_exists,
        )
        if not health.metadata_exists and not health.content_exists:
            facts.append(
                _source_fact(
                    source, None, "local_snapshot", "snapshot_missing", local_observation_id
                )
            )
        elif not health.eligible and not (
            health.recorded_content_hash and not health.content_hash_matches
        ):
            facts.append(
                _source_fact(
                    source, None, "local_snapshot", "snapshot_incomplete", local_observation_id
                )
            )
        if health.recorded_content_hash and not health.content_hash_matches:
            facts.append(
                _source_fact(
                    source,
                    None,
                    "local_snapshot",
                    "local_snapshot_hash_mismatch",
                    local_observation_id,
                )
            )

        history = observation_histories.get((source.investigation, source.source_id), [])
        observation = history[-1] if history else None
        if observation is None:
            facts.append(
                _source_fact(
                    source,
                    None,
                    "network",
                    "reachability_not_checked",
                    _observation_id(source, "network", "not_checked"),
                )
            )
        elif observation.state == "unreachable":
            facts.append(
                _source_fact(
                    source,
                    "unreachable",
                    "network",
                    "retrieval_failed",
                    _network_observation_id(
                        source,
                        "unreachable",
                        observation,
                        outage_boundary=_most_recent_retrieved_boundary(history[:-1]),
                    ),
                    observation=observation,
                )
            )
        elif observation.state == "invalid":
            facts.append(
                _source_fact(
                    source,
                    None,
                    "network",
                    "network_observation_invalid",
                    _network_observation_id(source, "invalid", observation),
                    observation=observation,
                )
            )
        else:
            observation_id = _network_observation_id(source, "retrieved", observation)
            facts.append(
                _source_fact(
                    source,
                    None,
                    "network",
                    "retrieval_succeeded",
                    observation_id,
                    observation=observation,
                )
            )
            if health.eligible and observation.content_hash != health.persisted_content_hash:
                facts.append(
                    _source_fact(
                        source,
                        "changed",
                        "network",
                        "live_content_hash_mismatch",
                        _network_observation_id(
                            source,
                            "changed",
                            observation,
                            baseline_snapshot_identity=(
                                health.identity,
                                health.persisted_content_hash,
                                health.recorded_content_hash,
                                health.metadata_exists,
                                health.content_exists,
                            ),
                        ),
                        observation=observation,
                    )
                )

        if include_expiry:
            expiry = source_expiry(
                item,
                declarations.get((source.investigation, source.source_id), {}),
                as_of=as_of,
            )
            if expiry.expired:
                facts.append(
                    _source_fact(
                        source,
                        "expired",
                        "tier_policy",
                        "tier_expiry_exceeded",
                        _observation_id(
                            source,
                            "expired",
                            "tier_policy",
                            expiry.tier,
                            expiry.source_date,
                            expiry.max_age_days,
                            True,
                        ),
                    )
                )

    ordered_facts = tuple(sorted(facts, key=_source_fact_sort_key))
    current_degradations = {
        (
            fact.source_investigation,
            fact.source_id,
            fact.cause,
            fact.observation_id,
        )
        for fact in ordered_facts
        if fact.cause is not None
    }
    active_acknowledgements = {
        _acknowledgement_key(event)
        for event in acknowledgement_events
        if _acknowledgement_key(event) in current_degradations
    }
    acknowledgement_statuses = tuple(
        DegradationAcknowledgementStatus(
            acknowledgement_id=str(event["acknowledgement_id"]),
            source_investigation=str(event["source_investigation"]),
            source_id=str(event["source_id"]),
            cause=cast(Literal["unreachable", "changed", "expired"], str(event["cause"])),
            observation_id=str(event["observation_id"]),
            reason=str(event["reason"]),
            by=str(event["by"]),
            date=str(event["date"]),
            status=("active" if _acknowledgement_key(event) in current_degradations else "stale"),
        )
        for event in acknowledgement_events
    )
    items = tuple(
        sorted(
            (
                DegradedSupportItem(
                    source_investigation=fact.source_investigation,
                    source_id=fact.source_id,
                    source_identity=fact.source_identity,
                    cause=fact.cause,
                    observation_source=fact.observation_source,
                    mechanical_fact=fact.mechanical_fact,
                    observation_id=fact.observation_id,
                    decision_investigation=decision.investigation,
                    claim_id=decision.claim_id,
                )
                for fact in ordered_facts
                if fact.cause is not None
                and (
                    fact.source_investigation,
                    fact.source_id,
                    fact.cause,
                    fact.observation_id,
                )
                not in active_acknowledgements
                for decision in _dependent_decisions_for_source_record(
                    layer, fact.source_investigation, fact.source_id
                )
            ),
            key=_degraded_support_sort_key,
        )
    )
    return DegradedSupportReport(
        facts=ordered_facts,
        items=items,
        acknowledgements=acknowledgement_statuses,
    )


def acknowledge_degradation(
    research: Research,
    degradation: DegradedSupportItem,
    *,
    network_observations: NetworkObservationHistory | Sequence[NetworkSourceObservation] = (),
    include_expiry: bool = False,
    as_of: date | None = None,
    reason: str,
    by: str,
    acknowledged_on: date | None = None,
    before_write: Callable[[Path], object] | None = None,
    after_write: Callable[[Path], object] | None = None,
) -> None:
    """Validate and persist one observation review under the shared ledger lock."""
    reason = reason.strip()
    by = by.strip()
    if not reason:
        raise ValueError("degradation acknowledgement requires a non-empty reason")
    if not by:
        raise ValueError("degradation acknowledgement requires a non-empty author")
    event_date = acknowledged_on or date.today()
    if event_date > date.today():
        raise ValueError("degradation acknowledgement date must not be future")
    if degradation.source_investigation != research.meta.slug:
        raise ValueError("degradation acknowledgement must be stored in the affected investigation")
    declared_source_ids = {
        str(source.get("id") or "") for source in snapshot_mod._iter_note_sources(research)
    }
    if degradation.source_id not in declared_source_ids:
        raise ValueError("degradation acknowledgement does not match a declared source record")

    path = research.artifact_path("notes/sources/verification.yaml")
    with ledger_directory_lock(path):
        canonical_report = report_degraded_support(
            research.root.parent,
            network_observations=network_observations,
            include_expiry=include_expiry,
            as_of=as_of,
        )
        matching_observations = {
            _degradation_observation_key(item)
            for item in canonical_report.items
            if _degradation_acknowledgement_key(item)
            == _degradation_acknowledgement_key(degradation)
        }
        if matching_observations != {_degradation_observation_key(degradation)}:
            raise ValueError(
                "degradation is not one unambiguous observation in the current "
                "degraded-support report"
            )
        acknowledgement_id = make_degradation_acknowledgement_id(
            degradation.source_investigation,
            degradation.source_id,
            degradation.cause,
            degradation.observation_id,
        )
        ledger = load_ledger(path)
        if any(
            entry.get("acknowledgement_id") == acknowledgement_id
            for entry in ledger["degradation_acknowledgements"]
        ):
            raise ValueError("degradation observation is already acknowledged")
        if before_write is not None:
            before_write(path)
        ledger["degradation_acknowledgements"].append(
            {
                "acknowledgement_id": acknowledgement_id,
                "source_investigation": degradation.source_investigation,
                "source_id": degradation.source_id,
                "cause": degradation.cause,
                "observation_id": degradation.observation_id,
                "reason": reason,
                "by": by,
                "date": event_date.isoformat(),
            }
        )
        save_ledger(path, ledger)
        if after_write is not None:
            after_write(path)


def compare_cross_investigation_layers(
    left: CrossInvestigationLayer, right: CrossInvestigationLayer
) -> bool:
    """Compare compatible derived layers, refusing unlike resolver chains."""
    if left.resolver_chain != right.resolver_chain:
        raise ValueError("resolver chains differ; layers are not comparable")
    return left.to_dict() == right.to_dict()


def _stored_source_metadata(notes: Path, source_id: str) -> dict[str, object]:
    path = notes / "sources" / source_id / "meta.yaml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return dict(loaded) if isinstance(loaded, dict) else {}


def _validated_source_declarations(research: Research) -> tuple[dict[str, object], ...]:
    declarations = tuple(snapshot_mod._iter_note_sources(research))
    seen: set[str] = set()
    for source in declarations:
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        if snapshot_mod._SOURCE_ID_RE.fullmatch(source_id) is None:
            raise ValueError(
                f"invalid source id in investigation {research.meta.slug!r} at declaration "
                f"{len(seen) + 1}; expected S<n>"
            )
        if source_id in seen:
            raise ValueError(f"duplicate source id {research.meta.slug}:{source_id}")
        seen.add(source_id)
    return declarations


def _network_observation_sequence(
    observations: NetworkObservationHistory | Sequence[NetworkSourceObservation],
) -> tuple[NetworkSourceObservation, ...]:
    if isinstance(observations, NetworkObservationHistory):
        return observations.observations
    return tuple(observations)


def _validate_network_observation_order(
    observations: Sequence[NetworkSourceObservation],
) -> None:
    latest: dict[tuple[str, str], datetime] = {}
    for observation in observations:
        key = (observation.investigation, observation.source_id)
        previous = latest.get(key)
        if previous is not None and observation.retrieved_at <= previous:
            raise ValueError(
                "network observation timestamp must be strictly ordered without replay for "
                f"{key[0]}:{key[1]}"
            )
        latest[key] = observation.retrieved_at


def _source_merges(sources: Sequence[SourceResolution]) -> tuple[SourceMerge, ...]:
    by_identity: dict[str, list[SourceResolution]] = {}
    for source in sources:
        by_identity.setdefault(source.identity, []).append(source)
    return tuple(
        SourceMerge(
            identity=identity,
            resolver=matches[0].resolver,
            identifier=matches[0].identifier,
            sources=tuple(f"{source.investigation}:{source.source_id}" for source in matches),
        )
        for identity, matches in sorted(by_identity.items())
        if len(matches) > 1
    )


def _dependent_decisions_for_source_record(
    layer: CrossInvestigationLayer, investigation: str, source_id: str
) -> tuple[DependentDecision, ...]:
    claim_ids = {
        claim.claim_id
        for claim in layer.evidence_claims
        if claim.investigation == investigation and claim.source_id == source_id
    }
    return tuple(
        sorted(
            (
                DependentDecision(investigation=investigation, claim_id=claim_id)
                for decision in layer.completed_decisions
                if decision.investigation == investigation
                for claim_id in decision.evidence_claim_ids or ()
                if claim_id in claim_ids
            ),
            key=lambda item: (item.investigation, item.claim_id),
        )
    )


def _source_joins(sources: Sequence[SourceResolution]) -> tuple[dict[str, object], ...]:
    by_identity: dict[str, list[SourceResolution]] = {}
    for source in sources:
        by_identity.setdefault(source.identity, []).append(source)

    joins: list[dict[str, object]] = []
    for identity, matches in sorted(by_identity.items()):
        by_investigation: dict[str, list[SourceResolution]] = {}
        for source in matches:
            by_investigation.setdefault(source.investigation, []).append(source)
        for left, right in combinations(sorted(by_investigation), 2):
            pair = sorted(by_investigation[left] + by_investigation[right], key=_source_sort_key)
            joins.append(
                {
                    "kind": "source_identity",
                    "investigations": [left, right],
                    "provenance": "explicit",
                    "resolver": pair[0].resolver,
                    "identifier": identity,
                    "origins": [f"{source.investigation}:{source.source_id}" for source in pair],
                }
            )
    return tuple(joins)


def _verified_anchors(
    research: Research,
    sources: Sequence[SourceResolution],
    current_claims: Sequence[VerificationItem],
) -> tuple[AnchoredClaim, ...]:
    source_identities = {source.source_id: source.identity for source in sources}
    anchors: list[AnchoredClaim] = []
    for claim in current_claims:
        source_id = claim.source_id
        normalized_sentence = normalize_text(claim.claim_text)
        if claim.state != "verified" or not normalized_sentence:
            continue
        source_identity = source_identities.get(source_id)
        if source_identity is None:
            continue
        anchors.append(
            AnchoredClaim(
                investigation=research.meta.slug,
                claim_id=claim.claim_id,
                source_id=source_id,
                source_identity=source_identity,
                normalized_sentence=normalized_sentence,
            )
        )
    return tuple(sorted(anchors, key=_anchor_sort_key))


def _evidence_claims(
    research: Research,
    sources: Sequence[SourceResolution],
    current_claims: Sequence[VerificationItem],
) -> tuple[EvidenceClaim, ...]:
    source_identities = {source.source_id: source.identity for source in sources}
    ledger = load_ledger(research.artifact_path("notes/sources/verification.yaml"))
    claims = [claim.to_dict() for claim in current_claims]
    current_ids = {claim.claim_id for claim in current_claims}
    claims.extend(
        {**entry, "state": "stale"}
        for entry in ledger["claims"]
        if isinstance(entry, dict) and str(entry.get("claim_id") or "") not in current_ids
    )
    known_ids = {str(claim.get("claim_id") or "") for claim in claims}
    claims.extend(
        entry["data"]
        for entry in ledger["legacy"]
        if entry.get("kind") == "stale_claim"
        and isinstance(entry.get("data"), dict)
        and str(entry["data"].get("claim_id") or "") not in known_ids
    )
    result: list[EvidenceClaim] = []
    for claim in claims:
        source_id = str(claim.get("source_id") or "")
        source_identity = source_identities.get(source_id)
        claim_id = str(claim.get("claim_id") or "")
        if source_identity is None or not claim_id:
            continue
        result.append(
            EvidenceClaim(
                investigation=research.meta.slug,
                claim_id=claim_id,
                source_id=source_id,
                source_identity=source_identity,
                current_evidence_health=str(claim.get("state") or "unknown"),
            )
        )
    return tuple(sorted(result, key=_evidence_claim_sort_key))


def _decision_lineage(research: Research) -> tuple[DecisionLineage, ...]:
    path = research.artifact_path("decision-memo.md")
    if not path.is_file():
        if "transfer" in research.meta.validation:
            return (
                DecisionLineage(
                    research.meta.slug,
                    None,
                    "lineage invalid: transfer validation inconsistent",
                ),
            )
        return ()
    if "transfer" not in research.meta.validation:
        return (
            DecisionLineage(
                research.meta.slug,
                None,
                "lineage unavailable: transfer validation missing",
            ),
        )
    transfer_issue = next(
        (
            issue
            for issue in lifecycle.check_consistency(research)
            if issue.startswith(lifecycle.consistency_issue_prefix("transfer"))
        ),
        None,
    )
    if transfer_issue is not None:
        return (
            DecisionLineage(
                research.meta.slug,
                None,
                "lineage invalid: transfer validation inconsistent",
            ),
        )
    frontmatter = parse_artifact(path).frontmatter
    if "evidence_claim_ids" not in frontmatter:
        claim_ids = None
    else:
        value = frontmatter["evidence_claim_ids"]
        claim_ids = tuple(value) if isinstance(value, list) else None
    return (DecisionLineage(research.meta.slug, claim_ids),)


def _claim_joins(anchors: Sequence[AnchoredClaim]) -> tuple[dict[str, object], ...]:
    by_sentence: dict[str, list[AnchoredClaim]] = {}
    for anchor in anchors:
        by_sentence.setdefault(anchor.normalized_sentence, []).append(anchor)

    joins: list[dict[str, object]] = []
    for sentence, matches in sorted(by_sentence.items()):
        by_investigation: dict[str, list[AnchoredClaim]] = {}
        for anchor in matches:
            by_investigation.setdefault(anchor.investigation, []).append(anchor)
        for left, right in combinations(sorted(by_investigation), 2):
            pair = sorted(by_investigation[left] + by_investigation[right], key=_anchor_sort_key)
            source_identities = sorted({anchor.source_identity for anchor in pair})
            join: dict[str, object] = {
                "kind": ("quote_propagation" if len(source_identities) > 1 else "anchored_claim"),
                "investigations": [left, right],
                "provenance": "explicit",
                "normalized_sentence": sentence,
                "origins": [f"{anchor.investigation}:{anchor.claim_id}" for anchor in pair],
                "sources": [f"{anchor.investigation}:{anchor.source_id}" for anchor in pair],
                "source_identities": source_identities,
            }
            joins.append(join)
    return tuple(joins)


def _source_sort_key(source: SourceResolution) -> tuple[str, str]:
    return source.investigation, source.source_id


def _anchor_sort_key(anchor: AnchoredClaim) -> tuple[str, str]:
    return anchor.investigation, anchor.claim_id


def _evidence_claim_sort_key(claim: EvidenceClaim) -> tuple[str, str]:
    return claim.investigation, claim.claim_id


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _source_fact(
    source: SourceResolution,
    cause: Literal["unreachable", "changed", "expired"] | None,
    observation_source: Literal["network", "local_snapshot", "tier_policy"],
    mechanical_fact: str,
    observation_id: str,
    *,
    observation: NetworkSourceObservation | None = None,
) -> SourceHealthFact:
    reachability_by_fact = {
        "reachability_not_checked": "not checked",
        "retrieval_succeeded": "reachable",
        "retrieval_failed": "unreachable",
        "live_content_hash_mismatch": "reachable",
    }
    return SourceHealthFact(
        source_investigation=source.investigation,
        source_id=source.source_id,
        source_identity=source.identity,
        cause=cause,
        observation_source=observation_source,
        reachability=cast(
            Literal["not checked", "reachable", "unreachable"] | None,
            reachability_by_fact.get(mechanical_fact),
        ),
        mechanical_fact=mechanical_fact,
        observation_id=observation_id,
        observation_timestamp=(observation.retrieved_at.isoformat() if observation else None),
        observation_state=observation.state if observation else None,
    )


def _observation_id(source: SourceResolution, *identity: object) -> str:
    canonical = json.dumps(
        [
            source.investigation,
            source.source_id,
            source.url,
            source.identity,
            source.resolver,
            source.identifier,
            *identity,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return f"source-observation-{hashlib.sha256(canonical.encode()).hexdigest()}"


def _network_observation_id(
    source: SourceResolution,
    fact: str,
    observation: NetworkSourceObservation,
    *,
    baseline_snapshot_identity: tuple[object, ...] = (),
    outage_boundary: tuple[object, ...] = (),
) -> str:
    if fact == "unreachable":
        return _observation_id(
            source,
            "network",
            "unreachable_episode",
            observation.state,
            *outage_boundary,
        )
    return _observation_id(
        source,
        "network",
        fact,
        observation.state,
        observation.declared_url,
        observation.observed_url,
        tuple(
            (redirect.url, redirect.status_code, redirect.location, redirect.target_url)
            for redirect in observation.redirects
        ),
        observation.status_code,
        observation.content_eligible,
        observation.content_hash,
        observation.failure_kind,
        *baseline_snapshot_identity,
        *outage_boundary,
    )


def _most_recent_retrieved_boundary(
    observations: Sequence[NetworkSourceObservation],
) -> tuple[object, ...]:
    for observation in reversed(observations):
        if observation.state == "retrieved":
            return (
                "retrieved_boundary",
                observation.retrieved_at.isoformat(),
                observation.observed_url,
                observation.status_code,
                observation.content_hash,
            )
    return ()


def _degradation_acknowledgements(
    research: Mapping[str, Research],
    declarations: Mapping[tuple[str, str], Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    events: list[Mapping[str, object]] = []
    for investigation, item in sorted(research.items()):
        ledger = load_ledger(item.artifact_path("notes/sources/verification.yaml"))
        issues = [
            issue
            for issue in validate_ledger(ledger)
            if "degradation acknowledgement" in issue or "degradation_acknowledgements" in issue
        ]
        for entry in ledger["degradation_acknowledgements"]:
            source_id = str(entry.get("source_id") or "")
            if (
                entry.get("source_investigation") != investigation
                or (
                    investigation,
                    source_id,
                )
                not in declarations
            ):
                issues.append(
                    "degradation acknowledgement does not match its affected source record"
                )
        if issues:
            raise LedgerValidationError("invalid degradation acknowledgement: " + "; ".join(issues))
        events.extend(ledger["degradation_acknowledgements"])
    return tuple(events)


def _acknowledgement_key(event: Mapping[str, object]) -> tuple[str, str, str, str]:
    return (
        str(event["source_investigation"]),
        str(event["source_id"]),
        str(event["cause"]),
        str(event["observation_id"]),
    )


def _source_fact_sort_key(fact: SourceHealthFact) -> tuple[str, str, str, str]:
    return (
        fact.source_investigation,
        fact.source_id,
        fact.observation_source,
        fact.mechanical_fact,
    )


def _degradation_acknowledgement_key(item: DegradedSupportItem) -> tuple[str, str, str, str]:
    return (item.source_investigation, item.source_id, item.cause, item.observation_id)


def _degradation_observation_key(item: DegradedSupportItem) -> tuple[str, ...]:
    return (
        item.source_investigation,
        item.source_id,
        item.source_identity,
        item.cause,
        item.observation_source,
        item.mechanical_fact,
        item.observation_id,
    )


def _degraded_support_sort_key(
    item: DegradedSupportItem,
) -> tuple[str, str, str, str, str]:
    return (
        item.source_investigation,
        item.source_id,
        item.cause,
        item.decision_investigation,
        item.claim_id,
    )
