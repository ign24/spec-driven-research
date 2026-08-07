# cross-investigation-reuse Specification

## Purpose
TBD - created by archiving change add-cross-investigation-reuse. Update Purpose after archive.
## Requirements
### Requirement: Derived cross-investigation layer
SDR MUST derive a cross-investigation layer from every research root under the configured root. The
layer MUST be regenerated from those investigations, MUST NOT be hand-edited, and MUST be safe to
delete and recompute without loss of information. Throughout this capability, a completed decision
MUST belong to an investigation whose persisted lifecycle `status` is exactly `done`; decisions
belonging to active investigations, including those with reopen history, or dropped investigations
MUST NOT qualify as completed decisions.

#### Scenario: Regenerate the layer
- **WHEN** the layer is deleted and recomputed from unchanged investigations
- **THEN** the recomputed layer is identical to the deleted one
- **THEN** no artifact inside any investigation was read as anything other than input

#### Scenario: Compute over a single investigation
- **WHEN** only one investigation exists
- **THEN** the layer is produced without error and reports zero cross-investigation joins

### Requirement: Resolved source identity joins investigations
Two investigations MUST be linked when their sources resolve to the same identity. Identity MUST be
produced by an ordered resolver chain that prefers a declared work identifier and falls back to a
normalized URL. The layer MUST NOT create a link from topic similarity, semantic similarity, or any
model-derived judgement.

#### Scenario: Two investigations cite one source
- **WHEN** two investigations declare sources that resolve to the same identity
- **THEN** the layer reports them as joined on that source
- **THEN** the join names the resolved identity and the resolver that produced it

#### Scenario: Two investigations share a topic but no source
- **WHEN** two investigations concern the same subject and share no resolved source and no claim
- **THEN** the layer reports no join between them

### Requirement: URL normalization
The URL resolver MUST normalize scheme, host case, a leading `www.`, tracking parameters, fragments,
and the trailing slash before producing an identity. URLs differing only in those respects MUST
resolve to one identity.

#### Scenario: Cite the same page in two forms
- **WHEN** one investigation cites `http://www.example.com/doc/` and another cites
  `https://example.com/doc?utm_source=x`
- **THEN** both resolve to the same identity
- **THEN** the layer reports the two investigations as joined

### Requirement: Work identifiers take precedence over location
When a source carries a declared work identifier, that identifier MUST be the resolved identity, so
the same work published at several locations resolves once. Extraction MUST be exact-match over a
declared identifier format and MUST NOT be heuristic.

#### Scenario: The same paper on two hosts
- **WHEN** one investigation cites a paper by its DOI and another cites the same paper on a
  preprint host carrying that same identifier
- **THEN** both resolve to one identity
- **THEN** the join names the work identifier that merged them

#### Scenario: No identifier is present
- **WHEN** a source carries no recognizable work identifier
- **THEN** the resolver falls through to the normalized URL
- **THEN** no merge occurs on the basis of a partial or guessed identifier

#### Scenario: A merge is inspected
- **WHEN** two sources are reported as one identity
- **THEN** the report names the identifier that caused the merge
- **THEN** an incorrect merge is visible in the output rather than silent

### Requirement: A derivation records its resolver chain
The derived layer MUST record the resolver chain and version that produced it. Layers produced under
different chains MUST NOT be presented as comparable.

#### Scenario: Inspect a derived layer
- **WHEN** a derived layer is read
- **THEN** it states the resolver chain and version that produced it

#### Scenario: Compare layers from different chains
- **WHEN** two layers produced under different resolver chains are compared
- **THEN** the comparison is refused rather than reported

### Requirement: Anchored claims join on the normalized sentence
Two investigations MUST be linked when they anchored claims that normalize to the same sentence,
using the same normalization the framework already applies to textual anchoring. When the joined
claims are anchored to different resolved sources, the layer MUST report the join as quote
propagation.

#### Scenario: Two investigations rely on the same fact
- **WHEN** two investigations anchor claims that normalize to the same sentence in one source
- **THEN** the layer reports them as joined on that claim
- **THEN** the join is distinguishable from a join on the source alone

#### Scenario: One sentence appears in two sources
- **WHEN** the same normalized sentence is anchored in two different resolved sources
- **THEN** the layer reports the join as quote propagation
- **THEN** the report names both sources, so apparent corroboration by two sources is visible as one
  sentence appearing twice

### Requirement: Every cross-investigation edge is explicit
Every edge the layer creates MUST declare its provenance, and MUST be derived from a declared source
identifier, a declared textual anchor, or a decision's declared `evidence_claim_ids`. The layer MUST
NOT create an inferred edge.

#### Scenario: Inspect an edge
- **WHEN** any edge produced by this layer is inspected
- **THEN** it declares explicit provenance
- **THEN** it names the declared source identifier, anchor, or decision claim reference it was
  derived from

### Requirement: Decision lineage remains historical
After completion, a decision's declared claim dependencies MUST remain historical lineage even if a
referenced claim later becomes stale, not anchored, or unverifiable. Current evidence health MUST be
reported separately and MUST NOT erase or rewrite the dependency. A completed legacy decision that
omits `evidence_claim_ids` MUST report `lineage unavailable`; an explicit empty list MUST report `no
declared claim dependencies`. Decision lineage MUST NOT be inferred from prose, similarity, or a
model.

#### Scenario: A referenced claim degrades after completion
- **WHEN** a completed decision references a claim that later becomes stale, not anchored, or
  unverifiable
- **THEN** the decision-to-claim dependency remains present as historical lineage
- **THEN** the claim's current evidence health is reported separately

