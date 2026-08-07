## Purpose

Define the material that may be published as the SDR repository and the deterministic checks that
keep research data, local state, and secret-like content outside that boundary.
## Requirements
### Requirement: Framework-only public tree
The repository MUST contain framework code, tests, package resources, public documentation,
canonical skills, integration adapters, synthetic examples, the synthetic evaluation corpus and its
harness, and public specifications only.

#### Scenario: Inspect a candidate public tree
- **WHEN** the repository tree is audited
- **THEN** every included path belongs to a documented public framework category
- **THEN** research outputs and local operator state are absent

#### Scenario: Audit the tree after an evaluation run
- **WHEN** the evaluation harness has executed and the repository tree is audited
- **THEN** the corpus and harness paths are accepted as a documented public category
- **THEN** no research root, lifecycle metadata, or run output produced by the harness is present in
  the tree

### Requirement: Prohibited material exclusion
The public tree MUST exclude real investigations, knowledge outputs, notebooks, environment files,
caches, nested repositories, credentials, private paths, private endpoints, and generated build
artifacts.

#### Scenario: Prohibited content is present
- **WHEN** an excluded path or sensitive value is detected
- **THEN** the audit reports its category and location without echoing the value
- **THEN** release readiness fails

### Requirement: Independent repository ownership
Repository publication operations MUST act only on the current repository and MUST NOT assume,
rewrite, or mutate another repository's history or working tree.

#### Scenario: Prepare the repository for publication
- **WHEN** Git identity and history are inspected
- **THEN** only this repository's object database, branch, and configured remotes are considered
- **THEN** no external working tree is changed

### Requirement: Public identity consistency
Repository, package, command, license, and copyright metadata MUST identify the same public project across packaging and governance files.

#### Scenario: Compare public metadata
- **WHEN** package and repository metadata are validated
- **THEN** the distribution is `spec-driven-research`
- **THEN** the import package and console command are `sdr`
- **THEN** the declared license is MIT

### Requirement: Synthetic public examples
Published examples MUST use invented identities and results, reserved-domain URLs, relative paths,
and reproducible local inputs rather than sanitized copies of real investigations.

#### Scenario: Audit an example
- **WHEN** an example fixture is reviewed or executed
- **THEN** its inputs are demonstrably synthetic
- **THEN** it contains no redistributed third-party snapshot or precomputed lifecycle metadata

### Requirement: Deterministic boundary audit
The repository MUST provide a repeatable public-tree audit whose findings have stable ordering and
whose output redacts any detected sensitive value.

#### Scenario: Run the public-tree audit twice
- **WHEN** the same unchanged tree is audited twice
- **THEN** both runs return the same ordered findings
- **THEN** safe trees return success and unresolved findings return failure

