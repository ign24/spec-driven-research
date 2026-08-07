# Workflow

SDR moves one investigation through five evidence stages. The CLI owns lifecycle
metadata; people own the question, evidence quality, interpretation, and approval.

## Start

Create a full investigation when you expect an executable test:

```bash
sdr new example-study --title "Evaluate an approach" \
  --question "Does the approach meet C1 and C2?" --mode full
```

Use `--mode light` only when sourced exploration can support an `assess` or
`hold` recommendation without a probe. Full mode is `intake -> explore -> probe
-> transfer -> reuse -> done`. Light mode is `intake -> explore -> transfer ->
reuse -> done`. `reuse` is required in both.

## Intake

Complete `brief.md` with a falsifiable question, hypothesis, context, in-scope
and out-of-scope boundaries, at least two measurable criteria (`C1`, `C2`, ...),
and adoption risks.

```bash
sdr check example-study --json
sdr advance example-study
```

## Explore

Create one or more notes in `notes/`. Declare dated sources with stable IDs,
tier classifications, and any alternative they support. Use at least one T1
source per alternative and distinct declared hosts where the gate requires
triangulation. This is a mechanical diversity count: hostnames and redirects do
not establish organizational independence.

End factual claims with `[S1]`. Use `[cf. S1]` for context or synthesis that
does not create a claim and does not enter textual matching.

```bash
sdr snapshot example-study
sdr verify-claims example-study --json
sdr check example-study --json
sdr advance example-study
```

Snapshots are local evidence copies, not proof of truth. If an otherwise valid
claim cannot be anchored deterministically, a person may record a scoped review:

```bash
sdr resolve-claim example-study CLAIM_ID --reason "Reviewed against the cited table" --by REVIEWER
```

That resolution does not replace or substitute approval of the decision memo.

## Probe

Put reproducible code and inputs under `probe/`. In `probe/results.md`, report
each brief criterion as meets, does not meet, or partial, and point to concrete
evidence. Declare an explicit runner:

```yaml
verify:
  action: run
  argv: ["python", "verify.py"]
  expect: "PASS"
  environment: clean
```

`sdr verify-probe` runs the argument vector without a shell, from `probe/`, and
stores the result with a hash. A changed probe invalidates the stored result.

```bash
sdr verify-probe example-study --json
sdr check example-study --json
sdr advance example-study
```

## Transfer

Write `decision-memo.md` for a named audience. State the recommendation,
technology-adoption ring, alternatives, selection criteria, risks, limitations,
and next steps. Full-mode `adopt` or `trial` recommendations require current
green probe evidence.

```bash
sdr check example-study --json
sdr approve example-study --by REVIEWER
sdr advance example-study
```

Approval is a human decision over the current memo. Change the memo after
approval only through an explicit revalidation workflow.

## Reuse and close

Add at least one asset under `assets/` with a supported `type` and `audience`.

```bash
sdr check example-study --json
sdr advance example-study
sdr archive example-study
```

Archive is a separate consolidation step after `done` or `dropped`.

## Backtrack and stop

Use explicit backtracking when new evidence invalidates an earlier stage:

```bash
sdr reopen example-study --to explore --reason "The probe exposed a missing alternative"
```

Reopen moves backward, records the reason, and invalidates affected hashes. Use
`sdr drop` with a reason when continuing has no value. Both commands preserve
the investigation and commit by default; add `--no-commit` if another system
owns Git.

## Offline operation

`--offline` skips URL checks and automatic snapshot capture. The output reports
those checks as skipped, never passed. It does not waive local snapshot or claim
anchoring requirements.
