## 1. Cross-investigation traversal

- [x] 1.1 Add a failing test asserting the layer derives from every research root under the configured root
- [x] 1.2 Add a failing test asserting a single-investigation root produces a layer with zero joins and no error
- [x] 1.3 Implement traversal reusing the existing research iteration primitive, without duplicating it
- [x] 1.4 Add a failing test asserting deleting and recomputing the layer over unchanged investigations yields an identical result
- [x] 1.5 Confirm the layer reads investigations only as input and writes nothing inside them

## 2. Source identity resolver chain

- [x] 2.1 Add a failing test asserting `http`/`https`, `www`/bare host, tracking parameters, fragments, and trailing slash resolve to one identity
- [x] 2.2 Implement the URL-normalizing resolver as a pure function over stored data
- [x] 2.3 Add a failing test asserting a DOI, arXiv id, PMID, and ISBN each resolve as the identity ahead of the URL
- [x] 2.4 Implement the work-identifier resolver with exact-match extraction only, never heuristic
- [x] 2.5 Add a failing test asserting a source with no recognizable identifier falls through to the normalized URL without merging
- [x] 2.6 Add a failing test asserting a reported merge names the identifier that caused it
- [x] 2.7 Implement the ordered chain and assert a mixed-domain root derives without per-investigation labelling
- [x] 2.8 Add a failing test asserting the derived layer records its resolver chain and version
- [x] 2.9 Add a failing test asserting layers produced under different chains refuse comparison

## 3. Joining on identity and claims

- [x] 3.1 Add a failing test asserting two investigations whose sources resolve alike are joined, naming the resolver
- [x] 3.2 Add a failing test asserting two investigations sharing a topic but no resolved source and no claim produce no join
- [x] 3.3 Add a failing test asserting two claims normalizing to the same sentence join, distinguishably from a source-only join
- [x] 3.4 Implement claim joining reusing `normalize_text` unchanged
- [x] 3.5 Add a failing test asserting one sentence anchored in two different resolved sources is reported as quote propagation naming both
- [x] 3.6 Add a failing test asserting every edge declares explicit provenance and names its origin
- [x] 3.7 Add a failing test asserting no edge is produced by similarity, ranking, or any model call

## 4. Dependency queries

- [x] 4.1 Add a failing template test asserting decision memos declare structured `evidence_claim_ids`
- [x] 4.2 Add failing schema tests asserting malformed and duplicate claim IDs are rejected deterministically
- [x] 4.3 Add failing transfer-gate tests asserting referenced claims must exist and be verified at transfer
- [x] 4.4 Add a failing legacy test asserting an omitted field means `lineage unavailable` while an explicit empty list means `no declared claim dependencies`
- [x] 4.5 Add a failing staleness test asserting later stale, not-anchored, or unverifiable health never erases historical lineage
- [x] 4.6 Add a failing test asserting decision dependencies are never inferred from prose, similarity, or a model
- [x] 4.7 Add a failing test asserting a source query reports citing investigations, anchored claims, and explicitly dependent completed decisions
- [x] 4.8 Add a failing test asserting every reported item and lineage status names its investigation
- [x] 4.9 Add a failing test asserting only decisions whose investigation has persisted `status: done` qualify, excluding `status: active` investigations both without and with reopen history, and `status: dropped` investigations
- [x] 4.10 Implement the template, schema, and transfer-gate support for validated `evidence_claim_ids`
- [x] 4.11 Implement the walk from source to anchored claims using the existing anchoring data, not re-parsing prose
- [x] 4.12 Implement the walk from claims to decisions using only persisted `evidence_claim_ids` and exact `done` status qualification

## 5. Degraded-support reporting

