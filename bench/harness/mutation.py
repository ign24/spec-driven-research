"""Disposable source mutation mechanics for blocking-control actor runs."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import TracebackType

from bench.harness.actor import (
    ActorKind,
    ActorResult,
    CheckoutIntegrityEvidence,
    RunRequest,
    ScriptedActor,
)
from bench.harness.arms import ArmOutcome, ArmRun, execute_arms
from bench.harness.controls import BlockingControl, control_for_defect
from bench.harness.corpus import BaselineProvenance, Corpus, CorpusItem
from bench.harness.detection import DefectScore, DetectionOutcome, DetectionScore, score_detection
from bench.harness.runspace import (
    REPOSITORY_ROOT,
    Runspace,
    SubprocessEnvironment,
    build_mutation_environment,
    build_scripted_environment,
)


class MutationError(RuntimeError):
    """Raised when a mutation declaration cannot be applied safely and exactly."""


class MutationValidationCode(StrEnum):
    """Typed reasons an exact baseline/mutation comparison can fail."""

    DUPLICATE_IDENTITY = "duplicate-identity"
    IDENTITY_MISMATCH = "identity-mismatch"
    UNCHANGED_DETECTION_PROJECTION = "unchanged-detection-projection"
    NO_ATTRIBUTABLE_BASELINE_CATCH = "no-attributable-baseline-catch"
    EXPECTED_CATCH_RETAINED = "expected-catch-retained"
    UNRELATED_OUTCOME_CHANGED = "unrelated-outcome-changed"
    UNHEALTHY_REPORTING_CONTROL = "unhealthy-reporting-control"
    ATTRIBUTION_MISMATCH = "attribution-mismatch"
    DETECTION_BASIS_CHANGED = "detection-basis-changed"
    CLEAN_FALSE_POSITIVES_CHANGED = "clean-false-positives-changed"


class MutationCoverageCode(StrEnum):
    """Typed reasons a blocking-control coverage audit can fail."""

    NONCANONICAL_CONTROL_ORDER = "noncanonical-control-order"
    UNCOVERED_CONTROL = "uncovered-control"
    AMBIGUOUS_COVERAGE = "ambiguous-coverage"
    MUTATION_CONTROL_MISMATCH = "mutation-control-mismatch"


@dataclass(frozen=True, order=True)
class DetectionIdentity:
    """Exact identity of one planted-defect outcome in a scripted run."""

    item: str
    arm: str
    repetition: int
    defect: str

    def as_tuple(self) -> tuple[str, str, int, str]:
        """Return the required comparison identity without aggregation."""
        return (self.item, self.arm, self.repetition, self.defect)


@dataclass(frozen=True, order=True)
class FalsePositiveIdentity:
    """Stable identity of one clean-item blocking finding."""

    item: str
    arm: str
    repetition: int
    interface: str
    control: str
    command_index: int
    json_path: str


class MutationValidationError(MutationError):
    """An inspectable exact-comparison failure for one source mutation."""

    def __init__(
        self,
        code: MutationValidationCode,
        message: str,
        *,
        identities: Sequence[DetectionIdentity] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.identities = tuple(identities)


class MutationCoverageError(MutationError):
    """An inspectable canonical blocking-control coverage failure."""

    def __init__(self, code: MutationCoverageCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExactReplacement:
    """One byte-exact source replacement with an optional explicit match count."""

    before: bytes
    after: bytes
    expected_matches: int | None = None

    def __post_init__(self) -> None:
        if not self.before:
            raise MutationError("the replacement match bytes cannot be empty")
        if self.before == self.after:
            raise MutationError("an exact replacement must change bytes")
        if self.expected_matches is not None and self.expected_matches < 1:
            raise MutationError("expected_matches must be at least 1 when declared")


@dataclass(frozen=True)
class MutationDeclaration:
    """A single-control, single-target source mutation and its defect attribution."""

    name: str
    blocking_control: BlockingControl
    target: Path
    transformation: ExactReplacement
    defect_kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise MutationError("a mutation name is required")
        if not isinstance(self.blocking_control, BlockingControl):
            raise MutationError("a mutation must name exactly one blocking control")
        normalized = _validate_target(self.target)
        object.__setattr__(self, "target", normalized)
        if not self.defect_kinds or any(not kind.strip() for kind in self.defect_kinds):
            raise MutationError("at least one attributable defect kind is required")
        if len(set(self.defect_kinds)) != len(self.defect_kinds):
            raise MutationError("attributable defect kinds must be unique")
        for defect in self.defect_kinds:
            declared = control_for_defect(defect)
            if declared is None:
                raise MutationError(
                    f"defect kind {defect!r} has no canonical blocking-control attribution"
                )
            if declared.blocking_control is not self.blocking_control:
                raise MutationError(
                    f"defect kind {defect!r} belongs to blocking control "
                    f"{declared.blocking_control.value!r}, not {self.blocking_control.value!r}"
                )


@dataclass(frozen=True)
class MutationValidation:
    """Successful exact comparison of one mutated projection with its baseline."""

    declaration: MutationDeclaration
    lost_catches: tuple[DetectionIdentity, ...]
    unchanged: tuple[DetectionIdentity, ...]


@dataclass(frozen=True)
class BlockingControlCoverage:
    """A control covered by source mutations or one reviewable infeasibility reason."""

    control: BlockingControl
    mutations: tuple[MutationDeclaration, ...] = ()
    infeasibility_reason: str | None = None


@dataclass(frozen=True)
class BlockingControlCoverageReport:
    """Canonical, complete mutation-coverage report."""

    entries: tuple[BlockingControlCoverage, ...]


def validate_mutation_detection(
    declaration: MutationDeclaration,
    baseline: Sequence[DetectionScore],
    mutated: Sequence[DetectionScore],
) -> MutationValidation:
    """Require exact attributable catch loss and byte-for-byte unrelated score stability."""
    baseline_projection = _detection_projection(baseline, label="baseline")
    mutated_projection = _detection_projection(mutated, label="mutated")
    baseline_ids = set(baseline_projection)
    mutated_ids = set(mutated_projection)
    if baseline_ids != mutated_ids:
        mismatched = tuple(sorted(baseline_ids ^ mutated_ids))
        raise MutationValidationError(
            MutationValidationCode.IDENTITY_MISMATCH,
            f"mutation {declaration.name!r} changed exact detection identities: "
            f"{[identity.as_tuple() for identity in mismatched]}",
            identities=mismatched,
        )
    _validate_score_projection(baseline, mutated, declaration)
    if baseline_projection == mutated_projection:
        raise MutationValidationError(
            MutationValidationCode.UNCHANGED_DETECTION_PROJECTION,
            f"mutation {declaration.name!r} left the detection projection unchanged; "
            "an inert control or unread scorer remains unresolved",
            identities=tuple(sorted(baseline_ids)),
        )

    attributable = {
        identity
        for identity, score in baseline_projection.items()
        if identity.defect in declaration.defect_kinds and score.outcome is DetectionOutcome.CAUGHT
    }
    if not attributable:
        raise MutationValidationError(
            MutationValidationCode.NO_ATTRIBUTABLE_BASELINE_CATCH,
            f"mutation {declaration.name!r} has no baseline catch attributable to declared "
            f"defect kinds {declaration.defect_kinds!r}",
        )

    for identity in sorted(attributable):
        declared = control_for_defect(identity.defect)
        assert declared is not None
        baseline_defect = baseline_projection[identity]
        finding = baseline_defect.finding
        if (
            baseline_defect.reporting_control != declared.reporting_identity
            or finding is None
            or (finding.interface, finding.control)
            != (declared.interface, declared.reporting_control)
        ):
            raise MutationValidationError(
                MutationValidationCode.ATTRIBUTION_MISMATCH,
                f"baseline finding for {identity.as_tuple()} does not match canonical "
                f"attribution {declared.reporting_identity!r}",
                identities=(identity,),
            )
        mutated_score = _score_for_identity(mutated, identity)
        execution = next(
            (
                entry
                for entry in mutated_score.reporting_executions
                if entry.defect == identity.defect
                and (entry.interface, entry.control)
                == (declared.interface, declared.reporting_control)
            ),
            None,
        )
        if (
            execution is None
            or not execution.healthy
            or not execution.exercised
            or mutated_projection[identity].outcome is DetectionOutcome.NOT_EXERCISED
        ):
            reason = (
                mutated_projection[identity].reason if execution is None else execution.detail
            ) or "exact reporting execution evidence is absent"
            raise MutationValidationError(
                MutationValidationCode.UNHEALTHY_REPORTING_CONTROL,
                f"mutation {declaration.name!r} did not execute healthy structured reporting "
                f"for {declared.reporting_identity!r} at {identity.as_tuple()}: {reason}",
                identities=(identity,),
            )

    retained = tuple(
        sorted(
            identity
            for identity in attributable
            if mutated_projection[identity].outcome is DetectionOutcome.CAUGHT
        )
    )
    if retained:
        raise MutationValidationError(
            MutationValidationCode.EXPECTED_CATCH_RETAINED,
            f"mutation {declaration.name!r} retained attributable baseline catches: "
            f"{[identity.as_tuple() for identity in retained]}",
            identities=retained,
        )

    changed_unrelated = tuple(
        sorted(
            identity
            for identity in baseline_ids - attributable
            if baseline_projection[identity] != mutated_projection[identity]
        )
    )
    if changed_unrelated:
        changes = ", ".join(
            f"{identity.as_tuple()}: {baseline_projection[identity].outcome.value} -> "
            f"{mutated_projection[identity].outcome.value}"
            for identity in changed_unrelated
        )
        raise MutationValidationError(
            MutationValidationCode.UNRELATED_OUTCOME_CHANGED,
            f"mutation {declaration.name!r} changed outcomes attributable only to other "
            f"controls: {changes}",
            identities=changed_unrelated,
        )

    return MutationValidation(
        declaration=declaration,
        lost_catches=tuple(sorted(attributable)),
        unchanged=tuple(sorted(baseline_ids - attributable)),
    )


def _validate_score_projection(
    baseline: Sequence[DetectionScore],
    mutated: Sequence[DetectionScore],
    declaration: MutationDeclaration,
) -> None:
    baseline_scores = _score_projection(baseline, label="baseline")
    mutated_scores = _score_projection(mutated, label="mutated")
    if set(baseline_scores) != set(mutated_scores):
        difference = tuple(sorted(set(baseline_scores) ^ set(mutated_scores)))
        raise MutationValidationError(
            MutationValidationCode.IDENTITY_MISMATCH,
            f"mutation {declaration.name!r} changed exact run identities: {difference}",
        )
    for identity in sorted(baseline_scores):
        before = baseline_scores[identity]
        after = mutated_scores[identity]
        if before.basis is not after.basis:
            raise MutationValidationError(
                MutationValidationCode.DETECTION_BASIS_CHANGED,
                f"mutation {declaration.name!r} changed detection basis for {identity}: "
                f"{before.basis.value} -> {after.basis.value}",
            )
        before_false_positives = _false_positive_projection(before, label="baseline")
        after_false_positives = _false_positive_projection(after, label="mutated")
        if before_false_positives != after_false_positives:
            changed = tuple(sorted(set(before_false_positives) ^ set(after_false_positives)))
            raise MutationValidationError(
                MutationValidationCode.CLEAN_FALSE_POSITIVES_CHANGED,
                f"mutation {declaration.name!r} changed clean-item false positives for "
                f"{identity}: {changed}",
            )


def _score_projection(
    scores: Sequence[DetectionScore], *, label: str
) -> dict[tuple[str, str, int], DetectionScore]:
    projection: dict[tuple[str, str, int], DetectionScore] = {}
    for score in scores:
        identity = (score.item_id, score.arm, score.repetition)
        if identity in projection:
            raise MutationValidationError(
                MutationValidationCode.DUPLICATE_IDENTITY,
                f"{label} score projection has duplicate run identity: {identity}",
            )
        projection[identity] = score
    return projection


def _false_positive_projection(
    score: DetectionScore, *, label: str
) -> dict[FalsePositiveIdentity, object]:
    projection: dict[FalsePositiveIdentity, object] = {}
    for finding in score.false_positives:
        identity = FalsePositiveIdentity(
            item=score.item_id,
            arm=score.arm,
            repetition=score.repetition,
            interface=finding.interface,
            control=finding.control,
            command_index=finding.command_index,
            json_path=finding.json_path,
        )
        if identity in projection:
            raise MutationValidationError(
                MutationValidationCode.DUPLICATE_IDENTITY,
                f"{label} false-positive projection has duplicate identity: {identity}",
            )
        projection[identity] = finding
    return projection


def _score_for_identity(
    scores: Sequence[DetectionScore], identity: DetectionIdentity
) -> DetectionScore:
    return next(
        score
        for score in scores
        if (score.item_id, score.arm, score.repetition)
        == (identity.item, identity.arm, identity.repetition)
    )


def audit_blocking_control_coverage(
    entries: Sequence[BlockingControlCoverage],
) -> BlockingControlCoverageReport:
    """Require one canonical entry and exactly one coverage basis for every control."""
    actual = tuple(entry.control for entry in entries)
    expected = tuple(BlockingControl)
    if actual != expected:
        canonical = ", ".join(control.value for control in expected)
        raise MutationCoverageError(
            MutationCoverageCode.NONCANONICAL_CONTROL_ORDER,
            f"blocking controls must appear exactly once in canonical order: {canonical}; "
            f"observed: {', '.join(control.value for control in actual) or '<none>'}",
        )
    for entry in entries:
        has_mutations = bool(entry.mutations)
        has_reason = bool(entry.infeasibility_reason and entry.infeasibility_reason.strip())
        if not has_mutations and not has_reason:
            raise MutationCoverageError(
                MutationCoverageCode.UNCOVERED_CONTROL,
                f"blocking control {entry.control.value!r} has no source mutation or specific "
                "infeasibility reason",
            )
        if has_mutations and has_reason:
            raise MutationCoverageError(
                MutationCoverageCode.AMBIGUOUS_COVERAGE,
                f"blocking control {entry.control.value!r} declares both mutations and an "
                "infeasibility reason",
            )
        invalid_kinds = tuple(
            getattr(declaration, "classification", type(declaration).__name__)
            for declaration in entry.mutations
            if not isinstance(declaration, MutationDeclaration)
        )
        if invalid_kinds:
            raise MutationCoverageError(
                MutationCoverageCode.MUTATION_CONTROL_MISMATCH,
                f"blocking control {entry.control.value!r} contains non-package mutations: "
                f"{tuple(str(kind) for kind in invalid_kinds)}",
            )
        mismatched = tuple(
            declaration.name
            for declaration in entry.mutations
            if declaration.blocking_control is not entry.control
        )
        if mismatched:
            raise MutationCoverageError(
                MutationCoverageCode.MUTATION_CONTROL_MISMATCH,
                f"blocking control {entry.control.value!r} contains mutations declared for a "
                f"different control: {mismatched}",
            )
    return BlockingControlCoverageReport(entries=tuple(entries))


def _detection_projection(
    scores: Sequence[DetectionScore], *, label: str
) -> dict[DetectionIdentity, DefectScore]:
    projection: dict[DetectionIdentity, DefectScore] = {}
    duplicates: list[DetectionIdentity] = []
    for score in scores:
        for defect in score.defects:
            identity = DetectionIdentity(
                item=score.item_id,
                arm=score.arm,
                repetition=score.repetition,
                defect=defect.defect,
            )
            if identity in projection:
                duplicates.append(identity)
            projection[identity] = defect
    if duplicates:
        ordered = tuple(sorted(set(duplicates)))
        raise MutationValidationError(
            MutationValidationCode.DUPLICATE_IDENTITY,
            f"{label} detection projection has duplicate exact identities: "
            f"{[identity.as_tuple() for identity in ordered]}",
            identities=ordered,
        )
    return projection


@dataclass(frozen=True)
class AppliedMutation:
    """Paths and hashes of one mutation while its disposable copy exists."""

    declaration: MutationDeclaration
    disposable_root: Path
    source_root: Path
    target: Path
    copy_before_sha256: str
    copy_after_sha256: str


@dataclass(frozen=True)
class MutationExecution:
    """One mutated scripted actor result with checkout-integrity evidence."""

    declaration: MutationDeclaration
    actor_result: ActorResult
    disposable_root: Path
    checkout_before_sha256: str
    checkout_after_sha256: str
    checkout_unchanged: bool


class PreparedMutation(AbstractContextManager[AppliedMutation]):
    """Context-managed package copy that applies and then removes one mutation."""

    def __init__(self, declaration: MutationDeclaration, source_package: Path) -> None:
        self.declaration = declaration
        self.source_package = source_package.resolve(strict=True)
        if not self.source_package.is_dir():
            raise MutationError(f"source package is not a directory: {self.source_package}")
        if self.source_package.name != "sdr":
            raise MutationError(f"source package must be the sdr package: {self.source_package}")
        self._disposable_root: Path | None = None
        self.checkout_before_sha256: str | None = None
        self.checkout_after_sha256: str | None = None
        self.checkout_unchanged: bool | None = None

    @property
    def disposable_root(self) -> Path:
        """The allocated temporary root, including after cleanup for audit assertions."""
        if self._disposable_root is None:
            raise MutationError("the disposable mutation root has not been allocated")
        return self._disposable_root

    def __enter__(self) -> AppliedMutation:
        checkout_before = _snapshot_tree(self.source_package)
        self.checkout_before_sha256 = _snapshot_sha256(checkout_before)
        root = Path(tempfile.mkdtemp(prefix="sdr-bench-mutation-")).resolve()
        self._disposable_root = root
        checkout_root = self.source_package.parents[1]
        try:
            if root.is_relative_to(checkout_root):
                raise MutationError(
                    f"disposable mutation root must be outside the checkout: {root}"
                )
            source_root = root / "src"
            copied_package = source_root / "sdr"
            shutil.copytree(self.source_package, copied_package, symlinks=True)
            target = source_root / self.declaration.target
            if target.is_symlink():
                raise MutationError(
                    f"mutation {self.declaration.name!r} target is a symlink: "
                    f"{self.declaration.target.as_posix()}"
                )
            resolved_target = target.resolve(strict=True)
            if not resolved_target.is_relative_to(copied_package):
                raise MutationError(
                    f"mutation {self.declaration.name!r} target escapes the package copy: "
                    f"{self.declaration.target.as_posix()}"
                )
            if not resolved_target.is_file():
                raise MutationError(
                    f"mutation {self.declaration.name!r} target is not a source file: "
                    f"{self.declaration.target.as_posix()}"
                )
            copy_before = _snapshot_tree(copied_package)
            body = resolved_target.read_bytes()
            transformed = _apply_replacement(self.declaration, body)
            resolved_target.write_bytes(transformed)
            copy_after = _snapshot_tree(copied_package)
            return AppliedMutation(
                declaration=self.declaration,
                disposable_root=root,
                source_root=source_root,
                target=resolved_target,
                copy_before_sha256=_snapshot_sha256(copy_before),
                copy_after_sha256=_snapshot_sha256(copy_after),
            )
        except BaseException:
            self._finalize()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._finalize()
        if self.checkout_unchanged is False:
            raise MutationError(
                f"mutation {self.declaration.name!r} changed checkout package bytes"
            ) from exc_value
        return None

    def _finalize(self) -> None:
        checkout_after = _snapshot_tree(self.source_package)
        self.checkout_after_sha256 = _snapshot_sha256(checkout_after)
        self.checkout_unchanged = self.checkout_before_sha256 == self.checkout_after_sha256
        if self._disposable_root is not None:
            shutil.rmtree(self._disposable_root, ignore_errors=True)


def prepare_mutation(
    declaration: MutationDeclaration,
    *,
    source_package: Path = REPOSITORY_ROOT / "src" / "sdr",
) -> PreparedMutation:
    """Prepare a context-managed disposable package mutation without executing it."""
    return PreparedMutation(declaration, source_package)


@dataclass(frozen=True)
class MutationActor:
    """Execute one scripted actor invocation against one disposable mutated package."""

    declaration: MutationDeclaration
    source_package: Path = REPOSITORY_ROOT / "src" / "sdr"
    scripted_actor: ScriptedActor = ScriptedActor()

    @property
    def kind(self) -> ActorKind:
        """Mutations remain offline scripted actors for arm separation."""
        return ActorKind.SCRIPTED

    def execute(self, request: RunRequest) -> ActorResult:
        """Execute through the Actor protocol and retain immutable integrity evidence."""
        return self.execute_with_evidence(request).actor_result

    def execute_with_evidence(self, request: RunRequest) -> MutationExecution:
        """Apply and execute once, returning the actor result plus checkout evidence."""
        prepared = prepare_mutation(self.declaration, source_package=self.source_package)
        with prepared as applied:
            actor = replace(
                self.scripted_actor,
                source_root=applied.source_root,
                environment_builder=(
                    self.scripted_actor.environment_builder or build_mutation_environment
                ),
            )
            result = actor.execute(request)
            disposable_root = applied.disposable_root
        assert prepared.checkout_before_sha256 is not None
        assert prepared.checkout_after_sha256 is not None
        assert prepared.checkout_unchanged is not None
        integrity = CheckoutIntegrityEvidence(
            disposable_root=disposable_root,
            before_sha256=prepared.checkout_before_sha256,
            after_sha256=prepared.checkout_after_sha256,
            unchanged=prepared.checkout_unchanged,
        )
        result = replace(result, checkout_integrity=integrity)
        return MutationExecution(
            declaration=self.declaration,
            actor_result=result,
            disposable_root=disposable_root,
            checkout_before_sha256=prepared.checkout_before_sha256,
            checkout_after_sha256=prepared.checkout_after_sha256,
            checkout_unchanged=prepared.checkout_unchanged,
        )


@dataclass(frozen=True)
class RegistryMutationResult:
    """Exact offline result for one feasible registry mutation."""

    declaration: MutationDeclaration
    baseline_scores: tuple[DetectionScore, ...]
    mutated_scores: tuple[DetectionScore, ...]
    validation: MutationValidation
    checkout_integrity: tuple[CheckoutIntegrityEvidence, ...]


@dataclass(frozen=True)
class RegistryMutationPlan:
    """Current-provenance corpus and feasible declarations for one offline run."""

    corpus_version: str
    baseline_provenance: BaselineProvenance
    items: tuple[CorpusItem, ...]
    declarations: tuple[MutationDeclaration, ...]


def plan_registry_mutations(corpus: Corpus) -> RegistryMutationPlan:
    """Build a stable registry plan only for the current corpus migration contract."""
    expected = BaselineProvenance(
        version=1,
        snapshot_schema_version=2,
        decision_lineage_field="evidence_claim_ids",
        preserved_baseline=None,
    )
    if corpus.baseline_provenance != expected:
        raise MutationError(
            "registry mutation orchestration requires current provenance: "
            f"expected {expected!r}, observed {corpus.baseline_provenance!r}"
        )
    coverage = audit_blocking_control_coverage(BLOCKING_CONTROL_COVERAGE)
    return RegistryMutationPlan(
        corpus_version=corpus.version,
        baseline_provenance=corpus.baseline_provenance,
        items=tuple(sorted(corpus.items, key=lambda item: item.id)),
        declarations=tuple(
            declaration for entry in coverage.entries for declaration in entry.mutations
        ),
    )


def execute_registry_mutations(
    corpus: Corpus,
    *,
    parent: Path | None = None,
    max_workers: int = 1,
) -> tuple[RegistryMutationResult, ...]:
    """Execute and exactly validate every feasible registry mutation offline."""
    plan = plan_registry_mutations(corpus)
    arms = ("light", "full")
    offline_actor = ScriptedActor(environment_builder=_orchestration_environment)
    baseline_runs = execute_arms(
        corpus,
        actor=offline_actor,
        arms=arms,
        max_workers=max_workers,
        parent=parent,
    )
    baseline_scores = _score_executed_runs(corpus, baseline_runs, label="baseline")
    results: list[RegistryMutationResult] = []
    for declaration in plan.declarations:
        mutated_runs = execute_arms(
            corpus,
            actor=MutationActor(declaration, scripted_actor=offline_actor),
            arms=arms,
            max_workers=max_workers,
            parent=parent,
        )
        mutated_scores = _score_executed_runs(
            corpus, mutated_runs, label=f"mutation {declaration.name!r}"
        )
        integrity = tuple(
            run.result.checkout_integrity
            for run in mutated_runs
            if run.outcome is ArmOutcome.EXECUTED
            and run.result is not None
            and run.result.checkout_integrity is not None
        )
        if len(integrity) != len(mutated_scores) or not all(
            evidence.unchanged
            and evidence.before_sha256 == evidence.after_sha256
            and not evidence.disposable_root.exists()
            for evidence in integrity
        ):
            raise MutationError(
                f"mutation {declaration.name!r} lacks exact post-cleanup checkout evidence"
            )
        results.append(
            RegistryMutationResult(
                declaration=declaration,
                baseline_scores=baseline_scores,
                mutated_scores=mutated_scores,
                validation=validate_mutation_detection(
                    declaration, baseline_scores, mutated_scores
                ),
                checkout_integrity=integrity,
            )
        )
    return tuple(results)


def _orchestration_environment(
    space: Runspace, *, executable: str | Path, package_root: Path
) -> SubprocessEnvironment:
    """Keep the environment allowlisted while making the proven Python available to probes."""
    prepared = build_scripted_environment(space, executable=executable, package_root=package_root)
    variables = dict(prepared.variables)
    executable_dir = str(prepared.provenance.executable_path.parent)
    variables["PATH"] = os.pathsep.join((executable_dir, os.defpath))
    return SubprocessEnvironment(variables=variables, provenance=prepared.provenance)


def _score_executed_runs(
    corpus: Corpus, runs: Sequence[ArmRun], *, label: str
) -> tuple[DetectionScore, ...]:
    failures = tuple(run for run in runs if run.outcome is ArmOutcome.ERRORED)
    if failures:
        detail = "; ".join(
            f"{run.item_id}/{run.arm}/{run.repetition}: {run.error}" for run in failures
        )
        raise MutationError(f"{label} arm execution failed: {detail}")
    items = {item.id: item for item in corpus.items}
    return tuple(
        score_detection(items[run.item_id], run)
        for run in runs
        if run.outcome is ArmOutcome.EXECUTED
    )


def _validate_target(target: Path) -> Path:
    if target.is_absolute() or not target.parts or ".." in target.parts:
        raise MutationError(f"mutation target must be a package-confined relative path: {target}")
    normalized = Path(*target.parts)
    if normalized.parts[0] != "sdr" or normalized == Path("sdr"):
        raise MutationError(f"mutation target must be a package-confined relative path: {target}")
    return normalized


def _apply_replacement(declaration: MutationDeclaration, body: bytes) -> bytes:
    transformation = declaration.transformation
    matches = body.count(transformation.before)
    identity = f"mutation {declaration.name!r} target {declaration.target.as_posix()!r}"
    if matches == 0:
        raise MutationError(f"{identity} matched zero times")
    if transformation.expected_matches is None:
        if matches != 1:
            raise MutationError(
                f"{identity} has ambiguous {matches} matches; declare an exact expected_matches"
            )
    elif matches != transformation.expected_matches:
        raise MutationError(
            f"{identity} expected {transformation.expected_matches} matches, found {matches}"
        )
    return body.replace(transformation.before, transformation.after, matches)


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            relative = path.relative_to(root).as_posix()
            raise MutationError(f"source package contains a symlink: {relative}")
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


def _snapshot_sha256(snapshot: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, body in snapshot.items():
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(hashlib.sha256(body).digest())
    return digest.hexdigest()


_TEXTUAL_ANCHORING_MUTATION = MutationDeclaration(
    name="accept-unanchored-claims",
    blocking_control=BlockingControl.TEXTUAL_ANCHORING,
    target=Path("sdr/verification.py"),
    transformation=ExactReplacement(
        before=b"return VerificationReport(passed=not failures, items=items, failures=failures)",
        after=b"return VerificationReport(passed=True, items=(), failures=())",
    ),
    defect_kinds=("unanchored-claim",),
)

_EXECUTABLE_MUTATION = MutationDeclaration(
    name="accept-failing-probe-execution",
    blocking_control=BlockingControl.EXECUTABLE,
    target=Path("sdr/probe_verify.py"),
    transformation=ExactReplacement(
        before=b"passed = exit_code == 0 and matches and error_code is None",
        after=b"passed = True",
    ),
    defect_kinds=("probe-expectation-mismatch",),
)

BLOCKING_CONTROL_COVERAGE: tuple[BlockingControlCoverage, ...] = (
    BlockingControlCoverage(
        control=BlockingControl.STRUCTURAL,
        infeasibility_reason=(
            "the current-provenance lifecycle corpus has no planted structural defect with a "
            "baseline catch attributable solely to Structural, so exact loss cannot be measured"
        ),
    ),
    BlockingControlCoverage(
        control=BlockingControl.EVIDENTIAL,
        infeasibility_reason=(
            "the current-provenance corpus's contradictory-sources defect is intentionally "
            "uncaught and supplies no Evidential baseline catch for exact-loss validation"
        ),
    ),
    BlockingControlCoverage(
        control=BlockingControl.TEXTUAL_ANCHORING,
        mutations=(_TEXTUAL_ANCHORING_MUTATION,),
    ),
    BlockingControlCoverage(
        control=BlockingControl.EXECUTABLE,
        mutations=(_EXECUTABLE_MUTATION,),
    ),
    BlockingControlCoverage(
        control=BlockingControl.HASH_CONSISTENCY,
        infeasibility_reason=(
            "the current-provenance corpus plants no stale-validation defect, so it has no Hash "
            "consistency baseline catch to compare by exact identity"
        ),
    ),
    BlockingControlCoverage(
        control=BlockingControl.HITL,
        infeasibility_reason=(
            "the current-provenance corpus plants no unapproved-decision defect and provides no "
            "HITL baseline catch that can be deterministically removed"
        ),
    ),
)
