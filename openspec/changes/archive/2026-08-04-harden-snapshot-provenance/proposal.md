## Why

Snapshot capture records a URL, extracted text, and a content hash, and treats that as sufficient
evidence. It conflates facts that must stay separate: the URL the researcher declared versus the URL
retrieval actually ended at, a successful retrieval versus one that returned an error page, and a
hash computed at capture time versus the bytes on disk now. A snapshot whose source redirected to a
different host, or that persisted an error page, or whose file was edited after capture, is today
indistinguishable from a clean one.

This was drafted inside `release-hardening-and-public-distribution` and extracted because it is not
release engineering. It is the evidence contract, and two things now depend on it. Textual anchoring
already trusts these snapshots to decide whether a claim is supported. And the cross-investigation
layer proposed in `add-cross-investigation-reuse` needs exactly these distinctions to work at all:
which URL is a source's identity when retrieval redirected, and whether a source's content changed
since it was cited. Neither question is answerable against today's snapshot metadata.

## What Changes

- Persist complete retrieval provenance per snapshot: distinct declared and final URLs, the ordered
  redirect record, terminal HTTP status, capture time, evidence status, and a hash over the exact
  persisted content bytes.
- Keep the declared URL as the source's identity. A redirected final URL stays visible as provenance
  and never substitutes for what the researcher declared.
- Fail closed on retrieval outcomes. Only a terminal 2xx response with supported non-empty text,
  complete provenance, and a valid persisted-byte hash may supply textual evidence; every other
  outcome keeps its real metadata and does not pass.
- Recompute the hash over the persisted bytes before anchoring, so a snapshot edited after capture
  invalidates its own use and any cached verification tied to it.
- Refuse to infer missing facts on legacy snapshots. An incomplete record requires recapture or a
  scoped human boundary rather than an assumed value.
- State the confidence boundary explicitly: provenance describes a recorded retrieval event and the
  occurrence of persisted text, and never implies publisher identity, authorship, authenticity, or
  truth.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sdr-lifecycle-evidence-contract`: strengthen deterministic textual anchoring and hash consistency,
  and add snapshot retrieval provenance, fail-closed HTTP outcomes, and the snapshot confidence
  boundary.

## Impact

- `src/sdr/network_policy.py` returns complete redirect and terminal-response provenance without
  weakening existing URL validation.
- `src/sdr/snapshot.py` writes versioned capture metadata with distinct declared and final URLs and
  hashes the exact persisted bytes.
- `src/sdr/verification.py` and `src/sdr/textual_anchoring.py` recompute the hash before anchoring,
  treat evidence-affecting provenance as part of snapshot identity, and stale cached resolutions when
  it changes.
- Synthetic lifecycle fixtures and their regression tests are updated so valid light and full
  workflows keep their current behaviour.
- Legacy snapshots captured before this change lack the new facts and become non-passing until
  recaptured or covered by a scoped human resolution. This is deliberate: inferring them would be the
  conflation this change exists to remove.
- Extracted from `release-hardening-and-public-distribution`, which retains the release, packaging,
  distribution, integration-acquisition, and public-governance work and is paused.
- Prerequisite for `add-cross-investigation-reuse`, which resolves source identity and detects
  changed sources from this metadata.
