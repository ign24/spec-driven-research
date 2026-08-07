## Context

SDR has strong workflow, evidence, security, CLI, integration, and validation references. The root READMEs currently combine onboarding and complete reference material, so readers meet stages, controls, hashes, side effects, and probe safety before seeing a finished research outcome. The repository also has no GitHub or PyPI release, so approachable documentation must remain explicit about source-only alpha availability.

## Goals / Non-Goals

**Goals:**

- Give SDR a recognizable documentation identity based on the path from question to reusable decision asset.
- Let a beginner reach a complete, synthetic light-mode outcome before reading detailed references.
- Provide task-oriented English and Spanish documentation navigation.
- Preserve exact lifecycle, Git, evidence, integration, compatibility, and release boundaries through deterministic validation.
- Use only verifiable badges and GitHub metadata.

**Non-Goals:**

- Copying OpenSpec prose, branding, finished imagery, community claims, or command model.
- Translating every detailed reference document.
- Adding a new example, changing CLI behavior, or publishing a release/package.
- Claiming PyPI availability, generic Linux support, or verified agent-host E2E.

## Decisions

### Lead with SDR's evidence chain

Both READMEs will open with the concise product outcome and the sequence `question -> evidence -> optional probe -> human-approved decision -> reusable asset`. Detailed command/control tables move below the tour or into existing references. This preserves rigor while using progressive disclosure.

### Use a shared banner slot

Both root READMEs reference the same local `assets/sdr-banner.png` before the title so visual identity remains consistent across languages.

### Use only evidence-backed badges and availability language

The hero may show current CI, Security, Python 3.12/3.13 CI, MIT, and Alpha badges. It will not show a package version, downloads, release, or PyPI badge. Installation will use the canonical GitHub source and optionally an immutable commit revision.

### Reuse the maintained light fixture

The five-minute tour and beginner guides use `examples/light-complete` through its runner. They do not duplicate fixture content, use network access, execute probes, or imply that later-stage artifacts are generated automatically. Full mode remains linked for readers who need executable evidence.

### Add paired navigation and beginner layers

Add `docs/README.md`, `docs/README.es.md`, `docs/getting-started.md`, and `docs/getting-started.es.md`. Detailed references remain canonical English documents and are linked from both homes. This limits translation drift while providing equivalent entry points.

### Extend concept-based validation

The existing parity validator will validate three pairs: root READMEs, docs homes, and beginner guides. Pair-specific contracts cover lifecycle order, source acquisition, release status, Git side effects, evidence limits, agent statuses, and fixture links. Local links must resolve. Literal translation remains unnecessary.

### Keep GitHub presentation honest

Set a concise repository description and evidence/research-oriented topics. Leave homepage empty until a maintained documentation site exists. README badges link only to live repository workflows and files.

## Risks / Trade-offs

- [Shorter README hides important constraints] -> Keep confidence boundaries and direct links before advanced references.
- [Bilingual pages drift] -> Validate concepts, commands, lifecycle arrows, statuses, and links deterministically.
- [Source install appears like a release] -> Label the project Alpha/source-only and state that GitHub/PyPI releases do not exist.
- [Worked example diverges from runtime] -> Reuse the test-covered fixture and validate every linked path.
- [Badges overstate readiness] -> Label Python as CI evidence and Alpha explicitly; omit release/package badges.

## Migration Plan

1. Add failing documentation-set, parity, link, acquisition, and fixture tests.
2. Extend the existing deterministic parity validator.
3. Rewrite paired READMEs and add paired home/getting-started documents.
4. Update maintenance guidance and changelog.
5. Run synthetic examples and the full validation suite.
6. Update GitHub About metadata after repository files pass.

Rollback restores the previous README/navigation files and GitHub metadata; runtime behavior is unaffected.

## Open Questions

None.
