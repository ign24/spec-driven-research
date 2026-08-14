## Purpose

Define agent-neutral canonical workflows and thin optional adapters that let supported coding agents
operate the same deterministic SDR CLI and filesystem contract.

## Requirements

### Requirement: Canonical skill set
The `skills/` directory MUST provide canonical workflows for `sdr-new`, every lifecycle stage, and
`sdr-status`, and those skills MUST be the source of truth for agent guidance.

#### Scenario: Validate canonical skills
- **WHEN** the canonical skill tree is checked
- **THEN** all seven skills exist
- **THEN** each stage skill identifies its artifact, gate, stage guard, and next valid action

### Requirement: Stage-safe agent operation
Stage skills MUST inspect structured status, refuse out-of-order artifact production, validate with
structured CLI output, and preserve full/light mode and human-approval semantics.

#### Scenario: Invoke a non-current stage skill
- **WHEN** an agent invokes a skill for a stage other than the investigation's current stage
- **THEN** the skill does not create that stage's artifact
- **THEN** it directs the agent to the current-stage workflow

### Requirement: Optional supported adapters
The supported public adapter set MUST consist exactly of Claude Code, Codex, and OpenCode without
coupling the Python runtime to any one platform.

#### Scenario: Use SDR without an agent platform
- **WHEN** no supported agent is installed
- **THEN** all core CLI commands remain available

#### Scenario: Install one supported adapter
- **WHEN** a user explicitly follows a Claude Code, Codex, or OpenCode adapter's installation instructions
- **THEN** that platform can discover the canonical workflows
- **THEN** the Python package remains unchanged

#### Scenario: An unsupported adapter is exposed
- **WHEN** validation finds an adapter directory, packaged descriptor, or public support claim outside the supported set
- **THEN** validation identifies the unsupported agent and fails

### Requirement: Thin adapter contract
Adapters MUST reference, link, or mechanically mirror canonical skills and MUST NOT independently
redefine stage order, gates, commands, or approval behavior.

#### Scenario: Compare an adapter with canonical skills
- **WHEN** integration consistency is validated
- **THEN** lifecycle semantics and public commands are equivalent
- **THEN** differences are limited to platform discovery and invocation

### Requirement: Least-privilege installation
Integration installation MUST be explicit and scoped, and MUST preserve unrelated global settings,
credentials, and user-authored instruction files.

#### Scenario: Destination content already exists
- **WHEN** an adapter would overwrite unrelated content
- **THEN** installation stops or reports a scoped conflict
- **THEN** the existing content remains unchanged

### Requirement: Portable and honest integration metadata
Adapters MUST use public commands and portable paths, avoid unpublished dependencies, and declare a
validation status of `verified`, `documented`, or `experimental` according to available evidence.

#### Scenario: Validate integration metadata
- **WHEN** an adapter references a missing skill, obsolete command, private path, or unsupported status
- **THEN** integration validation identifies the adapter and fails

### Requirement: Canonical agent routing contract
The project MUST define exactly one canonical routing block stating the conditions under which a host
agent invokes SDR and the conditions under which it MUST NOT. The block MUST be authored once as a
package resource; every published copy MUST be mechanically derived from that source. The block MUST
state that it is user-installed guidance and not enforcement.

#### Scenario: Validate routing block copies
- **WHEN** integration validation compares each published routing block with the canonical source
- **THEN** every copy is equivalent to the canonical text
- **THEN** a divergent copy is identified by adapter and fails validation

#### Scenario: Routing conditions are two-sided
- **WHEN** the canonical routing block is validated
- **THEN** it states conditions that select SDR and conditions that exclude SDR
- **THEN** a block stating only selecting conditions fails validation

### Requirement: Adapter routing guidance
Each supported adapter guide MUST publish the canonical routing block in the form its host consumes,
and MUST NOT redefine the routing conditions. Publishing a routing block MUST NOT change an adapter's
declared validation status.

#### Scenario: Read an adapter guide
- **WHEN** a user opens the Claude Code, Codex, or OpenCode adapter guide
- **THEN** the guide presents the routing block with host-specific installation instructions
- **THEN** the adapter's declared status is unchanged by the block's presence

#### Scenario: An adapter states its own routing conditions
- **WHEN** an adapter guide states routing conditions that differ from the canonical block
- **THEN** integration validation identifies the adapter and fails
