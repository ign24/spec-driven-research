# Evidence Model

SDR separates declared research content from deterministic validation records.
This separation makes gaps visible without claiming that automation can judge
the truth of a recommendation.

## Artifacts

- `brief.md` defines the question and criterion IDs.
- `notes/*.md` declare sources and contain cited exploration.
- local snapshots preserve text used for deterministic matching.
- `probe/results.md` maps every criterion to a result and reproducible evidence.
- probe files provide executable or inspectable evidence.
- `decision-memo.md` connects results, alternatives, limitations, and a ring.
- `assets/*.md` package a reusable output.
- `sdr.yaml` stores CLI-managed lifecycle and validation metadata.

## Validation controls

The public controls are **Structural**, **Evidential**, **Textual anchoring**,
**Executable**, **Hash consistency**, and **HITL**.

Structural validation checks schemas, required files, frontmatter, and sections.
Evidential validation checks source declarations, dates, tiers, triangulation by
distinct declared host, criterion references, artifact paths, and reproducibility
declarations. Redirect targets remain retrieval provenance and do not establish
organizational independence.

Textual anchoring extracts factual claims ending in `[S<n>]` and matches them
against local source snapshots. `[cf. S<n>]` validates the source reference but
does not create a claim and does not enter textual matching. The matcher does
not use models. A human-reviewed exception is explicit and scoped to the current
claim identity.

Executable validation runs an explicitly declared probe action and persists its
output, result, and current probe hash. Hash consistency detects changes after a
stage or probe was validated. HITL approval records who approved the current
decision memo and when.

## Snapshot provenance

Each schema-v2 snapshot stores `meta.yaml` beside the exact extracted bytes in
`content.md`. Its evidence-affecting metadata includes:

- `schema_version: 2`, the source `url`, and the distinct `declared_url` and
  `final_url`;
- `redirects` in retrieval order, with each hop's URL, status code, `Location`,
  and resolved target URL;
- terminal `http_status`, `captured_at`, `content_type`, and
  `content_eligible`;
- evidence `status` (`ok` or `unverifiable`); and
- `content_hash`, the SHA-256 digest of the exact bytes persisted in
  `content.md`, including its final newline when present.

The declared URL remains the source identity and must match the source
declaration. The final URL and ordered redirects describe where that retrieval
ended; they stay visible as provenance and never replace the declared identity.
In particular, a redirect to another host does not establish publisher identity
or organizational independence.

A snapshot can supply textual evidence only when provenance is complete, the
terminal response is 2xx, its content type is supported, extraction produces
non-empty text, `content_eligible` is true, `status` is `ok`, and the current
persisted bytes match `content_hash`. Verification recomputes SHA-256 from
`content.md` before anchoring. Non-2xx responses, unsupported content, empty
extraction, incomplete provenance, and hash mismatches fail closed while their
actual outcome metadata remains inspectable.

Pre-existing snapshots that lack schema-v2 provenance are not assumed to have
returned 200 or avoided redirects. Recapture the source through the snapshot
workflow so `meta.yaml` and `content.md` are regenerated together. If recapture
is unavailable, run `sdr verify-claims SLUG --json` to identify the unresolved
claim and use `sdr resolve-claim SLUG CLAIM_ID --reason TEXT --by NAME` only for
a documented, scoped human resolution. That resolution applies to the current
claim and snapshot identity, can become stale when either changes, and does not
repair or upgrade the legacy snapshot.

## Offline semantics

Offline mode skips outbound link checks and automatic snapshot capture. A
skipped check is reported as skipped, not passed. Previously captured local
snapshots can still be matched. Offline mode does not relax structure,
criterion coverage, executable evidence, consistency, or approval requirements.

## Context Graph

The Context Graph is a deterministic, derived view over selected criteria,
sources, results, decisions, and optional external references. It is useful for
coverage warnings, traces, exports, and queries. It is non-blocking and not
complete lineage: absence from the graph is not proof that a relationship does
not exist, and graph success is not an `advance` gate.

