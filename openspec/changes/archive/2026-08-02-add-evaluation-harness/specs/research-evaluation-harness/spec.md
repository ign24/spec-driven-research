## ADDED Requirements

### Requirement: Declared benchmark corpus
The harness MUST read a versioned corpus in which every item declares its identity, its intended
lifecycle mode, its research inputs, and the exact set of defects planted in it. Planted defects
MUST be drawn from a closed, named vocabulary so that detection can be scored without interpretation.

#### Scenario: Load a valid corpus
- **WHEN** the corpus is loaded
- **THEN** every item declares a unique identifier, a mode of `light` or `full`, and a planted-defect
  list whose entries all belong to the declared defect vocabulary
- **THEN** at least one item declares an empty planted-defect list

#### Scenario: Load a corpus with an undeclared defect kind
- **WHEN** an item declares a defect outside the vocabulary
- **THEN** loading fails with the offending item identifier and defect name
- **THEN** no run is executed

### Requirement: Synthetic corpus content
Corpus items MUST use invented identities, invented findings, and reserved or non-resolvable domains,
and MUST NOT redistribute third-party snapshots or copy real investigations. Planted unreachable
sources MUST be unreachable by construction rather than by depending on an external site failing.

#### Scenario: Audit a corpus item
- **WHEN** an item's sources are reviewed
- **THEN** every URL uses a reserved or non-resolvable domain
- **THEN** no source body is a copy of third-party published material

#### Scenario: Run the corpus offline
- **WHEN** the harness runs with network access disabled
- **THEN** items whose planted defects do not require network retrieval still execute and score
- **THEN** checks that were skipped for lack of network are recorded as skipped, not as passed

### Requirement: Isolated run roots
Each harness run MUST materialize its research root in a temporary location outside the repository
tree, and MUST NOT write research artifacts, lifecycle metadata, or Git commits into the repository.

#### Scenario: Execute a run
- **WHEN** a corpus item is executed in any arm
- **THEN** its research root is created outside the repository tree
- **THEN** the repository tree contains no `research` or `knowledge` directory after the run

#### Scenario: Audit the tree after a full harness execution
- **WHEN** the public-tree audit runs after the harness completes
- **THEN** the audit reports no findings introduced by the harness

### Requirement: Serial execution of shared state
Because lifecycle metadata has no concurrency control, the harness MUST NOT execute two runs against
the same research root concurrently. Parallelism, where offered, MUST be bounded to runs with
disjoint research roots.

#### Scenario: Request parallel execution
- **WHEN** the harness is asked to run multiple items in parallel
- **THEN** each parallel run receives its own research root
- **THEN** no two concurrent runs share a lifecycle metadata file

### Requirement: Three-arm comparison
The harness MUST execute each applicable corpus item in three arms: a no-SDR baseline that produces
research output without lifecycle gates, an SDR light-mode arm, and an SDR full-mode arm. Each arm
MUST be repeatable a configured number of times so that variance is observable.

#### Scenario: Execute a corpus item across arms
- **WHEN** an item is executed with a repetition count of N
- **THEN** the harness produces N run records per applicable arm
- **THEN** each run record names its actor, item, arm, repetition index, and terminal lifecycle state

#### Scenario: An item is not applicable to an arm
- **WHEN** a light-mode item is considered for the full-mode arm
- **THEN** the harness records the arm as not applicable rather than as a failure
- **THEN** the not-applicable record retains the actor identity

### Requirement: Planted-defect detection scoring
For every run the harness MUST classify each planted defect as caught or missed, and MUST count
findings on items with no planted defect as false positives. A defect counts as caught only when a
control blocked or reported it; a run that merely failed for an unrelated reason MUST NOT be scored
as a detection.

#### Scenario: Score a run with planted defects
- **WHEN** a run over an item with planted defects completes
- **THEN** each planted defect is recorded as caught or missed
- **THEN** each caught defect names the control that reported it

#### Scenario: Score a run over a clean item
- **WHEN** a run over an item with an empty planted-defect list completes
- **THEN** any blocking finding is recorded as a false positive with its control name
- **THEN** a run with no findings is recorded as clean

### Requirement: Cost accounting
The harness MUST record wall-clock duration per run and per lifecycle stage, and MUST record token
accounting per run when the executing agent reports usage. When token usage is unavailable, the run
record MUST mark it as unavailable rather than as zero.

#### Scenario: Record cost for a completed run
- **WHEN** a run completes
- **THEN** the run record contains total wall-clock duration and a per-stage breakdown
- **THEN** the run record contains token counts or an explicit unavailable marker

#### Scenario: Compare arm cost
- **WHEN** the report is generated
- **THEN** it states wall-clock and token cost per arm relative to matched no-SDR baseline records
  with the same actor, item, and repetition

### Requirement: Lifecycle friction accounting
The harness MUST record, per run, the number of `reopen` transitions, every gate failure grouped by
control type, and every claim closed through `resolve-claim`. Control types MUST use the documented
control vocabulary: structural, evidential, textual anchoring, executable, hash consistency, and
human approval.

#### Scenario: Record friction for a run that backtracked
- **WHEN** a run reopens a stage and later completes
- **THEN** the run record counts the reopen with its origin and target stage
- **THEN** each gate failure that preceded it is attributed to a documented control type

#### Scenario: Record a manually resolved claim
- **WHEN** a claim is closed through `resolve-claim` during a run
- **THEN** the run record counts it separately from claims that passed anchoring

### Requirement: Structured run record
Every run MUST produce a machine-readable record containing its identity, arm, terminal state,
detection scoring, cost accounting, and friction accounting, derived only from artifacts, exit
codes, and structured CLI output. Actor-reported timing and token observations MUST be persisted as
artifact evidence and referenced by the corresponding metrics. Filesystem-dependent evidence MUST
be captured before runspace teardown so the durable record is self-contained. The harness MUST NOT
use a language model to score any metric.

#### Scenario: Inspect a run record
- **WHEN** a run record is read
- **THEN** every metric it reports is traceable to an artifact path, an exit code, or a structured
  CLI field
- **THEN** no field is produced by model-based judgement

### Requirement: Deterministic comparison report
The harness MUST generate a report from durable run records only, aggregating per arm within separate
scripted and live sections with stable ordering. It MUST NOT aggregate scripted and live results.
Two report generations over the same unchanged set of run records MUST produce byte-identical
output; independent executions with variable wall-clock observations are not required to do so.

#### Scenario: Generate the report twice
- **WHEN** the report is generated twice from unchanged run records
- **THEN** both outputs are byte-identical

#### Scenario: Read the report
- **WHEN** the report is read
- **THEN** it states, per arm, detection rate over planted defects, false-positive count, cost
  relative to the baseline, and friction counts by control type
- **THEN** it states the corpus version and the repetition count used
