## ADDED Requirements

### Requirement: Reuse scenarios isolate immutable seeds and one focal investigation
Each reuse scenario MUST materialize one isolated external scenario root containing one or more
immutable completed seed investigations and exactly one focal investigation. Only the focal
investigation may change during execution. Reuse-history condition MUST be recorded independently
from the baseline, light, or full arm.

#### Scenario: Execute an isolated reuse scenario
- **WHEN** a reuse scenario is materialized
- **THEN** its root is not shared with another scenario or repetition
- **THEN** pre/post hashes prove seed investigations remained unchanged

#### Scenario: Record treatment factors
- **WHEN** a reuse run record is written
- **THEN** arm and reuse-history condition occupy separate fields
- **THEN** history-present is not represented as a fourth arm

### Requirement: Reuse fixtures cover non-software content and exact negative controls
The reuse corpus MUST include at least one non-software scenario and MUST declare exact positive and
negative structured expectations. Negative controls MUST include explicit identifiers or relations
that must be absent; an empty expectation alone is insufficient.

#### Scenario: Load the reuse corpus
- **WHEN** reuse fixtures are validated
- **THEN** at least one scenario's subject is outside software engineering
- **THEN** every scenario declares at least one exact negative control

#### Scenario: Evaluate a negative control
- **WHEN** the declared query is exercised
- **THEN** the prohibited investigation-qualified identifier or relation is absent exactly as declared
- **THEN** absence is scored as correct rather than not exercised

### Requirement: Cross consultation and exact retrieval have distinct outcomes
The harness MUST record whether the cross CLI was consulted and MUST compare each declared structured
query projection exactly. It MUST distinguish `not-consulted`, `not-exercised`, `incorrect`, and
`correct` and MUST NOT replace exact comparison with semantic judgement.

#### Scenario: Cross is never consulted
- **WHEN** no cross CLI command is invoked during the run
- **THEN** consultation state and every dependent check are `not-consulted`
- **THEN** the run is not reported as an incorrect retrieval

#### Scenario: Cross is consulted but a declared query is omitted
- **WHEN** at least one cross command is invoked but a declared check is not exercised
- **THEN** that check is `not-exercised`
- **THEN** it is distinct from `not-consulted` and `incorrect`

#### Scenario: A declared query returns the wrong projection
- **WHEN** the query executes and its structured projection differs from the exact expectation
- **THEN** the check is `incorrect` with an inspectable expected/observed difference

#### Scenario: A declared query matches
- **WHEN** the query executes and its structured projection exactly equals the expectation
- **THEN** the check is `correct`
- **THEN** no semantic applicability or usefulness conclusion is attached

### Requirement: Assisted and unassisted reuse are separate treatments
Assisted prompts MUST direct the agent to consult the cross CLI without revealing expected queries or
results. Unassisted prompts MUST omit cross-consultation guidance. Results from the two policies MUST
NOT be aggregated. The initial reuse pilot MUST be assisted; spontaneous discovery MUST be measured
only in a separately planned later unassisted treatment. A live assisted prompt MUST use the same
canonical `BuiltPrompt` construction, hash, and leak validation required by the research evaluation
harness; generic cross guidance MUST be incorporated before validation rather than appended by the
live connector.

#### Scenario: Run the first reuse pilot
- **WHEN** the initial reuse pilot plan is validated
- **THEN** its prompt policy is assisted
- **THEN** its submitted prompt exactly matches the validated canonical `BuiltPrompt`
- **THEN** its result is not labelled spontaneous discovery

#### Scenario: Report both policies
- **WHEN** assisted and later unassisted records exist
- **THEN** consultation and retrieval outcomes are grouped separately by policy
- **THEN** no combined consultation rate is computed

### Requirement: Reuse validation includes deterministic metamorphic relations
The harness MUST validate cross retrieval by applying declared transformations to fixture inputs and
asserting exact invariant or changed structured outputs. Metamorphic validation MUST leave package
code unchanged and MUST remain separate from blocking-control mutation validation.

#### Scenario: Preserve an invariant under seed ordering
- **WHEN** immutable seed materialization order is permuted
- **THEN** the normalized structured cross result is byte-identical

#### Scenario: Remove explicit join provenance
- **WHEN** a fixture transformation removes the only declared source, anchor, or decision-claim
  provenance for an expected edge
- **THEN** that edge is absent from the exact structured result
- **THEN** no semantic or inferred edge replaces it

#### Scenario: Change completed status
- **WHEN** a transformed seed investigation changes from `done` to `active`
- **THEN** its decision is excluded from dependent completed decisions
- **THEN** unrelated exact results remain unchanged

#### Scenario: Normalize equivalent source locations
- **WHEN** a declared URL is changed only by supported normalization differences
- **THEN** its resolved identity and expected joins remain unchanged

### Requirement: Reuse reporting is limited to deterministic evidence
Reuse reports MUST state consultation and exact positive, negative-control, and metamorphic outcomes.
They MUST NOT claim semantic applicability, recommendation quality, criterion-level reuse,
statistical significance, causal effect, or that cross consultation was valuable.

#### Scenario: Read a reuse result
- **WHEN** a reuse report is generated
- **THEN** exact structured outcomes, treatment policy, history condition, and scenario provenance are
  visible
- **THEN** none of the prohibited claims is stated or implied by an aggregate score
