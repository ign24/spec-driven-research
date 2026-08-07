## 1. External Release Preconditions

- [ ] 1.1 Record the operator-supplied canonical repository, documentation, issue tracker, package-index, immutable source, and durable provenance coordinates without placeholders or credentials.
- [ ] 1.2 Record the operator-supplied protected environment, authorized approvers, private vulnerability-reporting route, and intended first public version.
- [ ] 1.3 Add deterministic failing tests that reject missing, placeholder, private, mutable-only, or inconsistent public release inputs with redacted findings.
- [ ] 1.4 Implement release-input validation and rerun the focused tests followed by the full suite.

## 2. Package Metadata And Compatibility

- [ ] 2.1 Add failing packaging tests for real project URLs, version agreement, bounded Python support, Linux classifiers, license, authorship, and required public metadata.
- [ ] 2.2 Update `pyproject.toml` from the approved public coordinates without adding another version source; run focused packaging tests and the full suite.
- [ ] 2.3 Add failing tests that exercise the declared minimum version of each direct runtime and snapshot dependency independently of the locked current resolution.
- [ ] 2.4 Correct unsupported dependency floors, including the PyYAML floor, regenerate `uv.lock`, and run focused dependency and full tests.
- [ ] 2.5 Add failing policy tests mapping every claimed Python, platform, artifact, and optional profile to an installed-artifact matrix cell.
- [ ] 2.6 Align `requires-python`, classifiers, and compatibility validation with the passing release matrix; run focused and full tests.

## 3. Snapshot Provenance And Hash Consistency

Extracted to the `harden-snapshot-provenance` change. Section intentionally left empty
so later section numbers keep their historical identity.

## 4. Versioned Integration Acquisition

- [x] 4.1 Add failing tests requiring structured adapter directories, exact Hatch descriptor mappings, and public status tables to expose exactly Claude Code, Codex, and OpenCode with matching statuses.
- [x] 4.2 Remove Hermes Agent and OpenClaw adapters and their implementation, test, parity, documentation, and packaging references; run focused integration and public-contract tests.
- [x] 4.3 Add failing packaging tests requiring canonical skills and exactly three adapter descriptors at their declared `sdr/resources/integrations` wheel targets with byte equivalence to top-level sources, plus standalone artifact-audit tests requiring those wheel resources to be byte-equivalent to the matching sdist.
- [x] 4.4 Configure deterministic inclusion of those resources without creating a second authored source; run packaging, artifact-audit, and full tests.
- [x] 4.5 Add failing integration tests for installation from package resources outside a checkout, explicit destination and research roots, all-or-nothing conflict refusal, and absence of unrelated configuration writes.
- [x] 4.6 Expose package-resource installation through `sdr integrations install --destination PATH`, retain the module command for compatibility, and keep `SDR_ROOT` limited to research storage; run isolated `uv tool install`, focused, and full tests.
- [x] 4.7 Add failing status-evidence tests for `documented` and `verified`, keeping all adapters `documented` until complete host E2E evidence exists.
- [x] 4.8 Run no-credential, no-model isolated discovery canaries for Claude Code, Codex, and OpenCode; record sanitized machine-readable evidence without claiming an unexecuted host lifecycle.

## 5. Installed Artifact End-To-End Harness

- [ ] 5.1 Add a failing harness test proving execution uses a fresh environment, workspace outside the checkout, cleared source import paths, synthetic fixtures, no publication credentials, and the installed console script.
- [ ] 5.2 Implement the reusable installed-artifact harness with deterministic diagnostics and machine-readable results; run focused and full tests.
- [ ] 5.3 Add failing installed-console-script tests for a complete light lifecycle, explicit claim verification, approval, reuse, `done`, offline core operation, packaged resources, and JSON output.
- [ ] 5.4 Make wheel and sdist pass the installed light lifecycle on every claimed Python minor and core profile; run focused and full tests.
- [ ] 5.5 Add failing installed-console-script tests for a complete full lifecycle including `verify-probe`, approval, reuse, and `done`.
- [ ] 5.6 Make wheel and sdist pass the installed full lifecycle on every claimed Python minor and core profile; run focused and full tests.
- [ ] 5.7 Add installed failure-path cases for premature advancement, missing or stale claims, missing or stale probe evidence, missing approval, changed stage hashes, and integration conflicts.
- [ ] 5.8 Add installed snapshot-profile coverage using controlled synthetic retrieval without depending on unpublished external endpoints.
- [ ] 5.9 Define and enforce the exhaustive release matrix across claimed Python minors, wheel/sdist, core/snapshot profiles, direct minima, locked resolution, and every claimed Linux environment with actual execution identity recorded.

## 6. Public Boundary And Dependency Audits

