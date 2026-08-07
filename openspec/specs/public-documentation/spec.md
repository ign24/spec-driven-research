## Purpose

Define audience-specific, bilingual, test-backed documentation for people who operate SDR and agents
that contribute to or execute the framework.
## Requirements
### Requirement: English human guide
`README.md` MUST explain purpose, installation, quickstart, lifecycle stages, modes, evidence,
approval, main commands, integrations, safety boundaries, limitations, and contribution paths.

#### Scenario: Onboard an English-speaking user
- **WHEN** a user opens `README.md`
- **THEN** they can install SDR and identify the steps of a minimal supported lifecycle

### Requirement: Spanish human guide
`README.es.md` MUST provide equivalent supported commands, lifecycle behavior, limitations, and
safety guidance in Spanish, and both READMEs MUST link to each other.

#### Scenario: Onboard a Spanish-speaking user
- **WHEN** a user opens `README.es.md`
- **THEN** its installation and lifecycle commands match the English contract
- **THEN** the English guide is directly reachable

### Requirement: Deterministic bilingual parity
Documentation checks MUST compare public commands, stages, modes, integrations, compatibility,
links, and security warnings across both READMEs without requiring line-by-line translation.

#### Scenario: One README changes a command alone
- **WHEN** a supported command changes in only one language
- **THEN** parity validation fails with an actionable difference

### Requirement: Agent repository instructions
`AGENTS.md` MUST concisely identify sources of truth, contribution workflow, tests, canonical skills,
security constraints, Git constraints, and prohibited content for agents.

#### Scenario: An agent starts framework work
- **WHEN** the agent reads the root instructions
- **THEN** it can locate runtime code and required validations
- **THEN** it is instructed not to stage, commit, copy private material, or use destructive Git actions without authorization

### Requirement: Audience separation
Human operation guidance MUST remain usable without agent tooling, while agent-specific repository
constraints MUST not replace the human CLI documentation.

#### Scenario: A person operates SDR directly
- **WHEN** no agent integration is installed
- **THEN** the READMEs and detailed docs are sufficient to use the supported CLI

### Requirement: Evidence-backed claims
Public documentation MUST describe only tested behavior, identify probe verification as explicit
execution of user-controlled code, and avoid claims of semantic truth or stable internal APIs.

#### Scenario: A public claim lacks validation
- **WHEN** documentation review cannot map a behavior claim to implementation or a release check
- **THEN** the claim is removed or validation is added before release

### Requirement: Privacy-safe documentation examples
Documentation examples MUST use synthetic identities, reserved domains, repository-relative paths,
and invented results.

#### Scenario: Scan public prose
- **WHEN** documentation and examples are audited
- **THEN** no credentials, private paths, real investigation data, or unpublished endpoint appears

### Requirement: Outcome-first bilingual README
English and Spanish root READMEs MUST show the same prominent local banner, introduce SDR through the evidence chain from question to reusable asset, show only verifiable project badges and availability claims, provide source installation and a complete synthetic light-mode tour before detailed reference material, and preserve equivalent confidence and safety boundaries.

#### Scenario: A new reader opens the repository
- **WHEN** a reader opens either root README
- **THEN** they see the same local banner before the detailed content
- **THEN** they can identify what outcome SDR produces before reading the complete control reference
- **THEN** they can see that SDR is an alpha installed from source with no GitHub or package-index release
- **THEN** they can start the synthetic light tour and reach the corresponding documentation home

### Requirement: Bilingual documentation home
SDR MUST provide paired English and Spanish documentation homes that route readers to beginner operation, lifecycle, CLI, evidence, security, integrations, maintenance, and release guidance.

#### Scenario: A beginner opens the documentation home
- **WHEN** a reader opens either language's documentation home
- **THEN** the corresponding beginner path and detailed references are directly reachable
- **THEN** the other language is directly reachable

### Requirement: Synthetic beginner path
SDR MUST provide equivalent English and Spanish beginner guides for the complete light lifecycle using only the maintained synthetic light fixture.

#### Scenario: Complete the beginner lifecycle
- **WHEN** a reader follows the guide from a source checkout
- **THEN** the documented order is `intake -> explore -> transfer -> reuse -> done`
- **THEN** no probe is required or executed
- **THEN** transfer requires explicit approval and reuse remains mandatory
- **THEN** the final status is `done`

### Requirement: Honest onboarding claims
Onboarding documentation MUST match current acquisition, compatibility, Git, evidence, release, and integration evidence and MUST NOT claim unavailable package-index publication or unrecorded agent verification.

#### Scenario: Public package publication is disabled
- **WHEN** release policy states that package-index publication is not enabled
- **THEN** onboarding uses the canonical source repository
- **THEN** package-index installation, release badges, and availability claims fail validation

#### Scenario: Integration status is documented
- **WHEN** no complete version-matched host E2E exists
- **THEN** Claude Code, Codex, and OpenCode are reported as `documented`
- **THEN** they are not described as `verified`

### Requirement: Deterministic documentation navigation and parity
Documentation validation MUST compare the paired root READMEs, documentation homes, and beginner guides by concept and MUST verify that local links and synthetic fixture references resolve.

#### Scenario: One language changes alone
- **WHEN** lifecycle, command, side-effect, limitation, acquisition, release, or status guidance changes in only one language
- **THEN** validation fails with the document and missing concept

#### Scenario: A local target is missing
- **WHEN** a public onboarding page links to a missing local document or fixture
- **THEN** documentation validation identifies the source page and unresolved target

