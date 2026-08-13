## ADDED Requirements

### Requirement: Corpus baselines use current provenance
Before the harness preserves a new baseline, every retained corpus item MUST pass the current snapshot
provenance contract, and every completed decision that participates in lineage or reuse MUST declare
the required `evidence_claim_ids`. The harness MUST NOT relabel a baseline produced under an older
contract as current.

#### Scenario: Preserve a migrated baseline
- **WHEN** a retained corpus item is selected for a new baseline
- **THEN** its snapshots pass the current provenance validation
- **THEN** its participating completed decisions explicitly declare valid `evidence_claim_ids`

#### Scenario: A legacy item omits required lineage
- **WHEN** a retained item contains a participating completed decision without `evidence_claim_ids`
- **THEN** baseline preservation fails with the item and decision identity
- **THEN** lineage is not inferred from prose or copied from an old run record

### Requirement: Subprocess environments enforce credential boundaries
Scripted, mutation, and metamorphic actors MUST construct allowlisted subprocess environments that
exclude agent-host credentials. A live actor MUST use a separately constructed environment containing
only explicit credential variables from its connector-owned fixed allowlist. Callers MUST NOT extend
that allowlist or inject loader/runtime options, search paths, config roots, or general environment
maps. Live execution MUST use harness-owned isolated XDG config/data/cache/state roots. Executable
canonical path, device/inode identity, hash, and package/version provenance MUST be captured and fixed
before credential access or subprocess startup.

#### Scenario: Start a non-live subprocess
- **WHEN** a scripted, mutation, or metamorphic subprocess environment is built
- **THEN** credential-shaped and agent-host credential variables are absent
- **THEN** the executable or package provenance used by the subprocess is recorded

#### Scenario: Start an opted-in live subprocess
- **WHEN** both live opt-in keys are present and a connector is selected
- **THEN** the live environment is built independently from the non-live environment
- **THEN** only explicitly present credentials in the connector's fixed allowlist are admitted
- **THEN** loader/runtime injection and inherited XDG state are rejected

### Requirement: Live execution requires two opt-in keys
The harness MUST start a live host only when both the live environment opt-in and an explicit live CLI
flag are present. Without either key, no host process may start, no host credential may be read, and no
network request may be made.

#### Scenario: One live key is absent
- **WHEN** either the environment opt-in or explicit CLI flag is absent
- **THEN** no live host process is started
- **THEN** the default scripted path remains offline and credential-free

### Requirement: Live execution is bounded and repository-write-free
Every live session MUST execute inside a disposable external research root, MUST create a bounded
process group, and MUST enforce turn and wall-clock bounds over the complete process tree. It MUST NOT
write research artifacts, lifecycle metadata, results, or Git commits into the repository tree. A
live session MAY write only exact current-stage focal research artifacts derived internally from the
sealed request and materialized manifest. It MUST protect `sdr.yaml` from direct/unmediated writes
while permitting only an exact authorized metadata transition produced by the mediator-executed SDR
lifecycle command. It MUST protect immutable seeds, harness configuration, and results.
Every mediator-spawned lifecycle, inspection, verification, and export subprocess MUST share complete
bounds and cancellation propagation, terminate descendants, and be joined before teardown or return.

#### Scenario: A live session completes within bounds
- **WHEN** a live session completes before either bound
- **THEN** its artifacts and durable record exist only under configured external roots
- **THEN** the repository pre/post audit reports no harness-introduced write

#### Scenario: A live process exceeds a bound
- **WHEN** the turn or wall-clock bound is exceeded
- **THEN** the complete process tree is terminated and the disposable runspace is torn down
- **THEN** the run is errored with the exceeded bound and is not scored as a completed lifecycle run

#### Scenario: A live tool attempts an undeclared write
- **WHEN** a host requests a direct write to `sdr.yaml`, a seed, repository path, harness config,
  result, or undeclared focal file
- **THEN** the write is denied before dispatch
- **THEN** no broader filesystem permission or fallback tool is available

#### Scenario: An authorized lifecycle command changes metadata
- **WHEN** the mediator executes an allowed `verify-claims`, `verify-probe`, or `advance` command
- **THEN** the pre/post semantic `sdr.yaml` delta exactly equals the predicted command-specific
  transition
- **THEN** any missing, additional, stale, or otherwise unexplained metadata change fails attribution

