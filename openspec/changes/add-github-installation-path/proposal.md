## Why

SDR is meant to be adopted by Claude Code, Codex, and OpenCode users who install it directly from
its public repository. That path is currently undefended: the documented `uv tool install
"git+https://..."` command has never been executed against a clean environment, the repository URL it
names is not fixed by any test, and nothing tells a host agent when SDR applies. Publication to a
package index is explicitly out of scope, so the git installation path is the only supported route
and must carry its own evidence.

## What Changes

- Verify the documented git installation end to end: install from an explicit repository revision
  into a clean environment outside the checkout, then run a complete light lifecycle through the
  installed `sdr` console script.
- Establish a single canonical repository coordinate as the one source of truth, and reject
  documentation that names any other repository identity or that reaches the canonical repository
  only through a rename redirect.
- Add a copyable agent routing block stating when a host agent should invoke SDR and when it should
  not, published for all three supported adapters in both documentation languages.
- **BREAKING** for prior guidance: documentation stops presenting package-index installation as a
  planned route and presents the git revision install as the supported one.

## Capabilities

### New Capabilities
- `github-installation`: installing SDR from a pinned public repository revision, the evidence that
  the installed console script completes a lifecycle, and the canonical repository identity that
  installation instructions must name.

### Modified Capabilities
- `agent-integrations`: adds a required routing contract so each supported adapter states the
  conditions under which a host agent invokes SDR, and the conditions under which it must not.
- `public-documentation`: requires every documented repository coordinate to resolve directly to the
  canonical repository, without depending on a rename redirect.

## Impact

- `README.md`, `README.es.md`, `docs/`, and the three `integrations/*/README.md` adapter guides.
- `pyproject.toml` gains declared project URLs as the canonical coordinate source; no second version
  or identity source is introduced.
- New installation-verification harness under `tests/`, executed against a built artifact and a
  pinned revision.
- `sdr.readme_parity` gains repository-identity findings.
- Supersedes the installation-relevant portion of `release-hardening-and-public-distribution`; that
  change's package-index publication scope is withdrawn.
