"""Typed adapters from section 1-8 evidence into durable schema-v2 records."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from bench.harness.actor import ActorKind, TokenUsage
from bench.harness.arms import ArmOutcome, ArmRun
from bench.harness.corpus import Corpus, CorpusItem
from bench.harness.cost import RunCost, RunCostAccounting, StageElapsed
from bench.harness.cross_scoring import CrossConsultationResult
from bench.harness.detection import score_detection
from bench.harness.friction import CONTROL_VOCABULARY, FrictionAccounting
from bench.harness.live import LiveRunRequest, LiveSessionEvidence, MonetaryCost
from bench.harness.metamorphic import MetamorphicResult
from bench.harness.prompts import (
    BuiltPrompt,
    EvaluationQuestion,
    PromptInputs,
    PromptLeakSignals,
    build_prompt,
    validate_live_prompt,
)
from bench.harness.record import (
    ApprovalEvidence,
    BoundaryFileIdentity,
    BoundaryIdentities,
    ConfiguredBounds,
    CorpusProvenance,
    CrossCheckEvidence,
    CrossEvidence,
    DetectionEvidence,
    EvidenceRef,
    ExecutableProvenance,
    ExecutionBoundary,
    FrictionEvidence,
    LifecycleEvidence,
    LiveAttribution,
    LiveBoundaryEvidence,
    MediationEvidence,
    MonetaryAccounting,
    NegativeControlEvidence,
    ProcessGroupOutcome,
    PromptEvidence,
    RepositoryAudit,
    ResultsRootIdentity,
    RunRecord,
    RunRecordSet,
    SeedImmutability,
    StageDuration,
    TokenAccounting,
    UsageEvidence,
    XdgRootIdentities,
)
from bench.harness.reuse import ReuseScenario, SeedImmutabilityEvidence


@dataclass(frozen=True)
class DurableRecordContext:
    """Common provenance observed outside one question-specific scorer."""

    run_id: str
    started_at: str
    results_root: ResultsRootIdentity
    corpus: CorpusProvenance
    prompt: BuiltPrompt
    execution: ExecutionBoundary
    approval: ApprovalEvidence
    usage: UsageEvidence
    friction: tuple[FrictionEvidence, ...]
    evidence: tuple[EvidenceRef, ...]
    metric_evidence_ref_id: str

    def with_corpus(self, corpus: CorpusProvenance) -> DurableRecordContext:
        """Return a context with independently updated corpus provenance."""
        validated = CorpusProvenance.model_validate(corpus.model_dump(mode="python"))
        return replace(self, corpus=validated)


@dataclass(frozen=True)
class LifecycleRecordSource:
    """Actual lifecycle actor, scorer, cost, and friction evidence."""

    context: DurableRecordContext
    corpus: Corpus
    item: CorpusItem
    run: ArmRun
    cost: RunCostAccounting
    friction: FrictionAccounting | None
    mutation: object | None
    baseline_run_id: str | None


@dataclass(frozen=True)
class CrossRecordSource:
    """Actual reuse fixture, exact cross score, and immutability evidence."""

    context: DurableRecordContext
    scenario: ReuseScenario
    score: CrossConsultationResult
    seed_immutability: SeedImmutabilityEvidence
    resolver_chain_identity: str
    metamorphic: MetamorphicResult | None
    source_run_ids: tuple[str, ...]


@dataclass(frozen=True)
class LiveRecordSource:
    """Actual sealed request and live connector evidence plus mediator observations."""

    context: DurableRecordContext
    request: LiveRunRequest
    live: LiveSessionEvidence
    lifecycle: LifecycleEvidence
    friction: tuple[FrictionEvidence, ...]
    mediation: MediationEvidence


def build_lifecycle_control_record(source: LifecycleRecordSource) -> RunRecord:
    """Adapt one executed scripted lifecycle run without filling missing evidence."""
    context = source.context
    if context.prompt.treatment.evaluation_question is not (
        EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY
    ):
        raise ValueError("lifecycle builder requires the lifecycle-control prompt treatment")
    if source.item.id != source.run.item_id or source.item not in source.corpus.items:
        raise ValueError("lifecycle item/run/corpus identities are inconsistent")
    if source.run.outcome is not ArmOutcome.EXECUTED or source.run.result is None:
        raise ValueError("lifecycle durable records require an executed actor result")
    if not isinstance(source.cost, RunCost):
        raise ValueError("lifecycle durable record lacks actual cost provenance")
    if source.friction is None:
        raise ValueError("lifecycle durable record lacks friction provenance")
    if context.corpus.focal_investigation != source.item.id:
        raise ValueError("lifecycle corpus provenance identifies a different focal investigation")
    migration = f"baseline-provenance-v{source.corpus.baseline_provenance.version}"
    if context.corpus.migration_provenance_version != migration:
        raise ValueError("lifecycle corpus migration provenance is incompatible")
    manifest_hash = _artifact_hash(source.run, "evidence/corpus-item.json")
    corpus = context.corpus.model_copy(
        update={"version": source.corpus.version, "scenario_manifest_sha256": manifest_hash}
    )
    detection = score_detection(source.item, source.run)
    evidence_id = _metric_evidence(context)
    lifecycle = LifecycleEvidence(
        detections=[
            DetectionEvidence(
                defect=entry.defect,
                outcome=entry.outcome.value,
                reporting_control=entry.reporting_control,
                reason=entry.reason,
                evidence_ref_ids=[evidence_id],
            )
            for entry in detection.defects
        ],
        mutation_identity=(
            None
            if source.mutation is None
            else _required_attribute(source.mutation, "declaration", "name")
        ),
        baseline_run_id=source.baseline_run_id,
    )
    return RunRecord(
        schema_version=2,
        run_id=context.run_id,
        evaluation_question=EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
        actor=source.run.actor,
        scenario_id=None,
        item_id=source.item.id,
        arm=source.run.arm,
        repetition=source.run.repetition,
        started_at=context.started_at,
        terminal_state=_arm_terminal(source.run),
        results_root=context.results_root,
        corpus=corpus,
        prompt=_prompt_evidence(context.prompt),
        execution=context.execution,
        live=None,
        usage=_run_usage(source.cost),
        friction=_friction(source.friction, evidence_id),
        lifecycle=lifecycle,
        cross=None,
        approval=context.approval,
        mediation=None,
        evidence=list(context.evidence),
    )


def build_cross_retrieval_record(source: CrossRecordSource) -> RunRecord:
    """Adapt exact cross scoring and fixture evidence without semantic interpretation."""
    context = source.context
    scenario = source.scenario
    score = source.score
    if context.prompt.treatment.evaluation_question is not EvaluationQuestion.CROSS_RETRIEVAL:
        raise ValueError("cross builder requires the cross-retrieval prompt treatment")
    if (
        score.identity.scenario_id != scenario.id
        or score.identity.arm != scenario.arm
        or score.identity.history != scenario.history
    ):
        raise ValueError("cross score identity conflicts with its reuse scenario")
    if context.corpus.focal_investigation != scenario.focal.id:
        raise ValueError("cross corpus provenance identifies a different focal investigation")
    expected_manifest = hashlib.sha256(scenario.path.read_bytes()).hexdigest()
    if context.corpus.scenario_manifest_sha256 != expected_manifest:
        raise ValueError("cross scenario manifest provenance is incompatible")
    _validate_seed_evidence(context.corpus, source.seed_immutability)
    evidence_id = _metric_evidence(context)
    checks = [_cross_check(entry, evidence_id) for entry in score.checks]
    first = checks[0] if checks else None
    command_identity = None
    query_identity = None
    observed: Any = None
    expected: Any = None
    if score.consultation.outcome.value != "not-consulted" and first is not None:
        command_identity = first.command_identity
        query_identity = _query_identity(first.command_identity)
        observed = first.observed_projection
        expected = first.expected_projection
    cross = CrossEvidence(
        consultation=score.consultation.outcome.value,
        command_identity=command_identity,
        query_identity=query_identity,
        observed_projection=observed,
        observed_projection_sha256=_hash_or_none(observed),
        expected_projection=expected,
        expected_projection_sha256=_hash_or_none(expected),
        checks=checks,
        negative_controls=[
            NegativeControlEvidence(
                id=entry.id,
                outcome=entry.outcome,
                evidence_ref_ids=list(entry.evidence_ref_ids),
            )
            for entry in checks
            if entry.kind == "negative"
        ],
        metamorphic_relation=(
            None if source.metamorphic is None else source.metamorphic.relation.value
        ),
        source_run_ids=list(source.source_run_ids),
        resolver_chain_identity=source.resolver_chain_identity,
    )
    return RunRecord(
        schema_version=2,
        run_id=context.run_id,
        evaluation_question=EvaluationQuestion.CROSS_RETRIEVAL,
        actor=ActorKind.SCRIPTED,
        scenario_id=scenario.id,
        item_id=None,
        arm=scenario.arm,
        repetition=score.identity.repetition,
        started_at=context.started_at,
        terminal_state="completed",
        results_root=context.results_root,
        corpus=context.corpus,
        prompt=_prompt_evidence(context.prompt),
        execution=context.execution,
        live=None,
        usage=context.usage,
        friction=list(context.friction),
        lifecycle=None,
        cross=cross,
        approval=context.approval,
        mediation=None,
        evidence=list(context.evidence),
    )


def build_live_single_investigation_record(source: LiveRecordSource) -> RunRecord:
    """Adapt one exact live session, requiring separately captured mediator evidence."""
    context = source.context
    request = source.request
    live = source.live
    if context.prompt != request.prompt:
        raise ValueError("live record prompt differs from the sealed request BuiltPrompt")
    if context.prompt.treatment.evaluation_question is not (
        EvaluationQuestion.LIVE_SINGLE_INVESTIGATION
    ):
        raise ValueError("live builder requires the single-investigation prompt treatment")
    prompt = validate_live_prompt(request.prompt, request.prompt.text.encode())
    if not live.session.session_id:
        raise ValueError("live record lacks an exact host session identity")
    if live.transcript_persisted:
        raise ValueError("live record cannot persist a transcript")
    execution = _live_execution(live)
    identity = request.manifest.identity
    return RunRecord(
        schema_version=2,
        run_id=context.run_id,
        evaluation_question=EvaluationQuestion.LIVE_SINGLE_INVESTIGATION,
        actor=ActorKind.LIVE,
        scenario_id=None,
        item_id=identity,
        arm=request.manifest.arm,
        repetition=request.repetition,
        started_at=context.started_at,
        terminal_state=live.terminal_state,
        results_root=context.results_root,
        corpus=context.corpus,
        prompt=PromptEvidence(
            policy="standard",
            template_id=request.prompt.template.identifier,
            template_version=request.prompt.template.version,
            template_sha256=prompt.template_sha256,
            submitted_prompt_sha256=prompt.submitted_sha256,
            built_prompt_sha256=hashlib.sha256(request.prompt.text.encode()).hexdigest(),
            canonical_built_prompt=True,
            leak_validation_passed=prompt.leak_validation_passed,
        ),
        execution=execution,
        live=LiveAttribution(
            host=live.host.host,
            host_version=live.host.host_version,
            model=live.host.model or request.sealed_request.model,
            model_version=live.host.model_version,
            session_id=live.session.session_id,
            session_attribution_valid=live.session.attributed,
            session_attribution_reason=live.session.reason,
            transcript_persisted=False,
        ),
        usage=_live_usage(live),
        friction=list(source.friction),
        lifecycle=source.lifecycle,
        cross=None,
        approval=context.approval,
        mediation=source.mediation,
        evidence=list(context.evidence),
    )


def build_lifecycle_record_set(
    *,
    corpus: Corpus,
    runs: tuple[ArmRun, ...],
    started_at: str,
    repository_before_sha256: str,
    repository_after_sha256: str,
) -> RunRecordSet:
    """Build v2 records for executed scripted runs; planned absences are not run records."""
    failures = tuple(run for run in runs if run.outcome is ArmOutcome.ERRORED)
    if failures:
        identities = ", ".join(f"{run.item_id}/{run.arm}/{run.repetition}" for run in failures)
        raise ValueError(f"cannot persist incomplete errored lifecycle evidence: {identities}")
    items = {item.id: item for item in corpus.items}
    records: list[RunRecord] = []
    for run in runs:
        if run.outcome is not ArmOutcome.EXECUTED:
            continue
        item = items[run.item_id]
        prompt = build_prompt(
            PromptInputs(
                EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
                item.question,
                run.arm,
                None,
                None,
            ),
            PromptLeakSignals(
                planted_defect_identities=item.planted_defects,
                expected_detection_values=tuple(item.expected_detection.values()),
                expected_detection_metadata=tuple(item.expected_detection),
            ),
        )
        evidence = tuple(
            EvidenceRef(
                id=f"artifact-{index}",
                kind="artifact",
                artifact_path=artifact.path,
                command_index=None,
                exit_code=None,
                structured_field=None,
            )
            for index, artifact in enumerate(run.evidence_artifacts)
        )
        actor_id = next(
            (entry.id for entry in evidence if entry.artifact_path == "evidence/actor.json"),
            None,
        )
        if actor_id is None:
            raise ValueError("executed lifecycle run lacks actor evidence")
        root_identity = hashlib.sha256(str(run.research_root).encode()).hexdigest()
        run_cost = _required_run_cost(run)
        run_friction = _required_friction(run)
        context = DurableRecordContext(
            run_id=_run_id(corpus.version, run, started_at),
            started_at=started_at,
            results_root=ResultsRootIdentity(
                identity=f"external-runspace-sha256:{root_identity}", external=True
            ),
            corpus=CorpusProvenance(
                version=corpus.version,
                migration_provenance_version=(
                    f"baseline-provenance-v{corpus.baseline_provenance.version}"
                ),
                scenario_manifest_sha256="0" * 64,
                seed_artifact_sha256={},
                focal_investigation=item.id,
                history_condition="not-applicable",
                seed_immutability=SeedImmutability(
                    checked=False,
                    unchanged=None,
                    pre_sha256={},
                    post_sha256={},
                ),
            ),
            prompt=prompt,
            execution=_scripted_execution(
                run,
                repository_before_sha256=repository_before_sha256,
                repository_after_sha256=repository_after_sha256,
            ),
            approval=ApprovalEvidence(
                provenance="not-reached",
                state="not-reached",
                operator_record_ref=None,
                synthetic_reference=None,
                synthetic_excluded_from_live=False,
            ),
            usage=_run_usage(run_cost),
            friction=tuple(_friction(run_friction, actor_id)),
            evidence=evidence,
            metric_evidence_ref_id=actor_id,
        )
        records.append(
            build_lifecycle_control_record(
                LifecycleRecordSource(
                    context=context,
                    corpus=corpus,
                    item=item,
                    run=run,
                    cost=run_cost,
                    friction=run_friction,
                    mutation=None,
                    baseline_run_id=None,
                )
            )
        )
    return RunRecordSet(schema_version=2, records=tuple(records))


def _prompt_evidence(prompt: BuiltPrompt) -> PromptEvidence:
    body_hash = hashlib.sha256(prompt.text.encode()).hexdigest()
    policy = "standard" if prompt.treatment.policy is None else prompt.treatment.policy.value
    return PromptEvidence(
        policy=policy,
        template_id=prompt.template.identifier,
        template_version=prompt.template.version,
        template_sha256=prompt.template.sha256,
        submitted_prompt_sha256=body_hash,
        built_prompt_sha256=body_hash,
        canonical_built_prompt=prompt.leak_validation_passed,
        leak_validation_passed=prompt.leak_validation_passed,
    )


def _scripted_execution(
    run: ArmRun, *, repository_before_sha256: str, repository_after_sha256: str
) -> ExecutionBoundary:
    assert run.result is not None
    provenances = tuple(
        command.execution_provenance
        for command in run.result.commands
        if command.execution_provenance is not None
    )
    if provenances and any(value != provenances[0] for value in provenances[1:]):
        raise ValueError("scripted commands used incompatible executable/package provenance")
    if provenances:
        provenance = provenances[0]
        executable_path = provenance.executable_path
        executable_sha256 = provenance.executable_sha256
        package_root = provenance.package_root
        package_sha256 = provenance.package_sha256
    else:
        executable_path = Path(sys.executable).resolve(strict=True)
        package_root = Path(__file__).resolve().parents[2] / "src"
        executable_sha256 = hashlib.sha256(executable_path.read_bytes()).hexdigest()
        package_sha256 = _tree_sha256(package_root)
    return ExecutionBoundary(
        environment_policy="scripted-allowlist",
        environment_policy_version="1",
        executable=ExecutableProvenance(
            path=str(executable_path),
            sha256=executable_sha256,
            package_identity=package_root.name,
            package_sha256=package_sha256,
        ),
        repository_audit=RepositoryAudit(
            before_sha256=repository_before_sha256,
            after_sha256=repository_after_sha256,
            unchanged=repository_before_sha256 == repository_after_sha256,
        ),
        process_group=ProcessGroupOutcome(identity=None, reaped=True),
        bounds=ConfiguredBounds(max_turns=None, wall_clock_seconds=None),
        exceeded_bound=None,
        live_boundary=None,
    )


def _run_usage(cost: RunCost) -> UsageEvidence:
    tokens = (
        TokenAccounting(
            state="observed",
            input=cost.tokens.input_tokens,
            output=cost.tokens.output_tokens,
            reason=None,
        )
        if isinstance(cost.tokens, TokenUsage)
        else TokenAccounting(
            state="unavailable", input=None, output=None, reason=cost.tokens.reason
        )
    )
    return UsageEvidence(
        wall_clock_seconds=cost.total_seconds,
        stage_durations=[
            StageDuration(stage=entry.stage, seconds=entry.seconds)
            for entry in cost.stages
            if isinstance(entry, StageElapsed)
        ],
        tokens=tokens,
        money=MonetaryAccounting(
            state="unavailable",
            amount=None,
            currency=None,
            reason="scripted actor has no host-attributed monetary usage",
        ),
    )


def _live_usage(live: LiveSessionEvidence) -> UsageEvidence:
    tokens = (
        TokenAccounting(
            state="observed",
            input=live.tokens.input_tokens,
            output=live.tokens.output_tokens,
            reason=None,
        )
        if isinstance(live.tokens, TokenUsage)
        else TokenAccounting(
            state="unavailable", input=None, output=None, reason=live.tokens.reason
        )
    )
    money = (
        MonetaryAccounting(
            state="observed",
            amount=str(live.cost.amount),
            currency=live.cost.currency,
            reason=None,
        )
        if isinstance(live.cost, MonetaryCost)
        else MonetaryAccounting(
            state="unavailable", amount=None, currency=None, reason=live.cost.reason
        )
    )
    return UsageEvidence(
        wall_clock_seconds=live.wall_clock_seconds,
        stage_durations=[],
        tokens=tokens,
        money=money,
    )


def _friction(accounting: FrictionAccounting, evidence_id: str) -> list[FrictionEvidence]:
    by_control = {control.value: [] for control in CONTROL_VOCABULARY}
    for failure in accounting.gate_failures:
        by_control[failure.control.value].append(failure.detail)
    return [
        FrictionEvidence(
            control=control.value, events=by_control[control.value], evidence_ref_ids=[evidence_id]
        )
        for control in CONTROL_VOCABULARY
    ]


def _cross_check(entry: Any, evidence_id: str) -> CrossCheckEvidence:
    expected = _json_value(entry.expected)
    observed = _json_value(entry.observed)
    return CrossCheckEvidence(
        id=entry.id,
        kind=entry.kind,
        command_identity=" ".join(entry.command),
        outcome=entry.outcome.value,
        reason=entry.reason,
        expected_projection=expected,
        expected_projection_sha256=_hash_or_none(expected),
        observed_projection=observed,
        observed_projection_sha256=_hash_or_none(observed),
        evidence_ref_ids=[evidence_id],
    )


def _live_execution(live: LiveSessionEvidence) -> ExecutionBoundary:
    provenance = live.execution_provenance
    boundary = live.boundary
    preflight = live.preflight
    identities = BoundaryIdentities(
        executable=_boundary_identity(preflight.executable_identity),
        config=_boundary_identity(boundary.config_identity),
        plugin=_boundary_identity(boundary.plugin_identity),
    )
    environment = boundary.environment
    return ExecutionBoundary(
        environment_policy="live-opencode",
        environment_policy_version="1",
        executable=ExecutableProvenance(
            path=str(provenance.executable_path),
            sha256=provenance.executable_sha256,
            package_identity=provenance.package_root.name,
            package_sha256=provenance.package_sha256,
        ),
        repository_audit=RepositoryAudit(
            before_sha256=live.repository_audit.before_sha256,
            after_sha256=live.repository_audit.after_sha256,
            unchanged=live.repository_audit.unchanged,
        ),
        process_group=ProcessGroupOutcome(
            identity=live.process_group_id,
            reaped=live.process_reaped,
        ),
        bounds=ConfiguredBounds(
            max_turns=live.bounds.max_turns,
            wall_clock_seconds=live.bounds.wall_clock_seconds,
        ),
        exceeded_bound=None if live.exceeded_bound is None else live.exceeded_bound.value,
        live_boundary=LiveBoundaryEvidence(
            credential_allowlist_identity="opencode-fixed-v1",
            xdg_roots=XdgRootIdentities(
                config=environment["XDG_CONFIG_HOME"],
                data=environment["XDG_DATA_HOME"],
                cache=environment["XDG_CACHE_HOME"],
                state=environment["XDG_STATE_HOME"],
            ),
            identities=identities,
            revalidation_boundaries=["startup", "dispatch", "mediator-subprocess", "export"],
            effective_config_isolated=True,
            mediator_token_undisclosed=True,
            subprocesses_cancelled=live.intentional_stop or live.exceeded_bound is not None,
            subprocesses_joined=live.process_reaped,
        ),
    )


def _boundary_identity(value: Any) -> BoundaryFileIdentity:
    return BoundaryFileIdentity(
        path=str(value.path),
        device=value.device,
        inode=value.inode,
        sha256=value.sha256,
    )


def _validate_seed_evidence(corpus: CorpusProvenance, evidence: SeedImmutabilityEvidence) -> None:
    if not evidence.unchanged or not evidence.cleanup_deleted or evidence.cleanup_error is not None:
        raise ValueError("cross seed immutability evidence did not pass")
    if dict(corpus.seed_artifact_sha256) != dict(evidence.pre_declared_seed_hashes):
        raise ValueError("cross declared seed hashes conflict with durable provenance")
    observed = corpus.seed_immutability
    if (
        not observed.checked
        or observed.unchanged is not True
        or dict(observed.pre_sha256) != dict(evidence.pre_materialized_seed_hashes)
        or dict(observed.post_sha256) != dict(evidence.post_materialized_seed_hashes)
    ):
        raise ValueError("cross materialized seed hashes conflict with durable provenance")


def _metric_evidence(context: DurableRecordContext) -> str:
    if context.metric_evidence_ref_id not in {entry.id for entry in context.evidence}:
        raise ValueError("metric evidence reference is absent from durable evidence")
    return context.metric_evidence_ref_id


def _artifact_hash(run: ArmRun, path: str) -> str:
    matches = [artifact for artifact in run.evidence_artifacts if artifact.path == path]
    if len(matches) != 1:
        raise ValueError(f"run lacks exact artifact provenance for {path}")
    return hashlib.sha256(matches[0].content.encode()).hexdigest()


def _arm_terminal(run: ArmRun) -> str:
    if run.terminal_state.recorded:
        return run.terminal_state.status or run.terminal_state.stage or "recorded"
    return "completed"


def _query_identity(command: str) -> str | None:
    parts = command.split()
    return parts[2] if len(parts) == 4 and parts[:2] == ["cross", "source"] else command


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _hash_or_none(value: Any) -> str | None:
    if value is None:
        return None
    body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _required_attribute(value: object, parent: str, child: str) -> str:
    nested = getattr(value, parent, None)
    result = getattr(nested, child, None)
    if not isinstance(result, str) or not result:
        raise ValueError("mutation evidence lacks a typed declaration identity")
    return result


def _required_run_cost(run: ArmRun) -> RunCost:
    from bench.harness.cost import cost_for_run

    cost = cost_for_run(run)
    if not isinstance(cost, RunCost):
        raise ValueError("executed run lacks actual cost evidence")
    return cost


def _required_friction(run: ArmRun) -> FrictionAccounting:
    if run.friction is None:
        raise ValueError("executed run lacks actual friction evidence")
    return run.friction


def _run_id(corpus_version: str, run: ArmRun, started_at: str) -> str:
    identity = f"{corpus_version}\0{run.item_id}\0{run.arm}\0{run.repetition}\0{started_at}"
    return "run-" + hashlib.sha256(identity.encode()).hexdigest()


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
