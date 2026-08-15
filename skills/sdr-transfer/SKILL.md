---
name: sdr-transfer
description: >
  Turn validated SDR evidence into an actionable decision memo. Use only in the
  transfer stage to select an evidence-backed recommendation ring and obtain
  explicit human approval before advancing.
---

# Transfer the finding

## Enforce stage order

Read `research/<slug>/sdr.yaml` first and require `stage: transfer` with an
active status. If the stage differs, stop, route to the matching stage skill,
and do not create or modify stage artifacts.

## Read all prior evidence and write one artifact

Read `research/<slug>/brief.md`, all files under `research/<slug>/notes/`, and
`research/<slug>/probe/results.md` in full mode. Read mode and validation state
from `sdr.yaml` without editing it. Modify only
`research/<slug>/decision-memo.md`; do not change prior evidence or assets.

Preserve the Spanish headings `Recommendation`, `Alternatives evaluated`,
`Selection criteria`, `Risks and limitations`, `Next steps`, and
`Audience`. The recommendation must be a complete Y-statement tied to criterion
evidence. Choose `adopt`, `trial`, `assess`, or `hold`; `adopt` and `trial`
require a verified full-mode probe, while light mode is capped at `assess`.

## Reach a green gate and request approval

Run `sdr check <slug> --json`, correct only the decision memo, and repeat until
the command returns exit code 0. Then ask the user for explicit approval. The
agent must not approve on the human's behalf.

After confirmation, run:

```bash
sdr approve <slug> --by "<person>"
```

Then offer `sdr advance <slug>`. Approval and deterministic gates are both
required to enter mandatory `reuse`.

## Backtracking and closure

If synthesis exposes invalid prior assumptions, use
`sdr reopen <slug> --to <intake|explore|probe> --reason "<reason>"` and return to
that workflow. Do not silently weaken or rewrite validated evidence. If dropped,
record the reason and offer `sdr archive <slug>`; also offer archive after `done`.
