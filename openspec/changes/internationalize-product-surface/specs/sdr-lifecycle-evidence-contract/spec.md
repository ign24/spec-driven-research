## MODIFIED Requirements

### Requirement: Stage-specific artifact contract
Each stage MUST validate its declared artifact shape: `brief.md`, traceable `notes/*.md`, executable
`probe/results.md`, `decision-memo.md`, and reusable `assets/*.md` as applicable to the mode. Stage
artifacts MUST declare English section names, and those names MUST be derived from a single
declaration so that templates, required-section declarations, and gate lookups cannot diverge.

#### Scenario: A required artifact field is missing
- **WHEN** a stage artifact lacks required frontmatter, sections, or enumerated metadata
- **THEN** `sdr check` identifies the failing deterministic check
- **THEN** advancement remains blocked

#### Scenario: A section name is declared in more than one place
- **WHEN** a template, a required-section declaration, and a gate lookup name the same section
- **THEN** all three resolve from one declaration rather than repeating the literal

## ADDED Requirements

### Requirement: Migration to the English artifact contract
An investigation created under the previous Spanish artifact contract MUST be carriable forward by
`sdr migrate`, which rewrites its structural section headings to the English contract. Migration MUST
report what it changed, MUST be idempotent, and MUST NOT rewrite the user's own prose.

#### Scenario: Migrate an investigation created under the previous contract
- **WHEN** `sdr migrate` runs on a research root whose artifacts carry the Spanish section headings
- **THEN** the structural headings are rewritten to the English contract
- **THEN** the command reports the headings it changed
- **THEN** the investigation advances under the current gates

#### Scenario: Run migration twice
- **WHEN** `sdr migrate` runs again on an already migrated investigation
- **THEN** it makes no change and reports that none was required

#### Scenario: User-authored content is preserved
- **WHEN** an artifact contains prose the user wrote in any language beneath a structural heading
- **THEN** migration leaves that prose unchanged