- [x] 5.1 Add a failing test asserting an unreachable source reports its dependent completed decisions with cause `unreachable`
- [x] 5.2 Add a failing test asserting a snapshot that no longer matches the live source reports cause `changed`
- [x] 5.3 Add a failing test asserting a source past its tier expiry reports cause `expired`
- [x] 5.4 Add a failing test asserting the three causes are never collapsed into one flag
- [x] 5.5 Add a failing test asserting the default report excludes expiry and covers unreachable and changed
- [x] 5.6 Implement degraded-support detection reusing the existing snapshot and tier-expiry machinery
- [x] 5.7 Add a failing test asserting the report names the mechanical fact and never characterizes the conclusion

## 6. Advisory guarantee

- [x] 6.1 Add a failing test asserting a degraded-support report modifies no artifact on its own and changes no investigation status
- [x] 6.2 Add a failing test asserting no lifecycle transition is blocked after the layer reports degraded support
- [x] 6.3 Add a failing test asserting no gate reads the layer's output
- [x] 6.4 Confirm by inspection that no gate imports the new module

## 7. Acknowledging a degradation

- [x] 7.1 Add a failing test asserting an acknowledgement requires a non-empty reason and an author
- [x] 7.2 Add a failing test asserting an acknowledgement is stored in the affected investigation and appears in its trail
- [x] 7.3 Implement the acknowledgement following the `resolve-claim` and verification-ledger pattern, as an additive event rather than an edit
- [x] 7.4 Add a failing test asserting the layer stops reporting an acknowledged degradation
- [x] 7.5 Add a failing test asserting an acknowledgement is scoped to the reviewed degradation, not to the decision or source
- [x] 7.6 Add a failing test asserting a further change to the source marks the acknowledgement stale and reports the degradation again
- [x] 7.7 Add a failing test asserting a different cause on an acknowledged source is reported despite the acknowledgement

## 8. Offline derivation

- [x] 8.1 Add a failing test asserting the layer derives without network access
- [x] 8.2 Add a failing test asserting snapshot mismatch and tier expiry are still reported offline
- [x] 8.3 Add a failing test asserting an unchecked source reports reachability as not checked, never as reachable
- [x] 8.4 Implement reachability as an explicit opt-in operator action reusing the existing network policy

## 9. Knowledge-base-wide export

- [x] 9.1 Add a failing test asserting the export covers every investigation rather than one
- [x] 9.2 Add a failing test asserting the exported graph passes the same node and edge validation as a per-investigation graph
- [x] 9.3 Implement the knowledge-base entry point in the existing exporter without a second export path
- [x] 9.4 Confirm the per-investigation exports are unchanged

## 10. CLI surface

- [x] 10.1 Add a failing test asserting the derivation and query verbs are read-only and create no commit
- [x] 10.2 Implement the read-only surface alongside `sdr index` and `sdr context`, with a `--json` structured form
- [x] 10.3 Add a failing test asserting the acknowledgement verb writes and commits by default and honours `--no-commit`
- [x] 10.4 Implement the acknowledgement verb following the existing Git behaviour contract
- [x] 10.5 Add a failing test asserting no existing verb changed behaviour
- [x] 10.6 Add both verbs to the CLI reference with their exact mutation, network, and guard contract

## 11. Verification and documentation

- [x] 11.1 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [x] 11.2 Run `uv run python -m sdr.readme_parity .` and update both language pairs where the docs changed
- [x] 11.3 Run `PYTHONDONTWRITEBYTECODE=1 uv run python -m sdr.public_tree_audit . --exclude .git --exclude .claude --exclude .venv --exclude .pytest_cache --exclude .ruff_cache --exclude build --exclude dist --exclude examples/__pycache__ --exclude src/sdr/__pycache__ --exclude tests/__pycache__ --exclude bench/__pycache__ --exclude bench/harness/__pycache__` and confirm no findings; excluded ignored local, cache, and build state is not inspected or deleted
- [x] 11.4 Document the layer in the evidence model: what a join means, what it does not claim, and why it never blocks
- [x] 11.5 Add a `CHANGELOG.md` entry under Unreleased
- [x] 11.6 Record what the layer cannot answer yet, as input to the criterion-level follow-up
- [x] 11.7 Record the deferred index work: lifecycle status and evidence health are one column today and should become two
