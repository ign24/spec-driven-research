## 1. Canonical repository coordinate

- [x] 1.1 Add a failing test asserting `pyproject.toml` declares exactly one canonical repository coordinate under `[project.urls]`
- [x] 1.2 Declare `[project.urls]` from the real repository coordinate without adding a second identity source; run the focused packaging tests
- [x] 1.3 Add a failing test asserting every documented repository URL matches the declared coordinate exactly, reporting file, line, and documented value
- [x] 1.4 Add a failing test proving a URL that only resolves through a rename redirect is reported rather than accepted, without network access
- [x] 1.5 Implement repository-identity findings in `sdr.readme_parity`; run the focused parity tests and the full suite

## 2. Supported installation route

- [x] 2.1 Add a failing test rejecting documentation that claims or implies package-index installation
- [x] 2.2 Add a failing test requiring the documented install instructions to pin an explicit revision
- [x] 2.3 Update `README.md`, `README.es.md`, and `docs/` to present the revision-pinned route and state that no package-index release exists; run the documentation gates

## 3. Verified installation from a repository revision

- [x] 3.1 Add a failing harness test proving verification installs outside the checkout, with the checkout absent from the import path and no publication credentials present
- [x] 3.2 Implement the installation-verification harness against a pinned revision with machine-readable results; run focused tests
- [x] 3.3 Add a failing test driving a complete light lifecycle through the installed console script, including creation from packaged resources and a terminal state
- [x] 3.4 Make the pinned-revision install pass the light lifecycle on every claimed Python minor; run focused and full tests
- [x] 3.5 Add a failing test proving an unavailable verification records an explicit skip reason distinguishable from an absent verification
- [x] 3.6 Wire the verification into CI with network access declared, preserving read-only workflow permissions; run the automation tests and workflow syntax validation

## 4. Canonical agent routing block

- [x] 4.1 Add a failing test requiring exactly one canonical routing block authored as a package resource
- [x] 4.2 Add a failing test rejecting a routing block that states only selecting conditions and no excluding conditions
- [x] 4.3 Author the canonical routing block stating when a host agent invokes SDR, when it must not, and that it is user-installed guidance rather than enforcement
- [x] 4.4 Add a failing test comparing each published adapter copy with the canonical source and identifying a divergent adapter
- [x] 4.5 Publish the block in the Claude Code, Codex, and OpenCode guides with host-specific installation instructions; confirm declared adapter statuses are unchanged
- [x] 4.6 Extend `sdr.integration_validation` with routing-block findings; run integration and full tests

## 5. Bilingual parity

- [x] 5.1 Add failing parity checks requiring both languages to name the same coordinate, route, and routing conditions
- [x] 5.2 Update the Spanish documentation pair and run `sdr.readme_parity` plus the focused documentation tests

## 6. Withdraw the package-index scope

- [x] 6.1 Record the withdrawal of `release-hardening-and-public-distribution` with its reason and the scope inherited by this change
- [x] 6.2 Confirm no remaining public documentation, spec, or workflow claims a package-index release path

## 7. Final validation

- [x] 7.1 Update `CHANGELOG.md` and the affected English and Spanish documentation
- [x] 7.2 Run `uv run pytest`, `uv run ruff check .`, and `uv run ruff format --check .`
- [x] 7.3 Run strict OpenSpec, README parity, skill, integration, packaging, and public-tree validations with no skipped mandatory result
- [x] 7.4 Verify the installed artifact and public tree introduce no private path, credential, or research data
