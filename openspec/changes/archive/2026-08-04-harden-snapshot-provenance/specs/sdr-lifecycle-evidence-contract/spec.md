## MODIFIED Requirements

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

### Requirement: Hash consistency
Successful advancement MUST persist a hash of validated stage artifacts and later operations MUST block after change. Independently, snapshot anchoring MUST recompute SHA-256 over the exact persisted `content.md` bytes and compare it with capture metadata. Missing or mismatched hashes MUST invalidate snapshot use and cached verification tied to it.

#### Scenario: Validated evidence is edited
- **WHEN** a validated stage artifact changes without reopening and revalidation
- **THEN** consistency checking identifies the stage and blocks advancement

#### Scenario: Persisted snapshot changes after capture
- **WHEN** snapshot bytes no longer match `content_hash`
- **THEN** the snapshot is not used for anchoring
- **THEN** cached verification and scoped resolutions tied to the previous identity become stale

## ADDED Requirements

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
