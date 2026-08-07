## MODIFIED Requirements

### Requirement: Public identity consistency
Repository, package, command, license, and copyright metadata MUST identify the same public project across packaging and governance files.

#### Scenario: Compare public metadata
- **WHEN** package and repository metadata are validated
- **THEN** the distribution is `spec-driven-research`
- **THEN** the import package and console command are `sdr`
- **THEN** the declared license is MIT
