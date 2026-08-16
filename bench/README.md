# SDR evaluation harness

Maintenance tooling that measures SDR's lifecycle controls and cross-investigation retrieval. It
is not a shipped runtime capability: `bench/` is excluded from the wheel and the source
distribution, and nothing in `src/sdr/` imports it.

Every metric is computed from artifacts, exit codes, and structured CLI output. There is no
LLM-based scoring of any kind, consistent with the framework rule that matching does not use
models.

```
bench/
  corpus/
    corpus.yaml           corpus-level metadata; 'version' is required
    items/<item-id>.yaml  one corpus item per file, stem equal to its id
  reuse-corpus/            isolated reuse scenario roots for cross-retrieval evidence
  harness/
    corpus.py             typed loading, closed defect vocabulary, synthetic-source rule
    runspace.py           disposable Git-initialized research roots outside the repository
    actor.py              the actor protocol, the scripted actor, the live actor boundary
    arms.py               arm applicability, repetitions, isolated execution
    detection.py          planted-defect scoring from structured command payloads
    controls.py           the canonical blocking-control vocabulary and ordering
    mutation.py           declarative blocking-control mutations and coverage auditing
    reuse.py              reuse scenario schema, materializer, and seed immutability
    cross_scoring.py      exact cross-consultation and structured projection scoring
    metamorphic.py        fixture-only metamorphic relation execution
    prompts.py            versioned, expectation-blind prompt templates
    cost.py                wall-clock per run and per stage, token accounting
    friction.py            reopens, gate failures by control type, claim closures
    record.py              the durable schema-version-2 run-record shape and serialization
    record_builders.py     typed builders that assemble schema-version-2 records
    report.py              the deterministic, question-separated comparison report
    runner.py              the command-line entry point, including exact scalar `--live`
    live.py                 the two-key OpenCode connector and credential/XDG isolation
    enforcement.py          the harness-owned OpenCode plugin's pre-dispatch mediation boundary
    pilot.py                exact scalar pilot planning and observed-identity attribution
```

## Three separate evaluation questions

The harness answers three separate questions. Every run declares exactly one
`evaluation_question`, reports render one section per question, and the harness never computes a
combined score across them:

- **`lifecycle-control-observability`**: offline scripted mutation evidence over the
  lifecycle-control corpus. Weakens one named blocking control at a time in a throwaway package
  copy and checks whether the harness observes the expected loss.
- **`live-single-investigation`**: one bounded, exactly attributed, paid OpenCode session,
  measuring workflow, exact host-reported cost, and friction only.
- **`cross-retrieval`**: deterministic scripted/metamorphic exact-output validation of the
  cross-investigation CLI, followed by separately labelled assisted or unassisted live
  consultation observations.

None of these answers whether retrieved material was semantically applicable or useful, and no
output is scored by a model. See "Prohibited claims" below.

## Corpus contract

### Item schema

One YAML file per item under `corpus/items/`, whose stem must equal the item `id`. Items load
in sorted filename order, so loading is stable regardless of the filesystem.

Required keys:

| Key | Meaning |
| --- | --- |
| `id` | Matches `[a-z0-9][a-z0-9-]*`, unique across the corpus, equal to the file stem. |
| `mode` | `light` or `full`. The lifecycle mode the item is executed under. |
| `title` | Short human label. |
| `question` | The invented research question. |
| `planted_defects` | List of names from the closed vocabulary below. May be empty. |

Optional keys:

| Key | Meaning |
| --- | --- |
| `expected_detection` | Mapping from a planted defect to `caught` or `uncaught`. Unlisted planted defects default to `caught`. Naming a defect that is not planted is an error. |
| `sources` | List of `id`, `url`, `title`, `tier`, `date`, and an optional `snapshot` body. Source ids must be unique within the item. |
| `artifacts` | Mapping of research-root relative path to file body, written by the scripted actor before the commands run. Absolute paths and `..` are rejected. |
| `commands` | List of argv lists replayed in order by the scripted actor. |
| `probe` | Probe declaration. Only valid for `full` items, because `light` skips probe. |
| `notes` | Authoring notes. Not interpreted by the harness. |

At least one item must declare an empty `planted_defects` list. Clean items are what make false
positives measurable.

Note bodies use the section headings the artifact contract enforces today: the English headings
declared once in `src/sdr/schema.py`, which `src/sdr/gates.py` and the packaged templates both
resolve from. The corpus tracks the contract as it is, so a change to that declaration is a
corpus change.

