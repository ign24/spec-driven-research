## Context

SDR preserves evidence trails so a conclusion can be reviewed later, and makes `reuse` a mandatory
stage. Neither promise has a mechanism behind it once an investigation is done. `build_sdr_context_graph`
takes one `Research` and `write_context_graph` writes into that research's directory; `build_index`
produces a status table whose richest column is a recommendation ring. Nothing reads two
investigations together.

Three properties of the existing code determine how cheaply this can be fixed:

- `canonical_node_id("source", url)` already yields the same identifier for the same URL in any
  investigation. The join key exists and is stable.
- `index.py` already iterates every research root under a base directory and loads each one. The
  traversal primitive exists.
- `context_export.py` already exports a graph to Obsidian, Mermaid and DOT. The presentation layer
  exists and is scoped one level too low.

What does not exist is any notion of a completed decision's dependency on a source surviving beyond
the investigation that made it. That is the whole of this change.

## Goals / Non-Goals

**Goals:**

- Make `reuse` mean something operationally: a new investigation can find what previous ones
  established about the same sources.
- Answer which completed decisions rest on a source that has since died, changed, or expired.
- Derive everything from the investigations themselves, so the layer can be deleted and regenerated
  without loss.
- Stay deterministic and model-free, and remain fully computable offline from stored snapshots.
- Keep the derived layer advisory: it never blocks, mutates, or becomes a gate input. Separately,
  validate the decision artifact's explicit claim lineage at transfer.

**Non-Goals:**

- Topic or semantic similarity. Two things are related here only when they resolve to the same source
  identity or the same normalized anchored sentence.
- The domain-profile system itself. This change defines the pluggable identity chain that a profile
  will later select; it does not touch tier derivation, staleness policy, section headings, or
  recommendation vocabulary.
- Automatic invalidation. The layer reports which conclusions lost support; a human decides what that
  means. Rewriting a past decision without review is exactly the failure SDR exists to prevent.
- Competing with general knowledge managers on search, linking ergonomics, or editing. The export
  path exists so those tools can render this graph, not so SDR replaces them.
- Concurrency control, domain profiles, and the software-specific node types in the per-investigation
  graph. All real, all separate.

## Decisions

**Handoff from `harden-snapshot-provenance`:** source identity always resolves
the declared URL, never the final URL. This fixes only the identity input and
does not change the broader approved resolver, join, reporting, advisory, or
export scope of this change.

### Decision 1: Source identity is resolved by a pluggable chain, not by the raw URL

The current `canonical_node_id("source", url)` sanitizes characters and nothing else, which makes the
raw URL an unusable join key. Measured on the shipped function: `http` and `https` forms of one page
produce different identifiers, so do `www` and bare hosts, and so does a tracking parameter left on
a pasted link. Two investigations citing the same page fail to join, silently.

Identity is therefore resolved by an ordered chain of resolvers, applied at derivation time:

1. **Work identifier.** Extract a DOI, arXiv id, PMID, or ISBN from the URL or the stored snapshot
   metadata. When one is present it is the identity, so the same paper on `arxiv.org`, `doi.org`, and
   a journal host resolves to one source.
2. **Normalized URL.** Lowercase the host, drop a leading `www.`, unify the scheme, strip tracking
   parameters and fragments, and normalize the trailing slash.

The URL resolved is always the **declared** one. `harden-snapshot-provenance` establishes that a
recorded redirected final URL stays visible as provenance and never substitutes for the declared
identity, and this layer honours that: a source whose redirect target changes must not appear to
become a different source. That change is a prerequisite here, because today's snapshot metadata does
not separate the two.

The chain, not a per-investigation setting, is what a domain profile will later select. A scholarly
resolver applied to a software URL finds no identifier and falls through to the normalized URL, so
one derivation handles a root holding both biology and engineering investigations without labelling
anything. Every resolver is pure: regex and string work over stored data, no network, no model.

Alternatives considered. *Raw URL*: measurably broken, above. *Snapshot content hash as the identity*:
attractive because `snapshot.py` already stores `sha256` of the extracted text, and rejected as an
identity because extraction differs per host, so the same paper on two sites hashes differently while
the hash still changes for reasons that have nothing to do with the work. The content hash is the
right signal for detecting that a source changed, and the wrong one for deciding two sources are the
same. *Topic or embedding similarity*: rejected outright; it needs a model, and it would put
unverifiable relations into the one layer whose value is that its relations are checkable.

