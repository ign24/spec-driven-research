---
name: sdr-probe
description: >
  Build and verify the empirical probe for a full-mode SDR investigation. Use
  only in the probe stage to test every brief criterion with reproducible code,
  commands, measurements, and explicit results.
---

# Run the probe

## Enforce stage order

Read `research/<slug>/sdr.yaml` first and require `stage: probe`, `mode: full`,
and an active status. Light mode has no probe. If any precondition fails, stop,
route to the matching stage skill, and do not create or modify stage artifacts.

## Read prior evidence and write one artifact family

Read `research/<slug>/brief.md` for every criterion ID and
`research/<slug>/notes/` for the alternatives and risks that shaped the test.
Modify only files under `research/<slug>/probe/`; this includes test code, data,
outputs, and `research/<slug>/probe/results.md`. Do not modify earlier or later
stage artifacts or `sdr.yaml`.

In `results.md`, preserve the Spanish headings `Results by criterion` and
`Reproduction`. Report every criterion as `cumple`, `no cumple`, or `parcial`
with evidence. Referenced probe artifacts must exist, and benchmark tables need
an adjacent reproducible command. Declare current `verify.command` and
`verify.expect` metadata.

## Reach an executable green gate

Run the executable verification explicitly before the stage check:

```bash
sdr verify-probe <slug> --json
sdr check <slug> --json
```

Correct only probe files and repeat both commands until each returns exit code 0
and the stored probe hash is current. Then offer:

```bash
sdr advance <slug>
```

Advancing moves the investigation to `transfer`.

## Backtracking and closure

If results invalidate the brief or exploration assumptions, use
`sdr reopen <slug> --to <intake|explore> --reason "<reason>"`; do not edit a
validated prior artifact directly. Honest negative results may instead support
`sdr drop`. Offer `sdr archive <slug>` after `done` or `dropped`.
