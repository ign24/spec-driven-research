## ADDED Requirements

### Requirement: Canonical repository coordinate
The project MUST declare exactly one canonical public repository coordinate in `pyproject.toml` under
`[project.urls]`, and that declaration MUST be the only source of repository identity. No second
repository coordinate may be introduced elsewhere in the package or its runtime code.

#### Scenario: Read the declared coordinate
- **WHEN** the packaging metadata is inspected
- **THEN** exactly one canonical repository coordinate is declared
- **THEN** validation derives every repository expectation from that declaration

#### Scenario: Reject a second identity source
- **WHEN** a repository coordinate is added outside `[project.urls]`
- **THEN** validation fails and names the conflicting location

### Requirement: Documented repository coordinates resolve directly
Every repository URL in public documentation MUST match the declared canonical coordinate exactly. A
URL that reaches the canonical repository only through a rename redirect MUST be reported as a
finding, not accepted.

#### Scenario: Documentation names the canonical repository
- **WHEN** repository-identity validation runs over public documentation
- **THEN** it reports no finding

#### Scenario: Documentation names a superseded repository name
- **WHEN** a documented URL names a repository other than the declared coordinate
- **THEN** validation fails and names the file, line, and documented coordinate
- **THEN** the result does not depend on network access

### Requirement: Supported installation route
Public documentation MUST present installation from a pinned public repository revision as the
supported route, and MUST NOT claim or imply that installation from a package index is available.

#### Scenario: Install instructions name a revision
- **WHEN** a reader follows the documented installation instructions
- **THEN** the instructions install from the canonical repository at an explicit revision
- **THEN** the instructions state that no package-index release exists

### Requirement: Verified installation from a repository revision
The project MUST verify by execution that installing from a pinned repository revision produces a
working `sdr` console script. The verification MUST install into an environment outside the source
checkout with the checkout absent from the import path, and MUST drive a complete light lifecycle
through the installed console script rather than only an invocation check.

#### Scenario: Install and complete a lifecycle
- **WHEN** installation verification runs against the revision under test
- **THEN** the installed console script creates an investigation from packaged resources
- **THEN** the investigation reaches its terminal state without importing the source checkout

#### Scenario: Report an unavailable verification
- **WHEN** installation verification cannot run because network access is unavailable
- **THEN** the result records an explicit skip reason
- **THEN** the skipped verification is distinguishable from a verification that did not exist