#### Scenario: Read legacy or explicitly empty lineage
- **WHEN** a completed legacy decision omits `evidence_claim_ids`
- **THEN** its lineage is reported as `lineage unavailable`
- **WHEN** a decision declares an explicit empty `evidence_claim_ids` list
- **THEN** it is reported as `no declared claim dependencies`

### Requirement: Query what depends on a source
For any source the layer MUST report the investigations that cite it, the claims anchored to it, and
the completed decisions whose `evidence_claim_ids` declare those claims. Every result MUST be
qualified by investigation. The layer MUST NOT recover missing lineage by parsing prose or using a
model.

#### Scenario: Query a cited source
- **WHEN** a source identifier is queried
- **THEN** the citing investigations, anchored claims, and dependent completed decisions are reported
- **THEN** each reported item names the investigation it belongs to

#### Scenario: Query a legacy decision without lineage
- **WHEN** a source query encounters a completed legacy decision without `evidence_claim_ids`
- **THEN** the decision is not inferred as dependent from its prose
- **THEN** its investigation-qualified lineage status is `lineage unavailable`

#### Scenario: Qualify a dependent decision by persisted lifecycle status
- **WHEN** decisions declaring a source's anchored claims belong to investigations whose persisted
  lifecycle states are `status: done`, `status: active` without reopen history, `status: active` with
  reopen history, and `status: dropped`
- **THEN** only the decision belonging to the investigation whose status is exactly `done` is reported
  as a dependent completed decision
- **THEN** the decisions belonging to both active investigations and the dropped investigation are
  excluded

### Requirement: Report decisions whose support degraded
The layer MUST report completed decisions that depend on a source whose support has degraded, and
MUST distinguish the cause: the source is unreachable, the stored snapshot no longer matches the
live source, or the source has passed the expiry configured for its tier. Causes MUST NOT be
collapsed into a single flag. The default report MUST cover only the event-driven causes,
unreachable and changed; expiry MUST be excluded unless explicitly requested.

#### Scenario: A cited source is no longer reachable
- **WHEN** a source that a completed decision depends on cannot be retrieved
- **THEN** the decision is reported with the cause recorded as unreachable
- **THEN** the report names the source and the investigation that made the decision

#### Scenario: A stored snapshot no longer matches the live source
- **WHEN** a live source differs from the snapshot stored when it was cited
- **THEN** the dependent completed decisions are reported with the cause recorded as changed
- **THEN** the cause is distinguishable from unreachable and from expired

#### Scenario: A cited source passed its tier expiry
- **WHEN** a source is older than the expiry configured for its tier and expiry is requested
- **THEN** the dependent completed decisions are reported with the cause recorded as expired

#### Scenario: Read the default report
- **WHEN** the degraded-support report is produced without requesting expiry
- **THEN** unreachable and changed causes are reported
- **THEN** no decision is reported solely because a source passed its tier expiry

### Requirement: The layer never acts without human review
The derived layer MUST NOT block any lifecycle transition, MUST NOT modify any artifact on its own,
MUST NOT change any investigation's status, and MUST NOT be consumed by any gate. Transfer-time
validation of the decision artifact's `evidence_claim_ids` is an explicit lifecycle evidence-contract
modification and does not consume derived-layer output. Writing a human review is not a modification
by the layer.

#### Scenario: Degraded support is reported
- **WHEN** the layer reports that a completed decision lost source support
- **THEN** no artifact of that investigation is modified by the layer and its status is unchanged
- **THEN** no subsequent lifecycle transition is blocked by the report

### Requirement: A degraded-support report can be acknowledged
A researcher MUST be able to record that a reported degradation was reviewed. The acknowledgement
MUST require a reason and an author, MUST be stored inside the affected investigation, and MUST be
recorded as an additive lifecycle event rather than an edit to a past artifact. The layer MUST read
acknowledgements as input and MUST NOT report an acknowledged degradation again.

#### Scenario: Acknowledge a reported degradation
- **WHEN** a researcher records a review of a reported degradation with a reason and an author
- **THEN** the acknowledgement is stored in the affected investigation and appears in its trail
- **THEN** the layer no longer reports that degradation

#### Scenario: Acknowledge without a reason
- **WHEN** an acknowledgement is attempted with an empty reason or no author
- **THEN** it is refused and nothing is recorded

### Requirement: An acknowledgement is scoped and goes stale
An acknowledgement MUST be scoped to the specific degradation reviewed, not to the decision or the
source in general. When the source degrades again in a way that was not reviewed, the acknowledgement
MUST become stale and the degradation MUST be reported again.

#### Scenario: The source changes after acknowledgement
- **WHEN** an acknowledged source changes again after the review
- **THEN** the acknowledgement is marked stale
- **THEN** the degradation is reported again as a new fact

#### Scenario: A different cause appears
- **WHEN** a source acknowledged as changed later becomes unreachable
- **THEN** the unreachable cause is reported despite the existing acknowledgement

### Requirement: Offline derivation with visible reachability gaps
The layer MUST be fully derivable offline from stored snapshots and declared metadata. Reachability
MUST NOT be assumed when it was not checked; an unchecked source MUST be reported as not checked
rather than as reachable.

#### Scenario: Derive the layer offline
- **WHEN** the layer is derived without network access
- **THEN** snapshot mismatch and tier expiry are still reported
- **THEN** reachability is reported as not checked rather than as reachable or unreachable

### Requirement: Knowledge-base-wide export
The layer MUST be exportable through the existing export formats, covering every investigation rather
than one, and MUST express its content as validated nodes and provenance-carrying edges.

#### Scenario: Export the knowledge base
- **WHEN** the accumulated layer is exported
- **THEN** the export covers every investigation under the configured root
- **THEN** the exported graph passes the same node and edge validation as a per-investigation graph

