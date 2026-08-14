# Integrations

SDR integrations expose the seven canonical Agent Skills to external coding
agents. They do not change the deterministic CLI contract.

## Status

| Adapter | Discovery approach | Status | Local E2E claimed? |
| --- | --- | --- | --- |
| Claude Code | Project or user Agent Skills directory | `documented` | No |
| Codex | Repository or user Agent Skills directory | `documented` | No |
| OpenCode | Project or user Agent Skills directory | `documented` | No |

`documented` means official discovery behavior and installation instructions
have been recorded and adapter metadata is validated. It does not mean the
project executed a local end-to-end session with that agent.

## Isolated discovery canaries

The [sanitized canary evidence](../integrations/canary-evidence.json) is the
machine-readable source for the canary date, exact wheel identity and digest,
generic environment and host versions, execution policy, discovery results,
lifecycle results, and limitations. Validation rejects a missing or malformed
record, unsafe environment-specific material, adapter mismatches, unsupported
prose digests, and results that exceed `documented` status. The standalone
paired wheel/sdist artifact audit additionally reads this record from the sdist
and rejects it unless its version, wheel filename, and SHA-256 match the actual
candidate wheel bytes.

The record establishes package-resource installation and only the locally
observable discovery facts it names. Claude Code remains filesystem-only because
the tested host exposed no offline skill-introspection command. No canary ran an
SDR lifecycle, so none satisfies the complete installed-CLI lifecycle evidence
required for `verified` status.

## Canonical skills

The authored source of truth is `skills/sdr-new`, `sdr-intake`, `sdr-explore`,
`sdr-probe`, `sdr-transfer`, `sdr-reuse`, and `sdr-status`. Adapters must link,
install, or reference those directories. Released wheels contain byte-equivalent
package resources for installation; do not copy and modify skill content in an
integration because modified copies drift from stage guards and CLI behavior.

## Agent routing block

The conditions under which a host agent should reach for SDR, and the
conditions under which it must not, are stated once in a canonical routing
block shipped as a package resource. Each adapter guide publishes that exact
text with host-specific installation instructions and does not redefine its
conditions; validation reports any adapter whose published copy diverges from
the canonical source. The block is guidance a user installs into a host agent,
not enforcement, so publishing it does not change any adapter status.

## Validation and installation

Inspect each `integrations/<agent>/README.md` and `adapter.yaml` before installing.
Where the generated validator is supported:

```bash
python -m sdr.integration_validation validate .
sdr integrations install --destination PATH_TO_SKILLS
```

The installer copies all seven packaged skills as regular files and refuses the
entire installation if any target conflicts. `SDR_ROOT` controls only research
storage and is neither read nor written by installation. The installer does
not write credentials, hooks, permissions, or general agent configuration.
Agent platforms and installed skill directories remain separate trust boundaries.
The module command `python -m sdr.integration_validation install ...` remains
available for compatibility, but the installed `sdr` command is the primary
installation interface and does not depend on how `python` resolves on `PATH`.

## Runtime contract

An agent should read `sdr status <slug> --json`, select the skill matching the
current stage, run `sdr check <slug> --json`, and obey stage guards. The CLI,
not the adapter or agent model, determines whether evidence can advance.
