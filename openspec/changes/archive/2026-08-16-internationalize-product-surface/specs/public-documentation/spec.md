## MODIFIED Requirements

### Requirement: Deterministic bilingual parity
Documentation checks MUST compare public commands, stages, modes, integrations, compatibility,
links, and security warnings across both READMEs without requiring line-by-line translation.
Bilingual parity applies to documentation only and MUST NOT be read as permitting a non-English
product surface: the English and Spanish guides both describe the same English tool.

#### Scenario: One README changes a command alone
- **WHEN** a supported command changes in only one language
- **THEN** parity validation fails with an actionable difference

#### Scenario: A guide documents a non-English product surface
- **WHEN** either language guide quotes command output or artifact section names that are not English
- **THEN** validation fails and names the file

## ADDED Requirements

### Requirement: English documentation quotes the English product surface
English documentation and the canonical skills MUST NOT contain Spanish text. Where they quote
command output or artifact section names, the quoted text MUST match the product surface exactly.

#### Scenario: An English guide quotes command output
- **WHEN** an English document or canonical skill quotes tool output or an artifact section name
- **THEN** the quoted text is English and matches what the tool produces

#### Scenario: Spanish leaks into an English document
- **WHEN** an English document or canonical skill contains Spanish text
- **THEN** validation fails and names the file and line
