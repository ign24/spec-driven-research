# CLI Reference

The research root defaults to `research/` and can be changed with `SDR_ROOT`.
The knowledge directory defaults to `knowledge/` and can be changed with
`SDR_KNOWLEDGE`. Prefer `--json` for automation when the command offers it.

## Lifecycle commands

| Command | Guard and result | Side effects |
| --- | --- | --- |
| `sdr new SLUG --title TEXT --question TEXT [--mode full|light]` | Requires a new safe slug; starts at intake. | Creates metadata and brief; commits unless `--no-commit`. |
| `sdr check SLUG [--stage STAGE] [--offline] [--json]` | Evaluates structural/evidential rules and consistency. | May capture missing explore snapshots; never advances or commits. |
| `sdr check --all [--offline] [--json]` | Checks active investigations. | Same network behavior as single check. |
| `sdr advance SLUG [--offline]` | Current-stage gates, required verification, hashes, and approval must pass. | Changes metadata; commits unless `--no-commit`. Never runs the probe command. |
| `sdr reopen SLUG --to STAGE --reason TEXT` | Destination must be an earlier stage in the selected mode. | Records reason, invalidates later hashes, reactivates done work, commits unless `--no-commit`. |
| `sdr drop SLUG --reason TEXT` | Active work can be discarded explicitly. | Preserves evidence and commits unless `--no-commit`. |
| `sdr archive SLUG` | Requires `done` or `dropped`. | Writes a knowledge summary, marks archived, commits unless `--no-commit`. |
| `sdr migrate SLUG [--json]` | Migrates legacy schema metadata without changing stage. | Assigns source IDs, may fetch snapshots, and writes metadata. |

## Evidence commands

| Command | Purpose | Important behavior |
| --- | --- | --- |
| `sdr snapshot SLUG [--json]` | Capture declared explore sources. | Performs bounded outbound HTTP requests and writes local snapshots. |
| `sdr verify-claims SLUG [--json]` | Match current factual claims against local snapshots. | Deterministic and model-free; writes verification evidence. |
| `sdr resolve-claim SLUG CLAIM_ID --reason TEXT [--by NAME]` | Record human review of one current unresolved claim. | Scoped to claim identity; does not approve transfer. |
| `sdr verify-probe SLUG [--timeout SECONDS] [--json]` | Execute the declared probe runner and persist its result. | Requires `verify.action: run`; runs argv without a shell in `probe/`. |
| `sdr approve SLUG [--by NAME] [--offline]` | Approve the current transfer memo. | Transfer-only stage guard; writes approval metadata. |

## Reporting commands

| Command | Result |
| --- | --- |
| `sdr status [SLUG] [--json]` | Current stage, status, gate summary, timebox, and audit markers. Status uses an offline gate view, so network checks are skipped. |
| `sdr index` | Regenerates `research/INDEX.md`. |
| `sdr doctor [--json]` | Reports local readiness and deprecated environment variables. |

## Context commands

`sdr context build`, `inspect`, `trace`, `check`, `export`, and `query` operate
on a derived Context Graph. `check --strict` can return a failure for graph
warnings when explicitly invoked. Context commands remain auxiliary and
non-blocking: lifecycle `advance` does not call them, and the graph is not
complete lineage.

## Cross-investigation commands

The cross-investigation layer is derived from every investigation under
`SDR_ROOT`. Its reports are advisory: they never block a lifecycle transition,
change investigation status, or act as gate input. Every relation is derived
from exact stored declarations without similarity, ranking, or model calls.

