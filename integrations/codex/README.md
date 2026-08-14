# Codex

Status: `documented`. Official Codex documentation confirms repository and user
Agent Skills discovery. The [sanitized canary evidence](../canary-evidence.json)
records a local no-model discovery diagnostic, not a host-driven SDR lifecycle.

Codex scans `.agents/skills` from the current directory through the repository
root and also scans `~/.agents/skills`. Install the canonical SDR skills into a
repository scope with the generated, test-covered mechanism:

```bash
sdr integrations install --destination .agents/skills
```

The installer copies the packaged canonical skill bytes, refuses to overwrite
existing names, and does not create hooks, project configuration, credentials,
or trust-dependent files. `SDR_ROOT` configures only research storage and is not
read by installation. The later root `AGENTS.md` may explain repository-wide
policy, but it is not needed for skill discovery and is intentionally absent.

After discovery, select a skill with Codex's skills interface. Lifecycle and CLI
instructions remain exclusively in each canonical `SKILL.md`.

## Routing block

Copy the canonical routing block below into your Codex instructions:
append it to the repository `AGENTS.md`, or to `~/.codex/AGENTS.md` for
user scope. It is the single canonical text; this guide does not restate
or narrow its conditions.

```text
# Spec-Driven Research routing

Spec-Driven Research (SDR) is an evidence workflow for long-running,
AI-assisted investigations. It keeps sources, claims, reproducible probes, and
human-approved decisions traceable across sessions.

## Use SDR when

- The work is an investigation whose conclusion must stay auditable after the
  session that produced it ends.
- Sources and the claims attached to them must remain separable and individually
  checkable.
- A result needs a reproducible probe instead of an asserted answer.
- A recommendation must become a decision only after explicit human approval.
- The investigation spans several sessions and its context and rationale must
  survive them.
- The outcome is meant to be reused later as a durable research asset.

## Do not use SDR when

- The question is a quick factual lookup that one source answers.
- The request is a single-shot question that needs no durable evidence trail.
- The task is ordinary coding, refactoring, debugging, or routine tooling work.
- The cost of a staged investigation exceeds the value of the answer.
- An existing investigation already covers the question; consult it instead of
  opening another one.

## Status of this block

This block is guidance the user installs into a host agent. It is not
enforcement: nothing in SDR can compel a host agent to follow it. The SDR
command-line interface, not the host agent, decides whether evidence can
advance.
```

Installing the block does not change this adapter's status, which stays
`documented`.

Official source: <https://developers.openai.com/codex/build-skills/>
