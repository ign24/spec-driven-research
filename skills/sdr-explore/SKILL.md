---
name: sdr-explore
description: >
  Produce traceable exploration notes for an SDR investigation. Use only in the
  explore stage to compare alternatives, maturity, costs, and risks with sourced
  claims, snapshots, and deterministic claim verification.
---

# Explore alternatives

## Enforce stage order

Read `research/<slug>/sdr.yaml` first and require `stage: explore` with an active
status. If the stage differs, stop, identify the matching stage skill, and do not
create or modify stage artifacts.

## Read prior evidence and write one artifact family

Read `research/<slug>/brief.md`, especially its question, scope, hypothesis, and
criterion IDs. Read the current note scaffold conventions. Modify only files
under `research/<slug>/notes/`; do not change the brief, probe, decision memo,
assets, or `sdr.yaml`.

Across `research/<slug>/notes/`, preserve these Spanish headings:
`Alternativas evaluadas`, `Madurez`, `Costos`, `Riesgos`, and
`Contra-evidencia`. Each note needs dated sources with `url`, `tier`, and an ID.
Each alternative needs at least one T1 source and two distinct declared hosts.
This is a mechanical count, not evidence of organizational independence;
redirect targets do not add declared hosts.

Use `[S1]` only for a factual statement closely anchored in that source's local
snapshot. Use `[cf. S1]` for context, synthesis, or interpretation; it does not
create a claim for textual matching.

## Capture and verify evidence

Run in this order:

```bash
sdr snapshot <slug>
sdr verify-claims <slug> --json
sdr check <slug> --json
```

Correct only the active notes and repeat snapshot, claim verification, and check
as needed until all commands return exit code 0. A human may resolve a current
`not_anchored` or `unverifiable` claim with
`sdr resolve-claim <slug> <claim-id> --reason "<reason>" --by "<person>"`.
That scoped review does not replace transfer approval.

Offer `sdr advance <slug>` after the gate is green. Full mode advances to
`probe`; light mode advances to `transfer`.

## Backtracking and closure

If exploration invalidates the question or criteria, use
`sdr reopen <slug> --to intake --reason "<reason>"` rather than modifying prior
artifacts. Use `sdr drop` when evidence supports stopping. Offer
`sdr archive <slug>` for a `done` or `dropped` investigation.
