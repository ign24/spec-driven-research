"""Typed adapters from section 1-8 evidence into durable schema-v2 records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.harness.actor import ScriptedActor
from bench.harness.arms import execute_arms
from bench.harness.corpus import BaselineProvenance, Corpus, CorpusItem
from bench.harness.cost import cost_for_run
from bench.harness.cross_scoring import score_cross_consultation
from bench.harness.prompts import (
    EvaluationQuestion,
    HistoryCondition,
    PromptInputs,
    PromptLeakSignals,
    PromptPolicy,
    build_prompt,
)
from bench.harness.record import ApprovalEvidence, CorpusProvenance, RunRecord, RunRecordSet
from bench.harness.record_builders import (
    CrossRecordSource,
    DurableRecordContext,
    LifecycleRecordSource,
    build_cross_retrieval_record,
    build_lifecycle_control_record,
)
from bench.harness.reuse import load_reuse_corpus, prepare_reuse_scenario
from bench.harness.runspace import REPOSITORY_ROOT
from tests.bench_record_v2_fixtures import complete_record


def _context(question: EvaluationQuestion, *, focal: str, scenario: bool) -> DurableRecordContext:
    template = complete_record(question.value)
    parsed = RunRecord.from_json(json.dumps(template))
    policy = PromptPolicy.ASSISTED if question is EvaluationQuestion.CROSS_RETRIEVAL else None
    history = HistoryCondition.PRESENT if question is EvaluationQuestion.CROSS_RETRIEVAL else None
    prompt = build_prompt(
        PromptInputs(question, "What exact evidence was observed?", "light", policy, history),
        PromptLeakSignals(),
    )
    corpus = parsed.corpus.model_copy(
        update={
            "focal_investigation": focal,
            "history_condition": "history-present" if scenario else "not-applicable",
        }
    )
    return DurableRecordContext(
        run_id=f"adapter-{question.value}",
        started_at="2026-08-11T10:00:00Z",
        results_root=parsed.results_root,
        corpus=corpus,
        prompt=prompt,
        execution=parsed.execution.model_copy(update={"live_boundary": None}),
        approval=ApprovalEvidence(
            provenance="not-reached",
            state="not-reached",
            operator_record_ref=None,
            synthetic_reference=None,
            synthetic_excluded_from_live=False,
        ),
        usage=parsed.usage,
        friction=tuple(parsed.friction),
        evidence=tuple(parsed.evidence),
        metric_evidence_ref_id="ev-cli",
    )


def _item() -> CorpusItem:
    return CorpusItem(
        id="adapter-item",
        mode="light",
        title="Adapter item",
        question="What exact evidence was observed?",
        planted_defects=("unreachable-source",),
        expected_detection={"unreachable-source": "caught"},
        sources=(),
        artifacts={},
        commands=(("status", "adapter-item", "--json"),),
        probe=None,
        path=Path("bench/corpus/items/adapter-item.yaml"),
    )


def test_lifecycle_builder_adapts_actual_detection_cost_and_friction() -> None:
    item = _item()
    run = execute_arms((item,), actor=ScriptedActor(), arms=("light",), max_workers=1)[0]
    corpus = Corpus(
        "adapter-corpus",
        BaselineProvenance(1, 2, "evidence_claim_ids", None),
        (item,),
        REPOSITORY_ROOT / "bench" / "corpus",
    )
    context = _context(
        EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
        focal=item.id,
        scenario=False,
    )

    record = build_lifecycle_control_record(
        LifecycleRecordSource(
            context=context,
            corpus=corpus,
            item=item,
            run=run,
            cost=cost_for_run(run),
            friction=run.friction,
            mutation=None,
            baseline_run_id=None,
        )
    )

    assert record.evaluation_question is EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY
    assert record.lifecycle is not None
    assert record.lifecycle.detections[0].outcome == "not-exercised"
    assert record.lifecycle.detections[0].reason
    assert (
        record.prompt.built_prompt_sha256
        == hashlib.sha256(context.prompt.text.encode()).hexdigest()
    )
    assert RunRecord.from_json(record.to_json()) == record


def test_lifecycle_builder_rejects_missing_actual_friction_provenance() -> None:
    item = _item()
    run = execute_arms((item,), actor=ScriptedActor(), arms=("light",), max_workers=1)[0]
    corpus = Corpus(
        "adapter-corpus",
        BaselineProvenance(1, 2, "evidence_claim_ids", None),
        (item,),
        REPOSITORY_ROOT / "bench" / "corpus",
    )
    source = LifecycleRecordSource(
        context=_context(
            EvaluationQuestion.LIFECYCLE_CONTROL_OBSERVABILITY,
            focal=item.id,
            scenario=False,
        ),
        corpus=corpus,
        item=item,
        run=run,
        cost=cost_for_run(run),
        friction=None,
        mutation=None,
        baseline_run_id=None,
    )
    with pytest.raises(ValueError, match="friction provenance"):
        build_lifecycle_control_record(source)


def test_cross_builder_adapts_actual_fixture_score_and_seed_hash_evidence(tmp_path: Path) -> None:
    reuse = load_reuse_corpus()
    scenario = reuse.scenarios[0]
    score = score_cross_consultation(scenario, (), repetition=0)
    prepared = prepare_reuse_scenario(scenario, repetition=0, parent=tmp_path)
    with prepared:
        pass
    assert prepared.evidence is not None
    context = _context(EvaluationQuestion.CROSS_RETRIEVAL, focal=scenario.focal.id, scenario=True)
    corpus_data = context.corpus.model_dump(mode="python")
    corpus_data.update(
        {
            "version": reuse.version,
            "scenario_manifest_sha256": hashlib.sha256(scenario.path.read_bytes()).hexdigest(),
            "seed_artifact_sha256": dict(prepared.evidence.pre_declared_seed_hashes),
            "seed_immutability": {
                "checked": True,
                "unchanged": prepared.evidence.unchanged,
                "pre_sha256": dict(prepared.evidence.pre_materialized_seed_hashes),
                "post_sha256": dict(prepared.evidence.post_materialized_seed_hashes),
            },
        }
    )
    context = context.with_corpus(CorpusProvenance.model_validate(corpus_data))

    record = build_cross_retrieval_record(
        CrossRecordSource(
            context=context,
            scenario=scenario,
            score=score,
            seed_immutability=prepared.evidence,
            resolver_chain_identity="sdr-cross-resolver-v1",
            metamorphic=None,
            source_run_ids=(),
        )
    )

    assert record.cross is not None
    assert record.cross.consultation == "not-consulted"
    assert {check.outcome for check in record.cross.checks} == {"not-consulted"}
    assert record.corpus.seed_immutability.unchanged is True


def test_record_set_serializes_adapter_records_canonically() -> None:
    raw = complete_record("cross-retrieval")
    record = RunRecord.from_json(json.dumps(raw))
    records = RunRecordSet(schema_version=2, records=(record,))
    assert RunRecordSet.from_json(records.to_json()).to_json() == records.to_json()
