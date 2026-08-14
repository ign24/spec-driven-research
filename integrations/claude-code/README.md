# Claude Code

Status: `documented`. Official documentation confirms Agent Skills discovery.
The [sanitized canary evidence](../canary-evidence.json) records only
filesystem installation for the tested binary because it exposed no offline
skill-introspection command; it does not claim host E2E.

Claude Code discovers project skills under `.claude/skills` and personal skills
under `~/.claude/skills`. Install the canonical SDR skills packaged with the
current SDR version into one explicit scope:

```bash
sdr integrations install --destination .claude/skills
```

Run the command from the consuming project. It creates only missing skill files
and refuses to overwrite an existing name. Use an absolute destination under
your home directory instead for personal scope. It does not edit settings,
permissions, credentials, or hooks.

Confirm discovery with Claude Code's skill list, then invoke `sdr-new` for a new
investigation or `sdr-status` for an existing one. `SDR_ROOT` configures only
research storage and is not read by skill installation.

## Routing block

Copy the canonical routing block below into your Claude Code instructions:
append it to the project `CLAUDE.md`, or to `~/.claude/CLAUDE.md` for personal
scope. It is the single canonical text; this guide does not restate or narrow
its conditions.

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

Official source: <https://code.claude.com/docs/en/skills>