### Closed planted-defect vocabulary

`planted_defects` accepts only these names; anything else fails loading. Each is derived from the
failure taxonomy in `docs/evidence-model.md` and the README failure statement, deliberately not
from `src/sdr/gates.py`: writing planted defects from the gate implementations would guarantee a
perfect score and measure nothing.

| Defect | Taxonomy origin |
| --- | --- |
| `unreachable-source` | Sources that disappear or become inaccessible. |
| `unanchored-claim` | A claim its cited source does not support. |
| `unreproducible-result` | A result asserted without a reproducible check. |
| `contradictory-sources` | Evidence that contradicts itself across alternatives. |
| `probe-expectation-mismatch` | A probe whose expectation does not match its command. |
| `uncovered-criterion` | A criterion left without a mapped result. |
| `stale-validation` | Content changed after the stage or probe was validated. |
| `unapproved-decision` | A decision presented as approved without a recorded approval. |
| `inaccurate-source` | A passing gate does not make a source accurate. |
| `unrepresentative-benchmark` | A passing gate does not make a benchmark representative. |

The last two are confidence boundaries: no current control is expected to catch them. Keeping
them in the corpus is the point. A corpus that only contains defects SDR already catches measures
nothing about what it misses.

### Synthetic-source rule

Every URL in an item must use a reserved or guaranteed non-resolvable domain: `example.com`,
`example.net`, `example.org` and their subdomains (RFC 2606), or the `.invalid`, `.test`,
`.example` and `.localhost` top-level domains (RFC 6761). The rule applies to declared source
URLs and to every URL embedded in a source snapshot or an artifact body. Loading fails otherwise.

That keeps the corpus fully invented and offline: an unreachable source is unreachable by
construction, not because some external site happened to fail during a run. No corpus item
depends on a real organization, a real person, or a live endpoint.

### Corpus migration and baseline provenance

The lifecycle-control corpus predates current snapshot provenance and required decision
`evidence_claim_ids`. Every retained corpus item is migrated and revalidated against the current
snapshot provenance contract before a new baseline is preserved: completed seed decisions must
explicitly carry `evidence_claim_ids`, and omission is never converted into inferred lineage.
Corpus items also carry `migration_provenance_version` so a run can prove which migration pass it
was validated under.

Old baselines (recorded before this migration) are **historical inputs only** and are never
relabelled as current-version results. A new scripted baseline is preserved only after the
migration and lineage checks in `tests/test_bench_corpus.py` / `tests/test_bench_corpus_items.py`
pass for the current corpus version.

## The two actors and the credential boundary

An actor produces the research work of one run. The harness ships two implementations, and the
report never aggregates their results into one number.

**Scripted actor (default).** Replays the artifact writes and CLI invocations declared in the
corpus item, invoking the real CLI from `src/` inside the run's isolated root. It injects
`--offline` into every offline-capable command. Deterministic, free, no network, no API key.

**Live actor (opt-in, off by default).** Drives a real OpenCode session and reports its token
usage and real stage durations from that session's export only.

Subprocess environments are split by trust boundary:

- **Scripted, blocking-control mutation, and reuse metamorphic subprocesses** always run in an
  allowlisted, **credential-free** environment: credential-shaped variables are stripped before
  the subprocess starts, and executable/package provenance is recorded. Nothing in these paths can
  read an API key, a token, or a saved host credential.
