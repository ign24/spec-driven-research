## 1. Corpus and execution-boundary prerequisites

- [x] 1.1 Add failing tests that reject retained corpus snapshots lacking current provenance and participating completed decisions lacking required `evidence_claim_ids`
- [x] 1.2 Run the focused corpus tests and confirm they fail for the intended provenance and lineage reasons
- [x] 1.3 Migrate retained software lifecycle-control fixtures and add versioned baseline-provenance metadata without preserving a new baseline yet
- [x] 1.4 Run the focused corpus tests and make them pass
- [x] 1.5 Add failing tests that scripted, mutation, and metamorphic subprocess environments exclude credential-shaped variables and record executable/package provenance
- [x] 1.6 Run the focused environment tests and confirm they fail before subprocess execution
- [x] 1.7 Implement independent allowlisted non-live and narrowly inherited live environment builders
- [x] 1.8 Run the focused environment tests and make them pass before any mutation or live subprocess task

## 2. Honest lifecycle-control scoring

- [x] 2.1 Add failing tests for skipped-control and lifecycle-not-entered outcomes as `not-exercised`, including reporting-control or lifecycle reason
- [x] 2.2 Add failing tests that detection rate includes only caught and missed outcomes and reports `not-exercised` separately
- [x] 2.3 Run the focused detection tests and confirm the intended failures
- [x] 2.4 Implement the three lifecycle detection outcomes and denominator behavior
- [x] 2.5 Run the focused detection and deterministic-report tests and make them pass

## 3. Blocking-control mutation validation

- [x] 3.1 Add failing tests that a declared mutation changes only a throwaway package copy, fails on a stale/no-op transformation, and uses the credential-free environment
- [x] 3.2 Run the focused mutation-application tests and confirm the intended failures
- [x] 3.3 Implement declarative single-control mutations and isolated package-copy execution
- [x] 3.4 Run the focused mutation-application tests and make them pass
- [x] 3.5 Add failing tests that a mutation loses exactly baseline catches attributable to its control and that an unchanged detection projection fails validation
- [x] 3.6 Add a failing coverage audit in canonical control order, requiring a mutation or explicit infeasibility reason for every blocking control type
- [x] 3.7 Run the focused mutation-validation tests and confirm the intended failures
- [x] 3.8 Implement baseline comparison, no-op rejection, and explicit blocking-control coverage reporting
- [x] 3.9 Run the focused mutation-validation tests and make them pass offline

## 4. Isolated reuse corpus

- [x] 4.1 Add failing fixture-validation tests requiring one isolated scenario root, immutable completed seeds, exactly one focal investigation, and arm/history as orthogonal fields
- [x] 4.2 Add failing fixture-validation tests requiring current snapshot provenance, explicit seed decision `evidence_claim_ids`, a non-software scenario, and exact positive and negative controls
- [x] 4.3 Run the focused reuse-fixture tests and confirm the intended failures
- [x] 4.4 Implement the reuse scenario schema and materializer with pre/post seed hashes and disjoint external roots
- [x] 4.5 Add migrated software reuse fixtures plus at least one synthetic non-software fixture with exact negative controls
- [x] 4.6 Run the focused reuse-fixture tests and make them pass

## 5. Exact and metamorphic reuse validation

- [x] 5.1 Add failing tests distinguishing `not-consulted`, `not-exercised`, `incorrect`, and `correct` cross-check outcomes
- [x] 5.2 Add failing tests for exact investigation-qualified positive results and exact negative-control absences
- [x] 5.3 Run the focused cross-scoring tests and confirm the intended failures
- [x] 5.4 Implement cross consultation capture and exact structured projection comparison without model judgement
- [x] 5.5 Run the focused cross-scoring tests and make them pass
- [x] 5.6 Add failing metamorphic tests for seed-order invariance, URL-normalization invariance, explicit-provenance removal, and `done`-to-`active` decision exclusion
- [x] 5.7 Add a failing test proving fixture metamorphisms do not satisfy blocking-control mutation coverage
- [x] 5.8 Run the focused metamorphic tests and confirm the intended failures
- [x] 5.9 Implement fixture-only metamorphic execution and exact relation comparison
- [x] 5.10 Run the focused metamorphic and mutation-separation tests and make them pass offline

## 6. Prompt treatments and evidence separation

- [x] 6.1 Add failing tests that prompts leak no planted defect, retrieval expectation, negative control, or scoring vocabulary
- [x] 6.2 Add failing tests that assisted prompts require cross consultation without naming expected queries/results and unassisted prompts contain no cross guidance
- [x] 6.3 Add failing tests that assisted/unassisted policies, history conditions, evaluation questions, and incompatible template versions are never aggregated
- [x] 6.4 Run the focused prompt and grouping tests and confirm the intended failures
- [x] 6.5 Implement versioned expectation-blind templates and separate treatment grouping
- [x] 6.6 Run the focused prompt and grouping tests and make them pass

## 7. Bounded live connector

