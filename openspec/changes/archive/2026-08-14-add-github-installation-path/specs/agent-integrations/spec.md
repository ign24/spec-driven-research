## ADDED Requirements

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
