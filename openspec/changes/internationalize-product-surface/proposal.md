## Why

SDR is English-first everywhere a maintainer looks — documentation, specifications, canonical
skills, the agent routing block, code identifiers, commit history — and Spanish everywhere a user
looks. Someone who follows the English README, installs `v0.2.0`, and runs the tool is told
`Spec-Driven Research: harness de I+D aplicada trazable.`, then receives a `brief.md` whose sections
are `Pregunta`, `Hipótesis`, and `Criterios de evaluación`. The project was just tagged and is about
to be announced publicly, so the mismatch stops being a private inconsistency and becomes the first
impression.

The gap is invisible from inside the repository: every gate, audit, and parity check passes, because
none of them inspect the language of the product surface. It only appears by installing the tool and
using it, which is the same failure mode `add-github-installation-path` closed for the installation
command.

## What Changes

- **BREAKING** Translate the artifact contract to English. Stage templates, `schema.required_sections`,
  and every section lookup in the gate engine move from Spanish headings to English ones. Existing
  investigations on disk stop validating until migrated.
- Extend `sdr migrate` to rewrite the section headings of an existing investigation, so a research
  root created before this change can be carried forward rather than abandoned.
- Translate the command-line surface to English: command and option help, the group description, and
  every message the CLI prints on success, refusal, or failure.
- Translate user-facing messages raised by the gate engine, lifecycle, research store, schema loader,
  parser, claim resolution, probe verification, path resolution, trail recording, and network policy.
- Add `sdr --version`, which does not currently exist, reporting the single version source.
- Remove the Spanish that leaked into English documentation and canonical skills where those files
  quote CLI output or artifact headings. `README.es.md` and `docs/*.es.md` remain Spanish; they are
  translations of documentation, not product surface.
- Establish that the product surface is English-only. Spanish is supported as a documentation
  translation and is not offered as a runtime locale.

## Capabilities

### New Capabilities

- `product-language`: the language contract for everything a user reads from the installed tool —
  CLI help and messages, artifact templates, and artifact section names — including the rule that
  documentation translations do not extend to the product surface, and validation that detects a
  regression back into Spanish.

### Modified Capabilities

- `sdr-lifecycle-evidence-contract`: stage artifacts declare English section names, and the migration
  path carries an investigation created under the previous Spanish contract forward to it.
- `public-documentation`: English documentation and canonical skills must not quote Spanish product
  surface, and bilingual parity must not be satisfied by a Spanish-only runtime.

## Impact

Affects `src/sdr/templates/` (five templates), `schema.py` required-section declarations, section
lookups in `gates.py`, user-facing messages across `cli.py`, `gates.py`, `lifecycle.py`, `research.py`,
`schema.py`, `parser.py`, `claims.py`, `probe_verify.py`, `paths.py`, `trail.py`, and
`network_policy.py`, the `migrate` command, the maintained fixtures under `examples/`, roughly 31 test
modules that assert Spanish strings, the English pair of `docs/getting-started.md` and `docs/README.md`,
and the `sdr-intake`, `sdr-transfer`, and `sdr-probe` canonical skills.

Every research root created before this change requires `sdr migrate`. Because the tagged release is
alpha, source-only, and hours old, the population of affected roots is expected to be the maintainer's
own; the migration exists so that expectation does not have to be assumed.

No CLI command, option name, stage name, or lifecycle transition is renamed. The break is confined to
artifact section names and the language of human-readable text.