The consequence is still a deliberately sparse graph. Two investigations about the same subject that
share no source and no claim remain unrelated, because the claim this layer makes is evidentiary
rather than thematic.

### Decision 1b: A derivation records the resolver chain that produced it

Because identity is pluggable, the same investigations can yield different graphs under different
chains. The derived layer therefore records the resolver chain and its version, and a layer produced
under one chain is never compared against a layer produced under another.

Without this the layer would be reproducible only by accident, which would undermine the property
that makes the rest of SDR trustworthy.

### Decision 1c: Anchored claims join on the normalized sentence

Two investigations are also joined when they anchored a claim to the same sentence, compared through
the existing `normalize_text` used by textual anchoring. This is low frequency and high precision,
and it costs almost nothing because the normalization already exists.

It carries information the source join does not. Within one source it distinguishes "we both read
this document" from "we both relied on this exact fact", which is what reuse actually needs. Across
different sources it detects the same sentence appearing in two documents, which is quote
propagation: two sources that look like independent corroboration but are one source quoted twice.
SDR already checks triangulation inside an investigation; this extends that scrutiny across the
knowledge base, and false triangulation surviving across investigations is a defect worth surfacing.

### Decision 2: Derived and disposable, never authoritative

The layer is recomputed from the research roots on demand and is never an input to any gate, never
edited by hand, and never a source of truth that could drift from the investigations it summarizes.
`index.py` already establishes the precedent in its own docstring: regenerated, never hand-edited.

Alternatives considered. *A persisted, incrementally updated store*: faster on large corpora and
rejected anyway. A persisted derivation can disagree with the artifacts, and a knowledge layer that
silently disagrees with its evidence is worse than no knowledge layer. Recompute cost is bounded by
the number of investigations a single researcher has, which is small.

### Decision 2b: Decisions persist exact claim lineage

Each decision memo persists `evidence_claim_ids`, a structured list of the exact claim IDs the
decision used. The name describes historical evidence use without implying that those claims remain
currently verified. The transfer gate validates the declaration deterministically: every ID must be
well formed, unique, present in the same investigation, and verified at transfer. Malformed or
duplicate IDs fail safely. Dependency edges come only from this field; prose is never parsed and no
model infers lineage.

The distinction between absence and emptiness is intentional. A completed legacy decision that
omits the field reports `lineage unavailable`; an explicit empty list reports `no declared claim
dependencies`. Neither state is inferred from prose.

After completion, the declared relation is historical. A claim becoming stale, not anchored, or
unverifiable changes its current evidence health but does not erase the decision's dependency on it.
Queries report lineage and current health separately. This is a surgical modification to the
lifecycle evidence contract: decision artifact validation changes, while the derived layer remains
advisory and is not itself gate input.

### Decision 3: Invalidation is reported against completed decisions, and stays advisory

The valuable query is not "is this source alive" but "what did I conclude that rested on it". The
layer walks from a source to the claims anchored to it, then follows each decision's explicit
`evidence_claim_ids`, and reports the completed decisions whose support has degraded. Every result is
qualified by investigation so equal local IDs can never be confused across investigations.

"Completed" is a deterministic lifecycle qualification, not an interpretation of a decision memo:
the decision MUST belong to an investigation whose persisted lifecycle `status` is exactly `done`.
Decisions belonging to active, reopened, or dropped investigations are excluded from dependent
completed decisions.

Degradation has three distinct causes and they must not be collapsed: the source is unreachable, the
stored snapshot no longer matches the live source, or the source has passed the expiry for its tier.
Each carries different weight for a human, and reporting a single "stale" flag would throw that away.

The first two are events: something happened to the source. Expiry is only the clock advancing, and
every source eventually reaches it, so expiry is excluded from the default report and available on
request. Mixing a time-driven condition that will eventually match everything into a report about
things that happened would bury the two signals that carry information.

The report is advisory: no artifact is edited automatically, no status is downgraded, no gate
consumes it. A system that demoted a past conclusion on its own because a link broke would be making
exactly the unreviewed judgement SDR exists to prevent.

Advisory is not the same as inert. A researcher who reviews a degraded-support report and concludes
that the decision still stands must be able to record that, or the same report reappears forever and
the feature dies of alert fatigue. That review is a human judgement, so it is recorded where SDR
already records human judgement -- inside the affected investigation, with a mandatory reason and an
author, following `resolve-claim` and the verification ledger. The derived layer reads those
acknowledgements as input and stops reporting the acknowledged case.

