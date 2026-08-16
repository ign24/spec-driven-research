# Changelog

All notable user-visible changes are documented here. The format follows Keep a
Changelog, and releases use semantic versioning.

This file records releases; it is not the package version source of truth. The
single source of truth is `src/sdr/__init__.py`.

## [0.3.0] - 2026-08-16

### Changed

- **BREAKING.** Artifact sections are declared in English. `Pregunta`,
  `Hipótesis`, `Contexto`, `Alcance`, `Criterios de evaluación`,
  `Riesgos de adopción`, `Alternativas evaluadas`, `Madurez`, `Costos`,
  `Riesgos`, `Contra-evidencia`, `Resultados por criterio`, `Reproducción`,
  `Recomendación`, `Criterios de selección`, `Riesgos y limitaciones`,
  `Próximos pasos`, and `Audiencia` become `Question`, `Hypothesis`, `Context`,
  `Scope`, `Evaluation criteria`, `Adoption risks`, `Alternatives evaluated`,
  `Maturity`, `Costs`, `Risks`, `Counter-evidence`, `Results by criterion`,
  `Reproduction`, `Recommendation`, `Selection criteria`,
  `Risks and limitations`, `Next steps`, and `Audience`. An investigation
  created before this change does not validate until migrated. Every section
  name now resolves from one declaration, so templates, the required-section
  contract, and the gate engine cannot drift apart.
- The product surface is English: command help, every message printed on
  success, refusal, or failure, and the artifact templates the tool writes.
  Spanish remains a documentation translation and is not a runtime language.
  No command, option, stage, or field name changed.
- The transfer gate accepted only Spanish decision memos. `y_statement`
  matched Spanish tokens inside the user's own recommendation prose, so a memo
  written in English failed a gate the English documentation told the reader
  how to satisfy, with a message that did not say language was the problem. It
  now matches English and tolerates a clause split across a line wrap.
- `sdr context query` no longer maps Spanish question keywords. Questions must
  be asked in English.

### Added

- `sdr migrate` rewrites the structural section headings of an investigation
  created under the previous Spanish contract, reports each heading it changed,
  is idempotent, and leaves the author's own prose byte-identical.
- `sdr --version`, reporting the single declared version source.
- `sdr.product_language`, a deterministic offline check that the product
  surface is English, reporting each finding with its file and line. It scans
  the packaged modules, the artifact templates, the English documentation, and
  the canonical skills; documentation translations are excluded by path.

### Fixed

- `bench/harness/friction.py` classified advance blocked reasons by matching
  Spanish prose that the product no longer emitted, so every mapping was
  unreachable. Its tests passed because they built the reasons as literals
  instead of obtaining them from the lifecycle; they now drive real blocked
  states and assert against what the product actually returns.
- `cross_investigation` matched a consistency issue by its Spanish prefix. The
  prefix is now derived from `lifecycle`, so it cannot silently stop matching.

### Removed

- The `release-hardening-and-public-distribution` change directory. 0.2.0
  recorded it as withdrawn but left its files in `openspec/changes/`, where it
  read as active work and its unstarted sections implied a package-index release
  the project does not intend. Its only completed section specified the shipped
  `sdr integrations install --destination PATH` command and the separation of
  `SDR_ROOT` from framework source; those two requirements now live in the
  `agent-integrations` specification instead of only inside a withdrawn change.

## [0.2.0] - 2026-08-14

### Added

- Outcome-first English and Spanish onboarding, task-oriented documentation
  homes, and a verified offline beginner tour based on the maintained synthetic
  light fixture.
- Public English documentation, security guidance, contribution governance, and
  maintenance validation instructions.
- Public Spanish README and deterministic semantic parity validation for both
  language guides.
- Read-only Linux CI for Python 3.12 and 3.13, strict specification checks,
  dependency and secret scanning, and isolated wheel/sdist release audits.
- Sanitized machine-readable discovery-canary evidence for Claude Code, Codex,
  and OpenCode, with fail-closed validation against public integration claims.
