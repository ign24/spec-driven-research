## Why

The evaluation harness currently demonstrates scripted lifecycle behavior, but it does not yet
separate three materially different questions:

1. Are SDR's blocking lifecycle controls observable when deliberately weakened?
2. What workflow, cost, and friction does one bounded live investigation exhibit?
3. Does the cross-investigation CLI return exact, deterministic structured results for declared
   reuse fixtures, including negative controls?

Conflating those questions would invite claims the evidence cannot support. In particular, exact
cross-investigation retrieval does not establish semantic applicability, recommendation quality,
criterion-level reuse, statistical significance, causality, or the value of consulting the cross
CLI. The change therefore produces separate evidence for each question and preserves per-run facts
instead of presenting one combined effectiveness score.

The existing corpus also predates current snapshot provenance and required decision
`evidence_claim_ids`. It must be migrated and revalidated before any new scripted baseline is
preserved. Its software scenarios remain useful for lifecycle-control observability, while reuse
validation additionally needs at least one non-software scenario and exact negative controls.

## What Changes

- Keep lifecycle-control observability as an offline scripted mutation track. A skipped reporting
  control is scored `not-exercised`, and mutation validation proves that weakening a blocking control
  changes only the expected detection outcomes.
- Add a separate offline scripted/metamorphic reuse track. Each scenario uses one isolated scenario
  root containing immutable completed seed investigations plus one focal investigation. It validates
  exact structured cross-consultation, retrieval, and negative-control results without judging their
  usefulness.
- Treat reuse-history condition as orthogonal to baseline, light, and full arms rather than as a
  fourth arm. Assisted and unassisted prompt policies are separate treatments and are never
  aggregated. The first reuse pilot is assisted; spontaneous discovery under an unassisted policy is
  a later measurement.
- Add one bounded live OpenCode session path for single-investigation workflow, observed cost, and
  friction. Live execution remains disabled unless both the existing environment opt-in and an
  explicit CLI opt-in are present.
- Define a pilot as exactly one planned paid session identified by one scenario/item, arm,
  repetition, host, model, and prompt-template policy. It reports exact session-attributed usage and
  cost before any additional paid session is authorized.
- Stop live lifecycle runs at transfer while real operator approval is pending. The agent never
  invokes or impersonates HITL approval. Approval-not-reached, operator, and synthetic fixture states
  remain distinct, and the initial pilot does not use synthetic approval.
- **BREAKING** (to the unreleased run-record format): replace the incomplete prior record shape with
  one complete schema version 2 after all lifecycle, live, reuse, treatment, approval, and provenance
  fields are specified. Version 1 records are rejected rather than silently coerced.
- Fix subprocess credential inheritance before any mutation or live subprocess work. Scripted,
  mutation, and metamorphic paths remain credential-free; only the explicitly enabled live host
  receives the narrowly required inherited host environment.
- Preserve no repository writes, no transcript persistence, bounded process-tree teardown, exact
  session attribution, and host/model/version provenance.

## Capabilities

### New Capabilities

- `harness-mutation-validation`: offline validation that deliberately weakens blocking lifecycle
  controls and verifies that the harness observes the expected loss.
- `harness-reuse-validation`: isolated scripted/metamorphic and bounded live measurement of exact
  cross consultation and structured retrieval outcomes, with non-software fixtures and negative
  controls.

### Modified Capabilities

- `research-evaluation-harness`: adds honest `not-exercised` scoring, corpus migration controls,
  credential-isolated subprocess environments, a bounded two-key live session, exact pilot identity,
  approval-state handling, and complete run-record schema version 2.

## Impact

- Expected future implementation scope is confined to `bench/`, its tests, and harness
  documentation; shipped lifecycle behavior and cross-investigation semantics do not change.
- Existing software corpus items are migrated to current snapshot provenance and explicit decision
  lineage before replacement baselines are accepted.
- New reuse fixtures include at least one non-software domain, immutable completed seeds, one focal
  investigation per scenario root, and exact positive and negative expectations.
- Live run records and reports remain outside the repository research tree and persist aggregates and
  structured evidence only, never host transcripts.
- Scripted tests and validation require no network, agent host, credentials, or paid session.
- Release hardening remains owned by its separate active change and is not part of this change.

## Explicit Non-Claims

This change MUST NOT present its evidence as measuring semantic applicability, recommendation
quality, criterion-level reuse, statistical significance, causal effect, or the value of the cross
CLI. It also MUST NOT aggregate the three evaluation questions, assisted with unassisted treatments,
or reuse-history conditions into a single result.
