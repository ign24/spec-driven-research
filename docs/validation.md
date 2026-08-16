# Maintenance and Validation

This public document is the maintenance checklist for framework contributors.
Tests and automation should link here.

## Validation controls

Runtime documentation uses this conceptual order: **Structural**,
**Evidential**, **Textual anchoring**, **Executable**, **Hash consistency**, and
**HITL**. The implementation checks consistency before current-stage controls.
The Context Graph is auxiliary and non-blocking, not complete lineage.

## Local quality checks

Install all development and optional dependencies:

```bash
uv sync --all-extras --dev
```

Run focused tests during TDD, then the complete checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

GitHub Actions runs this gate on `ubuntu-latest` with Python 3.12 and Python 3.13.
This Ubuntu/Linux matrix is the current compatibility evidence. Windows and
macOS are not currently validated or claimed as supported platforms.

Package validation builds both a wheel and source distribution, verifies that
`src/sdr/__init__.py` supplies the version, runs `twine check`, enforces archive
member allowlists, audits extracted content with `sdr.public_tree_audit`, and
installs each artifact in a separate environment for smoke tests.

## Installation verification

`sdr.install_verification` proves by execution that the documented installation
route works. It installs the canonical repository at an explicit revision into a
temporary environment outside the checkout, with `PYTHONPATH` and publication
credentials stripped from the child environment, then drives a complete light
lifecycle through the installed `sdr` console script and compares the created
`brief.md` against the packaged template. Asserting only that `sdr --help` exits
zero would not detect missing packaged resources, which is the failure that
separates a working install from an importable one.

These tests carry the `installation` marker and require network access, because
the pinned revision must be reachable at the canonical repository. The default
gate above deselects them:

```bash
uv run pytest -rs -m "not installation"
uv run pytest -rs -m installation
```

A revision that is not yet published records an explicit skip reason rather than
passing silently, so a skipped verification stays distinguishable from a
verification that does not exist. Always run with `-rs` so those reasons are
printed. In CI the revision under test is always published, so the dedicated
`installation` job runs the verification for real.

## Documentation checks

`tests/test_templates.py` verifies the public documentation inventory,
validation terminology, full/light lifecycle, offline example, contextual
citation semantics, approval separation, trust boundaries, and probe execution
contract. `tests/test_examples_e2e.py` runs the maintained synthetic light and
full fixtures offline and verifies their final states without modifying them.
`sdr.readme_parity` verifies concept coverage across all three bilingual pairs:
the root READMEs, documentation homes, and beginner guides. It also resolves
their local links and fixture references and rejects package-index installation
claims. It derives the canonical repository coordinate from `[project.urls]` in
`pyproject.toml`, reports any documented repository URL that names a different
coordinate with its file, line, and documented value, and reports a documented
git install that does not pin an explicit revision. A URL that reaches the
canonical repository only through a rename redirect is a finding, not a pass;
the check never consults the network. Findings are actionable; equivalent
nonliteral translations are accepted.
`tests/test_packaging.py` verifies README, license, author, version, changelog,
and repository-coordinate metadata. `sdr.readme_parity` also requires both
languages to document the same set of installation routes and to reference the
same agent routing conditions, reporting the language that claims a route or
states a condition its counterpart does not.

`sdr.product_language` validates that the product surface is English. It scans the
string literals of `src/sdr/**/*.py` and every file under `src/sdr/templates/`,
which together are what a user reads from the installed tool, and reports each
Spanish marker with its file and line. It keys on the Spanish-specific characters
and on a closed list of Spanish function words that are not English words, so it
is conservative by construction: it will not catch English-looking Spanish and it
will not misread English prose. It is deterministic and never consults the
network. Documentation translations are excluded by path, not by heuristic:
`README.es.md`, `docs/*.es.md`, and anything under `openspec/changes/archive/`.
`src/sdr/readme_parity.py` and `src/sdr/product_language.py` are excluded for the
same reason, because both carry Spanish markers as validation data rather than as
text addressed to a user. Spanish remains a documentation translation; the tool
offers no runtime language selection.

`sdr.integration_validation` compares every published copy of the canonical
agent routing block with its package resource and reports a divergent adapter by
name. It also rejects a block that states only selecting conditions without
excluding ones, and one that omits the statement that the block is guidance
rather than enforcement. Author the block once; never edit a published copy.