| Command | Result | Mutation, network, and guard contract |
| --- | --- | --- |
| `sdr cross derive [--json]` | Derives the complete resolver-versioned layer, including source and anchored-claim joins. | Strictly read-only, creates no file or Git commit, performs no network request, and never blocks lifecycle work. |
| `sdr cross source SOURCE_IDENTITY [--json]` | Reports investigation-qualified citations, anchored claims, lineage states, and explicit dependencies for the exact resolved identity. | Strictly read-only, offline, model-free, advisory, and never blocks lifecycle work. Missing results produce empty deterministic collections rather than inferred lineage. |
| `sdr cross degraded [--online [--observed-at ISO-8601]] [--include-expiry --as-of YYYY-MM-DD] [--json]` | Reports mechanical source-health facts and affected completed decisions. | Strictly read-only and advisory. It is offline by default; `--online` is the explicit network opt-in. Online facts retain their explicit observation timestamp. Expiry is excluded by default and requires both `--include-expiry` and an explicit `--as-of` date. |
| `sdr acknowledge-degradation SLUG SOURCE_ID --cause unreachable\|changed\|expired --observation-id ID --reason TEXT --by NAME [--online [--observed-at ISO-8601]] [--include-expiry --as-of YYYY-MM-DD] [--no-commit] [--json]` | Recomputes the canonical report and records one exact current source/cause/observation selection as an additive ledger event. One acknowledgement suppresses every dependency item fanned out from that observation. | Requires a non-empty reason and author. This is the only mutating cross-investigation command. It commits by default and serializes canonical recomputation, ledger write, and focused commit under the shared ledger lock. It preserves pre-existing staging on unrelated paths but refuses a staged target ledger before writing, with guidance to use `--no-commit` or resolve staging; `--no-commit` still writes under the lock without committing. Without a Git repository it writes the acknowledgement and returns a safe warning. Network remains off unless `--online` is passed. It records review but does not approve, invalidate, or block a decision. |

Every command offering `--json` emits deterministic structured output on
success; offline JSON is byte-deterministic. Online JSON preserves provenance:
it includes the observation timestamp and is byte-deterministic only for a
supplied fixed observation time via `--observed-at`. Domain errors use stable
redacted diagnostics and a non-zero exit status; unsafe source declarations are
not echoed. Online observations are used for the current invocation and are not
persisted by read-only commands.

Source identity continues to resolve the declared URL. The final URL provenance
introduced intentionally by `harden-snapshot-provenance` remains visible but
does not replace that identity; this is an authorized provenance correction,
not a change to existing lifecycle verb behavior.

A source-identity join says only that records share an exact work identifier or
the normalized declared-URL fallback. An anchored-claim join says only that
verified anchors normalize to the same sentence; quote propagation says that
sentence appears under different resolved identities. A decision dependency
comes only from persisted `evidence_claim_ids` in an investigation whose status
is exactly `done`. None of these relations establishes publisher identity,
authorship, accuracy, truth, organizational independence, independent
corroboration, or the current validity of a conclusion.

`cross source` joins records by resolved identity. `cross degraded` evaluates
health against each exact investigation-qualified source record; one record's
health is not copied to every record with the same resolved identity. Declared
decision lineage remains historical when a claim later becomes stale, not
anchored, or unverifiable, and the query reports its current evidence health
separately. Omitted lineage is reported as unavailable rather than reconstructed
from prose.

An acknowledgement is scoped to one source record, cause, and observation, not
to the resolved identity, source generally, claim, or decision. It suppresses
only the reviewed current degradation. A later observation or different cause
makes it stale and reportable again; it never erases historical lineage.

The layer cannot yet answer criterion-level reuse questions such as which prior
criterion was established or whether prior evidence applies to a new criterion.
That requires a future explicit criterion relation, not semantic inference.
`INDEX.md` also presents lifecycle status and evidence-health audit information
in one status view today; deferred work should expose persisted lifecycle status
and current evidence health as separate axes.

## Integration commands

`sdr integrations install --destination PATH [--json]` copies all seven
canonical skills from the installed package into an explicit agent discovery
directory. It preflights every target and refuses the whole installation on any
conflict. It does not read `SDR_ROOT` or write agent configuration.

## Retired command

`sdr judge` is a tombstone that exits with guidance. Use `verify-claims` for
deterministic anchoring and `resolve-claim` for scoped human review.

## Git behavior

`new`, `advance`, `reopen`, `drop`, `archive`, and
`acknowledge-degradation` attempt focused commits. They preserve pre-existing
staging state, but operators should still inspect the worktree. Use
`--no-commit` when running in CI, an agent-managed repository, or any workflow
where another tool owns history.
