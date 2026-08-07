## MODIFIED Requirements

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
