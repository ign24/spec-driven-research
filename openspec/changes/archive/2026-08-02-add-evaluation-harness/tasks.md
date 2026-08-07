## 1. Boundary and packaging

- [x] 1.1 Add a failing test asserting the public-tree audit accepts `bench/` as a documented public category
- [x] 1.2 Extend `src/sdr/public_tree_audit.py` with the evaluation category and make 1.1 pass
- [x] 1.3 Add a failing test asserting `bench/` is absent from the built sdist and wheel
- [x] 1.4 Exclude `bench/` from the sdist in `pyproject.toml` next to `openspec/changes` and make 1.3 pass
- [x] 1.5 Confirm `uv run pytest tests/test_public_tree_audit.py tests/test_artifact_audit.py` passes

## 2. Corpus contract

- [x] 2.1 Add a failing test for corpus loading: unique item ids, mode in `light`/`full`, defects drawn from the closed vocabulary
- [x] 2.2 Define the planted-defect vocabulary from the failure taxonomy in `docs/evidence-model.md`, not from `gates.py`
- [x] 2.3 Implement `bench/harness/corpus.py` with typed loading and explicit failure on undeclared defect kinds
- [x] 2.4 Add a failing test asserting every corpus source URL uses a reserved or non-resolvable domain
- [x] 2.5 Implement the synthetic-content validator and make 2.4 pass

## 3. Corpus items

- [x] 3.1 Author at least two clean items with an empty planted-defect list, one `light` and one `full`
- [x] 3.2 Author an item with an unreachable source, unreachable by construction rather than by external failure
- [x] 3.3 Author an item with a claim not anchored in its cited source
- [x] 3.4 Author an item with contradictory sources across alternatives
- [x] 3.5 Author a `full` item whose probe expectation does not match its command
- [x] 3.6 Author at least one item whose defect no current control is expected to catch, and record that expectation
- [x] 3.7 Verify every note body uses the section headings the contract enforces today, including the Spanish headings

## 4. Run isolation

- [x] 4.1 Add a failing test asserting a run materializes its research root outside the repository tree
- [x] 4.2 Implement `bench/harness/runspace.py`: temporary root, `SDR_ROOT`, `git init`, guaranteed cleanup
- [x] 4.3 Add a failing test asserting the repository tree contains no `research` or `knowledge` directory after a run
- [x] 4.4 Add a failing test asserting parallel runs never share a research root or a lifecycle metadata file
- [x] 4.5 Implement bounded parallelism over disjoint roots and make 4.4 pass

## 5. Actor interface

- [x] 5.1 Define the typed actor protocol: execute a run, report stage boundaries, report token usage or unavailability
- [x] 5.2 Implement the scripted actor replaying declared artifact writes and CLI invocations from the corpus item
- [x] 5.3 Add a failing test asserting two scripted runs over one item produce identical detection scoring
- [x] 5.4 Implement the live actor behind an explicit opt-in flag, defaulting off, requiring no API key when unused
- [x] 5.5 Add a test asserting the harness runs end to end offline with the scripted actor

## 6. Arm execution

- [x] 6.1 Implement the three arms: no-SDR baseline, SDR light, SDR full
- [x] 6.2 Add a failing test asserting N repetitions produce N run records per applicable arm
- [x] 6.3 Implement not-applicable handling so a `light` item is not scored as a failure in the full arm
- [x] 6.4 Mark the scripted baseline arm as a control constant rather than a measured detection rate

## 7. Detection scoring

- [x] 7.1 Add a failing test asserting each planted defect is scored caught or missed with the reporting control named
- [x] 7.2 Implement detection scoring from `sdr check --json`, `sdr verify-claims --json`, and `sdr verify-probe --json`
- [x] 7.3 Add a failing test asserting a run that failed for an unrelated reason is not scored as a detection
- [x] 7.4 Add a failing test asserting a blocking finding on a clean item is scored as a false positive with its control

## 8. Cost accounting

- [x] 8.1 Add a failing test asserting a run record carries total wall-clock and a per-stage breakdown
- [x] 8.2 Implement stage-boundary timing from actor-reported stage transitions
- [x] 8.3 Add a failing test asserting missing token usage is recorded as unavailable, never as zero
- [x] 8.4 Implement token accounting from the live actor's reported usage

## 9. Friction accounting

- [x] 9.1 Add a failing test asserting reopen transitions are counted with origin and target stage
- [x] 9.2 Implement reopen counting from trail commits in the run's Git root
- [x] 9.3 Add a failing test asserting gate failures are attributed to the documented control vocabulary
- [x] 9.4 Implement the control-type mapping table with an explicit `unmapped` bucket for unmatched prose reasons
- [x] 9.5 Add a failing test asserting claims closed through `resolve-claim` are counted separately from claims that passed anchoring

## 10. Run record and report

- [x] 10.1 Add a failing test asserting every run-record metric traces to an artifact path, exit code, or structured CLI field
- [x] 10.2 Implement the typed run-record schema and its serialization
- [x] 10.3 Add a failing test asserting two report generations over unchanged run records are byte-identical
- [x] 10.4 Implement the report with stable ordering, fixed numeric precision, and no timestamps in the body
- [x] 10.5 Implement report sections separated by actor, never aggregating scripted and live results into one number
- [x] 10.6 Include corpus version, repetition count, token-coverage share, and the `unmapped` bucket in the report

## 11. Verification and documentation

- [x] 11.1 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [x] 11.2 Execute the full scripted harness offline and commit no run output to the repository
- [x] 11.3 Run the public-tree audit after a harness execution and confirm no findings
- [x] 11.4 Write `bench/README.md` covering the corpus contract, the two actors, how to run each arm, and how to read the report
- [x] 11.5 Add a `CHANGELOG.md` entry under Unreleased for the evaluation harness
- [x] 11.6 Record the first scripted baseline results and list defects no current control catches, as input to the next roadmap phase
