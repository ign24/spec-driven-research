> **Status: paused, 2026-08-02.** Not obsolete and not superseded: source-only distribution is the
> reason a researcher outside software cannot realistically install SDR, so this is the change that
> puts the framework in someone else's hands. It is paused on sequencing. Hardening publication now
> would ship the known domain-specific defaults -- tier derivation, staleness policy, section
> headings, recommendation vocabulary -- that block a non-software researcher on their first note.
> Publish after those are addressed, not before. Its snapshot-provenance work was extracted to
> `harden-snapshot-provenance` because it is the evidence contract rather than release engineering,
> and `add-cross-investigation-reuse` depends on it.

## Why

SDR's source-tree quality gates are strong, but the project does not yet establish a trustworthy public release from an immutable source revision through installation and full lifecycle use. Before broad distribution, release provenance, installed-artifact evidence, supported-environment claims, security reporting, source attribution, integration acquisition, and public documentation must become complete and fail closed.

## What Changes

- Establish an auditable public release path that promotes already validated wheel and source artifacts through least-privilege Trusted Publishing and records their digests, source revision, release identity, and provenance.
- Require clean-environment, installed-console-script end-to-end validation for representative light and full lifecycles, failure paths, optional snapshot support, and every claimed Python environment.
- Align Python, platform, direct dependency, and optional dependency claims with the compatibility and vulnerability evidence enforced by release automation.
- Complete the public repository identity and governance surface, including real project URLs, immutable release guidance, history-aware public audits, and an operational private vulnerability-reporting channel.
- Make canonical skills and the Claude Code, Codex, and OpenCode adapters obtainable from a version-identifiable public source, remove Hermes Agent and OpenClaw from the supported public surface, separate the research root from framework acquisition, and keep integration status proportional to actual E2E evidence.
- Update English and Spanish installation, compatibility, release verification, security, source-provenance, and integration documentation so every public claim maps to current automated or human-reviewed evidence.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `python-distribution`: Require public-index installation, an evidence-backed support matrix, complete public package metadata, and build-once artifact promotion after isolated installed-CLI validation.
- `release-quality-and-security`: Add authorized release identity, artifact digest/provenance, least-privilege publication, complete dependency-extra auditing, installed E2E evidence, and fail-closed release aggregation.
- `public-repository-boundary`: Extend public identity, governance, secret/private-material, and provenance checks to the actual publication surface and Git history.
- `public-documentation`: Require evidence-backed public installation, compatibility, release, security-reporting, source-verification, and integration guidance with bilingual parity.
- `agent-integrations`: Require version-identifiable public acquisition, unambiguous source and research roots, CLI compatibility, and evidence-based integration statuses.

## Impact

The change affects package metadata and dependency floors, release and security workflows, artifact and public-tree audits, installed CLI E2E tests, canonical integration packaging or acquisition, README and maintenance/security documentation, and the corresponding OpenSpec contracts. Public integration validation, tests, artifacts, and documentation will expose exactly Claude Code, Codex, and OpenCode; Hermes Agent and OpenClaw support will be removed. It introduces no intentional CLI or lifecycle-stage break; any temporary upper Python bound would narrow an currently overbroad compatibility claim and must be called out in release notes.
