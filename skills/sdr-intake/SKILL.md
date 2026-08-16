---
name: sdr-intake
description: >
  Complete the intake brief for an existing SDR investigation. Use only while
  the investigation is in the intake stage to define a falsifiable question,
  scope, measurable criteria, and known adoption risks.
---

# Complete intake

## Enforce stage order

Read `research/<slug>/sdr.yaml` first and require `stage: intake` with an active
status. If the file is absent, use `sdr-new`. If the stage differs, stop without
writing and route to the matching stage skill. Do not create or modify stage
artifacts when this precondition fails.

## Read inputs and write one artifact

Read the investigation metadata and the existing brief scaffold. Modify only
`research/<slug>/brief.md`; do not edit `sdr.yaml`, notes, probe files, the
decision memo, or assets.

Preserve the current template structure and its Spanish artifact headings:

- `Question`: a falsifiable research question.
- `Hypothesis`: the tentative answer to test.
- `Context`: why the investigation matters.
- `Scope`: explicit in-scope and out-of-scope boundaries.
- `Evaluation criteria`: at least two stable IDs (`C1`, `C2`, ...), each with
  a measurable threshold, unit, and verification method.
- `Adoption risks`: cost, maturity, lock-in, compliance, and team risks as
  applicable.

## Reach a green gate

Run:

```bash
sdr check <slug> --json
```

Use the JSON failures to correct only `brief.md`, then rerun the command until it
returns exit code 0. Do not report intake complete while the gate is red.

Offer, but do not run without the user's intent:

```bash
sdr advance <slug>
```

Advancing moves the investigation to `explore`.

## Backtracking and closure

If later evidence invalidates this brief, return through
`sdr reopen <slug> --to intake --reason "<reason>"`; never rewrite a validated
brief in place. If work is dropped, record it with `sdr drop` and offer
`sdr archive <slug>` once the investigation is `done` or `dropped`.
