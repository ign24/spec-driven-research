## Purpose

Define the language contract of the installed product surface and the deterministic validation that
keeps it from drifting, separating the tool's single runtime language from its bilingual
documentation.
## Requirements
### Requirement: English product surface
Every string a user reads from the installed tool MUST be English. This covers command and option
help, the command group description, messages printed on success, refusal, or failure, and the
artifact templates the tool writes. Documentation translations MUST NOT extend to the product
surface, and the tool MUST NOT offer a runtime language selection.

#### Scenario: Read the installed command help
- **WHEN** a user runs the installed console script with `--help`
- **THEN** the group description, every command summary, and every option description are English

#### Scenario: Receive a generated artifact
- **WHEN** the tool creates a stage artifact from a packaged template
- **THEN** the artifact's section names and guidance text are English

#### Scenario: A message is added in another language
- **WHEN** a user-facing string in the product surface is written in Spanish
- **THEN** validation fails and names the file and line

### Requirement: Deterministic product-language validation
The project MUST validate the language of the product surface deterministically, without a model and
without network access. Validation MUST report each finding with its file and line, MUST scan only
the product surface, and MUST exclude documentation translations by path rather than by heuristic.

#### Scenario: Validate a clean tree
- **WHEN** product-language validation runs over a tree whose product surface is English
- **THEN** it reports no finding

#### Scenario: Documentation translations are not findings
- **WHEN** product-language validation encounters the Spanish documentation pair
- **THEN** those files are excluded by path and produce no finding

#### Scenario: Validation is deterministic and offline
- **WHEN** product-language validation runs twice over an unchanged tree
- **THEN** both runs produce identical output and neither consults the network

### Requirement: Version reporting
The installed console script MUST report its version on request, derived from the single version
source, without introducing a second version source.

#### Scenario: Ask the installed tool for its version
- **WHEN** a user runs the installed console script with `--version`
- **THEN** it prints the version declared by the single version source and exits successfully
