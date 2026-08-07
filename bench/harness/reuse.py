"""Typed reuse fixtures and disposable isolated scenario materialization."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import frontmatter
import yaml

from bench.harness.corpus import is_synthetic_url
from bench.harness.runspace import REPOSITORY_ROOT
from sdr import lifecycle
from sdr.claims import extract_claims
from sdr.research import Research
from sdr.schema import validate_evidence_claim_ids
from sdr.textual_anchoring import match_text
from sdr.verification import evaluate_source_snapshot

DEFAULT_REUSE_CORPUS_ROOT = REPOSITORY_ROOT / "bench" / "reuse-corpus"
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ARMS = ("baseline", "light", "full")
_HISTORY_CONDITIONS = ("history-present", "history-absent")
_KIND = "cross-investigation-reuse"
_NEGATIVE_RECORD_SHAPES = {
    ("citations",): frozenset(
        {"investigation", "source_id", "url", "identity", "resolver", "identifier"}
    ),
    ("claims",): frozenset(
        {
            "investigation",
            "claim_id",
            "source_id",
            "source_identity",
            "current_evidence_health",
        }
    ),
    ("lineage_statuses",): frozenset({"investigation", "status"}),
    ("dependent_decisions",): frozenset({"investigation", "claim_id"}),
    ("joins",): None,
}

_SOURCE_PROJECTION_KEYS = (
    "source_identity",
    "citations",
    "claims",
    "lineage_statuses",
    "dependent_decisions",
)
_SOURCE_RECORD_KEYS = {
    "citations": frozenset(
        {"investigation", "source_id", "url", "identity", "resolver", "identifier"}
    ),
    "claims": frozenset(
        {
            "investigation",
            "claim_id",
            "source_id",
            "source_identity",
            "current_evidence_health",
        }
    ),
    "lineage_statuses": frozenset({"investigation", "status"}),
    "dependent_decisions": frozenset({"investigation", "claim_id"}),
}


class ReuseCorpusError(ValueError):
    """Raised when a reuse fixture violates its declared contract."""


class ReuseMaterializationError(RuntimeError):
    """Raised when a reuse scenario cannot be isolated or seeds change."""


@dataclass(frozen=True)
class ArtifactFixture:
    """One persisted fixture file with its exact source-byte hash."""

    path: str
    sha256: str
    source: Path


@dataclass(frozen=True)
class InvestigationFixture:
    """One complete seed or focal investigation input."""

    id: str
    role: Literal["seed", "focal"]
    status: str
    schema_version: int
    evidence_claim_ids: tuple[str, ...]
    artifacts: tuple[ArtifactFixture, ...]


@dataclass(frozen=True)
class SourceDependencyProjection:
    """Complete canonical projection of ``cross source <id> --json``."""

    source_identity: str
    citations: tuple[Mapping[str, str], ...]
    claims: tuple[Mapping[str, str], ...]
    lineage_statuses: tuple[Mapping[str, str], ...]
    dependent_decisions: tuple[Mapping[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_identity": self.source_identity,
            "citations": [dict(record) for record in self.citations],
            "claims": [dict(record) for record in self.claims],
            "lineage_statuses": [dict(record) for record in self.lineage_statuses],
            "dependent_decisions": [dict(record) for record in self.dependent_decisions],
        }


@dataclass(frozen=True)
class DeriveProjection:
    """Canonical resolver and explicit-edge projection of ``cross derive --json``."""

    resolver_chain: tuple[Mapping[str, str], ...]
    joins: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolver_chain": [dict(record) for record in self.resolver_chain],
            "joins": [dict(record) for record in self.joins],
        }


PositiveProjection = SourceDependencyProjection | DeriveProjection


@dataclass(frozen=True)
class PositiveExpectation:
    """One exact structured query projection expected to be present."""

    id: str
    command: tuple[str, ...]
    projection: PositiveProjection


@dataclass(frozen=True)
class QualifiedAbsence:
    """One exact record that must be absent at a validated cross-source JSON path."""

    path: tuple[str, ...]
    record: Mapping[str, Any]


@dataclass(frozen=True)
class NegativeControl:
    """One exact query and non-empty set of prohibited structured records."""

    id: str
    command: tuple[str, ...]
    absent: tuple[QualifiedAbsence, ...]


@dataclass(frozen=True)
class ReuseScenario:
    """A reuse-specific, discriminated scenario declaration."""

    kind: Literal["cross-investigation-reuse"]
    id: str
    domain: str
    category: str
    arm: str
    history: str
    seeds: tuple[InvestigationFixture, ...]
    focal: InvestigationFixture
    positive_expectations: tuple[PositiveExpectation, ...]
    negative_controls: tuple[NegativeControl, ...]
    path: Path


@dataclass(frozen=True)
class ReuseCorpus:
    """A versioned reuse corpus in stable scenario order."""

    version: str
    scenario_schema_version: int
    scenarios: tuple[ReuseScenario, ...]
    root: Path

    def by_id(self, scenario_id: str) -> ReuseScenario:
        """Return one exact scenario identity."""
        for scenario in self.scenarios:
            if scenario.id == scenario_id:
                return scenario
        raise KeyError(scenario_id)


@dataclass(frozen=True)
class SeedImmutabilityEvidence:
    """Declared-source and materialized hashes plus verified teardown evidence."""

    pre_declared_seed_hashes: Mapping[str, str]
    post_declared_seed_hashes: Mapping[str, str]
    pre_materialized_seed_hashes: Mapping[str, str]
    post_materialized_seed_hashes: Mapping[str, str]
    unchanged: bool
    cleanup_deleted: bool
    cleanup_error: str | None


@dataclass(frozen=True)
class MaterializedReuseScenario:
    """One live disposable scenario root while its context is open."""

    scenario_id: str
    repetition: int
    path: Path
    research_root: Path
    seed_roots: Mapping[str, Path]
    focal_root: Path
    pre_materialized_seed_hashes: Mapping[str, str]

    def verify_seed_hashes(self) -> Mapping[str, str]:
        """Hash current seed bytes without changing their permissions or contents."""
        for root in self.seed_roots.values():
            _reject_symlink_components(root, within=self.path)
        return MappingProxyType(
            {seed_id: _tree_sha256(root) for seed_id, root in sorted(self.seed_roots.items())}
        )


class PreparedReuseScenario:
    """Context-managed scenario retaining post-teardown immutability evidence."""

    def __init__(self, scenario: ReuseScenario, repetition: int, parent: Path | None) -> None:
        if repetition < 0:
            raise ReuseMaterializationError("repetition must be non-negative")
        self.scenario = scenario
        self.repetition = repetition
        self.parent = None if parent is None else parent.resolve()
        if self.parent is not None and self.parent.is_relative_to(REPOSITORY_ROOT):
            raise ReuseMaterializationError("reuse scenario roots must be outside the repository")
        self._path: Path | None = None
        self._materialized: MaterializedReuseScenario | None = None
        self._pre_declared_seed_hashes: Mapping[str, str] = MappingProxyType({})
        self.evidence: SeedImmutabilityEvidence | None = None

    def __enter__(self) -> MaterializedReuseScenario:
        if self.parent is not None and not self.parent.is_dir():
            raise ReuseMaterializationError(f"materialization parent does not exist: {self.parent}")
        prefix = f"sdr-reuse-{self.scenario.id}-r{self.repetition}-"
        path = Path(
            tempfile.mkdtemp(prefix=prefix, dir=None if self.parent is None else str(self.parent))
        ).resolve()
        self._path = path
        try:
            if path.is_relative_to(REPOSITORY_ROOT):
                raise ReuseMaterializationError(
                    f"reuse scenario root must be outside the repository: {path}"
                )
            research_root = path / "research"
            research_root.mkdir()
            self._pre_declared_seed_hashes = _declared_seed_hashes(self.scenario.seeds)
            seed_roots: dict[str, Path] = {}
            if self.scenario.history == "history-present":
                for seed in self.scenario.seeds:
                    target = research_root / seed.id
                    _materialize_investigation(seed, target)
                    _make_read_only(target)
                    seed_roots[seed.id] = target
            focal_root = research_root / self.scenario.focal.id
            _materialize_investigation(self.scenario.focal, focal_root)
            pre_hashes = MappingProxyType(
                {seed_id: _tree_sha256(root) for seed_id, root in sorted(seed_roots.items())}
            )
            self._materialized = MaterializedReuseScenario(
                scenario_id=self.scenario.id,
                repetition=self.repetition,
                path=path,
                research_root=research_root,
                seed_roots=MappingProxyType(seed_roots),
                focal_root=focal_root,
                pre_materialized_seed_hashes=pre_hashes,
            )
            return self._materialized
        except BaseException as error:
            cleanup_error = _cleanup_path(path)
            if cleanup_error is not None:
                raise ReuseMaterializationError(cleanup_error) from error
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> bool | None:
        materialized = self._materialized
        if materialized is None or self._path is None:
            return None
        post_declared: Mapping[str, str] = MappingProxyType({})
        post_materialized: Mapping[str, str] = MappingProxyType({})
        verification_error: BaseException | None = None
        cleanup_error: str | None = None
        try:
            post_declared = _declared_seed_hashes(self.scenario.seeds)
            post_materialized = materialized.verify_seed_hashes()
        except BaseException as error:
            verification_error = error
        finally:
            cleanup_error = _cleanup_path(self._path)
        unchanged = verification_error is None and (
            dict(self._pre_declared_seed_hashes) == dict(post_declared)
            and dict(materialized.pre_materialized_seed_hashes) == dict(post_materialized)
        )
        cleanup_deleted = not os.path.lexists(self._path)
        self.evidence = SeedImmutabilityEvidence(
            pre_declared_seed_hashes=self._pre_declared_seed_hashes,
            post_declared_seed_hashes=post_declared,
            pre_materialized_seed_hashes=materialized.pre_materialized_seed_hashes,
            post_materialized_seed_hashes=post_materialized,
            unchanged=unchanged,
            cleanup_deleted=cleanup_deleted,
            cleanup_error=cleanup_error,
        )
        if cleanup_error is not None:
            raise ReuseMaterializationError(cleanup_error) from verification_error or exc_value
        if verification_error is not None:
            if isinstance(verification_error, ReuseMaterializationError):
                raise verification_error from exc_value
            raise ReuseMaterializationError(
                f"reuse scenario {self.scenario.id!r} seed verification failed: "
                f"{verification_error}"
            ) from exc_value
        if not unchanged:
            raise ReuseMaterializationError(
                f"reuse scenario {self.scenario.id!r} changed immutable seed bytes"
            ) from exc_value
        return None


def prepare_reuse_scenario(
    scenario: ReuseScenario, *, repetition: int, parent: Path | None = None
) -> PreparedReuseScenario:
    """Prepare one disposable external root without starting a process or reading the host."""
    return PreparedReuseScenario(scenario, repetition, parent)


def load_reuse_corpus(root: Path | str | None = None) -> ReuseCorpus:
    """Load and validate the separate cross-investigation reuse corpus."""
    corpus_root = Path(root or DEFAULT_REUSE_CORPUS_ROOT).resolve()
    manifest = _read_mapping(corpus_root / "corpus.yaml")
    version = _required_string(manifest, "version", "reuse corpus")
    schema_version = manifest.get("scenario_schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ReuseCorpusError("reuse corpus scenario_schema_version must be 1")
    scenarios_dir = corpus_root / "scenarios"
    paths = sorted(scenarios_dir.glob("*.yaml")) if scenarios_dir.is_dir() else []
    if not paths:
        raise ReuseCorpusError("reuse corpus declares no scenarios")
    scenarios = tuple(_load_scenario(path, corpus_root) for path in paths)
    identities = [scenario.id for scenario in scenarios]
    if len(identities) != len(set(identities)):
        raise ReuseCorpusError("reuse scenario identities must be unique")
    check_ids = [
        check.id
        for scenario in scenarios
        for check in (*scenario.positive_expectations, *scenario.negative_controls)
    ]
    if len(check_ids) != len(set(check_ids)):
        raise ReuseCorpusError("reuse corpus check ids must be unique across all declarations")
    if not any(scenario.domain != "software-engineering" for scenario in scenarios):
        raise ReuseCorpusError("reuse corpus must include a non-software domain")
    return ReuseCorpus(version, schema_version, scenarios, corpus_root)


def _load_scenario(path: Path, root: Path) -> ReuseScenario:
    data = _read_mapping(path)
    identity = _required_string(data, "id", path.name)
    if identity != path.stem or not _ID_PATTERN.fullmatch(identity):
        raise ReuseCorpusError(f"scenario {identity!r}: id must match its kebab-case file stem")
    kind = data.get("kind")
    if kind != _KIND:
        raise ReuseCorpusError(f"scenario {identity!r}: kind must be {_KIND!r}")
    domain = _required_string(data, "domain", identity)
    category = _required_string(data, "category", identity)
    arm = _required_string(data, "arm", identity)
    if arm not in _ARMS:
        raise ReuseCorpusError(f"scenario {identity!r}: arm must be one of {_ARMS}")
    history = _required_string(data, "history", identity)
    if history not in _HISTORY_CONDITIONS:
        raise ReuseCorpusError(
            f"scenario {identity!r}: history must be one of {_HISTORY_CONDITIONS}"
        )
    raw_seeds = data.get("seeds")
    if not isinstance(raw_seeds, list) or not raw_seeds:
        raise ReuseCorpusError(f"scenario {identity!r}: at least one seed is required")
    seeds = tuple(_load_investigation(item, "seed", identity, root) for item in raw_seeds)
    if "focal" not in data or "focals" in data:
        raise ReuseCorpusError(
            f"scenario {identity!r}: exactly one focal investigation is required"
        )
    focal = _load_investigation(data["focal"], "focal", identity, root)
    investigation_ids = [seed.id for seed in seeds] + [focal.id]
    if len(investigation_ids) != len(set(investigation_ids)):
        raise ReuseCorpusError(f"scenario {identity!r}: seed and focal identities must be unique")
    positives = _load_positive_expectations(data.get("positive_expectations"), identity)
    negatives = _load_negative_controls(data.get("negative_controls"), identity)
    check_ids = [item.id for item in positives] + [item.id for item in negatives]
    if len(check_ids) != len(set(check_ids)):
        raise ReuseCorpusError(f"scenario {identity!r}: check ids must be unique")
    return ReuseScenario(
        kind=_KIND,
        id=identity,
        domain=domain,
        category=category,
        arm=arm,
        history=history,
        seeds=seeds,
        focal=focal,
        positive_expectations=positives,
        negative_controls=negatives,
        path=path,
    )


def _load_investigation(
    raw: object, role: Literal["seed", "focal"], scenario_id: str, root: Path
) -> InvestigationFixture:
    if not isinstance(raw, dict):
        raise ReuseCorpusError(f"scenario {scenario_id!r}: {role} must be a mapping")
    identity = _required_string(raw, "id", f"scenario {scenario_id} {role}")
    if not _ID_PATTERN.fullmatch(identity):
        raise ReuseCorpusError(f"scenario {scenario_id!r}: invalid {role} id {identity!r}")
    if raw.get("role") != role:
        raise ReuseCorpusError(f"scenario {scenario_id!r}: {identity!r} role must be {role!r}")
    status = _required_string(raw, "status", identity)
    if role == "seed" and status != "done":
        raise ReuseCorpusError(f"scenario {scenario_id!r}: seed {identity!r} status must be done")
    if role == "focal" and status == "done":
        raise ReuseCorpusError(f"scenario {scenario_id!r}: focal {identity!r} must remain writable")
    schema_version = raw.get("schema_version")
    if type(schema_version) is not int or schema_version != 2:
        raise ReuseCorpusError(f"scenario {scenario_id!r}: {identity!r} schema_version must be 2")
    try:
        claim_ids = validate_evidence_claim_ids(raw.get("evidence_claim_ids"))
    except ValueError as error:
        raise ReuseCorpusError(
            f"scenario {scenario_id!r}: {identity!r} invalid evidence_claim_ids: {error}"
        ) from error
    if role == "seed" and not claim_ids:
        raise ReuseCorpusError(
            f"scenario {scenario_id!r}: seed {identity!r} requires evidence_claim_ids"
        )
    source_root = (root / _required_string(raw, "source", identity)).resolve()
    if not source_root.is_relative_to(root) or not source_root.is_dir():
        raise ReuseCorpusError(f"scenario {scenario_id!r}: invalid fixture source for {identity!r}")
    artifacts = _load_artifacts(raw.get("artifacts"), identity, source_root)
    fixture = InvestigationFixture(identity, role, status, schema_version, claim_ids, artifacts)
    _validate_investigation_fixture(fixture)
    return fixture


def _load_artifacts(raw: object, identity: str, source_root: Path) -> tuple[ArtifactFixture, ...]:
    if not isinstance(raw, list) or not raw:
        raise ReuseCorpusError(f"investigation {identity!r}: artifacts must be non-empty")
    artifacts: list[ArtifactFixture] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReuseCorpusError(f"investigation {identity!r}: artifact must be a mapping")
        relative = _required_string(entry, "path", identity)
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or relative in seen:
            raise ReuseCorpusError(
                f"investigation {identity!r}: invalid artifact path {relative!r}"
            )
        seen.add(relative)
        source = (source_root / candidate).resolve()
        if not source.is_relative_to(source_root) or not source.is_file():
            raise ReuseCorpusError(f"investigation {identity!r}: missing artifact {relative!r}")
        expected = entry.get("sha256")
        actual = _file_sha256(source)
        if (
            not isinstance(expected, str)
            or not _SHA256_PATTERN.fullmatch(expected)
            or expected != actual
        ):
            raise ReuseCorpusError(
                f"investigation {identity!r}: artifact {relative!r} persisted-byte hash mismatch"
            )
        artifacts.append(ArtifactFixture(relative, expected, source))
    actual_files = {
        path.relative_to(source_root).as_posix()
        for path in source_root.rglob("*")
        if path.is_file()
    }
    if actual_files != seen:
        raise ReuseCorpusError(
            f"investigation {identity!r}: artifact manifest does not exactly cover fixture files"
        )
    return tuple(artifacts)


def _validate_investigation_fixture(fixture: InvestigationFixture) -> None:
    files = {artifact.path: artifact.source for artifact in fixture.artifacts}
    metadata_path = files.get("sdr.yaml")
    if metadata_path is None:
        raise ReuseCorpusError(f"investigation {fixture.id!r}: missing sdr.yaml")
    metadata = _read_mapping(metadata_path)
    if (
        metadata.get("slug") != fixture.id
        or metadata.get("schema_version") != fixture.schema_version
        or metadata.get("status") != fixture.status
    ):
        raise ReuseCorpusError(
            f"investigation {fixture.id!r}: declared identity/status/schema differ from sdr.yaml"
        )
    try:
        research = Research.load(metadata_path.parent)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise ReuseCorpusError(
            f"investigation {fixture.id!r}: invalid research metadata"
        ) from error
    _validate_snapshots(fixture.id, research, files)
    decision_path = files.get("decision-memo.md")
    if fixture.role == "seed" and decision_path is None:
        raise ReuseCorpusError(
            f"investigation {fixture.id!r}: completed seed lacks decision-memo.md"
        )
    if decision_path is not None:
        decision = frontmatter.load(str(decision_path))
        try:
            persisted_ids = validate_evidence_claim_ids(decision.metadata.get("evidence_claim_ids"))
        except ValueError as error:
            raise ReuseCorpusError(
                f"investigation {fixture.id!r}: decision has invalid evidence_claim_ids: {error}"
            ) from error
        if persisted_ids != fixture.evidence_claim_ids:
            raise ReuseCorpusError(
                f"investigation {fixture.id!r}: declared evidence_claim_ids differ from decision"
            )
        _validate_claim_lineage(fixture, files)
        if fixture.role == "seed":
            if "transfer" not in research.meta.validation:
                raise ReuseCorpusError(
                    f"investigation {fixture.id!r}: completed lineage requires transfer validation"
                )
            issues = lifecycle.check_consistency(research)
            if issues:
                raise ReuseCorpusError(
                    f"investigation {fixture.id!r}: transfer hash consistency failed: "
                    + "; ".join(issues)
                )
    for artifact in fixture.artifacts:
        for url in _urls(artifact.source.read_text(encoding="utf-8")):
            if not is_synthetic_url(url):
                raise ReuseCorpusError(
                    f"investigation {fixture.id!r}: artifact {artifact.path!r} uses "
                    "non-reserved URL"
                )


def _validate_snapshots(identity: str, research: Research, files: Mapping[str, Path]) -> None:
    declarations: dict[str, str] = {}
    for relative, source in sorted(files.items()):
        path = Path(relative)
        if path.parent != Path("notes") or path.suffix != ".md":
            continue
        post = frontmatter.load(str(source))
        for declaration in post.metadata.get("sources") or []:
            if not isinstance(declaration, dict):
                continue
            source_id = str(declaration.get("id") or "")
            url = str(declaration.get("url") or "")
            if source_id in declarations and declarations[source_id] != url:
                raise ReuseCorpusError(
                    f"investigation {identity!r}: conflicting source declaration {source_id!r}"
                )
            declarations[source_id] = url
    snapshot_ids = {
        Path(relative).parent.name
        for relative in files
        if relative.endswith("/meta.yaml") and "/notes/sources/" in f"/{relative}"
    }
    if snapshot_ids != set(declarations):
        raise ReuseCorpusError(
            f"investigation {identity!r}: snapshot set differs from source declarations"
        )
    for source_id, declared_url in sorted(declarations.items()):
        if not is_synthetic_url(declared_url):
            raise ReuseCorpusError(
                f"investigation {identity!r}: source declaration {source_id!r} is not synthetic"
            )
        health = evaluate_source_snapshot(research, source_id, declared_url=declared_url)
        if not health.eligible or not health.content_hash_matches:
            raise ReuseCorpusError(
                f"investigation {identity!r}: snapshot {source_id!r} lacks current provenance "
                "or does not match its source declaration"
            )


def _validate_claim_lineage(fixture: InvestigationFixture, files: Mapping[str, Path]) -> None:
    claims = {}
    for path, source in sorted(files.items()):
        if Path(path).parent != Path("notes") or Path(path).suffix != ".md":
            continue
        for claim in extract_claims(source.read_text(encoding="utf-8"), note_path=path):
            claims[claim.id] = claim
    for claim_id in fixture.evidence_claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            raise ReuseCorpusError(
                f"investigation {fixture.id!r}: evidence_claim_ids contains unknown claim "
                f"{claim_id}"
            )
        content = files.get(f"notes/sources/{claim.source_id}/content.md")
        if content is None or match_text(claim.text, content.read_text(encoding="utf-8")) is None:
            raise ReuseCorpusError(
                f"investigation {fixture.id!r}: claim {claim_id} is not anchored in its snapshot"
            )


def _load_positive_expectations(raw: object, scenario_id: str) -> tuple[PositiveExpectation, ...]:
    if not isinstance(raw, list) or not raw:
        raise ReuseCorpusError(f"scenario {scenario_id!r}: positive expectations must be non-empty")
    values = []
    for entry in raw:
        if not isinstance(entry, dict) or not isinstance(entry.get("projection"), dict):
            raise ReuseCorpusError(
                f"scenario {scenario_id!r}: positive projection must be exact and non-empty"
            )
        command = _command(entry.get("command"), scenario_id)
        values.append(
            PositiveExpectation(
                _required_string(entry, "id", scenario_id),
                command,
                _load_positive_projection(entry["projection"], command, scenario_id),
            )
        )
    return tuple(values)


def _load_positive_projection(
    raw: dict[str, Any], command: tuple[str, ...], scenario_id: str
) -> PositiveProjection:
    if command[:2] == ("cross", "source"):
        if set(raw) != set(_SOURCE_PROJECTION_KEYS):
            missing = [key for key in _SOURCE_PROJECTION_KEYS if key not in raw]
            detail = f"missing {', '.join(missing)}" if missing else "unsupported fields"
            raise ReuseCorpusError(f"scenario {scenario_id!r}: source projection {detail}")
        source_identity = raw["source_identity"]
        if not isinstance(source_identity, str) or not source_identity:
            raise ReuseCorpusError(f"scenario {scenario_id!r}: source_identity must be non-empty")
        records = {
            key: _projection_records(raw[key], key, _SOURCE_RECORD_KEYS[key], scenario_id)
            for key in _SOURCE_RECORD_KEYS
        }
        return SourceDependencyProjection(
            source_identity,
            records["citations"],
            records["claims"],
            records["lineage_statuses"],
            records["dependent_decisions"],
        )
    if set(raw) != {"resolver_chain", "joins"}:
        raise ReuseCorpusError(
            f"scenario {scenario_id!r}: derive projection requires resolver_chain and joins"
        )
    resolver_chain = _projection_records(
        raw["resolver_chain"], "resolver_chain", frozenset({"name", "version"}), scenario_id
    )
    joins = _projection_records(raw["joins"], "joins", None, scenario_id)
    for join in joins:
        if (
            join.get("provenance") != "explicit"
            or not isinstance(join.get("kind"), str)
            or not isinstance(join.get("investigations"), list)
            or len(join["investigations"]) != 2
            or not isinstance(join.get("origins"), list)
            or not join["origins"]
        ):
            raise ReuseCorpusError(
                f"scenario {scenario_id!r}: joins must contain canonical explicit edge provenance"
            )
    return DeriveProjection(resolver_chain, joins)


def _projection_records(
    raw: object,
    field: str,
    keys: frozenset[str] | None,
    scenario_id: str,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list) or not raw:
        raise ReuseCorpusError(f"scenario {scenario_id!r}: {field} must be non-empty")
    records: list[Mapping[str, Any]] = []
    for record in raw:
        if not isinstance(record, dict) or (keys is not None and set(record) != keys):
            raise ReuseCorpusError(f"scenario {scenario_id!r}: invalid {field} record shape")
        if any(value in (None, "", [], {}) for value in record.values()):
            raise ReuseCorpusError(f"scenario {scenario_id!r}: {field} records must be non-empty")
        records.append(MappingProxyType(dict(record)))
    return tuple(records)


def _load_negative_controls(raw: object, scenario_id: str) -> tuple[NegativeControl, ...]:
    if not isinstance(raw, list) or not raw:
        raise ReuseCorpusError(f"scenario {scenario_id!r}: negative controls must be non-empty")
    controls = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReuseCorpusError(f"scenario {scenario_id!r}: negative control must be a mapping")
        raw_absent = entry.get("absent")
        if not isinstance(raw_absent, list) or not raw_absent:
            raise ReuseCorpusError(
                f"scenario {scenario_id!r}: negative control absent must be non-empty"
            )
        absent = []
        for item in raw_absent:
            if not isinstance(item, dict):
                raise ReuseCorpusError(f"scenario {scenario_id!r}: absent item must be a mapping")
            raw_path = item.get("path")
            path = (raw_path,) if isinstance(raw_path, str) else ()
            if path not in _NEGATIVE_RECORD_SHAPES:
                raise ReuseCorpusError(
                    f"scenario {scenario_id!r}: unsupported negative JSON path {raw_path!r}"
                )
            record = item.get("record")
            if not isinstance(record, dict) or not record:
                raise ReuseCorpusError(
                    f"scenario {scenario_id!r}: negative record must be non-empty"
                )
            expected_shape = _NEGATIVE_RECORD_SHAPES[path]
            if (expected_shape is not None and set(record) != expected_shape) or not all(
                value not in (None, "", [], {}) for value in record.values()
            ):
                raise ReuseCorpusError(
                    f"scenario {scenario_id!r}: negative record has unsupported shape "
                    f"at {raw_path!r}"
                )
            absent.append(QualifiedAbsence(path, MappingProxyType(dict(record))))
        controls.append(
            NegativeControl(
                _required_string(entry, "id", scenario_id),
                _command(entry.get("command"), scenario_id),
                tuple(absent),
            )
        )
    return tuple(controls)


def _command(raw: object, identity: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw or not all(isinstance(token, str) for token in raw):
        raise ReuseCorpusError(f"scenario {identity!r}: command must be a non-empty argv list")
    command = tuple(raw)
    if not (
        (len(command) == 4 and command[:2] == ("cross", "source") and command[-1] == "--json")
        or command == ("cross", "derive", "--json")
    ):
        raise ReuseCorpusError(
            f"scenario {identity!r}: command must be cross source <identity> --json "
            "or cross derive --json"
        )
    return command


def _materialize_investigation(fixture: InvestigationFixture, target: Path) -> None:
    target.mkdir()
    for artifact in fixture.artifacts:
        destination = target / artifact.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        body = artifact.source.read_bytes()
        if hashlib.sha256(body).hexdigest() != artifact.sha256:
            raise ReuseMaterializationError(
                f"fixture bytes changed before materialization: {fixture.id}/{artifact.path}"
            )
        destination.write_bytes(body)


def _make_read_only(root: Path) -> None:
    for path in sorted(_tree_entries(root), reverse=True):
        mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        kind = path.lstat().st_mode
        if stat.S_ISDIR(kind):
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        os.chmod(path, mode, follow_symlinks=False)
    os.chmod(
        root,
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
        follow_symlinks=False,
    )


def _make_writable(root: Path) -> None:
    if not os.path.lexists(root):
        return
    if stat.S_ISLNK(root.lstat().st_mode):
        return
    os.chmod(root, root.lstat().st_mode | stat.S_IWUSR | stat.S_IXUSR, follow_symlinks=False)
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        for name in [*names, *filenames]:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                continue
            writable = mode | stat.S_IWUSR | (stat.S_IXUSR if stat.S_ISDIR(mode) else 0)
            try:
                os.chmod(path, writable, follow_symlinks=False)
            except FileNotFoundError:
                pass


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _tree_entries(root):
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_file_sha256(path)))
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if path.is_symlink():
            raise ReuseMaterializationError(f"symlink is not permitted: {path}") from error
        raise
    digest = hashlib.sha256()
    try:
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise ReuseMaterializationError(f"non-regular fixture file is not permitted: {path}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _tree_entries(root: Path) -> list[Path]:
    root_mode = root.lstat().st_mode
    if stat.S_ISLNK(root_mode):
        raise ReuseMaterializationError(f"symlink is not permitted: {root}")
    if not stat.S_ISDIR(root_mode):
        raise ReuseMaterializationError(f"tree root is not a directory: {root}")
    entries: list[Path] = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        for name in [*names, *filenames]:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ReuseMaterializationError(f"symlink is not permitted: {path}")
            if not stat.S_ISDIR(mode) and not stat.S_ISREG(mode):
                raise ReuseMaterializationError(f"special file is not permitted: {path}")
            entries.append(path)
    return sorted(entries)


def _reject_symlink_components(path: Path, *, within: Path) -> None:
    try:
        relative = path.relative_to(within)
    except ValueError as error:
        raise ReuseMaterializationError(f"path escapes reuse scenario root: {path}") from error
    current = within
    for segment in relative.parts:
        current /= segment
        if stat.S_ISLNK(current.lstat().st_mode):
            raise ReuseMaterializationError(f"symlink is not permitted: {current}")


def _declared_seed_hashes(
    seeds: tuple[InvestigationFixture, ...],
) -> Mapping[str, str]:
    hashes: dict[str, str] = {}
    for seed in sorted(seeds, key=lambda item: item.id):
        digest = hashlib.sha256()
        for artifact in sorted(seed.artifacts, key=lambda item: item.path):
            relative = artifact.path.encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(bytes.fromhex(artifact.sha256))
            digest.update(bytes.fromhex(_file_sha256(artifact.source)))
        hashes[seed.id] = digest.hexdigest()
    return MappingProxyType(hashes)


def _cleanup_path(path: Path) -> str | None:
    try:
        if os.path.lexists(path):
            if stat.S_ISLNK(path.lstat().st_mode):
                path.unlink()
            else:
                _make_writable(path)
                shutil.rmtree(path)
        if os.path.lexists(path):
            return f"reuse scenario cleanup did not delete {path}"
    except OSError as error:
        return f"reuse scenario cleanup failed for {path}: {error}"
    return None


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReuseCorpusError(f"missing reuse corpus file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ReuseCorpusError(f"invalid reuse YAML {path.name}: {error}") from error
    if not isinstance(data, dict):
        raise ReuseCorpusError(f"reuse file {path.name} must contain a mapping")
    return data


def _required_string(data: Mapping[str, Any], key: str, identity: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReuseCorpusError(f"{identity}: {key} must be a non-empty string")
    return value


def _urls(text: str) -> Iterator[str]:
    yield from re.findall(r"https?://[^\s<>'\"\[\]]+", text)
