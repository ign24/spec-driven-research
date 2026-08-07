## Purpose

Define SDR's deterministic research lifecycle, artifact contract, evidence layers, explicit
backtracking, and human decision boundary.
## Requirements
### Requirement: Ordered lifecycle modes
SDR MUST use `intake -> explore -> probe -> transfer -> reuse -> done` in full mode and MUST omit
only `probe` in light mode. Reuse remains required in both modes.

#### Scenario: Advance a full investigation
- **WHEN** every current-stage control passes
- **THEN** stages advance in full-mode order until status becomes `done`

#### Scenario: Advance a light investigation
- **WHEN** intake and explore pass in light mode
- **THEN** the next stages are transfer and reuse rather than probe
- **THEN** recommendations are capped at `assess`

### Requirement: Stage-specific artifact contract
Each stage MUST validate its declared artifact shape: `brief.md`, traceable `notes/*.md`, executable
`probe/results.md`, `decision-memo.md`, and reusable `assets/*.md` as applicable to the mode.

#### Scenario: A required artifact field is missing
- **WHEN** a stage artifact lacks required frontmatter, sections, or enumerated metadata
- **THEN** `sdr check` identifies the failing deterministic check
- **THEN** advancement remains blocked

### Requirement: Structural and evidential gates
Intake MUST contain at least two identifiable criteria; explore MUST declare dated tiered sources with
required maturity, cost, risk, counter-evidence, and triangulation; probe MUST map every criterion to
a result and reproducible evidence; transfer MUST provide a complete Y-statement and evidence-backed
ring; reuse MUST declare a supported type and audience.

#### Scenario: Evidence is incomplete
- **WHEN** a stage lacks a required criterion mapping, source property, recommendation element, or asset property
- **THEN** its gate fails with an actionable detail

### Requirement: Offline semantics
Offline mode MUST skip network link checks without reporting them as passed and MUST retain all local
structural, evidential, textual, executable-evidence, consistency, and approval requirements.

#### Scenario: Check explore offline
- **WHEN** `sdr check <slug> --offline` runs with valid local artifacts
- **THEN** link resolution is marked skipped
- **THEN** skipped network checks do not conceal any local gate failure

### Requirement: Deterministic textual anchoring
Factual explore claims marked `[S<n>]` MUST match the corresponding current local snapshot or carry a current scoped human resolution. A snapshot is usable only when its declared URL matches the source declaration, provenance metadata is complete, the terminal outcome is eligible, status is `ok`, persisted content is non-empty, and SHA-256 of the exact persisted bytes matches `content_hash`. A recorded redirected final URL MUST remain visible but MUST NOT substitute for the declared identity. `[cf. S<n>]` references MUST NOT create factual claims.

#### Scenario: A factual claim is not locally anchored
- **WHEN** deterministic matching cannot verify the current claim against an eligible snapshot
- **THEN** the claim is `not_anchored` or `unverifiable`
- **THEN** explore advancement remains blocked until evidence changes or scoped review is recorded

#### Scenario: A contextual citation is present
- **WHEN** a note uses `[cf. S<n>]`
- **THEN** source declaration rules apply and no textual-match claim is created

#### Scenario: Persisted provenance or content is inconsistent
- **WHEN** declared identity, eligible status, provenance, or persisted hash does not match current evidence
- **THEN** the snapshot is unusable and dependent verification becomes stale

### Requirement: Explicit executable evidence
Probe execution MUST occur only through `sdr verify-probe` after an explicit run declaration, and
probe advancement MUST consume a persisted passing result whose hash matches the current probe tree.

#### Scenario: Probe has not been explicitly verified
- **WHEN** `sdr advance` runs at probe without a current green verification
- **THEN** it does not execute the probe
- **THEN** advancement is blocked with a `verify-probe` instruction

#### Scenario: Probe content changes after verification
- **WHEN** any hashed probe content changes
- **THEN** the persisted verification becomes stale
- **THEN** adopt and trial recommendations remain unavailable until re-verification passes

### Requirement: Hash consistency
Successful advancement MUST persist a hash of validated stage artifacts and later operations MUST block after change. Independently, snapshot anchoring MUST recompute SHA-256 over the exact persisted `content.md` bytes and compare it with capture metadata. Missing or mismatched hashes MUST invalidate snapshot use and cached verification tied to it.

