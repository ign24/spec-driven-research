# SDR evaluation harness

Maintenance tooling that measures which planted defects the SDR controls report, what the
lifecycle costs, and how much friction it produces. It is not a shipped runtime capability:
`bench/` is excluded from the wheel and the source distribution, and nothing in `src/sdr/`
imports it.

Every metric is computed from artifacts, exit codes, and structured CLI output. There is no
LLM-based scoring of any kind, consistent with the framework rule that matching does not use
models.

```
bench/
  corpus/
    corpus.yaml          corpus-level metadata; 'version' is required
    items/<item-id>.yaml one corpus item per file, stem equal to its id
  harness/
    corpus.py    typed loading, closed defect vocabulary, synthetic-source rule
    runspace.py  disposable Git-initialized research roots outside the repository
    actor.py     the actor protocol, the scripted actor, the live actor boundary
    arms.py      arm applicability, repetitions, isolated execution
    detection.py planted-defect scoring from structured command payloads
    cost.py      wall-clock per run and per stage, token accounting
    friction.py  reopens, gate failures by control type, claim closures
    record.py    the durable run-record schema and its serialization
    report.py    the deterministic comparison report
    runner.py    the command-line entry point
```

## Corpus contract

### Item schema

One YAML file per item under `corpus/items/`, whose stem must equal the item `id`. Items load
in sorted filename order, so loading is stable regardless of the filesystem.

Required keys:

| Key | Meaning |
| --- | --- |
| `id` | Matches `[a-z0-9][a-z0-9-]*`, unique across the corpus, equal to the file stem. |
| `mode` | `light` or `full`. The lifecycle mode the item is executed under. |
| `title` | Short human label. |
| `question` | The invented research question. |
| `planted_defects` | List of names from the closed vocabulary below. May be empty. |

Optional keys:

| Key | Meaning |
| --- | --- |
| `expected_detection` | Mapping from a planted defect to `caught` or `uncaught`. Unlisted planted defects default to `caught`. Naming a defect that is not planted is an error. |
| `sources` | List of `id`, `url`, `title`, `tier`, `date`, and an optional `snapshot` body. Source ids must be unique within the item. |
| `artifacts` | Mapping of research-root relative path to file body, written by the scripted actor before the commands run. Absolute paths and `..` are rejected. |
| `commands` | List of argv lists replayed in order by the scripted actor. |
| `probe` | Probe declaration. Only valid for `full` items, because `light` skips probe. |
| `notes` | Authoring notes. Not interpreted by the harness. |

At least one item must declare an empty `planted_defects` list. Clean items are what make false
positives measurable.

Note bodies use the section headings the artifact contract enforces today, including the Spanish
headings in `src/sdr/schema.py`, `src/sdr/gates.py`, and the packaged templates. The corpus
tracks the contract as it is, not as it might be translated later.

### Closed planted-defect vocabulary

`planted_defects` accepts only these names; anything else fails loading. Each is derived from the
failure taxonomy in `docs/evidence-model.md` and the README failure statement, deliberately not
from `src/sdr/gates.py`: writing planted defects from the gate implementations would guarantee a
perfect score and measure nothing.

| Defect | Taxonomy origin |
| --- | --- |
| `unreachable-source` | Sources that disappear or become inaccessible. |
| `unanchored-claim` | A claim its cited source does not support. |
| `unreproducible-result` | A result asserted without a reproducible check. |
| `contradictory-sources` | Evidence that contradicts itself across alternatives. |
| `probe-expectation-mismatch` | A probe whose expectation does not match its command. |
| `uncovered-criterion` | A criterion left without a mapped result. |
| `stale-validation` | Content changed after the stage or probe was validated. |
| `unapproved-decision` | A decision presented as approved without a recorded approval. |
| `inaccurate-source` | A passing gate does not make a source accurate. |
| `unrepresentative-benchmark` | A passing gate does not make a benchmark representative. |

The last two are confidence boundaries: no current control is expected to catch them. Keeping
them in the corpus is the point. A corpus that only contains defects SDR already catches measures
nothing about what it misses.

### Synthetic-source rule

Every URL in an item must use a reserved or guaranteed non-resolvable domain: `example.com`,
`example.net`, `example.org` and their subdomains (RFC 2606), or the `.invalid`, `.test`,
`.example` and `.localhost` top-level domains (RFC 6761). The rule applies to declared source
URLs and to every URL embedded in a source snapshot or an artifact body. Loading fails otherwise.

That keeps the corpus fully invented and offline: an unreachable source is unreachable by
construction, not because some external site happened to fail during a run. No corpus item
depends on a real organization, a real person, or a live endpoint.

## The two actors

An actor produces the research work of one run. The harness ships two implementations, and the
report never aggregates their results into one number.

**Scripted actor (default).** Replays the artifact writes and CLI invocations declared in the
corpus item, invoking the real CLI from `src/` inside the run's isolated root. It injects
`--offline` into every offline-capable command. Deterministic, free, no network, no API key.