Add public maintenance guidance here and keep assertions tied to observable
behavior. When any onboarding pair or the synthetic tour changes, preserve the
shared contract in both languages and run the focused gates first:

```bash
uv run pytest tests/test_readme_parity.py tests/test_templates.py tests/test_examples_e2e.py
uv run python -m sdr.readme_parity .
uv run python examples/runner.py light-complete --root "$(mktemp -d)/research"
```

Then run the broader documentation and repository checks:

```bash
uv run python -m sdr.skill_validation .
uv run python -m sdr.integration_validation validate .
uv run python -m sdr.product_language .
npm install --global @fission-ai/openspec@1.2.0
openspec validate --specs --strict --no-interactive
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.7
```

## Release checklist

1. Confirm an active OpenSpec change covers user-visible behavior.
2. Ensure new behavior was developed from a failing focused test.
3. Update affected English and Spanish docs plus `CHANGELOG.md`.
4. Confirm `src/sdr/__init__.py` remains the only version source of truth.
5. Run the full test, lint, and format commands above.
6. Build artifacts and run the public-tree audit before publication.
7. Inspect package metadata and wheel contents for private material.
8. Verify integration statuses remain honest; `documented` is not E2E-tested.
9. Follow [Releasing](releasing.md); no package-index publishing route exists.

## Public content audit

Never publish research directories, knowledge artifacts, notebooks, environment
files, caches, nested repositories, credentials, private paths, organization
names, or real investigation data. Run:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run python -m sdr.public_tree_audit . \
  --exclude .git --exclude .claude --exclude .venv --exclude .codegraph \
  --exclude .pytest_cache --exclude .ruff_cache \
  --exclude build --exclude dist --exclude examples/__pycache__ \
  --exclude src/sdr/__pycache__ --exclude tests/__pycache__ \
  --exclude bench/__pycache__ --exclude bench/harness/__pycache__
```

Review findings manually. Pattern matching cannot prove that content is safe.
The exclusions are ignored local, cache, and build state outside the candidate
public surface. This command does not inspect or delete excluded state.

## Security checks

The security workflow exports locked runtime requirements and runs `pip-audit`
without ignored advisories. It also uses the official Gitleaks CLI fixed at
`v8.28.0` to scan the working tree, complete Git history, staged patch, and
built archives with `--redact`. The CLI tag resolves to commit
`39fdb480a06768cc41a84ef86959c07ff33091c4`.

The commercial `gitleaks-action` v2 is intentionally not used because
organization repositories require a license secret and its PR-comment behavior
needs broader GitHub API access. The secretless CLI is complemented by the local
public-tree and artifact auditors:

```bash
uv export --locked --no-dev --no-emit-project --no-hashes --output-file /tmp/sdr-runtime.txt
uv run pip-audit --requirement /tmp/sdr-runtime.txt
# Run the exact candidate-surface public-tree command above.
gitleaks dir --redact --no-banner .
gitleaks git --redact --no-banner --log-opts="--all" .
git diff --cached --binary | gitleaks --redact --no-banner stdin
uv build
uv run twine check dist/*
uv run python -m sdr.artifact_audit dist/*
gitleaks dir --redact --no-banner --max-archive-depth=1 dist
```

Pass the candidate wheel and matching sdist to the artifact auditor together.
Their independent content checks are preserved, and the paired checks compare
packaged integration resources and bind the sdist's sanitized canary evidence
to the wheel filename and version.

The canary evidence also records the SHA-256 of the wheel the canaries actually
ran against. Every rebuild from changed sources produces a different digest, so
that field is provenance rather than a routine gate. Add `--release` when
auditing the exact artifacts being promoted; it additionally requires the
recorded digest to match the audited wheel bytes:

```bash
uv run python -m sdr.artifact_audit --release dist/*
```

`--release` therefore requires re-running the documented discovery canaries
against the candidate build before publication.

All external GitHub Actions are pinned to full commit SHAs, each followed on the
same line by a `# vX.Y.Z` comment recording the release tag that SHA claims to
be. Both properties are enforced by `tests/test_automation.py`, and every pin of
the same action must agree across workflows. The comment has to trail the ref
because that is the only form Dependabot keeps in step with the SHA it writes;
`.github/dependabot.yml` checks GitHub Actions and the Python/uv lock inputs
weekly.