- An installed `sdr integrations install --destination PATH` command for copying
  all seven packaged canonical skills from isolated tool environments.
- A synthetic planted-defect corpus and an offline evaluation harness that scores
  detection, cost, and lifecycle friction across a no-SDR baseline, light mode,
  and full mode, with run records and a deterministic comparison report. It is
  maintenance tooling excluded from the wheel and the source distribution, it
  runs without network or an API key, and it materializes every research root
  outside the repository tree.
- A deterministic, model-free cross-investigation reuse layer with exact source
  and anchored-claim joins, explicit historical decision lineage, advisory
  source-health reporting, scoped degradation acknowledgements, and offline
  operation with explicit online reachability opt-in.
- A two-key, scalar live OpenCode pilot for one bounded investigation, with an
  isolated mediated tool boundary, exact session attribution, and external-only
  research and result roots.
- A canonical public repository coordinate declared under `[project.urls]`, with
  validation that every documented repository URL matches it exactly and that a
  URL reaching the repository only through a rename redirect is reported rather
  than accepted. The check is textual and does not consult the network.
- Executed verification of the documented installation route: SDR is installed
  from a pinned repository revision into an environment outside the checkout,
  with the source tree absent from the import path and publication credentials
  removed, and a complete light lifecycle is driven through the installed `sdr`
  console script. An unavailable verification records an explicit skip reason so
  it stays distinguishable from a verification that never existed. The suite runs
  under the `installation` marker and in a dedicated CI job with network access
  declared.
- A canonical agent routing block shipped as a package resource, stating when a
  host agent invokes SDR and when it must not, published unchanged in the Claude
  Code, Codex, and OpenCode guides and in both README languages. Integration
  validation reports any divergent or one-sided copy. The block is guidance a
  user installs into a host agent, not enforcement, and publishing it does not
  change any adapter status.

### Changed

- Snapshot evidence now records schema-v2 retrieval provenance and verifies the
  exact persisted bytes; pre-existing snapshots require recapture or scoped
  claim resolution.
- Public onboarding now states the alpha, source-only release boundary, provides
  canonical GitHub and immutable-revision installation, and validates bilingual
  navigation, fixture links, Git effects, confidence limits, and documented
  agent status.
- Installation documentation now presents the revision-pinned Git route as the
  supported one and states that no package-index release exists. Pinning a
  revision is required rather than optional, and an unpinned documented install
  command is reported. Previous guidance that presented package-index
  installation as a planned future route has been removed; the
  `release-hardening-and-public-distribution` change is withdrawn, with its
  installation-relevant scope inherited here and its publication scope dropped.
- Current and future distributions are licensed under MIT; historical Apache-2.0 grants are not revoked.
- Narrowed the documented Agent Skills adapter set to Claude Code, Codex, and
  OpenCode; removed Hermes Agent and OpenClaw adapters and support claims.
- The artifact audit now binds canary evidence to the wheel filename and version
  on every run, and requires its recorded digest to match the audited bytes only
  under the new `--release` flag. The digest identifies the build the canaries
  actually ran against, so every rebuild from changed sources changed it and made
  routine audits fail on evidence that was still accurate for its own build.

### Fixed

- The five-minute tour in both READMEs cloned `spec-driven-research` and then
  changed into a `sdr` directory that the clone never created, so the first
  command a new reader copied could not succeed.
- The source distribution no longer packages a local `.codegraph` index
  directory.
- Committed corpus fixtures under `bench/corpus` and `bench/reuse-corpus` are no
  longer reported as harness residue by the public-tree audit; lifecycle metadata
  elsewhere in the evaluation tree still is.

## [0.1.0] - 2026-07-27

### Added

- Five-stage full and light Spec-Driven Research lifecycle.
- Deterministic structural, evidential, textual, executable, consistency, and
  human-approval controls.
- Source snapshots, scoped claim resolution, reproducible probe execution,
  backtracking, archiving, status reporting, and optional Context Graph views.
- Documented adapters for supported Agent Skills hosts.
