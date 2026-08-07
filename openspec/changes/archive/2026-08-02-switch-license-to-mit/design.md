## Context

SDR has one copyright holder in the current public history and has not published a GitHub release or package-index version. The repository currently declares Apache-2.0 in the root license, Python metadata, package classifier, specifications, tests, and bilingual documentation. A partial replacement would create ambiguous public and artifact licensing.

## Goals / Non-Goals

**Goals:**

- Make MIT the single current license identity across source, package metadata, built artifacts, specifications, and documentation.
- Use the canonical MIT text with the existing 2026 copyright holder.
- Preserve evidence that historical Apache-2.0 grants are not retroactively revoked.

**Non-Goals:**

- Rewriting published Git history.
- Providing legal advice or changing third-party dependency licenses.
- Changing runtime behavior, version, release status, or contributor policy beyond the project license identifier.

## Decisions

The root `LICENSE` file will contain the canonical MIT License text and `Copyright (c) 2026 Ignacio Zúñiga Navarro`. Package metadata will use SPDX expression `MIT` and the standard MIT classifier. English and Spanish README text, consolidated and active OpenSpec requirements, and tests will use the exact `MIT` identifier where machine comparison matters.

The changelog will record the relicensing under Unreleased. Existing public commits remain part of history; replacing the current license file governs the current distribution but does not attempt to rescind permissions already granted under Apache-2.0.

## Risks / Trade-offs

- [MIT does not include Apache-2.0's explicit patent grant] -> Record the deliberate license choice and keep third-party license obligations separate.
- [A stale Apache reference creates ambiguity] -> Add deterministic repository and built-metadata assertions and search all public text.
- [Historical files remain visible under Apache-2.0] -> Do not rewrite history; document the current relicensing in the changelog.

## Migration Plan

1. Add failing license consistency tests.
2. Replace the root license and update package/public/spec metadata.
3. Build wheel and sdist and verify their license metadata and embedded license.
4. Run all documentation, OpenSpec, artifact, security, and quality gates.

Rollback before release restores the previous license declarations together. No published release rollback is required because SDR has no releases.

## Open Questions

None.