- **The live OpenCode connector** is the only path that reads any credential, and it does so
  through a separate, deliberately narrow **two-key opt-in**: the `SDR_BENCH_LIVE_ACTOR`
  environment variable must be set *and* the runner's exact scalar `--live` CLI flag must be
  passed. Missing either key starts no host, reads no credential, and makes no network request.
  When both keys are present, the connector copies only the names in its own fixed, code-owned
  credential allowlist (for example `OPENCODE_API_KEY`); the caller cannot add names or pass
  through a general environment map, and loader/runtime injection variables are rejected. The
  connector also points `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and
  `XDG_STATE_HOME` at isolated, harness-owned directories in the runspace, so no user config,
  cache, saved permission, or unrelated host state is inherited.

**Detection is measured under the scripted actor; cost and friction are meaningful only under the
live actor.** The scripted actor measures the controls: it says whether a gate fires on a defect,
which is exactly what has to keep working across commits, and it is the only way to catch a gate
that silently stopped firing. It says nothing about whether SDR helps a real investigation, and
its timings are process-startup cost, not research cost. Its token accounting is recorded as
unavailable, never as zero. The live actor measures the workflow, at the price of variance,
money, and non-reproducibility.

Under the scripted actor the baseline arm is degenerate: no gate runs, so every planted defect is
missed by construction. That is recorded as a control constant and printed as `control-constant`,
never as a measured 0% detection rate.

## Blocking-control mutation validation

For `lifecycle-control-observability`, the harness copies `src/sdr` to a throwaway location,
declaratively weakens exactly one named blocking control, and re-runs the scripted corpus against
that mutated copy in the credential-free environment. It compares the mutated detection outcomes
against the unmutated scripted baseline: a mutation must lose exactly the baseline catches
attributable to that control, an unchanged detection projection fails validation, and a
stale/no-op transformation is rejected rather than silently passing. A coverage audit, run in
canonical control order, requires either a mutation or an explicit infeasibility reason for every
blocking control type.

## Exact and metamorphic reuse validation

For `cross-retrieval`, one isolated scenario root holds immutable completed seed investigations
plus exactly one focal, writable investigation; no scenario shares a root with another scenario or
repetition. Seed manifests and artifact hashes are captured before execution and verified after.
Fixtures include at least one **non-software** scenario alongside software ones, and every fixture
declares exact positive expectations and exact **negative controls** — an explicit expected
absence, not merely a lack of expectations.

The harness records whether the cross CLI was consulted and captures the exact structured
query/result projection the fixture expects. Every expected check gets one of four outcomes,
intentionally kept distinct:

| Outcome | Meaning |
| --- | --- |
| `correct` | The declared command was exercised and its structured projection exactly matched. |
| `incorrect` | The declared command was exercised and its structured projection did not match. |
| `not-exercised` | Consultation occurred, but that declared query/check was not run. |
| `not-consulted` | No cross command was consulted in the run at all. |

Separately, metamorphic validation transforms fixture inputs while shipped code stays unchanged
(URL-spelling normalization, removing a shared identity so a join must disappear, a `done`-to-
`active` transition that must exclude a decision, permuted seed materialization order requiring
identical output). Metamorphic fixtures validate deterministic retrieval relations, not whether a
lifecycle control blocks, and a failing test in `tests/test_bench_mutation_validation.py` proves
fixture metamorphisms alone do not satisfy blocking-control mutation coverage.

## Treatments: orthogonal, never aggregated

Three factors are tracked and reported separately. None is folded into another, and none is
aggregated across:

- **Arm** (`baseline`, `light`, or `full`) — the lifecycle mode a scripted or live run executes
  under.
- **Reuse-history condition** (for example `history-present` / `history-absent`) — whether the
  scenario root's seed investigations exist. This is a separate, **orthogonal** condition, never
  encoded as a fourth arm.
- **Prompt policy** (`assisted` or `unassisted`) — whether the prompt explicitly requires cross
  consultation (`assisted`) or omits cross guidance entirely and measures spontaneous discovery
  (`unassisted`). The two policies are reported separately and are **never aggregated**. The first
  reuse pilot uses `assisted` only; `unassisted` measurement is deferred until separately planned
  and authorized, and spontaneous cross discovery during an assisted run is not treated as
  unassisted evidence.

Reports also reject incompatible schema, corpus, resolver-chain, prompt-template, treatment, or
host/model groupings rather than silently combining them.

## Pilot identity: one exact scalar `--live` session

A pilot is exactly **one** planned paid session — never a matrix expanded across arms,
repetitions, items, or hosts. Its plan fixes exactly one scenario/item identifier, one arm, one
repetition index, one host and host version, one model identity, one prompt-template
policy/version, bounds (max turns and wall-clock), and one external results root.

The runner exposes this only as the exact scalar form of `--live`:

```
uv run python -m bench.harness --live \
  --live-scenario cross-onboarding --live-item cross-onboarding-focal \
  --live-arm light --live-repetition 1 \
  --live-host opencode --live-host-version <version> \
  --live-model <model-id> \
  --live-prompt-policy assisted --live-template-version <version> \
  --live-max-turns <n> --live-wall-clock <seconds> \
  --live-results-root /outside/the/repository/results.json
