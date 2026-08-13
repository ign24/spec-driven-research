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
credential-shaped variables removed. Each live connector owns a fixed, code-defined credential
variable allowlist; the caller cannot add names or pass through a general environment map. The live
path copies only those explicitly present credential values plus fixed non-secret harness values and
rejects loader/runtime injection variables, including language/runtime option and search-path
overrides. `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME` point to distinct
harness-owned directories in the runspace, so no user config, data, cache, state, or saved permission
is inherited. The OpenCode executable is resolved before credential access and its canonical path,
device/inode identity, bytes hash, and package/version provenance are fixed for the run. This ordering
prevents a mutation test from accidentally becoming the first credential-inheritance test.

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

The first connector drives the real OpenCode plugin/socket path headlessly in the disposable scenario
root. It captures and reconciles every session-bearing event, not only the first and last, and requires
one non-empty immutable session identifier across the complete stream and sanitized export. A missing,
second, or conflicting identifier fails attribution. Usage and monetary cost come only from that exact
session's sanitized export, never from a host-wide aggregate, maintained price table, transcript, or
model interpretation. Missing or unavailable export usage is `unavailable` with a reason and has no
model fallback. The durable record stores aggregates and selected structured evidence, not the
transcript or raw export.

Every live process starts in a new process group. Turn and wall-clock bounds apply to the complete
process tree and to every mediator-spawned lifecycle, inspection, verification, and export subprocess.
Timeout, transfer interruption, parent cancellation, or any failed invariant propagates cancellation,
terminates descendants, waits for and joins every child, and only then tears down the runspace. No
mediator subprocess may outlive its request or the host. The repository tree is hashed/audited before
and after execution, and all run outputs live under the operator-configured external results root.

Reaching transfer is a successful intentional harness stop, not a normal host completion and not a
generic timeout/error. After the harness has interrupted and reaped the host process group, it still
executes the non-session-creating export command for the immutable captured session identifier. The
export is reportable only when its top-level identity and all attributed assistant records match that
identifier. Failure to export or attribute after the intentional stop produces explicit unavailable
usage and failed attribution without erasing the transfer observation.

Live execution requires two independent keys: the existing environment opt-in and an explicit CLI
flag. Defaults start no host, read no host credential, and make no network request.

### Decision 7: Pilot identity is a complete paid-session plan

A pilot is exactly one planned paid session, not one item expanded across arms or repetitions. Its
plan fixes one scenario/item identifier, one arm, one repetition index, one host and host version,
one model identity, one prompt-template policy/version, bounds, and one external results root. The
runner exposes this only as the exact scalar pilot form of `--live`; booleans that expand a matrix,
lists, ranges, or implicit defaults that add sessions are invalid. Observed identity is not initialized
from or completed with plan fields. The harness internally derives the canonical repository root and
the materialized focal and seed roots, verifies their containment/disjointness, and derives observed
identity from exact bytes/digests of the materialized corpus/scenario manifest and sealed execution
request, canonical prompt evidence, resolved runspace, captured executable and host version, every
structured event, and exact session export. Scalar caller attestations such as scenario, root, hash,
arm, or treatment strings are not evidence identities. The plan is used only for the final
field-by-field comparison. Missing evidence or any mismatch fails attribution. The command exits after
that session and reports observed usage, cost, wall-clock, terminal state, and approval state.
Additional paid sessions require a new explicit operator authorization based on that observation.

The initial reuse pilot, if selected, is assisted. It cannot be described as spontaneous discovery.

### Decision 8: A harness-owned OpenCode plugin mediates the live boundary

The harness materializes a dedicated OpenCode configuration and one harness-owned plugin into the
external runspace. Before host startup it captures their paths, exact bytes, SHA-256 hashes, ownership
within the runspace, device/inode identities, OpenCode executable provenance, and the host's machine-
readable effective configuration. The executable, config, and plugin canonical paths, device/inode
identities, and byte hashes are fixed and revalidated before credential access/host startup, before
each tool dispatch, before and after every mediator subprocess boundary, and before export. Any
replacement, symlink/containment change, hash mismatch, or inability to revalidate is fatal. Startup
fails closed unless the effective configuration demonstrates the expected isolated XDG roots, config
root, and exact plugin identity and demonstrates that external plugins, MCP servers, inherited or
saved permission grants, loader/runtime injection, and every unmediated bash/shell/command-execution
facility are absent or denied. The live environment does not inherit caller-selected config/XDG
variables or a caller-extended credential list. A preflight that cannot prove the effective state is
a refusal, not a warning or an unavailable provenance field.

The plugin/socket control token is created by and retained in the harness host process. It is passed
only through the private control-channel setup needed to authenticate the plugin, is never available
to the model/tool environment, and is redacted from errors, debug logs, structured events, records,
and exports. Missing redaction proof or any observed token value terminates the run.