- [ ] 6.1 Add failing public-boundary tests for candidate tree, reachable publication history, incomplete ref scope, stable finding order, and redaction.
- [ ] 6.2 Extend the public-boundary audit to inspect the complete intended publication surface and fail closed on unknown scope; run focused and full tests.
- [ ] 6.3 Add failing artifact-audit tests for complete metadata, all runtime/integration resources, prohibited content, and digest-bound artifact identity.
- [ ] 6.4 Extend artifact auditing while preserving safe archive handling and redacted output; run artifact, packaging, and full tests.
- [ ] 6.5 Add failing security-policy tests requiring separate vulnerability results for core and every supported extra and rejecting invalid or expired exceptions.
- [ ] 6.6 Implement deterministic dependency-profile exports and audits for release evidence; run focused and full tests.

## 7. Release Manifest And Readiness

- [ ] 7.1 Add failing tests for a release manifest containing version, tag, immutable source commit, workflow/run identity, artifact names, sizes, and SHA-256 digests.
- [ ] 7.2 Implement deterministic manifest generation and verification that rejects missing, additional, renamed, modified, or source-mismatched artifacts; run focused and full tests.
- [ ] 7.3 Add failing tests requiring agreement among tag, `src/sdr/__init__.py`, changelog, wheel, sdist, intended index version, and source revision.
- [ ] 7.4 Implement fail-closed release-identity validation without introducing another version source; run focused and full tests.
- [ ] 7.5 Add failing tests for aggregation of quality, documentation, integration, dependency, public-boundary, artifact, digest, provenance, identity, installed-E2E, environment, and approval evidence.
- [ ] 7.6 Implement deterministic release-readiness reporting that blocks on every missing, skipped, stale, or failing mandatory result; run focused and full tests.

## 8. CI, Security, And Publication Workflows

- [ ] 8.1 Add failing workflow-policy tests for recorded Linux execution evidence, claimed Python coverage, lower-bound checks, independent optional audits, installed E2E, full-SHA actions, read-only untrusted workflows, and fail-closed dependencies.
- [ ] 8.2 Update PR/main CI and security workflows to enforce the required focused matrix and audits; run automation tests, workflow syntax validation, and the full suite.
- [ ] 8.3 Add failing workflow tests for a tag-triggered build-once candidate flow that validates identity, creates the manifest, and hands the same digest-bound artifacts to every gate.
- [ ] 8.4 Add a publication-disabled release-candidate workflow with read-only defaults, explicit evidence retention, no token, and no upload step; run automation tests and workflow syntax validation.
- [ ] 8.5 Add failing tests for a protected public-index publication job with complete readiness dependencies, manifest verification, `contents: read`, job-scoped `id-token: write`, no stored token, and a full-SHA PyPA action.
- [ ] 8.6 After external preconditions are supplied, add the least-privilege Trusted Publishing path for the declared public index; an optional TestPyPI rehearsal MUST NOT be mandatory.

## 9. Governance And Public Documentation

- [ ] 9.1 Add failing public-contract tests for consistent repository, package, command, license, documentation, issues, changelog, security, release, package-index, and immutable source identities.
- [ ] 9.2 Replace provisional security-reporting guidance with the approved operational private route and supported response process; run public-contract and full tests.
- [ ] 9.3 Document immutable release/source policy, supported-version policy, digest/provenance verification, incident response, yanking, and the prohibition on replacing published versions.
- [ ] 9.4 Add failing bilingual parity checks for public installation, compatibility, release verification, security route, snapshot provenance, and only the Claude Code, Codex, and OpenCode integration contract.
- [ ] 9.5 Update `README.md`, `README.es.md`, affected `docs/`, retained adapter guides, and `CHANGELOG.md`; remove Hermes Agent and OpenClaw support claims and run all documentation checks.

## 10. Release Rehearsal And Final Validation

- [ ] 10.1 Run the publication-disabled workflow from an immutable rehearsal tag and verify build-once behavior, manifest, exhaustive matrix, security results, approval blocking, and durable evidence handoff.
- [ ] 10.2 Have the operator confirm that the declared public-index Trusted Publisher identity, protected environment, approvers, reporting route, and provenance storage exactly match the reviewed workflow.
- [ ] 10.3 With explicit operator approval, publish only digest-matched candidate artifacts to the declared public index through OIDC; TestPyPI MAY be used as an optional rehearsal.
- [ ] 10.4 Install the exact public version in every supported environment, compare downloaded artifacts with the manifest, and execute the installed acceptance suite.
- [ ] 10.5 Exercise and document digest-mismatch, missing-approval, immutable-version, and yank-plus-new-version incident paths without publishing to production PyPI.
- [ ] 10.6 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .` on the supported matrix.
- [ ] 10.7 Run strict OpenSpec, README parity, skill, integration, workflow, package, public-boundary, dependency, secret, history, manifest, and installed-artifact validations with no skipped mandatory result.
- [ ] 10.8 Produce a final release-readiness report identifying source revision, artifact digests, validation runs, supported matrix, external attestations, public-index result, and any remaining production blocker; an omitted TestPyPI rehearsal is not a blocker.