```

Boolean flags that would expand a matrix, lists, ranges, or implicit defaults that add sessions
are invalid and refused. Observed pilot identity is never initialized from or completed with plan
fields: the harness independently derives the canonical repository root, the materialized focal
and seed roots, and derives observed identity from exact bytes/digests of the materialized
corpus/scenario manifest, sealed execution request, canonical prompt evidence, resolved runspace,
captured executable/host version, every structured event, and the exact session export. Scalar
caller attestations (a scenario string, a root path, a hash, an arm name) are never accepted as
evidence identity by themselves. The plan is used only for the final field-by-field comparison
against that independently derived observed identity; a missing field or any mismatch fails
attribution. The command exits after that one session and reports observed usage, cost,
wall-clock, terminal state, and approval state. Any additional paid session requires a new,
separate, explicit operator authorization.

## HITL stop: the harness never approves

Live lifecycle execution only ever progresses through `transfer`. On reaching the approval
boundary, the live session stops with terminal state `awaiting-operator-approval` and approval
state `operator-pending`. The harness-owned OpenCode plugin denies `approve`, every
`resolve-claim`, generic `sdr` execution, and direct/unmediated lifecycle metadata writes at the
pre-dispatch boundary; it exposes only a narrow, stage-specific, argv/no-shell lifecycle tool.

The harness **never** calls `approve`, fabricates an approver, or substitutes `resolve-claim` for
a real operator decision. A separate real operator record is required before a run's approval
state may move to `operator-approved` or `operator-rejected`; that decision references the
stopped run and is recorded outside the agent's authority. Synthetic approval states
(`synthetic-approved` / `synthetic-rejected`) exist only for scripted fixture validation, are
visibly synthetic, are never accepted as live operator evidence, and are never used by the initial
pilot.

## Schema version 2

`bench/harness/record.py` defines **schema version 2** as the single authoritative durable
run-record shape. It is complete: lifecycle, live, reuse, treatment, approval, and provenance
fields are all specified in one migration, with no intermediate persisted schema shape. Loading a
record whose `schema_version` is version 1 (the prior, incomplete run-record shape) is rejected
explicitly rather than coerced into version 2. Every version-2 record carries, among other fields,
`evaluation_question`, corpus/provenance and migration identities, prompt-template/hash and
leak-validation evidence, subprocess environment policy and executable
provenance, live host/session/approval evidence when applicable, lifecycle detection outcomes
(`caught` / `missed` / `not-exercised`), and cross-check outcomes (`correct` / `incorrect` /
`not-exercised` / `not-consulted`). See Decision 11 in
`openspec/changes/add-live-harness-evidence/design.md` for the exhaustive field list.

## Running it

Every run materializes its research root in a disposable, Git-initialized temporary directory
outside the repository, sets `SDR_ROOT` to it, and removes it afterwards. The entry point refuses
any `--records` or `--report` path that resolves inside the repository tree, so a harness
invocation cannot leave run output, a research root, or lifecycle metadata in the working tree.

All three arms over the whole corpus, report to stdout:

```
uv run python -m bench.harness
```

One arm at a time. `--arm` is repeatable; an item is applicable to the arm matching its declared
mode and to the mode-free baseline, so a `light` item produces a `not-applicable` record in the
`full` arm rather than a failure:

```
uv run python -m bench.harness --arm baseline
uv run python -m bench.harness --arm light
uv run python -m bench.harness --arm full
uv run python -m bench.harness --arm light --arm full
```

Repetitions, and durable output outside the repository:

```
uv run python -m bench.harness --repetitions 3 \
  --records /tmp/sdr-bench/records.json \
  --report /tmp/sdr-bench/report.md
```

Run records to stdout with no report, for piping:

```
uv run python -m bench.harness --records - --no-report
```

Re-render a stored record set without executing anything. This is the only determinism the
harness claims, and it is byte-identical:

```
uv run python -m bench.harness --from-records /tmp/sdr-bench/records.json
```

Remaining flags: `--corpus` points at another corpus root, `--max-workers` bounds parallelism over
disjoint roots, and `--durations omitted` drops the wall-clock columns. `--help` lists all of them,
including the `--live-*` scalar pilot flags documented above.

Refused invocations -- a missing corpus, a repetition count below one, an output path inside the
repository, a `--live` invocation missing a required scalar field or attempting a matrix -- print
one line on stderr and exit 2.

The harness has its own tests, and the repository's public-tree audit must stay clean after a run:

```
uv run pytest tests/test_bench_corpus.py tests/test_bench_corpus_items.py \
  tests/test_bench_runspace.py tests/test_bench_actor.py tests/test_bench_arms.py \
  tests/test_bench_detection.py tests/test_bench_cost.py tests/test_bench_friction.py \
  tests/test_bench_record.py tests/test_bench_record_v2.py tests/test_bench_report.py \
  tests/test_bench_runner.py tests/test_bench_environment.py tests/test_bench_mutation.py \
  tests/test_bench_mutation_validation.py tests/test_bench_reuse_corpus.py \
  tests/test_bench_cross_scoring.py tests/test_bench_metamorphic.py tests/test_bench_live.py \
  tests/test_bench_enforcement.py tests/test_bench_docs_contract.py