### Requirement: OpenCode live execution proves isolated mediation
The harness MUST materialize and hash a dedicated OpenCode configuration and harness-owned mediation
plugin. It MUST fail closed before host startup unless the machine-readable effective configuration
proves that exact config/plugin provenance and proves external plugins, MCP servers, saved grants,
loader/runtime environment injection, unmediated bash/shell execution, and generic command tools are
absent or denied. Executable, config, and plugin canonical path, device/inode identity, and hash MUST
be fixed and revalidated before startup, every tool dispatch, every mediator subprocess boundary, and
export. The mediator control token MUST remain only in the harness host process and MUST never appear
in model/tool input, debug output, structured events, durable records, or exports.

#### Scenario: Effective configuration matches the harness boundary
- **WHEN** live OpenCode preflight inspects the effective configuration
- **THEN** the selected config root and plugin bytes match the captured harness-owned hashes
- **THEN** isolated XDG roots and fixed executable/config/plugin identities validate
- **THEN** only the declared mediation tool and required focal artifact edits are available

#### Scenario: Isolation cannot be demonstrated
- **WHEN** effective configuration is unavailable, ambiguous, inherits an external plugin/MCP/grant, or
  leaves an unmediated execution path enabled
- **THEN** startup fails before a paid session, credential use, or network request
- **THEN** the deficiency is not downgraded to a warning

### Requirement: Lifecycle actions use one pre-dispatch argv mediator
The live host MUST receive no generic SDR or shell tool. A harness-owned plugin MUST expose one narrow
lifecycle tool requiring `verify.action: run` and argv without a shell. The tool MUST serialize
commands and enforce stateful stage-specific command, flag, and focal-artifact allowlists derived from
independently parsed state. As applicable those allowlists MUST include `verify-claims`, `verify-probe`,
and `advance`, and MUST always deny `approve`, every `resolve-claim`, direct lifecycle metadata writes,
and every undeclared lifecycle action. Before exposing tools and after every attempted command, the
mediator MUST independently schema-validate and semantically reconcile
`sdr status <slug> --json` and exactly `sdr check <slug> --offline --json` with the manifest, sealed
request, filesystem, and expected command transition before permitting another command.
Intake MAY edit only declared brief artifacts and invoke `advance`; explore MAY edit only declared
notes and invoke `verify-claims` or `advance`; probe MAY edit only declared probe artifacts and invoke
`verify-probe` or `advance`; transfer MUST expose no mutation/transition; and reuse MAY edit only
declared assets and invoke `advance` in a separately operator-authorized resumed run. Read-only status,
offline check, and plan-declared cross queries remain exact argv operations where required. Every
advance MUST be exactly `sdr advance <slug> --offline --no-commit`; verification flags and timeout
MUST be fixed by the sealed request.

#### Scenario: Execute one authorized lifecycle command
- **WHEN** the plugin receives an allowed `verify.action: run` argv request
- **THEN** exactly one fixed-executable command runs without a shell
- **THEN** structured status and offline check plus the exact pre/post semantic metadata transition
  are captured and validated before another lifecycle request

#### Scenario: Initial state is inconsistent
- **WHEN** initial status/check schemas or semantics disagree with each other, the manifest, sealed
  request, derived roots, or stage allowlist
- **THEN** no host tool or lifecycle command is exposed
- **THEN** caller scalar attestations cannot repair or replace the missing exact identity

#### Scenario: Attempt to bypass mediation
- **WHEN** the host requests a command string, alternate executable, concurrent lifecycle command,
  forbidden subcommand, direct metadata write, or external execution tool
- **THEN** the request is denied before dispatch
- **THEN** the run cannot treat post-hoc observation as enforcement

### Requirement: Live usage has exact session attribution
Live token usage and monetary cost MUST come from the selected host's structured export for the exact
immutable session identifier captured from that run. Every session-bearing event in the complete
stream and every session-bearing export record MUST be reconciled and agree with that identifier. The
harness MUST NOT use a host-wide aggregate, estimate cost from a price table, or use a model fallback
when export is unavailable. It MUST persist aggregates and evidence references only, never a
transcript or raw host export. An intentional transfer interrupt MUST still attempt the exact
attributed export after the host process group and all mediator subprocesses are cancelled and joined.

#### Scenario: Record live usage
- **WHEN** a live session export reports usage
- **THEN** the run record ties usage and cost to that session id and records host, host version, model,
  and model version when reported
- **THEN** session-attribution validation passes before the values are reportable

