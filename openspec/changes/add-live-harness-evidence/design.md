## Context

The existing harness has a deterministic scripted actor, a three-arm baseline/light/full comparison,
and an opt-in `LiveActor` without a concrete session. It also has two validity problems: skipped
controls are scored as misses, and the harness has not shown that it notices when a blocking control
is weakened. Since that harness was designed, SDR also gained deterministic cross-investigation
retrieval and required decision `evidence_claim_ids`, while current snapshot provenance invalidated
the assumptions behind preserving the old corpus baselines unchanged.

The rescope treats three questions as separate experiments:

- **Lifecycle control observability:** offline scripted mutation evidence over the lifecycle-control
  corpus.
- **Live single-investigation behavior:** one bounded paid session at a time, measuring workflow,
  exact host-reported cost, and friction only.
- **Deterministic cross-investigation retrieval:** scripted/metamorphic exact-output validation first,
  followed by separately labelled assisted or unassisted live consultation observations.

None of these answers whether retrieved material was semantically applicable or useful. No output is
scored by a model.

## Goals / Non-Goals

**Goals:**

- Make skipped controls visible as `not-exercised` rather than `missed`.
- Prove blocking-control observability with offline mutations.
- Revalidate the corpus under current snapshot provenance and decision-lineage requirements before
  preserving new baselines.
- Validate exact cross CLI retrieval and exact negative controls in isolated scenario roots,
  including a non-software scenario.
- Observe one bounded live investigation's workflow, cost, and friction without crossing HITL on the
  agent's authority.
- Preserve exact treatment, session, host, model, version, and template provenance.
- Keep all default and CI paths deterministic, offline, credential-free, and repository-write-free.

**Non-Goals:**

- Semantic applicability, recommendation quality, criterion-level reuse, statistical significance,
  causal inference, or proof that cross consultation adds value.
- Aggregating the three evaluation questions or aggregating assisted and unassisted treatments.
- Treating spontaneous cross discovery as part of the first assisted reuse pilot.
- Changing shipped lifecycle controls, cross-investigation behavior, or HITL requirements.
- Persisting host transcripts or research artifacts in the repository.
- Release hardening, packaging hardening, or changes owned by the separate hardening change.

## Decisions

### Decision 1: Establish provenance and credential safety before execution work

Before preserving a new baseline, every retained corpus item is migrated and revalidated against the
current snapshot provenance contract. Completed seed decisions must explicitly carry the required
`evidence_claim_ids`; omission is not converted into inferred lineage. Old baselines are historical
inputs only and are not relabelled as current-version results.

Subprocess environments are then split by trust boundary before mutation or live execution is added.
Scripted, mutation, and metamorphic subprocesses receive an allowlisted environment with
credential-shaped variables removed. The live path receives only the inherited variables required by
the selected host, after executable provenance is captured. This ordering prevents a mutation test
from accidentally becoming the first credential-inheritance test.

### Decision 2: Keep the three evaluation questions and reports separate

Each run declares exactly one evaluation question:

- `lifecycle-control-observability`
- `live-single-investigation`
- `cross-retrieval`

Reports render separate sections and do not compute a combined score. Lifecycle-control mutation
results say whether a control's loss was observable. Live results report observed workflow, cost, and
friction. Cross results report consultation and exact structured retrieval outcomes. The report has a
standing limitations block prohibiting semantic, quality, criterion-level, significance, causal, and
cross-value claims.

### Decision 3: Reuse scenarios have one isolated root and orthogonal factors

One reuse scenario root contains immutable completed seed investigations and exactly one focal
investigation. Seed manifests and artifact hashes are captured before execution and verified after
execution. The focal investigation is the only writable investigation. No scenario shares a root
with another scenario or repetition.

The lifecycle arm remains `baseline`, `light`, or `full`. Reuse history is a separate condition such
as `history-present` or `history-absent`; it is never encoded as another arm. Prompt policy is another
factor with exactly `assisted` or `unassisted`. Assisted prompts explicitly require cross
consultation. Unassisted prompts do not mention cross consultation and measure spontaneous discovery.
The two policies are reported separately and never aggregated. The first reuse pilot uses
`assisted`; unassisted measurement is deferred until separately planned and authorized.

### Decision 4: Cross measurement is exact and tri-state where execution can be absent

The harness records whether the cross CLI was consulted and captures the exact structured query and
result projection needed by the fixture. Expected identifiers, investigation-qualified entities,
edge provenance, resolver metadata, lineage status, and ordering are compared without semantic
interpretation.

Each expected check is classified as:

- `correct`: the declared command was exercised and its structured projection exactly matched.
- `incorrect`: the declared command was exercised and its structured projection did not match.
- `not-exercised`: consultation occurred, but that declared query/check was not run.
- `not-consulted`: no cross command was consulted in the run.

The last two are intentionally distinct. A negative control is an exact expected absence, not a lack
of expectations. Fixtures include same-topic/no-explicit-edge cases, unrelated source/claim/decision
identifiers, and at least one non-software domain.

### Decision 5: Scripted/metamorphic reuse validation is not control mutation validation

Blocking-control mutations copy `src/sdr` to a throwaway location, weaken one named blocking control,
and compare detection outcomes against an unmutated scripted baseline. A no-op mutation fails.

