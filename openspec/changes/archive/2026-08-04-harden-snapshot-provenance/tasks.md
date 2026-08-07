## 1. Retrieval provenance

- [x] 1.1 Add failing network-policy tests for declared URL, final URL, ordered redirects, terminal status, and bounded response metadata
- [x] 1.2 Extend bounded retrieval to return complete redirect and terminal-response provenance without weakening URL validation; run network-policy and full tests

## 2. Snapshot capture

- [x] 2.1 Add failing snapshot tests for versioned provenance, 2xx-only eligibility, empty extraction, non-2xx outcomes, and SHA-256 over exact persisted bytes
- [x] 2.2 Implement versioned snapshot capture with distinct declared/final URLs, redirect records, fail-closed status, and exact persisted-byte hashing; run snapshot and full tests
- [x] 2.3 Add a failing test asserting the declared URL remains the source identity and a redirected final URL never substitutes for it

## 3. Verification and anchoring

- [x] 3.1 Add failing verification tests for incomplete legacy metadata, URL/provenance changes, non-eligible HTTP outcomes, persisted hash mismatch, and stale resolution invalidation
- [x] 3.2 Recompute snapshot hashes before anchoring, include evidence-affecting provenance in snapshot identity, and conservatively reject unknown legacy facts; run verification, textual-anchoring, and full tests
- [x] 3.3 Add a failing test asserting a failed or non-passing capture preserves its actual outcome metadata rather than discarding it

## 4. Confidence boundary

- [x] 4.1 Add a failing test asserting an eligible snapshot with a deterministic match reports local textual anchoring and never publisher identity or truth
- [x] 4.2 Add a failing test asserting a redirect ending at another apparent organization leaves both locations visible and infers no identity, independence, or authorship

## 5. Fixtures and regression

- [x] 5.1 Update synthetic lifecycle fixtures and regression tests so valid light/full workflows retain their existing behavior
- [x] 5.2 Confirm the documented offline path still completes and that skipped network checks remain reported as skipped

## 6. Verification and documentation

- [x] 6.1 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [x] 6.2 Run `uv run python -m sdr.readme_parity .` and update both language pairs where the docs changed
- [x] 6.3 Document the new snapshot metadata and the recapture path for legacy snapshots in the evidence model
- [x] 6.4 Add a `CHANGELOG.md` entry under Unreleased, calling out that pre-existing snapshots require recapture or scoped resolution
- [x] 6.5 Note for `add-cross-investigation-reuse` that source identity resolves the declared URL, never the final one
