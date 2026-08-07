## Why

SDR claims that its lifecycle and gates make AI-assisted research auditable, but the repository has
no evidence for or against that claim: every existing test validates declared structure, not
outcomes. Without an outcome measurement, maturity work is prioritized by intuition, gates that add
only friction cannot be distinguished from gates that catch real defects, and the cost of the
workflow is unknown.

## What Changes

- Add a synthetic benchmark corpus of research items with known ground truth. Each item declares the
  defects planted in it: unreachable sources, claims not anchored in their cited source, contradictory
  sources, and a probe whose expectation does not match its command. Clean items with no planted
  defect are included to measure false positives.
- Add a deterministic harness that executes each corpus item across three arms (no-SDR baseline,
  SDR light mode, SDR full mode) with repetitions, isolating every run in its own research root
  outside the repository tree.
- Record three deterministic metric families per run: planted-defect detection (caught, missed,
  false positive), cost (wall-clock and token accounting per arm and per stage), and lifecycle
  friction (reopens, gate failures grouped by control type, claims closed through `resolve-claim`).
- Emit a machine-readable run record and a reproducible summary report comparing arms, with stable
  ordering so two runs over the same corpus and the same recorded agent behavior are comparable.
- Keep the harness out of the wheel and source distribution. It is maintenance tooling for the
  framework, not a shipped runtime capability.
- No LLM-based scoring. Every metric is computed from artifacts, exit codes, and structured CLI
  output, consistent with the existing rule that matching does not use models.

## Capabilities

### New Capabilities

- `research-evaluation-harness`: the benchmark corpus contract, the arm execution model, the
  deterministic metric definitions, the run record schema, and the comparison report.

### Modified Capabilities

- `public-repository-boundary`: the framework-only public tree category list must admit the
  evaluation corpus and harness as a documented public category, and must require harness execution
  to materialize research roots outside the repository tree so that audited prohibited names such as
  `research` never appear in it.

## Impact

- New top-level `bench/` tree holding the corpus, the harness, and the report generator; excluded
  from the source distribution alongside `openspec/changes`.
- `src/sdr/public_tree_audit.py` and its spec gain the new public category.
- `src/sdr/artifact_audit.py` sdist expectations confirm `bench/` is absent from built artifacts.
- New tests under `tests/` covering corpus validation, metric computation, and report determinism.
- Consumes existing structured interfaces only (`sdr status --json`, `sdr check --json`,
  `sdr verify-claims --json`, `sdr verify-probe --json`, the verification ledger, and the Git trail).
  No change to the lifecycle, the gates, or the artifact contract.
- Explicitly out of scope: LLM-as-judge scoring, probe sandboxing, artifact-contract translation,
  concurrency locking for parallel agents, an MCP server, and index-based release.
