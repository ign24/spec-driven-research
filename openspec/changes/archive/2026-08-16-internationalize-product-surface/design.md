## Context

The product surface is Spanish and everything else is English. Concretely: `cli.py` carries 59 lines
of Spanish help and docstrings, roughly 150 further lines of Spanish user-facing text are spread
across eleven modules, and the five templates under `src/sdr/templates/` are the artifacts a user
actually receives from `sdr new`.

The templates are not merely text. `schema.py` declares `required_sections` using the Spanish heading
strings, and `gates.py` resolves sections by those same literals — `section("Criterios de evaluación")`
appears in the criteria extraction and the brief/probe cross-reference. The heading text is therefore
part of the artifact contract, not presentation, and translating a template without translating the
schema and the gate lookups in lockstep produces an investigation that cannot advance.

Around 31 test modules assert Spanish strings, and the maintained fixtures under `examples/` are
materialized artifacts carrying Spanish headings. `docs/getting-started.md` and `docs/README.md` are
English documents with a few Spanish lines, which is leakage from quoting CLI output or headings, not
translation.

`v0.2.0` was tagged hours ago. It is alpha, source-only, has no package-index release, and had no
distribution before the tag, so the set of research roots created under the Spanish contract is
expected to be the maintainer's own.

## Goals / Non-Goals

**Goals:**

- Make every string a user reads from the installed tool English, so the product matches its own
  documentation.
- Keep the artifact contract coherent across templates, schema declarations, and gate lookups, so no
  intermediate state exists in which an investigation validates against one and not the other.
- Carry an investigation created under the Spanish contract forward, rather than stranding it.
- Detect a regression back into Spanish mechanically, so this does not silently return.

**Non-Goals:**

- A runtime locale system, translation catalogue, or `gettext` dependency. Spanish stays as a
  documentation translation.
- Renaming any command, option, stage, lifecycle transition, or JSON field. Machine-readable
  identifiers are already English and are not in scope.
- Translating `README.es.md` or `docs/*.es.md` out of Spanish. Those are the translation, and they
  stay.
- Retranslating the archived OpenSpec history under `openspec/changes/archive/`.

## Decisions

### Decision 1: English-only product surface, no localization framework

The product surface becomes English and stays single-language. The alternative — a locale mechanism
serving Spanish and English — was rejected on cost and on evidence: it doubles every future
user-facing string, requires parity validation for a second runtime surface on top of the
documentation parity that already exists, and serves a demand no user has expressed. SDR's audience
is explicitly international, and its documentation, specifications, and skills already resolved this
question in favour of English.

This is stated as a contract rather than left implicit, so that adding a Spanish string later is a
spec violation rather than a matter of taste.

### Decision 2: The artifact contract changes in one atomic step

Templates, `schema.required_sections`, and every `section(...)` lookup in `gates.py` change together,
in a single task, verified by the maintained fixtures advancing end to end. Splitting them across
tasks would produce a repository state in which `sdr check` fails against freshly created artifacts,
and the failure would be indistinguishable from a real regression.

The section names are derived from one declaration rather than repeated. Today the string
`"Criterios de evaluación"` appears in eleven places across source, fixtures, skills, and docs; the
English replacement is declared once in `schema.py` and referenced, so the gate engine cannot drift
from the templates again. This mirrors the fix applied to the repository coordinate in
`add-github-installation-path`: derive from the authoritative declaration instead of storing copies.

### Decision 3: Migration translates headings in place, with no permanent compatibility layer

`sdr migrate` gains the ability to rewrite an investigation's section headings from the Spanish
contract to the English one. Accepting both heading sets on read was considered and rejected: a
permanent compatibility layer would make the artifact contract ambiguous, and the ambiguity would
outlive the handful of roots it exists to serve. A migration is a bounded, auditable operation with
an existing command and an existing place in the lifecycle.

Migration reports what it changed and refuses to run twice, so it stays idempotent and its effect is
inspectable rather than silent.

### Decision 4: A validator, not a review habit, enforces the language contract

A deterministic check scans the product surface — CLI text, templates, and messages raised by the
runtime modules — and reports Spanish-language text with file and line. Relying on review would
guarantee the regression, because the entire reason this went unnoticed is that no automated check
looked at the language of the product surface.

The check keys on the Spanish-specific characters (`á é í ó ú ñ ¿ ¡`) plus a small closed list of
unambiguous Spanish function words. That is deliberately conservative: it will not catch English-
looking Spanish, and it is not a language classifier. It catches the actual failure mode — text
written in Spanish — at zero risk of misreading English prose. Documentation translations are
excluded by path, not by heuristic.

### Decision 5: Tests are retranslated, never loosened

The roughly 31 test modules asserting Spanish strings are updated to assert the English strings. The
tempting shortcut — relaxing assertions to substring or regex matches that survive either language —
would delete the coverage that makes these tests worth having. Where a test asserts an exact message,
it continues to assert an exact message.

## Risks / Trade-offs

- **A partially translated artifact contract silently breaks every investigation.** → Templates,
  schema, and gate lookups change in one task, and the maintained `examples/` fixtures must complete
  a full lifecycle before the task is considered done.
- **A research root created before the change becomes unusable.** → `sdr migrate` translates headings
  in place, reports its changes, and is idempotent. The release notes state the requirement
  explicitly rather than leaving a user to discover it through a failing gate.
- **The language check produces false positives on proper nouns or code identifiers.** → It keys on
  Spanish-specific characters and a closed word list, scans only the product surface, and excludes
  documentation translations by path. A false positive is a visible finding with a file and line, not
  a silent failure.
- **Retranslating 31 test modules risks weakening assertions under time pressure.** → Exact-message
  assertions stay exact; the change is the expected string, never the comparison.
- **`v0.2.0` documents a Spanish CLI in its release notes.** → That release stays as published;
  published versions are never rewritten. The next release states the surface is English and that
  existing roots require migration.

## Migration Plan

1. Land the language contract and its validator first, failing against the current tree, so the
   target state is defined before anything is translated.
2. Translate the artifact contract — templates, schema declarations, gate lookups — atomically, and
   regenerate the maintained fixtures.
3. Extend `sdr migrate`, proving on a fixture root created under the Spanish contract that it
   advances after migration and that a second run is a no-op.
4. Translate the CLI and runtime messages, retranslating the affected tests alongside each module.
5. Add `sdr --version`.
6. Clear the Spanish leakage from English documentation and canonical skills.
7. Cut the next release with the break, the migration requirement, and the language contract recorded
   in `CHANGELOG.md`.

Rollback is `git revert` of the change plus `sdr migrate` in the reverse direction for any root
migrated in the interim; because the tagged release is unaffected, no published artifact needs
withdrawal.

## Open Questions

- Whether `sdr migrate` should also translate free-text content a user wrote in Spanish inside their
  artifacts, or only the structural headings it owns. Current position is headings only: the user's
  own prose is theirs, and rewriting it would exceed what a structural migration should do.
