## Context

SDR is alpha, source-only, and has a single operator. Its test suite validates declared structure:
schema conformance, gate wiring, README parity, public-tree and artifact audits. Nothing measures
whether the lifecycle changes research outcomes, what it costs, or which controls earn their
friction.

Two properties of the current codebase constrain any measurement design:

- Lifecycle metadata has no concurrency control. Nothing locks `sdr.yaml`, so two processes acting on
  one research root can interleave writes. Isolation, not locking, is the only safe option now.
- The public tree audit treats `research` and `knowledge` as prohibited directory names. Any run that
  materializes a research root inside the repository would fail the project's own release gate.

The harness also has to answer a question the codebase cannot answer by itself: what did the agent do?
SDR observes artifacts and exit codes; it never observes the agent's reasoning or token usage. That
gap drives the central design decision below.

## Goals / Non-Goals

**Goals:**

- Measure planted-defect detection: which deliberately introduced defects the controls catch, which
  slip through, and how often clean items are flagged.
- Measure cost: wall-clock per stage and per run, and token usage when the executing agent reports it.
- Measure lifecycle friction: reopens, gate failures by control type, and claims closed manually.
- Produce byte-identical reports from unchanged inputs, so results are comparable across commits.
- Run the detection measurement offline and in CI without an API key.

**Non-Goals:**

- Scoring output quality. No LLM-as-judge, no rubric, no model-based metric of any kind.
- Statistical significance. Sample sizes here support direction, not inference.
- Benchmarking agents against each other. The unit under test is SDR, not the model.
- Changing the lifecycle, gates, artifact contract, or any shipped behavior.
- Shipping the harness to users. It is maintenance tooling.

## Decisions

### Decision 1: Pluggable actor, scripted by default

The harness defines an actor interface responsible for producing the research work of one run. Two
implementations:

- **Scripted actor (default).** Replays a declared sequence of artifact writes and CLI invocations
  recorded in the corpus item. Deterministic, offline, free, CI-runnable. Measures the controls.
- **Live actor (opt-in).** Drives a real agent session, reports token usage, and records real stage
  durations. Non-deterministic, costs money, requires network. Measures the workflow.

Alternatives considered. *Live actor only*: the honest measurement, but it cannot run in CI, cannot
be reproduced across commits, and confounds gate regressions with model variance, so a gate that
silently stopped firing would be invisible. *Scripted actor only*: fully reproducible but incapable
of producing cost or genuine friction data, which are two of the three metric families.

Consequence to state plainly in the report: **detection is measured under the scripted actor; cost
and friction are measured under the live actor.** Mixing them in one number would be misleading, so
the report separates scripted and live sections and never aggregates across them. Every `ArmRun`
retains its actor identity, including errored and not-applicable outcomes, so section membership does
not depend on successful execution.

### Decision 2: The no-SDR baseline arm is only meaningful under the live actor

Under the scripted actor the baseline arm is degenerate: no gates run, so every planted defect is
missed by construction. That value is recorded as a control constant, not presented as a finding.
The comparison that carries information -- does an agent without SDR ship defects a human would have
caught -- requires the live actor. The report labels the degenerate case explicitly rather than
quietly reporting a 0% baseline detection rate as if it were measured.

### Decision 3: Every run gets a disposable, Git-initialized research root

Each run creates a temporary directory outside the repository, sets `SDR_ROOT` to it, and runs
`git init` inside it. Git initialization is deliberate: the trail writes one commit per lifecycle
transition, which makes reopen counting a `git log` query rather than prose parsing. The repository
under test is never written to and never committed to. Filesystem-dependent artifact and Git reopen
evidence is collected inside `arms._work_for` before the disposable runspace is torn down, then
stored in the durable run record. Teardown remains guaranteed even when collection or execution
fails.

Alternative considered: `--no-commit` on every transition. Rejected because it discards the cleanest
available signal for friction accounting.

### Decision 4: Metrics come from structured outputs only

Friction and detection derive from `sdr check --json`, `sdr verify-claims --json`,
`sdr verify-probe --json`, the verification ledger, and trail commits. No prose parsing of human
messages where a JSON field exists.

Actor-reported stage boundaries, durations, and token observations are also evidence, not exempt
metadata: the harness persists those observations as artifacts and run-record metrics reference the
persisted artifact. This keeps timing and token accounting traceable after the runspace is gone.

Where only prose exists -- `lifecycle.py` composes `blocked_reason` as free text, currently in
Spanish -- the harness maps it to a control type through an explicit table and routes anything
unmatched to an `unmapped` bucket that the report prints. An unmapped bucket that grows is itself a
finding: it means the CLI needs stable machine-readable failure codes. The harness surfaces that
need rather than hiding it behind a regex that silently guesses.

### Decision 5: Corpus defects derive from the failure taxonomy, not from `gates.py`

Planted defects are written from the failure modes SDR claims to address in `docs/evidence-model.md`
and the README comparison table: sources that disappear, citations that do not support their claim,
results asserted without a reproducible check, contradictory evidence. Writing them from the gate
implementations instead would guarantee a perfect score and measure nothing. A defect that no
current control catches is a valid corpus entry and an expected finding.

### Decision 6: `bench/` at top level, excluded from the source distribution

The corpus and harness live in a new top-level `bench/` tree, versioned with the contract they
measure, and excluded from the sdist next to `openspec/changes`. Placement under `tests/` was
rejected: pytest would collect it, and the corpus would ship inside the distribution.

### Decision 7: Determinism through explicit ordering and separated timestamps

Run records carry timestamps; the comparison report does not. The report sorts by item, then arm,
then repetition, and formats numbers at fixed precision. This makes report equality a usable
regression assertion. Byte-identical means rendering repeatedly from unchanged run records; it does
not mean independent executions with variable wall-clock observations produce identical records or
reports.

Run records are self-contained durable inputs to reporting. Report generation reads only those
records and never consults a disposable runspace, live actor state, or repository evidence again.
Relative arm cost is calculated only over matched baseline populations with the same actor, item,
and repetition; unmatched outcomes are reported but do not enter that relative comparison.

### Decision 8: Corpus fixtures match today's contract

Note bodies use the section headings the contract enforces today, including the Spanish headings in
`schema.py`, `gates.py`, and `templates/note.md`. The harness does not anticipate a translated
contract. If those headings are later translated, the corpus is updated by that change, not this one.

## Risks / Trade-offs

- **The scripted actor measures the gates, not the workflow.** A perfect scripted detection score
  says the controls fire on defects, not that SDR helps a real investigation. → The live arm exists
  precisely for that claim, and the report never presents scripted results as evidence about agents.
- **Live-arm variance will be large at feasible sample sizes.** → Report per-run values alongside
  aggregates and make no significance claims; treat live results as direction, not proof.
- **The corpus can drift toward what SDR already catches.** → Decision 5 sources defects from the
  documented failure taxonomy, and the corpus is required to retain entries no control catches.
- **Prose-only failure reasons weaken control attribution.** → Explicit mapping table plus a visible
  `unmapped` bucket; growth in that bucket becomes the argument for stable CLI error codes.
- **Token accounting depends on the agent host reporting usage.** → Declared as an actor-interface
  responsibility; when unavailable the run record marks it unavailable rather than zero, and the
  report shows coverage.
- **New top-level directory expands the public boundary.** → Handled as an explicit spec delta on
  `public-repository-boundary` rather than an undocumented addition, with an audit scenario asserting
  no harness residue in the tree.
- **Harness maintenance competes with framework work.** → Kept small and dependency-free, reusing
  existing structured interfaces and adding no runtime dependency to the package.
