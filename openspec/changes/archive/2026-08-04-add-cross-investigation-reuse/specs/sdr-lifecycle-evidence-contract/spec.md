## ADDED Requirements

### Requirement: Decision memos declare exact claim lineage
Each new decision memo MUST persist `evidence_claim_ids` as a structured list of the exact claim IDs
used by the decision. Every listed ID MUST be well formed and unique. Transfer MUST validate that
every listed ID is present in the same investigation and verified at that time. A malformed list,
duplicate ID, missing claim, or unverified claim MUST fail with a deterministic validation error and
MUST keep transfer blocked.

#### Scenario: Transfer a decision with declared evidence
- **WHEN** a decision memo declares unique, well-formed `evidence_claim_ids`
- **THEN** transfer succeeds only if every referenced claim exists in that investigation and is
  verified at transfer
- **THEN** the exact declared IDs persist with the decision

#### Scenario: Transfer with an invalid claim declaration
- **WHEN** `evidence_claim_ids` is malformed, contains a duplicate, or references a missing or
  unverified claim
- **THEN** deterministic decision artifact validation fails
- **THEN** transfer remains blocked
