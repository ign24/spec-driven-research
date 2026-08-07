# Changelog

All notable user-visible changes are documented here. The format follows Keep a
Changelog, and releases use semantic versioning.

This file records releases; it is not the package version source of truth. The
single source of truth is `src/sdr/__init__.py`.

## [Unreleased]

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

### Changed

- Snapshot evidence now records schema-v2 retrieval provenance and verifies the
  exact persisted bytes; pre-existing snapshots require recapture or scoped
  claim resolution.
- Public onboarding now states the alpha, source-only release boundary, provides
  canonical GitHub and immutable-revision installation, and validates bilingual
  navigation, fixture links, Git effects, confidence limits, and documented
  agent status.
- Current and future distributions are licensed under MIT; historical Apache-2.0 grants are not revoked.
- Narrowed the documented Agent Skills adapter set to Claude Code, Codex, and
  OpenCode; removed Hermes Agent and OpenClaw adapters and support claims.

## [0.1.0] - 2026-07-27

### Added

- Five-stage full and light Spec-Driven Research lifecycle.
- Deterministic structural, evidential, textual, executable, consistency, and
  human-approval controls.
- Source snapshots, scoped claim resolution, reproducible probe execution,
  backtracking, archiving, status reporting, and optional Context Graph views.
- Documented adapters for supported Agent Skills hosts.
