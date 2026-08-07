## ADDED Requirements

### Requirement: A mutation weakens one named blocking control
A mutation MUST declare one blocking control, one transformation, and the planted-defect kinds that
control reports. It MUST apply only to a throwaway package copy outside the repository, using the
credential-free scripted subprocess environment.

#### Scenario: Apply a mutation
- **WHEN** a declared mutation is applied
- **THEN** only its throwaway package copy is transformed
- **THEN** the repository tree remains byte-identical and no agent-host credential reaches the run

#### Scenario: A transformation no longer matches
- **WHEN** the declared transformation does not match its target
- **THEN** validation fails with the mutation and target identity
- **THEN** the mutation is not skipped or reported as passing

### Requirement: Mutation validation observes the expected control loss
Mutation validation MUST compare a mutated scripted run with its current-provenance unmutated
baseline. The mutation MUST change exactly the outcomes attributable to the weakened control. A
mutation that changes no relevant outcome MUST fail.

#### Scenario: Validate an observable control
- **WHEN** a mutation weakens control C
- **THEN** baseline catches attributable to C are no longer caught
- **THEN** outcomes attributable only to other controls remain unchanged

#### Scenario: A mutation changes nothing
- **WHEN** mutated and baseline detection projections are identical
- **THEN** validation fails rather than treating the control as observable
- **THEN** the result identifies an inert control or unread scorer as unresolved alternatives

### Requirement: Blocking-control mutation coverage is explicit
Every documented control type capable of blocking a transition MUST have at least one declared
mutation or a recorded reason that no deterministic mutation is feasible. Control types MUST be
reported in the canonical order: structural, evidential, textual anchoring, executable, hash
consistency, and HITL.

#### Scenario: Audit mutation coverage
- **WHEN** mutation coverage is audited
- **THEN** every blocking control type has a mutation or explicit reason
- **THEN** an uncovered type without a reason fails the audit

### Requirement: Mutation validation remains distinct from reuse metamorphisms
Control mutation validation MUST transform a throwaway package copy and measure blocking-control
observability. It MUST NOT use fixture transformations or cross-retrieval exactness as evidence that a
blocking control is observable.

#### Scenario: Classify a validation transformation
- **WHEN** a validation transformation changes fixture inputs but not package code
- **THEN** it is classified as reuse metamorphic validation
- **THEN** it does not satisfy blocking-control mutation coverage