#### Scenario: Usage is absent or cannot be attributed
- **WHEN** usage fields are absent or exact session attribution fails
- **THEN** usage is unavailable with a reason rather than zero
- **THEN** no transcript or raw export is persisted to compensate

#### Scenario: Transfer intentionally interrupts the host
- **WHEN** structured status first reports transfer and the harness interrupts the host process group
- **THEN** the exact immutable session id is exported without starting or continuing another session
- **THEN** transfer evidence remains operator-pending even if export usage is unavailable

#### Scenario: A second session identity appears
- **WHEN** a later event or exported assistant record identifies a different session
- **THEN** session attribution fails
- **THEN** values from the conflicting identity are not reported

### Requirement: A pilot is one fully planned paid session
A pilot MUST execute exactly one planned paid session identified by one scenario/item, arm, repetition,
host, model, prompt policy, prompt-template version, bounds, and external results root. The runner MUST
wire the live path through an exact scalar `--live` pilot and MUST reject matrix/list/range expansion.
The observed session MUST match that plan, and the harness MUST exit after it without expanding the
item across arms or repetitions. The harness MUST derive the canonical repository root and
materialized focal/seed roots internally and validate containment and disjointness. Observed identity
MUST be derived independently from exact materialized manifest and sealed-request identities,
canonical prompt, runspace, host, every event, and export evidence; it MUST NOT be copied from the plan
or accepted as scalar caller attestation.

#### Scenario: Execute a pilot plan
- **WHEN** an operator invokes a valid pilot plan
- **THEN** exactly one matching paid session executes
- **THEN** observed usage, cost, wall-clock, terminal state, and approval state are reported before exit

#### Scenario: Observed identity differs from the plan
- **WHEN** the host, model, template policy, arm, item, or repetition differs from the pilot plan
- **THEN** attribution fails and the session is not combined with the planned treatment

#### Scenario: Observed identity lacks independent evidence
- **WHEN** an observed identity field exists only in the authorization plan or is filled from it
- **THEN** attribution fails before the session is reported as matching
- **THEN** the plan remains comparison input rather than observation input

### Requirement: Live runs preserve the HITL boundary
A live agent MUST stop at transfer while real operator approval is pending. It MUST NOT invoke or
impersonate approval, fabricate an approver, edit lifecycle metadata to cross the boundary, or treat
claim resolution as approval. Approval provenance MUST distinguish not reached, operator pending or
decided, and synthetic fixture states. Approval and terminal state MUST form an allowed pair:
pre-transfer error/completion is `not-reached` and not terminal `awaiting-operator-approval`;
intentional transfer stop is `awaiting-operator-approval` plus `operator-pending`; later
operator-decided states require a separate real operator record referencing that run while the
immutable session terminal remains `awaiting-operator-approval`; and synthetic states are fixture-only
and cannot pair with a live terminal. Synthetic approval MUST NOT count as live operator evidence.

#### Scenario: A live run reaches transfer
- **WHEN** the agent reaches the transfer approval boundary
- **THEN** the plugin refuses further tools and the harness immediately interrupts the host without
  waiting for another turn
- **THEN** execution stops with `operator-pending`
- **THEN** no approve action or synthetic approval is recorded

#### Scenario: A run stops before transfer
- **WHEN** execution ends before reaching the approval boundary
- **THEN** approval state is `not-reached`
- **THEN** it is not reported as operator-pending or synthetic

#### Scenario: A scripted fixture needs an approved state
- **WHEN** deterministic validation uses fixture approval
- **THEN** its state is explicitly synthetic and remains separate from operator evidence
- **THEN** the initial live pilot rejects the synthetic state

#### Scenario: Approval and terminal states disagree
- **WHEN** a record contains a pairing outside the allowed live or fixture state matrix
- **THEN** validation rejects the record rather than normalizing either state
- **THEN** initial-session evidence cannot contain an operator-decided or synthetic approval

### Requirement: Prompt treatments are versioned and expectation-blind
Every prompt MUST declare a versioned template and treatment policy. Prompts MUST exclude planted
defects, expected detections, expected retrievals, negative controls, and scoring vocabulary. Reports
MUST NOT aggregate differing template versions or assisted and unassisted policies.
Live prompts MUST be canonical immutable `BuiltPrompt` values created by
`build_prompt(PromptInputs, PromptLeakSignals)`. The transfer-stop instruction MUST be included before
construction and leak validation. The exact submitted bytes, template hash, and submitted-prompt hash
MUST be recomputed and validated before host startup; no text may be appended afterward.