The ledger precedent carries one property that matters more than the pattern itself: a resolution can
become `stale`. An acknowledgement is scoped to the degradation that was reviewed, so if the source
changes again the acknowledgement goes stale and the report returns, because that is a new fact
rather than the one already reviewed.

This is why the earlier draft of this decision was wrong. It said the layer never modifies any
artifact, which is the correct prohibition on automatic invalidation but also forbids recording the
human review that makes the advisory report survivable. The prohibition is on acting without review,
not on writing down a review.

### Decision 4: Reachability is optional and its absence is visible

Snapshot mismatch and tier expiry are computable offline from stored data. Reachability is not. The
layer therefore computes what it can offline and marks reachability as not checked rather than
assuming a source is alive.

This follows the framework's existing rule that skipped checks are reported as skipped, not passed,
and it keeps the whole layer usable on a plane. An online refresh is an explicit operator action.

### Decision 5: Export the knowledge base through the existing exporters

The cross-investigation graph is expressed in the existing `ContextGraph` node and edge types where
they fit, and reuses `context_export.py` rather than growing a second export path. A researcher
should be able to open the whole accumulated graph in Obsidian, not one investigation at a time.

This constrains the design usefully: whatever the layer produces has to be expressible as validated
nodes and provenance-tagged edges, which is the same discipline the per-investigation graph already
enforces.

### Decision 6: Provenance tagging carries over unchanged

Every cross-investigation edge declares its provenance from the existing vocabulary, and every edge
this layer creates is `explicit` -- derived from a declared source id, a declared anchor, or a
decision's declared `evidence_claim_ids`, never inferred. An edge in this layer that cannot be
labelled `explicit` does not belong in this version.

Note for implementation: `ALLOWED_PROVENANCE` currently includes `judge`, which no code path in the
framework produces and which contradicts the standing rule against model-based scoring. This change
does not use it and does not remove it; it should be resolved separately rather than quietly relied
upon or deleted here.

## Risks / Trade-offs

- **The layer's value scales with the number of investigations, and a new user has one.** The most
  compelling query needs history to be interesting. → Accept it. This is infrastructure for the user
  SDR wants in a year, and the invalidation query is valuable from the second investigation onward.
- **A resolver chain is a new place for identity to go wrong.** An over-eager identifier extractor
  could merge two distinct works, which is worse than missing a join: a false merge would attach one
  investigation's conclusions to another's evidence. → Every resolver must be exact-match over a
  declared identifier format, never heuristic; a merge must be traceable to the identifier that
  caused it; and the report must name that identifier so a wrong merge is visible rather than silent.
- **Sparse joins may make the graph look empty.** Investigations that share no source produce no
  edges, which can read as the feature being broken. → Report the join count and the number of shared
  sources explicitly, so an empty graph is legibly empty rather than apparently failing.
- **Recomputation cost grows with corpus size and snapshot comparison is I/O bound.** → Bounded by
  single-researcher scale; revisit only if a real corpus makes it slow, and never by persisting a
  derivation that can drift.
- **Reporting degraded support could be read as SDR judging past work.** → The output names the
  mechanical fact, the source and the cause, and never characterizes the conclusion. The researcher
  decides what a broken link means.
- **An acknowledgement is a place to hide a problem.** Recording "reviewed, still stands" makes a
  real warning disappear, and a careless acknowledgement is indistinguishable from a careful one. →
  Reason and author are mandatory, the acknowledgement is scoped to the specific degradation rather
  than to the decision, and it goes stale when the source changes again. The trail keeps it visible.
- **`INDEX.md` still shows a completed investigation as green when its evidence has decayed.**
  Lifecycle status and evidence health are two axes rendered as one column, which matters when
  someone else picks the work up and trusts the green. → Deferred deliberately. Separating them
  touches shipped audit markers and the index contract for a benefit that only appears at handoff,
  and it is recorded as follow-up rather than folded in here.
- **This adds surface to an alpha framework whose core has no outcome evidence yet.** → Stated
  plainly in the paused `add-live-harness-evidence` change. The bet is deliberate: knowledge
  accumulated on an unvalidated base is tidy garbage, and this change does not make that risk smaller.
