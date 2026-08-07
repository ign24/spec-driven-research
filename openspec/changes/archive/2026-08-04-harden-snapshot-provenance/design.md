## Context

`snapshot.py` currently persists a URL, extracted text, and `sha256` of the extracted content at
capture time. Everything downstream trusts that record: textual anchoring matches claims against the
stored text, and the verification ledger caches results keyed to it. The record cannot express the
difference between a page that answered directly and one that redirected to another host, between a
2xx response and a persisted error page, or between the bytes hashed at capture and the bytes on
disk now.

The gap is now blocking two separate uses. Textual anchoring already trusts snapshots to decide
whether a claim is supported. The proposed cross-investigation layer also needs to distinguish the
declared source from the retrieval destination and to detect whether persisted source content changed.
Today's metadata cannot support either use safely.

This change was drafted as section 3 of `release-hardening-and-public-distribution` and extracted
whole. Its requirements are unchanged by the move; only its framing and sequencing are.

## Goals / Non-Goals

**Goals:**

- Make every evidence-affecting retrieval fact explicit and separately inspectable.
- Keep the declared URL as identity, with the final URL visible as provenance.
- Fail closed: a capture that is not demonstrably clean does not supply evidence.
- Detect post-capture modification of a snapshot before it is used for anchoring.
- Preserve the existing confidence boundary in the stronger metadata: more provenance must not read
  as more authority.

**Non-Goals:**

- Proving publisher identity, authorship, authenticity, or truth. Richer provenance describes a
  retrieval event more precisely; it says nothing more about the world.
- Archiving or content-addressed storage beyond the persisted snapshot already written.
- Changing which URLs are allowed. The network policy's existing validation is extended with
  provenance, not relaxed.
- Migrating legacy snapshots by inference. They are marked incomplete, not repaired.

## Decisions

### Decision 1: The declared URL is identity, the final URL is provenance

A redirect is recorded in full and remains visible, and the declared URL stays the source's identity.
The researcher cited a location; where retrieval landed is evidence about that citation, not a
replacement for it.

Alternatives considered. *Final URL as identity*: it would silently rewrite what the researcher
declared, and a source whose redirect target changes would appear to become a different source.
*Discarding the redirect chain*: it hides the case that matters most, where retrieval crosses to
another host and the researcher should see that before trusting the snapshot.

This decision constrains `add-cross-investigation-reuse`: its resolver chain normalizes and resolves
the declared URL, never the final one.

### Decision 2: The hash covers exact persisted bytes and is recomputed before anchoring

Hashing at capture proves what was fetched. Hashing before use proves what is being read. Only the
second protects against a snapshot edited after capture, which is the failure the hash-consistency
control exists to catch elsewhere in the lifecycle.

Consequence: hashes are computed over the exact persisted `content.md` bytes rather than over an
in-memory extraction, so the stored artifact and the hashed object are the same thing.

### Decision 3: Fail closed, and keep the real outcome

A non-2xx terminal response, unsupported content, empty extraction, incomplete provenance, or a hash
mismatch all make a snapshot non-passing. In every case the actual outcome metadata is preserved
rather than discarded.

Discarding a failed capture would lose the most useful record: that this source was reachable once,
returned this status, and no longer supports the claim. Keeping the outcome is what makes the
cross-investigation degraded-support report possible.

### Decision 4: Legacy snapshots are marked incomplete, never inferred

An existing snapshot without the new facts is not assumed to have redirected nowhere and returned
200. It requires recapture or a scoped human resolution.

Alternatives considered. *Defaulting missing facts to the benign case*: it would silently convert
unknown provenance into asserted provenance, which is the exact conflation this change removes.
*Best-effort backfill by re-fetching*: it would attribute a fresh retrieval's provenance to a capture
made at another time, and cannot be done offline.

## Risks / Trade-offs

- **Existing investigations break.** Every snapshot captured before this change becomes non-passing
  until recaptured or resolved. → Deliberate and unavoidable if the contract is to mean anything;
  the scoped human resolution already exists as the escape hatch, and the framework is alpha.
- **Recapture requires network and may fail or return different content.** A source alive at capture
  time may be gone now, so recapture can turn a previously passing investigation into a blocked one.
  → That is a true statement about the evidence, not a regression. It is also precisely the signal
  the cross-investigation layer will report.
- **More provenance can read as more authority.** A record showing TLS, redirects, and hashes looks
  like proof of authenticity to a casual reader. → The confidence-boundary requirement is part of
  this change rather than left to documentation, and the metadata must not claim what it cannot show.
- **Extraction remains the weak link.** The hash covers persisted bytes, and persisted bytes are the
  output of text extraction, which can differ across library versions. → Out of scope here; noted so
  that a future change can version the extractor alongside the capture.
