## 1. Language contract and its validator

- [x] 1.1 Add a failing test asserting product-language validation reports Spanish text in the product surface with file and line
- [x] 1.2 Add a failing test proving documentation translations are excluded by path and never reported
- [x] 1.3 Add a failing test proving validation is deterministic across two runs and consults no network
- [x] 1.4 Implement `sdr.product_language` with a module CLI; confirm it fails against the current tree and lists the known Spanish surface
- [x] 1.5 Wire product-language validation into the documented validation commands and CI without weakening existing gates; run the automation tests and workflow syntax validation

## 2. Single declaration of artifact section names

- [x] 2.1 Add a failing test asserting every stage section name resolves from one declaration, with no section literal repeated in `gates.py` or the templates
- [x] 2.2 Implement the single section-name declaration in `schema.py` and make `gates.py` resolve every `section(...)` lookup from it, leaving the names Spanish for now; run the full suite to prove the refactor is behaviour-preserving

## 3. English artifact contract

- [x] 3.1 Add a failing test asserting the packaged templates declare English section names
- [x] 3.2 Translate the five templates, the section-name declaration, and `schema.required_sections` in one step, and retranslate the gate and schema tests that assert section names
- [x] 3.3 Regenerate the maintained `examples/light-complete` and `examples/full-complete` fixtures and prove each completes its lifecycle end to end
- [x] 3.4 Run the full suite and confirm no intermediate state exists in which a freshly created artifact fails its own gate

## 4. Migration for existing investigations

- [x] 4.1 Add a failing test driving `sdr migrate` over a fixture root carrying Spanish headings and asserting it advances afterwards
- [x] 4.2 Add a failing test asserting migration reports the headings it changed and that a second run reports no change required
- [x] 4.3 Add a failing test asserting user-authored prose beneath a structural heading is left byte-identical
- [x] 4.4 Implement heading migration in the `migrate` command; run the focused migration tests and the full suite

## 5. Command-line surface

- [x] 5.1 Add a failing test asserting the group description, every command summary, and every option description are English
- [x] 5.2 Translate `cli.py` help text, the group description, and docstrings that surface to users; retranslate the affected CLI tests with exact-message assertions preserved
- [x] 5.3 Add a failing test asserting `sdr --version` reports the single version source
- [x] 5.4 Implement `--version` without introducing a second version source; run the focused CLI tests

## 6. Runtime messages

- [x] 6.1 Translate user-facing messages in `gates.py` and `schema.py`, retranslating their tests in the same step
- [x] 6.2 Translate user-facing messages in `lifecycle.py`, `research.py`, and `trail.py`, retranslating their tests in the same step
- [x] 6.3 Translate user-facing messages in `parser.py`, `claims.py`, and `probe_verify.py`, retranslating their tests in the same step
- [x] 6.4 Translate user-facing messages in `paths.py` and `network_policy.py`, retranslating their tests in the same step
- [x] 6.5 Translate user-facing messages in `archive.py`, `verification.py`, `verification_ledger.py`, `snapshot.py`, `index.py`, `textual_anchoring.py`, `context_query.py`, and `context_graph.py`, retranslating their tests in the same step
- [x] 6.6 Run the full suite and confirm product-language validation reports no finding for `src/sdr/`

## 6b. Consumers that parse product prose

- [x] 6b.1 Add a failing test proving `bench/harness/friction.py` classifies a blocked reason produced by `lifecycle`, rather than by a literal built in the test
- [x] 6b.2 Retarget `_PROSE_PATTERNS` and `ADVANCE_BLOCKED_PREFIX` at the English prose, preserving the deliberately ambiguous structural/evidential mapping; run the bench friction tests
- [x] 6b.3 Search the repository for any other consumer that matches on Spanish product prose and retarget or report it
- [x] 6b.4 Retarget `cross_investigation.py`'s match on `lifecycle.check_consistency` prose, which reads the translated `stage 'transfer':` text

## 6c. Language-dependent validation logic

- [x] 6c.1 Record that `gates._check_y_statement` matched Spanish tokens in the user's own decision prose, so the transfer gate accepted only Spanish memos; an English memo failed a gate the documentation told the user to satisfy
- [x] 6c.2 Rewrite every Y-statement the project ships or tests so it satisfies the retargeted matcher: the packaged `decision-memo.md` template, both maintained `examples/` memos, `tests/test_e2e.py`, `tests/test_lifecycle.py`, and the eight `bench/corpus/items/*.yaml`
- [x] 6c.3 Add a failing test proving the transfer gate accepts an English Y-statement and still rejects one missing a decision, a context, an evidence clause, or an accepted downside

## 7. Documentation and skills

- [x] 7.1 Add a failing test asserting English documents and canonical skills contain no Spanish text
- [x] 7.2 Clear the Spanish leakage from `docs/getting-started.md`, `docs/README.md`, and the `sdr-intake`, `sdr-transfer`, and `sdr-probe` skills, making every quoted string match the product surface exactly
- [x] 7.3 Update `README.md`, `README.es.md`, `docs/getting-started.es.md`, and `docs/README.es.md` so both languages describe the same English tool and state the migration requirement
- [x] 7.4 Run `sdr.readme_parity`, `sdr.skill_validation`, and `sdr.integration_validation`
- [x] 7.5 Translate the maintained `examples/` fixtures, which the five-minute tour materializes for the reader to inspect, and bring `examples/` into the validated surface
- [x] 7.6 Regenerate the stage hashes and declared digests the fixture translation invalidates, in that order

## 8. Release and final validation

- [x] 8.1 Record the break, the migration requirement, and the English product-surface contract in `CHANGELOG.md`
- [x] 8.2 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [x] 8.3 Run strict OpenSpec, README parity, skill, integration, packaging, product-language, and public-tree validations with no skipped mandatory result
- [x] 8.4 Build the artifacts, re-run the discovery canaries against the new wheel, and record the wheel identity and digest
- [x] 8.5 Install the built artifact in a clean environment and confirm `--help`, `--version`, and a created `brief.md` are English