**Live actor (opt-in, off by default).** Drives a real agent session and reports its token usage
and real stage durations. Constructing it performs no I/O and reads no API key; it refuses to
execute unless `SDR_BENCH_LIVE_ACTOR` is set *and* a `LiveSession` is supplied. The harness does
not implement a session, so the live actor has no command-line surface: using it means calling
`bench.harness.arms.execute_arms` from Python with an explicit `LiveActor`.

**Detection is measured under the scripted actor; cost and friction are meaningful only under the
live actor.** The scripted actor measures the controls: it says whether a gate fires on a defect,
which is exactly what has to keep working across commits, and it is the only way to catch a gate
that silently stopped firing. It says nothing about whether SDR helps a real investigation, and
its timings are process-startup cost, not research cost. Its token accounting is recorded as
unavailable, never as zero. The live actor measures the workflow, at the price of variance,
money, and non-reproducibility.

Under the scripted actor the baseline arm is degenerate: no gate runs, so every planted defect is
missed by construction. That is recorded as a control constant and printed as `control-constant`,
never as a measured 0% detection rate.

## Running it

Every run materializes its research root in a disposable, Git-initialized temporary directory
outside the repository, sets `SDR_ROOT` to it, and removes it afterwards. The entry point refuses
any `--records` or `--report` path that resolves inside the repository tree, so a harness
invocation cannot leave run output, a research root, or lifecycle metadata in the working tree.

All three arms over the whole corpus, report to stdout:

```
uv run python -m bench.harness
```

One arm at a time. `--arm` is repeatable; an item is applicable to the arm matching its declared
mode and to the mode-free baseline, so a `light` item produces a `not-applicable` record in the
`full` arm rather than a failure:

```
uv run python -m bench.harness --arm baseline
uv run python -m bench.harness --arm light
uv run python -m bench.harness --arm full
uv run python -m bench.harness --arm light --arm full
```

Repetitions, and durable output outside the repository:

```
uv run python -m bench.harness --repetitions 3 \
  --records /tmp/sdr-bench/records.json \
  --report /tmp/sdr-bench/report.md
```

Run records to stdout with no report, for piping:

```
uv run python -m bench.harness --records - --no-report
```

Re-render a stored record set without executing anything. This is the only determinism the
harness claims, and it is byte-identical:

```
uv run python -m bench.harness --from-records /tmp/sdr-bench/records.json
```

Remaining flags: `--corpus` points at another corpus root, `--max-workers` bounds parallelism over
disjoint roots, and `--durations omitted` drops the wall-clock columns. `--help` lists all of them.

Refused invocations -- a missing corpus, a repetition count below one, an output path inside the
repository -- print one line on stderr and exit 2.

The harness has its own tests, and the repository's public-tree audit must stay clean after a run:

```
uv run pytest tests/test_bench_corpus.py tests/test_bench_corpus_items.py \
  tests/test_bench_runspace.py tests/test_bench_actor.py tests/test_bench_arms.py \
  tests/test_bench_detection.py tests/test_bench_cost.py tests/test_bench_friction.py \
  tests/test_bench_record.py tests/test_bench_report.py tests/test_bench_runner.py
uv run python -m sdr.public_tree_audit .
```

## Reading the report

The report has one section per actor present in the record set, and never aggregates across them.
Rows are ordered by item, then arm, then repetition; numbers are printed at fixed precision; no
timestamp appears in the body. Run records carry timestamps, the report does not.

The header states `corpus_version`, `repetitions`, `run_records`, and which actors are present.

**Detection.** `planted_defects` counts declared defects over measured runs, and `caught` /
`missed` split them by what a structured finding actually reported. `detection_rate` is
`caught / planted_defects` and is printed only when a rate may honestly be claimed; otherwise the
cell reads `control-constant` (true by construction), `not-measured` (no measured run), or
`no-planted-defects`. `false_positives` counts blocking findings on clean items. `basis` says
whether the arm's numbers were measured, are a control constant, or mix both.

**Cost.** `costed_runs` and `uncosted_runs` separate runs that produced a wall-clock observation
from those that did not. `tokens_total` reads `unavailable` when no run reported usage -- that is
not zero tokens -- and `token_coverage` shows how many costed runs reported any. Relative cost is
computed only over `matched_runs`: identities costed in both the arm and the baseline, under the
same actor. Unmatched runs are printed but never enter the ratio. The stage line breaks wall-clock
down by lifecycle stage in stage order.

**Friction.** Reopens counted from trail commits, gate failures attributed to the documented
control vocabulary (`structural`, `evidential`, `textual-anchoring`, `executable`,
`hash-consistency`, `human-approval`), claims closed through `resolve-claim` counted separately
from claims that passed anchoring.

**Unmapped gate failures.** `lifecycle.py` composes its blocked reason as free Spanish prose. The
harness maps that prose to a control through an explicit table and routes anything unmatched, or
anything matching more than one control, to `unmapped` rather than guessing. Every unmapped entry
is printed in full with its item, arm, repetition, and exit code. **A growing unmapped bucket is
itself a finding**: it means the CLI needs stable machine-readable failure codes.

