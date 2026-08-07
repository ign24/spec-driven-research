## Why

`reuse` is a mandatory lifecycle stage, but nothing in SDR accumulates across investigations. The
Context Graph is built per research and written inside that research's directory. `INDEX.md` is a
flat status table that knows each investigation's slug, stage and ring but nothing about what it
established. Between the two there is no layer where one investigation can inform the next, so the
stage that justifies the whole workflow has no substrate.

The gap is sharpest where SDR is otherwise strongest. Sources die, change, and expire, and SDR
already detects that inside the investigation being checked. Nothing tells the researcher which
*completed* decisions rested on a source that has since disappeared. A framework that preserves
evidence trails specifically so a conclusion can be reviewed later cannot answer the most obvious
review question: what did I conclude that no longer stands up.

Source identity is already canonical and stable across investigations, and snapshots are already
stored locally. The link between investigations that cite the same source is therefore deterministic,
verifiable, and free of models -- the same evidence discipline SDR applies within an investigation,
applied between them.

## What Changes

- Add a derived cross-investigation layer over every research root under `SDR_ROOT`, regenerated
  from the investigations themselves and never hand-edited, following the existing `INDEX.md` rule.
- Establish resolved source identity as the cross-investigation join. The raw URL is not usable as a
  key: the shipped identifier treats `http` and `https`, `www` and bare hosts, and a stray tracking
  parameter as three different sources. Identity is resolved by an ordered chain that prefers a work
  identifier (DOI, arXiv, PMID, ISBN) and falls back to a normalized URL, so the same paper published
  on several hosts resolves once.
- Make that chain pluggable, so the domain profile work can later select it without redesigning this
  layer. The chain is a property of the derivation, applied uniformly, not a per-investigation label.
- Join investigations on anchored claims as well: two claims normalized to the same sentence are
  linked. Within one source this distinguishes reading the same document from relying on the same
  fact. Across different sources it surfaces quote propagation, where apparent corroboration by two
  sources is one source quoted twice.
- Require decision memos to persist `evidence_claim_ids`, a structured list of the exact claim IDs
  used by the decision. Transfer validates that every referenced claim exists and is verified at
  that time; the persisted relation remains historical if the claim later degrades.
- Answer, for any source: which investigations cite it, which claims are anchored to it, and which
  completed decisions explicitly declare those claims as dependencies. A completed decision is
  deterministically one belonging to an investigation whose persisted lifecycle `status` is exactly
  `done`; decisions in active, reopened, or dropped investigations are excluded. Current evidence
  health is reported separately and never erases the historical dependency.
- Answer the inverse and more valuable question: given a source that is now unreachable, whose
  snapshot no longer matches, or that has passed its tier's expiry, list the completed decisions
  that rest on it, so the researcher can review conclusions that lost their footing.
- Report only event-driven degradation by default: the source became unreachable, or its content
  changed. Tier expiry is time-driven and eventually matches every source, so it is available on
  request rather than mixed into the default report.
- Let a researcher record that a reported degradation was reviewed, with a mandatory reason and
  author, following the existing `resolve-claim` pattern. The layer reads acknowledgements and stops
  repeating a reviewed case; an acknowledgement is scoped to the degradation reviewed and goes stale
  when the source changes again. Without this the report repeats forever and stops being read.
- Keep the derived layer non-blocking. Its output never gates a transition, changes lifecycle state,
  or alters an artifact on its own. The decision memo's declared lineage is separately validated at
  transfer as an explicit evidence-contract correction. Recording a human review is an additive
  lifecycle event, not the layer acting by itself.
- Raise the existing Obsidian, Mermaid and DOT exports from one investigation to the whole knowledge
  base, so the accumulated graph is browsable in tools the researcher already uses.
- Compute every relation deterministically. No model scores, ranks, or infers a link, consistent
  with the rule that matching does not use models.

## Capabilities

### New Capabilities

- `cross-investigation-reuse`: the derived layer spanning investigations, source identity as the
  join, the invalidation query over completed decisions, the non-blocking guarantee, and the
  knowledge-base-wide export.

### Modified Capabilities

- `sdr-lifecycle-evidence-contract`: decision memos gain required structured
  `evidence_claim_ids`; transfer validates those declared claim references without making the
  derived cross-investigation layer a gate input.

## Impact

- New module deriving the cross-investigation layer by iterating research roots, reusing the
  existing iteration primitive in `src/sdr/index.py`.
- New source-identity resolver chain with a generic URL-normalizing resolver and a scholarly
  work-identifier resolver. Both are pure functions over stored data: no network, no model. The
  existing `canonical_node_id` remains as the node-id sanitizer and is no longer treated as identity.
- Claim joining reuses `normalize_text` from `src/sdr/textual_anchoring.py` unchanged.
- New read-only CLI surface for the queries, alongside `sdr index` and `sdr context`, plus one
  writing verb that records an acknowledgement and follows the existing Git behaviour contract. No
  existing verb changes behaviour.
- `src/sdr/context_export.py` gains a knowledge-base-wide entry point; its per-investigation exports
  are unchanged.
- No lifecycle order or status changes. The decision artifact contract and transfer gate gain the
  explicit `evidence_claim_ids` validation authorized by this correction; derived query and health
  outputs remain advisory and are never gate inputs. The other new persisted data is the
  acknowledgement record, stored inside the affected investigation alongside the existing
  verification-ledger resolutions it is modelled on.
- No new runtime dependency. Source reachability and snapshot comparison reuse the existing network
  policy and snapshot machinery, and the layer must be fully derivable offline from stored snapshots.
- Explicitly out of scope: linking by topic or meaning, criterion-level reuse, editing or
  invalidating past artifacts automatically, concurrency control for shared research roots, and the
  domain-profile system itself. This change defines the pluggable identity chain a profile will
  select; it does not touch tier derivation, staleness policy, section headings, or recommendation
  vocabulary.
