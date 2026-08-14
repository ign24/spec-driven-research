# OpenCode

Status: `documented`. OpenCode's official documentation confirms native Agent
Skills discovery. The [sanitized canary evidence](../canary-evidence.json)
records pure local discovery without model invocation; no host E2E is asserted.

This is the public SDR adapter. It uses the native `skill` tool and does not
depend on a selected model, private configuration, MCP server, or plugin.
OpenCode discovers project skills at `.opencode/skills` and user skills at
`~/.config/opencode/skills`.

Install the canonical skills packaged with the current SDR version into the
current project:

```bash
sdr integrations install --destination .opencode/skills
```

The installer is scoped to the destination passed by the operator, creates no
agent configuration, and refuses to overwrite existing skills. OpenCode exposes
the seven skill descriptions and loads a `SKILL.md` through its native tool only
when selected. Use `sdr-new` to start or `sdr-status` to inspect work. Workflow
details remain in the installed skill files. `SDR_ROOT` configures only research
storage and is not read by skill installation.

## Routing block

Copy the canonical routing block below into your OpenCode instructions:
append it to the project `AGENTS.md`, or to `~/.config/opencode/AGENTS.md`
for user scope. It is the single canonical text; this guide does not
restate or narrow its conditions.

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

Official source: <https://opencode.ai/docs/skills/>
