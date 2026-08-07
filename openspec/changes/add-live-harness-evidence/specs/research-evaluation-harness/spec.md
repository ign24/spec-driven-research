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
only the inherited host variables required by its declared connector. Executable provenance MUST be
captured before either environment starts a subprocess.

#### Scenario: Start a non-live subprocess
- **WHEN** a scripted, mutation, or metamorphic subprocess environment is built
- **THEN** credential-shaped and agent-host credential variables are absent
- **THEN** the executable or package provenance used by the subprocess is recorded

#### Scenario: Start an opted-in live subprocess
- **WHEN** both live opt-in keys are present and a connector is selected
- **THEN** the live environment is built independently from the non-live environment
- **THEN** only the connector's declared inherited host variables are admitted

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
write research artifacts, lifecycle metadata, results, or Git commits into the repository tree.

#### Scenario: A live session completes within bounds
- **WHEN** a live session completes before either bound
- **THEN** its artifacts and durable record exist only under configured external roots
- **THEN** the repository pre/post audit reports no harness-introduced write

#### Scenario: A live process exceeds a bound
- **WHEN** the turn or wall-clock bound is exceeded
- **THEN** the complete process tree is terminated and the disposable runspace is torn down
- **THEN** the run is errored with the exceeded bound and is not scored as a completed lifecycle run

### Requirement: Live usage has exact session attribution
Live token usage and monetary cost MUST come from the selected host's structured export for the exact
session identifier captured from that run. The harness MUST NOT use a host-wide aggregate or estimate
cost from a price table. It MUST persist aggregates and evidence references only, never a transcript
or raw host export.

#### Scenario: Record live usage
- **WHEN** a live session export reports usage
- **THEN** the run record ties usage and cost to that session id and records host, host version, model,
  and model version when reported
- **THEN** session-attribution validation passes before the values are reportable

#### Scenario: Usage is absent or cannot be attributed
- **WHEN** usage fields are absent or exact session attribution fails
- **THEN** usage is unavailable with a reason rather than zero
- **THEN** no transcript or raw export is persisted to compensate

### Requirement: A pilot is one fully planned paid session
A pilot MUST execute exactly one planned paid session identified by one scenario/item, arm, repetition,
host, model, prompt policy, and prompt-template version. The observed session MUST match that plan, and
the harness MUST exit after it without expanding the item across arms or repetitions.

#### Scenario: Execute a pilot plan
- **WHEN** an operator invokes a valid pilot plan
- **THEN** exactly one matching paid session executes
- **THEN** observed usage, cost, wall-clock, terminal state, and approval state are reported before exit

#### Scenario: Observed identity differs from the plan
- **WHEN** the host, model, template policy, arm, item, or repetition differs from the pilot plan
- **THEN** attribution fails and the session is not combined with the planned treatment

### Requirement: Live runs preserve the HITL boundary
A live agent MUST stop at transfer while real operator approval is pending. It MUST NOT invoke or
impersonate approval, fabricate an approver, edit lifecycle metadata to cross the boundary, or treat
claim resolution as approval. Approval provenance MUST distinguish not reached, operator pending or
decided, and synthetic fixture states. Synthetic approval MUST NOT count as live operator evidence.

#### Scenario: A live run reaches transfer
- **WHEN** the agent reaches the transfer approval boundary
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

### Requirement: Prompt treatments are versioned and expectation-blind
Every prompt MUST declare a versioned template and treatment policy. Prompts MUST exclude planted
defects, expected detections, expected retrievals, negative controls, and scoring vocabulary. Reports
MUST NOT aggregate differing template versions or assisted and unassisted policies.

#### Scenario: Build an expectation-blind prompt
- **WHEN** a prompt is built for any evaluation question
- **THEN** it contains no fixture expectation or scoring answer
- **THEN** a detected leak fails before host execution

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
required by the authoritative schema-v2 design. Version 1 MUST be rejected explicitly. The record
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