## Cross-investigation reuse

The cross-investigation layer is a disposable view derived from every
investigation under `SDR_ROOT`. It records its resolver chain and version, and
all of its relations come from stored declarations:

- A **source join** means that two declared source records resolved to the same
  exact work identifier, or to the same normalized declared URL when no work
  identifier was present. It means the records share a deterministic identity
  key; it does not establish a common publisher, authorship, authenticity,
  accuracy, truth, organizational independence, or current evidence health.
- An **anchored-claim join** means that verified textual anchors in two
  investigations normalize to the same sentence. When those anchors name
  different resolved source identities, SDR reports quote propagation. This
  surfaces repeated text for review; it does not prove copying, common
  authorship, causal dependence, independent corroboration, or truth.
- A **decision dependency** means that a completed investigation's decision
  memo explicitly persisted the claim ID in `evidence_claim_ids`. SDR does not
  infer this relation from decision prose, topic or semantic similarity,
  ranking, or a model. A missing field is `lineage unavailable`; an explicit
  empty list is `no declared claim dependencies`.

These joins and queries are deterministic and model-free. They are advisory and
non-blocking: the derived output is not consumed by a gate, does not alter an
artifact or lifecycle status, and cannot block `advance` or any other lifecycle
transition. Transfer separately validates the decision memo's declared claim
IDs; that artifact check does not make the derived layer a gate input.

### Identity, lineage, and current health

Resolved identity and source-record health answer different questions. Identity
joins declarations across investigations and lets `sdr cross source` query the
citations, claims, and explicit completed-decision dependencies associated with
that key. Health is evaluated for the exact source record identified by its
investigation and local source ID. A failed or changed record is not attributed
to every other record that resolves to the same identity.

Decision-to-claim lineage is historical: a claim later becoming stale, not
anchored, or unverifiable changes `current_evidence_health` but does not erase
the persisted dependency. The report presents lineage and current health
separately. It reports mechanical source facts and never characterizes whether
a past conclusion remains valid.

Derivation and source queries are offline. The degraded-support report is also
offline by default: local snapshot facts remain available, while reachability is
`not checked`, never assumed reachable. `--online` is an explicit operator
opt-in that observes current reachability and content without making a read-only
command persist the observation. Expiry is a separate time-driven cause and is
included only when explicitly requested with an `--as-of` date.

An acknowledgement records human review of one exact source record, cause, and
observation. It requires a reason and author, is additive, and does not approve,
invalidate, or rewrite a decision. While that exact degradation remains current,
it suppresses the repeated dependent-decision items. A new observation or a
different cause is not covered: the acknowledgement becomes `stale` and the new
degradation is reported. Historical lineage remains unchanged in either case.

### Current limits and follow-up

The layer cannot yet answer which criterion a prior investigation established,
whether two criterion IDs express the same requirement, whether prior evidence
is applicable to a new criterion, or whether findings satisfy, conflict with,
or supersede that criterion. Criterion-level reuse is explicit follow-up work;
it requires a structured criterion relation rather than inference from prose or
similarity. Until then, an absent join or dependency is not evidence that two
investigations are unrelated.

`INDEX.md` also combines lifecycle status and evidence-health audit information
in one status view today. A completed investigation can therefore still look
complete after its supporting evidence degrades. Deferred index work must split
these into two independent axes: persisted lifecycle status and current evidence
health. The cross-investigation report does not silently redefine the existing
index column.

## Confidence boundaries

A passing gate means the declared contract passed. It does not mean a source is
accurate, a benchmark is representative, an executable is benign, or a human
decision is correct. Those judgments require review outside deterministic checks.
Snapshot provenance records a retrieval event and textual anchoring records that
persisted text matched a claim. URLs, redirects, HTTP or TLS outcomes, hashes,
organization heuristics, and textual matches do not prove publisher identity,
authorship, authenticity, accuracy, or truth.