#### Scenario: Validated evidence is edited
- **WHEN** a validated stage artifact changes without reopening and revalidation
- **THEN** consistency checking identifies the stage and blocks advancement

#### Scenario: Persisted snapshot changes after capture
- **WHEN** snapshot bytes no longer match `content_hash`
- **THEN** the snapshot is not used for anchoring
- **THEN** cached verification and scoped resolutions tied to the previous identity become stale

### Requirement: Human approval boundary
Transfer advancement MUST require explicit approval of the current decision memo. A scoped claim
resolution MUST NOT substitute for decision approval.

#### Scenario: Transfer gates pass without approval
- **WHEN** a valid decision memo has no recorded approver and date
- **THEN** advancement to reuse is blocked

#### Scenario: One claim was human-reviewed
- **WHEN** `resolve-claim` records a scoped explore resolution
- **THEN** transfer still requires a separate `sdr approve`

### Requirement: Explicit backtracking and terminal states
`sdr reopen` MUST move only backward with a reason, invalidate validation hashes from the destination
stage onward, and reactivate done research. Dropping MUST record a reason rather than deleting evidence.

#### Scenario: Reopen to an earlier stage
- **WHEN** a valid earlier stage and non-empty reason are supplied
- **THEN** the transition is recorded and later validation hashes are removed
- **THEN** the investigation becomes active at the requested stage

### Requirement: Structured automation interface
Operational commands used by agents MUST support structured JSON where documented, and lifecycle
transitions MUST preserve stage guards rather than relying on agent-specific behavior.

#### Scenario: An automation checks current state
- **WHEN** it invokes a supported command with `--json`
- **THEN** it receives machine-readable status or gate details suitable for deterministic decisions

### Requirement: Snapshot retrieval provenance
Each terminal snapshot capture MUST persist distinct declared and final URLs, an ordered redirect record, terminal HTTP status, capture time, evidence status, and hash of exact persisted content bytes. Missing legacy facts MUST NOT be inferred.

#### Scenario: Capture without redirects
- **WHEN** a declared URL returns a terminal response directly
- **THEN** declared and final URLs, empty redirect record, status, time, evidence state, and content hash are persisted

#### Scenario: Capture through redirects
- **WHEN** retrieval follows allowed redirects
- **THEN** the original declaration remains distinct from the final URL
- **THEN** every followed redirect is recorded in order

#### Scenario: Legacy provenance is incomplete
- **WHEN** an existing snapshot lacks required retrieval facts
- **THEN** SDR does not infer them
- **THEN** the snapshot requires recapture or an allowed scoped human boundary before verified anchoring

### Requirement: Fail-closed HTTP snapshot outcomes
Only a terminal 2xx response with supported text, non-empty extraction, complete provenance, and a valid persisted-byte hash MAY receive `ok` and supply textual evidence. Non-2xx, unsupported, empty, incomplete, or hash-mismatched captures MUST remain non-passing while preserving actual outcome metadata.

#### Scenario: Terminal response is non-2xx
- **WHEN** retrieval receives a terminal response outside 2xx
- **THEN** status and final URL are persisted
- **THEN** the body is not usable evidence and the snapshot is not `ok`

#### Scenario: Successful response has no extractable evidence
- **WHEN** a 2xx response yields no supported non-empty text
- **THEN** the outcome remains recorded and the snapshot is `unverifiable`

### Requirement: Snapshot confidence boundary
Snapshot metadata and textual anchoring MUST describe only the recorded retrieval event and occurrence of persisted text. SDR MUST NOT represent URLs, redirects, HTTP/TLS outcomes, organization heuristics, hashes, or matches as proof of publisher identity, authorship, authenticity, accuracy, or truth.

#### Scenario: Complete provenance and textual match succeed
- **WHEN** an eligible snapshot deterministically matches a claim
- **THEN** SDR may report local textual anchoring
- **THEN** it does not report authenticated publisher identity or factual truth

#### Scenario: Retrieval crosses apparent organizations
- **WHEN** a redirect ends at another hostname or apparent organization
- **THEN** both locations remain visible
- **THEN** SDR infers neither identity, independence, authorship, nor truth

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