uv run python -m sdr.public_tree_audit .
```

## Reading the report

The report has one section per `evaluation_question` present in the record set, then one
sub-section per actor within it, and never aggregates across questions, actors, arms, treatments,
or reuse-history conditions. Rows are ordered by item, then arm, then repetition; numbers are
printed at fixed precision; no timestamp appears in the body. Run records carry timestamps, the
report does not.

The header states `corpus_version`, `repetitions`, `run_records`, and which actors are present.

**Lifecycle-control-observability.** `planted_defects` counts declared defects over measured runs,
and `caught` / `missed` / `not-exercised` split them by what a structured finding actually
reported. `detection_rate` is `caught / (caught + missed)` and is printed only when a rate may
honestly be claimed; otherwise the cell reads `control-constant` (true by construction),
`not-measured` (no measured run), or `no-planted-defects`. `not-exercised` outcomes (a skipped
reporting control, or the lifecycle never entered) are reported separately and are never folded
into `missed`. `false_positives` counts blocking findings on clean items. `basis` says whether the
arm's numbers were measured, are a control constant, or mix both. Mutation validation reports
which blocking controls had an observed loss and any explicit unresolved coverage reason.

**Cross-retrieval.** Each expected check is reported as `correct`, `incorrect`, `not-exercised`,
or `not-consulted`, plus negative-control outcomes and the metamorphic relation identity when
applicable. Assisted and unassisted prompt-policy observations are printed in separate blocks and
are never combined into one consultation rate.

**Live-single-investigation.** `costed_runs` and `uncosted_runs` separate runs that produced a
wall-clock observation from those that did not. `tokens_total` reads `unavailable` when no run
reported usage -- that is not zero tokens -- and `token_coverage` shows how many costed runs
reported any. Relative cost is computed only over `matched_runs`: identities costed in both the
arm and the baseline, under the same actor. Unmatched runs are printed but never enter the ratio.
The stage line breaks wall-clock down by lifecycle stage in stage order. Terminal and approval
state (including `awaiting-operator-approval` / `operator-pending`) are printed alongside cost.

**Friction.** Reopens counted from trail commits, gate failures attributed to the documented
control vocabulary (`structural`, `evidential`, `textual-anchoring`, `executable`,
`hash-consistency`, `human-approval`), claims closed through `resolve-claim` counted separately
from claims that passed anchoring.

**Unmapped gate failures.** `lifecycle.py` composes its blocked reason as free English prose. The
harness maps that prose to a control through an explicit table and routes anything unmatched, or
anything matching more than one control, to `unmapped` rather than guessing. Every unmapped entry
is printed in full with its item, arm, repetition, and exit code. **A growing unmapped bucket is
itself a finding**: it means the CLI needs stable machine-readable failure codes.

**Runs.** One row per run record, including `not-applicable` and `errored` outcomes, so an absent
number is always distinguishable from a zero. Absence is labelled `not-run`, `unavailable`, or
`not-costed`, never rendered as `0`.

**Prohibited claims.** Every report carries a standing limitations block. The harness's evidence
MUST NOT be presented as a semantic, quality, criterion-level, statistical-significance, causal, or
cross-value claim: exact structured retrieval does not establish that retrieved material was
semantically applicable, that a recommendation was good, that a criterion-level reuse decision was
correct, that a result is statistically significant, that consulting the cross CLI caused an
outcome, or that consultation added value. The three evaluation questions, and the assisted and
unassisted treatments within `cross-retrieval`, are also never aggregated into one combined
result.

## First scripted baseline

Observed on 2026-08-01 from `uv run python -m bench.harness`, corpus version `1`, 8 items, 3 arms,
1 repetition, 24 run records, scripted actor only, `evaluation_question` =
`lifecycle-control-observability`. Detection and friction counts reproduce across executions;
wall-clock does not, and the figures below come from one run. This baseline predates the corpus
migration described above and is retained as a historical input only; it is not the current
migrated baseline.

| Arm | Executed | Measured | Planted | Caught | Missed | Detection rate | False positives |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 8 | 0 | 0 | 0 | 0 | control-constant | 0 |
| light | 5 | 5 | 4 | 1 | 3 | 0.250 | 0 |
| full | 3 | 3 | 2 | 1 | 1 | 0.500 | 0 |

Per planted defect:

| Item | Arm | Defect | Observed | Reporting control |
| --- | --- | --- | --- | --- |
| `unanchored-claim-parser-metrics` | light | `unanchored-claim` | caught | `verify-claims` / textual-anchoring |
| `probe-expectation-mismatch-hash-guard` | full | `probe-expectation-mismatch` | caught | `verify-probe` / executable |
| `unreachable-source-cache-warmer` | light | `unreachable-source` | missed | none; `links_resolve` skipped offline |
| `contradictory-sources-retry-window` | light | `contradictory-sources` | missed | none |
| `uncaught-inaccurate-source-throughput` | light | `inaccurate-source` | missed | none |
| `uncaught-unrepresentative-benchmark-queue` | full | `unrepresentative-benchmark` | missed | none |

Neither clean item produced a false positive: `clean-light-label-router` and
`clean-full-checksum-shim` both reached `reuse` with zero blocking findings.

Friction, scripted: no reopens in any arm. The light arm attributed 3 gate failures to
textual-anchoring and 10 claims passed anchoring; the full arm attributed 3 to executable and 6
claims passed. No claim was closed through `resolve-claim`. Two blocked reasons landed in the
`unmapped` bucket, both the same `approve` stage-guard message
(`approve only applies in the transfer stage; ...`), which is a stage guard rather than a control
failure and has no control to attribute.

Cost, scripted: baseline 0.014 s over 8 runs, light 14.207 s over 5 runs (mean 2.841 s), full
10.242 s over 3 runs (mean 3.414 s). These are CLI process-startup and gate-execution costs of a
replay, not the cost of doing research; the arm-versus-baseline ratios (about 1500x and 2000x) are
ratios against a baseline arm that runs no command at all, and mean nothing about workflow cost.
Token coverage is 0/8, 0/5 and 0/3: the scripted actor reports no token usage, and this is
recorded as unavailable, never as zero.

### Defects no current control caught

Input to the next roadmap phase, in decreasing order of how much the gap contradicts what the
framework claims:

1. **`contradictory-sources`** (`contradictory-sources-retry-window`, light). The evidence model
   presents evidential validation -- source declarations, tiers, triangulation -- as the control
   over evidence that contradicts itself across alternatives, but no structured check reports the
   contradiction. `bench/harness/detection.py` has no control mapped for this defect because no
   control emits one. This is the one uncaught defect the documentation implies should be caught.
2. **`unreachable-source`** (`unreachable-source-cache-warmer`, light). Not a control gap: the
   `links_resolve` check exists and is the right control, but offline it is skipped, never passed
   (`skipped (--offline)` in the run record's `skipped_checks`). This is now scored
   `not-exercised` rather than `missed`.
3. **`inaccurate-source`** (`uncaught-inaccurate-source-throughput`, light) and
   **`unrepresentative-benchmark`** (`uncaught-unrepresentative-benchmark-queue`, full). Expected
   uncaught, and observed uncaught. These are the documented confidence boundaries: a passing gate
   does not mean a source is accurate or a benchmark representative. They are not defects to fix;
   they are the measured evidence that the boundaries in `docs/evidence-model.md` are real, and
   they should stay in the corpus permanently.

Two coverage gaps in the measurement itself, not in the framework:

- Four vocabulary entries are not planted in any item yet: `unreproducible-result`,
  `uncovered-criterion`, `stale-validation`, and `unapproved-decision`. The first three have a
  mapped control in `detection.py` and are therefore cheap corpus additions that would test
  controls nothing currently exercises. `unapproved-decision` has no mapped control at all, so
  planting it requires deciding which structured field a missing HITL approval surfaces in.
- The `live-single-investigation` pilot (Section 11 of
  `openspec/changes/add-live-harness-evidence/tasks.md`) requires a real operator's explicit
  authorization and HITL decision and has not run; every number above describes the controls, not
  the workflow.

For the exhaustive design rationale behind every claim in this document -- corpus migration,
credential isolation, exact reuse outcomes, orthogonal treatments, pilot identity, the HITL stop,
and schema version 2 -- see Decisions 1-11 in
`openspec/changes/add-live-harness-evidence/design.md`.
