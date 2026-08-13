<p align="center">
  <img src="assets/sdr-banner.png" alt="Spec-Driven Research">
</p>

# Spec-Driven Research

[![CI](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml/badge.svg)](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml)
[![Security](https://github.com/ign24/spec-driven-research/actions/workflows/security.yml/badge.svg)](https://github.com/ign24/spec-driven-research/actions/workflows/security.yml)
[![Python CI: 3.12 | 3.13](https://img.shields.io/badge/Python_CI-3.12_%7C_3.13-3776AB?logo=python&logoColor=white)](https://github.com/ign24/spec-driven-research/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Status: Alpha](https://img.shields.io/badge/Status-Alpha-orange.svg)](CHANGELOG.md)

[Español](README.es.md)

## AI can write the report. Can you trust the research?

**Spec-Driven Research is an evidence workflow for long-running, AI-assisted
investigations.**

It keeps sources, claims, reproducible checks, and human decisions traceable
across sessions, so a convincing answer can become a reviewable decision and a
reusable asset:

`question -> evidence -> optional probe -> human-approved decision -> reusable asset`

## Why SDR exists

Research agents are fast, but their output is difficult to audit. Sources may
disappear or be inaccessible. A citation may not support the claim attached to
it. Results can be asserted without a reproducible check. During long
investigations, context and rationale are gradually lost across sessions.

SDR gives the investigation a durable structure. It preserves the evidence
trail, detects stale validation, separates sourced claims from executable
checks, and requires explicit human approval before a recommendation becomes a
decision.

| Typical agent research | Spec-Driven Research |
| --- | --- |
| Convincing final answer | Reviewable evidence trail |
| URLs in a response | Sources, metadata, and snapshots |
| Claims mixed with inference | Explicitly anchored claims |
| Context lost between sessions | Persistent staged investigation |
| Results asserted in prose | Reproducible probes |
| Agent-generated recommendation | Human-approved decision |
| Another isolated report | Reusable research asset |

## Install with your agent

Paste this into Claude Code, Codex, or OpenCode:

```text
Install Spec-Driven Research from https://github.com/ign24/spec-driven-research
in this project. Read its installation and agent-integration documentation
first. Explain what you will change and ask for my approval, then install SDR
and the project-local skills for this agent and run the documented checks. Do
not overwrite existing files or create commits.
```

> **Alpha, source-only software.** SDR has no GitHub release and no PyPI release.
> Install it from the canonical GitHub source. Interfaces and artifact contracts
> can still change before the first public release.

## Five-minute synthetic tour

This maintained example is invented, offline, and light mode. It records an
explicit synthetic approval, does not run a probe, and completes mandatory reuse.

```bash
git clone https://github.com/ign24/spec-driven-research.git
cd sdr
uv sync --locked --all-extras --dev
TOUR_ROOT="$(mktemp -d)/research"
uv run python examples/runner.py light-complete --root "$TOUR_ROOT"
SDR_ROOT="$TOUR_ROOT" uv run sdr status synthetic-light --json
```

The runner prints `synthetic-light: done`. The status JSON reports `"mode":
"light"`, `"stage": "reuse"`, `"status": "done"`, and approval by `Example
Reviewer`. Inspect the materialized evidence at:

- `$TOUR_ROOT/synthetic-light/brief.md`
- `$TOUR_ROOT/synthetic-light/notes/landscape.md`
- `$TOUR_ROOT/synthetic-light/decision-memo.md`
- `$TOUR_ROOT/synthetic-light/assets/checklist.md`

Follow the [beginner guide](docs/getting-started.md) to run the same fixture with
the public CLI, including explicit `sdr approve` and `--no-commit` on every
commit-producing transition.

## Install from source

SDR requires Python 3.12 or newer. Install the current canonical source:

```bash
uv tool install "git+https://github.com/ign24/spec-driven-research"
sdr --help
```

For a reproducible install, replace `REVISION` with a full commit SHA:

```bash
REVISION=REPLACE_WITH_FULL_COMMIT_SHA
uv tool install "git+https://github.com/ign24/spec-driven-research@${REVISION}"
```

From an existing checkout, `uv tool install .` is the isolated-tool equivalent.
`python -m pip install .` also installs that checkout. Contributors should use
`uv sync --locked --all-extras --dev`; snapshot extraction is the optional
`snapshot` extra. These are source installs, not package-index installation.

## Choose a mode

| Mode | Lifecycle | Use it when |
| --- | --- | --- |
| `light` | `intake -> explore -> transfer -> reuse -> done` | Sourced comparison and human review are sufficient. No probe is required; reuse is still mandatory. |
| `full` | `intake -> explore -> probe -> transfer -> reuse -> done` | The decision needs executable evidence from a reproducible probe. |

The five stages are `intake`, `explore`, `probe`, `transfer`, and `reuse`.
Detailed artifacts, guards, and transitions are canonical in the
[workflow guide](docs/workflow.md).

## Confidence boundaries

SDR validates declared structure and local evidence. It does not prove source
truth or guarantee that cited material is true. People remain responsible for
source quality, safe execution, interpretation, and the final recommendation.

Controls run in this conceptual order: **Structural**, **Evidential**, **Textual anchoring**,
**Executable**, **Hash consistency**, and **HITL**. `advance` checks
consistency before the current stage controls. The optional Context Graph is
non-blocking and is not complete lineage.

- Use `[S<n>]` for factual claims intended for deterministic local matching.
- `[cf. S<n>]` is contextual: it does not create a claim and does not enter textual matching.
  Matching does not use models.
- `sdr resolve-claim` records scoped human review; it does not replace or substitute
  transfer-level `sdr approve`.
- `sdr cross` derives deterministic, model-free, advisory joins across stored
  investigations. Shared identity or text does not prove publisher identity,
  truth, or independent corroboration, and the layer never blocks a transition.
- `sdr check --offline` skips network checks and automatic snapshot capture.
  Skipped checks are reported as skipped, not passed. For example:
  `uv run sdr check example-study --offline`.
- Probe execution requires `verify.action: run`; prefer `verify.argv`. SDR runs
  `argv` directly, without a shell. This is not a sandbox and does not make an
  executable trustworthy.

Read the canonical [evidence model](docs/evidence-model.md),
[validation reference](docs/validation.md), and
[security model](docs/security-model.md) before using real sources or commands.
Treat Notes, Snapshots, Repositories, URLs, Probe commands, Git, credentials, and
the host environment as trust boundaries.

## Git behavior

`new`, `advance`, `reopen`, `drop`, and `archive` commit by default.
`acknowledge-degradation` also commits by default. Use `--no-commit` when you,
CI, or an agent owns Git history. Read-only evidence/reporting commands,
including `sdr cross`, do not create commits. See the exact mutation, network,
and guard contract in the [CLI reference](docs/cli-reference.md).

## Agent integrations

SDR packages seven canonical stage skills. Exactly three agent adapters are
currently documented:

| Agent | Install into the current project | Status |
| --- | --- | --- |
| Claude Code | `sdr integrations install --destination .claude/skills` | `documented` |
| Codex | `sdr integrations install --destination .agents/skills` | `documented` |
| OpenCode | `sdr integrations install --destination .opencode/skills` | `documented` |

The general form is `sdr integrations install --destination PATH_TO_SKILLS`.
The installer copies package resources and does not use `SDR_ROOT`, which only
controls research storage. `documented` means discovery guidance and
deterministic adapter checks exist, not host E2E. `verified` requires recorded,
version-matched host E2E evidence; `experimental` marks a provisional contract.
See [integrations](docs/integrations.md).

## Find the right documentation

Start at the [task-oriented documentation home](docs/README.md).

| Goal | Canonical guide |
| --- | --- |
| Complete the smallest supported lifecycle | [Getting started](docs/getting-started.md) |
| Understand stages and backtracking | [Workflow](docs/workflow.md) |
| Look up `sdr new`, `sdr check`, `sdr advance`, `sdr status`, `sdr snapshot`, `sdr verify-claims`, `sdr resolve-claim`, `sdr verify-probe`, `sdr approve`, `sdr reopen`, `sdr drop`, `sdr archive`, `sdr index`, `sdr doctor`, `sdr migrate`, `sdr context`, `sdr cross`, or `sdr acknowledge-degradation` | [CLI reference](docs/cli-reference.md) |
| Evaluate claims and evidence limits | [Evidence model](docs/evidence-model.md) |
| Review threats and trust boundaries | [Security model](docs/security-model.md) and [SECURITY.md](SECURITY.md) |
| Install agent skills | [Integrations](docs/integrations.md) |
| Validate a contribution | [Maintenance and validation](docs/validation.md) |
| Understand publication status | [Releasing](docs/releasing.md) |

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md) and agent work also
follows [AGENTS.md](AGENTS.md). SDR is licensed under the [MIT License](LICENSE).