The plugin is the pre-dispatch enforcement boundary. It denies `approve`, every `resolve-claim`,
generic `sdr` execution, direct/unmediated lifecycle metadata writes, and direct bash/shell use. It
exposes one narrow custom SDR tool whose request must declare `verify.action: run` and an argv array;
command strings, shell parsing, alternate executables, undeclared flags, caller paths, and caller
environment additions are rejected. The mediator owns a stateful stage-specific command and focal-
artifact allowlist derived from the independently parsed current stage: intake permits its declared
brief edits and `advance`; explore permits declared note edits, `verify-claims`, and `advance`; probe
permits declared probe artifacts, `verify-probe`, and `advance`; transfer permits no mutation or
transition and triggers the stop; reuse, only in a separately operator-authorized resumed run, permits
declared asset edits and `advance`. Fixed read-only `status`, `check`, and plan-declared cross queries
are available where required. `check` is always exactly `sdr check <slug> --offline --json`, lifecycle
advances are exactly `sdr advance <slug> --offline --no-commit`, verification flags/timeouts are fixed
by the sealed request, and no stage permits `approve`, `resolve-claim`, `reopen`, `drop`, `archive`,
`new`, or a generic escape to the SDR CLI.

Before exposing any tool, the mediator independently runs and schema-validates both
`sdr status <slug> --json` and `sdr check <slug> --offline --json`, then verifies semantic consistency
among status, check, manifest, sealed request, filesystem, and stage/artifact allowlists. Only one
lifecycle command may be in flight. For every authorized command the mediator captures the pre-state
semantic metadata and protected-file identities, predicts the exact command-specific metadata
transition, dispatches one fixed executable without a shell, then independently repeats both
inspections and semantic consistency validation even when the command fails. A successful
`verify-claims`, `verify-probe`, or `advance` may change `sdr.yaml` only through the mediator-executed
CLI and only by the exact predicted fields/values and stage transition; direct writes, no-op success,
additional fields, missing expected fields, stale validation, or any otherwise unexplained metadata
delta fail attribution and block every later action. A failed command permits no metadata delta unless
its documented exact failure transition was preauthorized. Thus `sdr.yaml` is protected from direct
or unmediated writes, not from the exact legitimate CLI-owned transition being measured.

Stage decisions use only the independently validated structured results. When status first reports
transfer, the plugin signals the harness over its authenticated runspace-local control channel and
refuses every subsequent tool request. The harness immediately interrupts the complete host process
group, without waiting for another model turn, joins mediator work, then follows the intentional-stop
export path in Decision 6.

The plugin also mediates file mutation. It rejects direct writes to `sdr.yaml`, all seed
investigations, repository paths, config/plugin files, result records, and undeclared files. It
permits only the exact current-stage focal artifact identities derived from the sealed request and
materialized manifest under the internally derived focal root. Pre/post hashes and device/inode
identities prove protected files stayed unchanged, except that mediated lifecycle commands must
produce the exact authorized `sdr.yaml` semantic transition described above. A denied or ambiguous
path, alias, replacement, or unexplained metadata delta terminates attribution rather than falling
back to a broader write permission.

Three approaches were considered:

1. **Harness-owned plugin mediation (selected):** enforces commands and writes before dispatch while
   retaining the current headless OpenCode connector. Its configuration and plugin can be isolated,
   hash-pinned, fake-host tested, and included in run provenance.
2. **PATH wrapper around `sdr` (rejected):** cannot prevent an agent from invoking an absolute binary,
   another shell, a file tool, or direct metadata writes, so it is bypassable and cannot establish the
   HITL or write boundary.
3. **Embedded OpenCode SDK/session driver (deferred):** could provide stronger in-process control but
   requires a broader connector and lifecycle rewrite than this evidence change. It remains a future
   option if plugin hooks cannot expose a demonstrably complete pre-dispatch boundary.

### Decision 9: Live lifecycle execution never impersonates HITL

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

Approval and terminal states are validated together. Before transfer, any error, bound, cancellation,
or host completion has approval `not-reached` and cannot use terminal
`awaiting-operator-approval`. The intentional transfer stop has terminal state
`awaiting-operator-approval` and approval `operator-pending`. A later `operator-approved` or
`operator-rejected` decision requires a separate real operator record referencing that stopped run;
when joined for reporting, the immutable session terminal remains `awaiting-operator-approval` and the
decision supersedes pending approval without implying another agent session. These decided states
cannot appear in initial-session evidence. Synthetic approval is valid only for scripted fixture
terminal states and can never pair with a live terminal. Any other pairing is rejected rather than
normalized.

### Decision 10: Canonical prompts preserve treatment identity without leaking expectations