**Runs.** One row per run record, including `not-applicable` and `errored` outcomes, so an absent
number is always distinguishable from a zero. Absence is labelled `not-run`, `unavailable`, or
`not-costed`, never rendered as `0`.

## First scripted baseline

Observed on 2026-08-01 from `uv run python -m bench.harness`, corpus version `1`, 8 items, 3 arms,
1 repetition, 24 run records, scripted actor only. Detection and friction counts reproduce across
executions; wall-clock does not, and the figures below come from one run.

| Arm | Executed | Measured | Planted | Caught | Missed | Detection rate | False positives |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | 8 | 0 | 0 | 0 | 0 | control-constant | 0 |
| light | 5 | 5 | 4 | 1 | 3 | 0.250 | 0 |
| full | 3 | 3 | 2 | 1 | 1 | 0.500 | 0 |

Per planted defect:

| Item | Arm | Defect | Observed | Reporting control |
| --- | --- | --- | --- | --- |
| `unanchored-claim-parser-metrics` | light | `unanchored-claim` | caught | `verify-claims` / textual-anchoring |
| `probe-expectation-mismatch-hash-guard` | full | `probe-expectation-mismatch` | caught | `verify-probe` / executable |
| `unreachable-source-cache-warmer` | light | `unreachable-source` | missed | none; `links_resolve` skipped offline |
| `contradictory-sources-retry-window` | light | `contradictory-sources` | missed | none |
| `uncaught-inaccurate-source-throughput` | light | `inaccurate-source` | missed | none |
| `uncaught-unrepresentative-benchmark-queue` | full | `unrepresentative-benchmark` | missed | none |

Neither clean item produced a false positive: `clean-light-label-router` and
`clean-full-checksum-shim` both reached `reuse` with zero blocking findings.

Friction, scripted: no reopens in any arm. The light arm attributed 3 gate failures to
textual-anchoring and 10 claims passed anchoring; the full arm attributed 3 to executable and 6
claims passed. No claim was closed through `resolve-claim`. Two blocked reasons landed in the
`unmapped` bucket, both the same `approve` stage-guard message
(`approve solo aplica en etapa transfer; ...`), which is a stage guard rather than a control
failure and has no control to attribute.

Cost, scripted: baseline 0.014 s over 8 runs, light 14.207 s over 5 runs (mean 2.841 s), full
10.242 s over 3 runs (mean 3.414 s). These are CLI process-startup and gate-execution costs of a
replay, not the cost of doing research; the arm-versus-baseline ratios (about 1500x and 2000x) are
ratios against a baseline arm that runs no command at all, and mean nothing about workflow cost.
Token coverage is 0/8, 0/5 and 0/3: the scripted actor reports no token usage, and this is
recorded as unavailable, never as zero.

### Defects no current control caught

Input to the next roadmap phase, in decreasing order of how much the gap contradicts what the
framework claims:

1. **`contradictory-sources`** (`contradictory-sources-retry-window`, light). The evidence model
   presents evidential validation -- source declarations, tiers, triangulation -- as the control
   over evidence that contradicts itself across alternatives, but no structured check reports the
   contradiction. `bench/harness/detection.py` has no control mapped for this defect because no
   control emits one. This is the one uncaught defect the documentation implies should be caught.
2. **`unreachable-source`** (`unreachable-source-cache-warmer`, light). Not a control gap: the
   `links_resolve` check exists and is the right control, but offline it is skipped, never passed
   (`omitido (--offline)` in the run record's `skipped_checks`). The scorer counts it as missed,
   which understates the controls. Two follow-ups: score an offline-skipped control as
   *not exercised* rather than missed, and give the corpus a way to exercise link resolution
   without network -- otherwise every offline execution keeps reporting a caught defect as missed.
3. **`inaccurate-source`** (`uncaught-inaccurate-source-throughput`, light) and
   **`unrepresentative-benchmark`** (`uncaught-unrepresentative-benchmark-queue`, full). Expected
   uncaught, and observed uncaught. These are the documented confidence boundaries: a passing gate
   does not mean a source is accurate or a benchmark representative. They are not defects to fix;
   they are the measured evidence that the boundaries in `docs/evidence-model.md` are real, and
   they should stay in the corpus permanently.

Two coverage gaps in the measurement itself, not in the framework:

- Four vocabulary entries are not planted in any item yet: `unreproducible-result`,
  `uncovered-criterion`, `stale-validation`, and `unapproved-decision`. The first three have a
  mapped control in `detection.py` and are therefore cheap corpus additions that would test
  controls nothing currently exercises. `unapproved-decision` has no mapped control at all, so
  planting it requires deciding which structured field a missing HITL approval surfaces in.
- Cost and friction remain unmeasured in the sense that matters: there is no live-actor run, so
  every number above describes the controls, not the workflow. The scripted baseline arm is a
  control constant, so the comparison the proposal actually cares about -- does an agent without
  SDR ship defects a reviewer would have caught -- has not been run.