Reuse metamorphic validation instead transforms fixture inputs while leaving shipped code unchanged.
Examples include changing URL spellings that must normalize to the same identity, removing an
explicit shared identity so a join must disappear, changing an investigation from `done` to `active`
so its decision must be excluded, and permuting seed materialization order while requiring identical
structured output. These tests validate deterministic retrieval relations, not whether a lifecycle
control blocks.

### Decision 6: The live host is bounded, exactly attributed, and transcript-free

The first connector drives OpenCode headlessly in the disposable scenario root and captures the
session identifier from its structured event stream. Usage and monetary cost come only from that
session's sanitized export, never from a host-wide aggregate or a maintained price table. Missing
usage is `unavailable` with a reason, never zero. The durable record stores aggregates and selected
structured evidence, not the transcript or raw export.

Every live process starts in a new process group. Turn and wall-clock bounds apply to the complete
process tree; timeout or cancellation terminates descendants and tears down the runspace. The
repository tree is hashed/audited before and after execution, and all run outputs live under the
operator-configured external results root.

Live execution requires two independent keys: the existing environment opt-in and an explicit CLI
flag. Defaults start no host, read no host credential, and make no network request.

### Decision 7: Pilot identity is a complete paid-session plan

A pilot is exactly one planned paid session, not one item expanded across arms or repetitions. Its
plan fixes one scenario/item identifier, one arm, one repetition index, one host and host version,
one model identity, and one prompt-template policy/version. Any mismatch between plan and observed
session fails attribution. The command exits after that session and reports observed usage, cost,
wall-clock, terminal state, and approval state. Additional paid sessions require a new explicit
operator authorization based on that observation.

The initial reuse pilot, if selected, is assisted. It cannot be described as spontaneous discovery.

### Decision 8: Live lifecycle execution never impersonates HITL

The agent may progress only through transfer. On reaching the approval boundary, the live session
stops with `awaiting-operator-approval`; it does not invoke `approve`, fabricate an approver, edit
lifecycle metadata, or substitute `resolve-claim` for approval.

Approval provenance has distinct states:

- `not-reached`: execution stopped or failed before the approval boundary.
- `operator-pending`: transfer was reached and a real operator decision is pending.
- `operator-approved` or `operator-rejected`: a real operator supplied the recorded decision in an
  explicitly resumed/recorded step.
- `synthetic-approved` or `synthetic-rejected`: a deterministic fixture supplied the state for
  scripted validation only.

Synthetic states are visibly synthetic, are never accepted as live operator evidence, and are not
used by the initial pilot.

### Decision 9: Prompt templates preserve treatment identity without leaking expectations

Prompts are versioned by evaluation question, arm, and prompt policy. They are built from declared
scenario inputs only and exclude planted defects, expected detections, expected retrievals, negative
controls, and scoring vocabulary. Assisted reuse prompts may require use of the cross CLI but may not
name expected queries or results. Unassisted prompts omit cross guidance entirely. Differing template
versions or policies are not aggregated.

### Decision 10: Schema version 2 is defined once and is complete

After the preceding fields are fixed, schema version 2 is the single authoritative durable record
shape. Version 1 is rejected explicitly. Every record contains:

- `schema_version`, `run_id`, `evaluation_question`, `actor`, `scenario_id`, `item_id`, `arm`,
  `repetition`, `started_at`, `terminal_state`, and external `results_root` identity.
- `corpus_version`, corpus migration/provenance version, scenario manifest hash, seed artifact hashes,
  focal investigation identity, reuse-history condition, and seed immutability result.
- prompt policy, prompt-template identifier/version/hash, and a no-expectation-leak validation result.
- subprocess environment policy/version, executable path/hash or package provenance, repository
  pre/post audit result, process-group identity, configured bounds, and any exceeded bound.
- for live records: host name/version, model identity/version when reported, exact host session id,
  session-attribution validation, and transcript-persistence value fixed to `false`.
- wall-clock and per-stage durations; token and monetary usage values or explicit unavailable reasons;
  lifecycle friction events in the documented control order.
- lifecycle detection outcomes with reporting control and `caught`, `missed`, or `not-exercised`;
  mutation identity and baseline reference when applicable.
- cross consultation state, exact command/query identity, structured result projection/hash, expected
  projection/hash for fixtures, per-check `correct`, `incorrect`, `not-exercised`, or `not-consulted`,
  negative-control outcomes, metamorphic relation identity, and source run references.
- approval provenance/state, operator record reference when present, and a flag that synthetic
  approval was excluded from live evidence.
- traceable evidence references limited to artifacts, exit codes, and structured CLI fields; never a
  model judgement, transcript, or raw host export.

Reports reject incompatible schema, corpus, resolver-chain, prompt-template, treatment, host/model,
or scenario-provenance groupings rather than silently combining them.

## Risks / Trade-offs

- **One paid session cannot support population claims.** The pilot is an operational observation and
  spend gate only; it is not a statistical sample.
- **Assistance changes discovery behavior.** Assisted and unassisted are explicit non-aggregated
  treatments, and only assisted is in the first reuse pilot.
- **Exact retrieval can be correct but useless.** The harness reports exactness and consultation only
  and makes no applicability, recommendation, criterion-level, causal, or value claim.
- **A live run may never reach transfer.** `not-reached` remains distinct from an operator-pending
  boundary and from synthetic fixture state.
- **A host may spawn descendants or reshape exports.** Process-group teardown bounds descendants;
  host version and unavailable reasons preserve interpretability without guessing.
- **Corpus migration can change historical scores.** New baselines are versioned after migration;
  old records are not rewritten.