#### Scenario: Build an expectation-blind prompt
- **WHEN** a prompt is built for any evaluation question
- **THEN** it contains no fixture expectation or scoring answer
- **THEN** a detected leak fails before host execution

#### Scenario: Submit a live prompt
- **WHEN** a canonical `BuiltPrompt` is prepared for the host
- **THEN** its construction provenance, leak result, template hash, and exact text hash validate
- **THEN** the submitted bytes equal the validated text without post-validation mutation

#### Scenario: Compare prompt treatments
- **WHEN** records use assisted and unassisted policies or different template versions
- **THEN** they are rendered as separate treatment groups
- **THEN** no combined treatment result is computed

## MODIFIED Requirements

### Requirement: Planted-defect detection scoring
For every lifecycle-control run the harness MUST classify each planted defect as caught, missed, or
not exercised, and MUST count findings on items with no planted defect as false positives. A defect
counts as caught only when a control blocked or reported it. A defect is not exercised when every
reporting control was skipped or the lifecycle was not entered. Detection rate MUST use caught and
missed only.

#### Scenario: Score an exercised planted defect
- **WHEN** a reporting control runs for a planted defect
- **THEN** the defect is caught or missed and any caught outcome names the reporting control
- **THEN** the outcome enters the detection-rate denominator

#### Scenario: The reporting control did not run
- **WHEN** every reporting control was skipped or the lifecycle was not entered
- **THEN** the defect is `not-exercised` and names the skipped control or lifecycle reason
- **THEN** the outcome does not enter the detection-rate denominator

#### Scenario: Score a clean item
- **WHEN** a run over an item with no planted defect completes
- **THEN** each blocking finding is a false positive with its control name
- **THEN** a run with no finding is clean

### Requirement: Cost accounting
The harness MUST record wall-clock duration per run and lifecycle stage and token accounting when the
actor reports it. Unavailable usage MUST carry a reason and MUST NOT be represented as zero. Live
usage additionally requires exact session attribution and host/model/version provenance.

#### Scenario: Record completed-run cost
- **WHEN** a run completes
- **THEN** total and per-stage wall-clock durations are recorded
- **THEN** token and monetary usage are observed values or explicit unavailable values with reasons

#### Scenario: Report live cost
- **WHEN** a live cost is reportable
- **THEN** its exact session, host, host version, model, and available model version are named
- **THEN** records lacking required attribution are rejected rather than aggregated

### Requirement: Structured run record
Every run MUST produce a schema-version-2 machine-readable record containing the identity,
evaluation-question, corpus/scenario provenance, treatment factors, execution boundary, terminal and
approval states, question-specific outcomes, cost, friction, and traceable structured evidence
required by authoritative Design Decision 11. Version 1 MUST be rejected explicitly. The record
MUST NOT contain a transcript, raw host export, or model-judged metric.

#### Scenario: Validate a version-2 record
- **WHEN** a run record is persisted
- **THEN** every field required for its evaluation question and actor is present and internally
  consistent
- **THEN** every metric traces to an artifact, exit code, or structured CLI field

#### Scenario: Read an older record
- **WHEN** a version-1 record is supplied
- **THEN** validation rejects it with an explicit unsupported-version message
- **THEN** no field is silently coerced into version 2

### Requirement: Deterministic comparison report
The harness MUST render durable run records in separate lifecycle-control-observability,
live-single-investigation, and cross-retrieval sections with stable ordering. It MUST keep scripted
and live actors, assisted and unassisted policies, history conditions, incompatible provenance, and
incompatible templates in separate groups. Two renderings over unchanged records MUST be
byte-identical. The report MUST state the prohibited claims from this change.

#### Scenario: Generate a report twice
- **WHEN** the report is generated twice from unchanged records
- **THEN** both outputs are byte-identical

#### Scenario: Read separated evidence
- **WHEN** the report contains records for multiple evaluation questions or treatments
- **THEN** no combined effectiveness score or mixed-treatment aggregate is present
- **THEN** lifecycle observability, live workflow/cost/friction, and exact cross retrieval are stated
  separately

#### Scenario: Read report limitations
- **WHEN** any results section is read
- **THEN** the report does not claim semantic applicability, recommendation quality, criterion-level
  reuse, statistical significance, causal effect, or cross CLI value
- **THEN** corpus, scenario, repetition, actor, treatment, and relevant provenance are visible