- [ ] 7.1 Add failing tests that either missing live opt-in key prevents host startup, credential reads, and network access
- [ ] 7.2 Add failing fake-host tests for external working root, exact structured session-id capture, host/model/version provenance, and repository pre/post audit
- [ ] 7.3 Add failing fake-host tests that usage comes only from that session export, unavailable values carry reasons, and neither transcript nor raw export is persisted
- [ ] 7.4 Add failing process-tree tests for turn-bound and wall-clock-bound descendant termination and runspace teardown
- [ ] 7.5 Run the focused live-connector tests and confirm the intended failures
- [ ] 7.6 Implement the two-key OpenCode connector, exact session export attribution, aggregate-only persistence, and bounded process-group teardown
- [ ] 7.7 Run the focused live-connector tests and make them pass without a real host or credential

## 8. Approval boundary and pilot planning

- [ ] 8.1 Add failing tests that live execution stops at transfer as `operator-pending` without invoking `approve`, fabricating an approver, editing metadata, or substituting `resolve-claim`
- [ ] 8.2 Add failing tests distinguishing `not-reached`, operator pending/decided, and synthetic fixture states and rejecting synthetic state from initial live evidence
- [ ] 8.3 Run the focused approval-state tests and confirm the intended failures
- [ ] 8.4 Implement transfer-stop behavior and explicit approval provenance states
- [ ] 8.5 Run the focused approval-state tests and make them pass
- [ ] 8.6 Add failing tests that a pilot plan identifies exactly one scenario/item, arm, repetition, host/model, and prompt policy/template and cannot expand across arms or repetitions
- [ ] 8.7 Add failing tests that observed identity mismatch fails attribution and that the first reuse pilot accepts assisted policy only
- [ ] 8.8 Run the focused pilot tests and confirm the intended failures
- [ ] 8.9 Implement exact one-session pilot planning, validation, execution, and exit reporting
- [ ] 8.10 Run the focused pilot tests and make them pass against the fake host

## 9. Complete run-record schema version 2

- [ ] 9.1 Add failing schema tests covering every field in Design Decision 10 for lifecycle-control, live-single-investigation, and cross-retrieval records
- [ ] 9.2 Add failing schema tests for version-1 rejection, conditional live attribution, approval-state consistency, treatment/provenance incompatibility, and transcript/raw-export prohibition
- [ ] 9.3 Run the focused schema tests and confirm the intended failures
- [ ] 9.4 Implement the complete schema version 2 in one migration, with no intermediate persisted schema shape or version-1 coercion
- [ ] 9.5 Run the focused schema, serialization, and fixture tests and make them pass
- [ ] 9.6 Add failing report tests for three separate question sections, four cross outcomes, non-aggregated treatments/history, stable rendering, and the explicit non-claims block
- [ ] 9.7 Run the focused report tests and confirm the intended failures
- [ ] 9.8 Implement deterministic question-specific reporting from schema-version-2 records only
- [ ] 9.9 Run the focused report tests twice over unchanged records and make them byte-identical

## 10. Scripted evidence and baseline preservation

- [ ] 10.1 Run the migrated corpus validation and preserve new versioned scripted lifecycle baselines only after provenance and lineage checks pass
- [ ] 10.2 Run blocking-control mutation validation offline and record observable controls and explicit unresolved coverage reasons without making effectiveness claims
- [ ] 10.3 Run scripted/metamorphic reuse validation offline, including the non-software scenario and exact negative controls
- [ ] 10.4 Generate separate deterministic lifecycle-control and cross-retrieval evidence sections and verify all prohibited claims are absent

## 11. Operator and HITL gates

- [ ] 11.1 Present the exact proposed initial assisted pilot plan to the operator, including scenario/item, arm, repetition, host/version, model, template policy/version, bounds, and external results root
- [ ] 11.2 Obtain explicit operator authorization for exactly that one paid session before enabling both live keys
- [ ] 11.3 Execute exactly the authorized pilot, stop at transfer pending real operator approval, and persist only schema-version-2 aggregate/structured evidence outside the repository
- [ ] 11.4 Present observed usage, cost, wall-clock, workflow/friction, consultation outcome, terminal state, and approval state to the operator
- [ ] 11.5 Obtain and record the real operator HITL decision separately; do not let the agent approve and do not substitute synthetic approval
- [ ] 11.6 Obtain a separate explicit operator decision before any additional paid session or later unassisted spontaneous-discovery measurement

## 12. Final verification and documentation

- [ ] 12.1 Add failing documentation-contract tests for the three separate questions, corpus migration, credential boundary, exact reuse outcomes, treatments, pilot identity, HITL stop, schema version 2, and prohibited claims
- [ ] 12.2 Run the focused documentation tests and confirm the intended failures
- [ ] 12.3 Update harness documentation to satisfy the new contract without changing release-hardening scope
- [ ] 12.4 Run the focused documentation tests and make them pass
- [ ] 12.5 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [ ] 12.6 Run the public-tree audit after scripted and authorized live execution and confirm no repository writes, transcript, raw export, credential, or private path was introduced
- [ ] 12.7 Record remaining evidence gaps as follow-up inputs without modifying or absorbing the separate release-hardening change