Prompts are versioned by evaluation question, arm, and prompt policy. They are built from declared
scenario inputs only and exclude planted defects, expected detections, expected retrievals, negative
controls, and scoring vocabulary. Assisted reuse prompts may require use of the cross CLI but may not
name expected queries or results. Unassisted prompts omit cross guidance entirely. Differing template
versions or policies are not aggregated.

Every live prompt is constructed immediately before execution through the canonical
`build_prompt(PromptInputs, PromptLeakSignals)` path and remains one immutable `BuiltPrompt`. The
transfer-stop instruction is part of the declared canonical template/input before construction; it is
never appended after leak validation. The harness recomputes and records the template hash and exact
submitted-prompt hash, requires `leak_validation_passed`, and proves that the submitted bytes are the
validated `BuiltPrompt.text`. A caller-supplied string, reconstructed treatment fields, stale hash, or
post-validation mutation fails before host startup.

### Decision 11: Schema version 2 is defined once and is complete

After the preceding fields are fixed, schema version 2 is the single authoritative durable record
shape. Version 1 is rejected explicitly. Every record contains:

- `schema_version`, `run_id`, `evaluation_question`, `actor`, `scenario_id`, `item_id`, `arm`,
  `repetition`, `started_at`, `terminal_state`, and external `results_root` identity.
- `corpus_version`, corpus migration/provenance version, scenario manifest hash, seed artifact hashes,
  focal investigation identity, reuse-history condition, and seed immutability result.
- prompt policy, prompt-template identifier/version/hash, exact submitted-prompt hash, canonical
  `BuiltPrompt` construction result, and a no-expectation-leak validation result.
- subprocess environment policy/version, executable path/hash or package provenance, repository
  pre/post audit result, process-group identity, configured bounds, and any exceeded bound; for
  OpenCode, fixed credential allowlist identity, isolated XDG roots, executable/config/plugin
  path-device-inode-hash revalidation evidence, effective-config isolation proof, mediator-token
  non-disclosure proof, and subprocess cancellation/join outcomes.
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
- lifecycle mediation outcomes, protected-file pre/post hashes, intentional-transfer-stop evidence,
  command-specific expected/observed metadata transitions, initial/post-command status/check
  consistency, exact manifest/sealed-request identities, and independently derived observed-identity
  evidence references.
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
- **Plugin completeness depends on host hooks and effective-config introspection.** The harness fails
  closed when either cannot prove pre-dispatch mediation; a compatible OpenCode version is therefore
  a prerequisite, not something the harness silently works around.
- **An intentional stop can race session persistence.** Export occurs only after process-group reap and
  is tied to the immutable event session id; absent or conflicting export identity remains
  unattributed while transfer evidence is preserved.
- **Corpus migration can change historical scores.** New baselines are versioned after migration;
  old records are not rewritten.

## Remaining Evidence Gaps

Follow-up inputs surfaced by the section 10 real offline evidence run and by section 11 (live
pilot, real operator HITL approval) being blocked on a real human decision. These are inputs for
future, separately authorized work, not scope this change absorbs, and they do not expand or
alter the release-hardening change's scope.

- **The live pilot itself has not run.** Section 11 requires the operator to review and explicitly
  authorize exactly one paid `live-single-investigation` session, then to supply a real HITL
  approval/rejection decision after transfer. No agent may perform or simulate either step; both
  remain outstanding operator actions.
- **`cross-retrieval` production-path provenance was only partly exercised.** The section 10
  `cross-retrieval` evidence run's per-record `execution.executable` provenance (`path`,
  `sha256`, `package_sha256`) was populated from test-fixture placeholder values rather than a
  real `sdr` CLI subprocess invocation's captured executable identity. The scripted/metamorphic
  retrieval outcomes themselves (`correct` / `incorrect` / `not-exercised` / `not-consulted` and
  the negative controls) were exercised for real; what remains open is wiring the same evidence
  path through an actual CLI-orchestrated subprocess so `execution.executable` reflects observed
  bytes rather than a fixture stand-in.
- **`unassisted` prompt-policy measurement is still deferred.** Only `assisted` is authorized for
  the first reuse pilot; spontaneous cross discovery under an unassisted policy needs its own
  separate planning and operator authorization before it can be measured.
- **Additional planted-defect vocabulary remains unexercised in the corpus.**
  `unreproducible-result`, `uncovered-criterion`, `stale-validation`, and `unapproved-decision`
  are not yet planted in any corpus item; the first three already have a mapped control, and
  `unapproved-decision` first needs a decision about which structured field a missing HITL
  approval should surface in.
- **The public-tree audit after live execution (task 12.6) is unrun.** It depends on scripted
  evidence *and* authorized live execution both having happened; since section 11 has not run, that
  audit has no live artifacts to check yet and stays open until a pilot is authorized and executed.
