## Why

SDR should use the short, permissive MIT License selected by the copyright holder before its first public release. The repository, package metadata, artifacts, specifications, and bilingual documentation currently identify Apache-2.0 and must change together to avoid contradictory licensing claims.

## What Changes

- Replace the root Apache-2.0 license text with the canonical MIT License text and retain the existing copyright holder and year.
- Change Python package metadata and classifier from Apache-2.0 to MIT.
- Update English and Spanish public documentation, changelog, tests, artifact expectations, and release-hardening artifacts to identify MIT consistently.
- Treat already published Git history as historical material whose prior Apache-2.0 grant is not revoked; current and future repository distributions use MIT.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `public-repository-boundary`: Change the required public project license identity from Apache-2.0 to MIT and require repository and package surfaces to remain consistent.

## Impact

This change affects `LICENSE`, `pyproject.toml`, package metadata and artifacts, README files, changelog, license assertions, the consolidated public repository specification, and the active release-hardening change. It does not change runtime behavior, dependencies, CLI commands, schemas, or research artifacts.
